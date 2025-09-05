"""
This module contains a variety of functions to create different input files.

@author: Jeffrey Wade, Xia Feng
"""

import copy
import datetime
import fnmatch
import glob
import json
import os
import subprocess
import logging
import shutil
import math
from pathlib import Path
from typing import List, Union, Dict
from collections import OrderedDict

import geopandas as gpd
import pandas as pd
import yaml

from mswm.utils import settings


logger = logging.getLogger(__name__)


class QuotedDumper(yaml.SafeDumper):
    pass


class UnquotedDumper(yaml.SafeDumper):
    pass


class ForcingDumper(yaml.SafeDumper):
    pass


def quoted_str_presenter(dumper, data):
    return dumper.represent_scalar('tag:yaml.org,2002:str', data, style="'")


def inline_list_presenter(dumper, data):
    return dumper.represent_sequence('tag:yaml.org,2002:seq', data, flow_style=True)


ForcingDumper.add_representer(list, inline_list_presenter)
QuotedDumper.add_representer(str, quoted_str_presenter)


def is_probably_regex(pattern):
    return any(c in pattern for c in ['^', '$', '.', '(', '[', '|', '\\'])


__all__ = [
    'create_walk_file',
    'create_cfe_input',
    'create_noah_input',
    'create_noah_input_template',
    'create_sft_smp_input',
    'create_snow17_input',
    'create_ueb_input',
    'create_sac_input',
    'change_sac_snow17_input',
    'create_pet_input',
    'create_lasam_input',
    'change_lasam_input',
    'create_lstm_input',
    'change_smp_input',
    'change_sft_input',
    'change_topmodel_input',
    'create_topmodel_input',
    'create_troute_config',
    'update_forcing_config',
    'create_reg_realization_file',
    'create_realization_file',
    'create_calib_config_file',
    'create_partition_file',
]


def create_walk_file(
        gageID: str,
        gpkg_file: Union[str, Path],
        walk_file: Union[str, Path],
) -> None:
    """ Create crosswalk file

    Parameters
    ----------
    gageID : stream gage ID at the outlet of basin
    gpkg_file : hydrofabric GeoPackage file
    walk_file : crosswalk file

    Returns
    ----------
    None

    """

    df_cat = gpd.read_file(gpkg_file, layer='divides')
    df_cat.set_index('divide_id', inplace=True)
    df_nexus = gpd.read_file(gpkg_file, layer='nexus')

    # read hl_uri info from network or hydrolocations layers and make sure the gageID is contained in the hl_uri column
    # check the hydrolocations layer first, if conditions are not met, check the network layer
    df_network = gpd.read_file(gpkg_file, layer='network')
    df_hydro = gpd.read_file(gpkg_file, layer='hydrolocations')
    if (len(df_network) > 0) and ('toid' in df_network.columns) and ('hl_uri' in df_network.columns) and (
            df_network['hl_uri'].str.contains(gageID).any()):
        df_network = df_network[['toid', 'hl_uri']].drop_duplicates()
        df_network.columns = ['id', 'hl_uri']
        df_nexus = df_nexus.merge(df_network, on="id")
    else:
        if (len(df_hydro) > 0) and ('nex_id' in df_hydro.columns) and ('hl_uri' in df_hydro.columns) and (
                df_hydro['hl_uri'].str.contains(gageID).any()):
            df_hydro = df_hydro[['nex_id', 'hl_uri']].drop_duplicates()
            df_hydro.columns = ['id', 'hl_uri']
            df_nexus = df_nexus.merge(df_hydro, on="id")

    if 'hl_uri' not in df_nexus.columns:
        if ('hl_uri' not in df_network.columns) and ('hl_uri' not in df_hydro.columns):
            try:
                raise Exception(f"Gage id {gageID}: 'hl_uri' column not found in network or hydrolocations layers in {gpkg_file}")
            except Exception as e:
                logger.critical(e)
                raise
        else:
            try:
                raise Exception(f"Gage id {gageID} not found in 'hl_uri' column in network or hydrolocations layers in {gpkg_file}")
            except Exception as e:
                logger.critical(e)
                raise

    df_nexus.set_index('id', inplace=True)
    df_flowpaths = gpd.read_file(gpkg_file, layer='flowpaths')
    df_flowpaths = df_flowpaths.sort_values('hydroseq')
    df_flowpaths.set_index('toid', inplace=True)

    gageid = []
    cw = {}
    for x in df_cat.index:
        nex_id = df_cat.loc[x, 'toid']
        catcw = {x: {"Gage_no": ""}}

        if nex_id in df_nexus.index:
            try:
                hu_list = df_nexus.loc[nex_id, 'hl_uri']
            except KeyError:
                try:
                    raise Exception(f"Gage id {gageID}: nex_id '{nex_id}' could not be accessed in df_nexus when retrieving hl_uri for file {gpkg_file}")
                except Exception as e:
                    logger.critical(e)
                    raise

            if isinstance(hu_list, str) or hu_list is None:
                hu_list = [hu_list]
            elif isinstance(hu_list, pd.Series):
                hu_list = list(hu_list)
            else:
                try:
                    raise Exception(f"Gage id {gageID}: Unsupported return value for hl_uri; must be None, str, or pd.Series (got {type(hu_list)}) for file {gpkg_file}")
                except Exception as e:
                    logger.critical(e)
                    raise
            for hu in hu_list:
                if hu and hu.lower().startswith('gage'):
                    if len(hu.split(',')) > 1 and gageID in hu:
                        gage = gageID
                    else:
                        gage = hu.split('-')[1]
                    gageid.append(gage)
                    if gage == gageID:
                        subdf = df_flowpaths.loc[[df_cat.loc[x, 'toid']]]
                        if subdf.shape[0] == 1:
                            catcw = {x: {"Gage_no": gage}}
                            break
                        else:
                            # Select nearest one among multiple catchments draining to the gage
                            if subdf['id'].iloc[-1].replace('wb', 'cat') == x:
                                catcw = {x: {"Gage_no": gage}}
                                break

        cw.update(catcw)

    if len(set(gageid)) > 1:
        logger.info(f'More than 1 gage found in hydrofabric GeoPackage file {gpkg_file}')
    with open(walk_file, 'w') as outfile:
        json.dump(cw, outfile, indent=4, separators=(", ", ": "), sort_keys=False)


def create_cfe_input(
        catids: List[str],
        modules: Union[List[str], List[List[str]]],
        dfa: Union[str, Path],
        cfe_input_dir: Union[str, Path],
        run_type: str
) -> None:
    """ Create BMI initial configuration file for CFE with Schaake or Xianjiang infiltration and runoff scheme

    Parameters
    ----------
    catids : catchment IDs in the basin
    modules: list of modules in the formulation
    dfa: dataframe containing model parameter attributes
    cfe_input_dir: directory to save configuration files
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

    Note
    ----------
    User needs to compute GIUH using other software like R whitebox package following the example
    https://github.com/NOAA-OWP/SoilMoistureProfiles/blob/ajk/basin_workflow/basin_workflow/giuh_twi/giuh.R
    and replace the fixed GIUH assigned in this code with the calculated value.

    """

    os.makedirs(cfe_input_dir, exist_ok=True)

    # surface partitioning scheme
    scheme = 'Schaake'
    if run_type != 'regionalization':
        mods = modules
        if 'cfex' in mods:
            scheme = 'Xinanjiang'

    # Create bmi config files
    for i in range(len(catids)):

        catID = catids[i]

        # Set module list for each catchment during regionalization
        if run_type == 'regionalization':
            mods = modules[i]
            if ('cfex' in mods):
                scheme = 'Xinanjiang'

        # Set sft coupling
        if 'sft' in mods:
            sft_coupled = '1'
        else:
            sft_coupled = '0'

        # # TODO Temporary fix: set bexp value to very small value if it equals 0. Otherwise SMP raises an error
        bexp_val = dfa.loc[catID]['mode.bexp_soil_layers_stag=1']
        if bexp_val <= 0:
            bexp_val = 0.001

        satpsi_val = dfa.loc[catID]['geom_mean.psisat_soil_layers_stag=1']
        if satpsi_val <= 0:
            satpsi_val = 0.001

        cfe_bmi_file = os.path.join(cfe_input_dir, catID + "_bmi_config_cfe.txt")
        f = open(cfe_bmi_file, "w")
        f.write("%s" % ("forcing_file=BMI\n"))
        f.write("%s" % ("verbosity=1\n"))
        f.write("%s" % ("surface_water_partitioning_scheme=" + scheme + "\n"))
        f.write("%s" % ("surface_runoff_scheme=GIUH\n"))
        f.write("%s" % ("DEBUG=0\n"))
        f.write("%s" % ("num_timesteps=1\n"))
        if 'cfes' in mods:
            f.write("%s" % ("is_sft_coupled=" + sft_coupled + "\n"))
            f.write("%s" % ("ice_content_threshold=0.15\n"))
        f.write("%s" % ("alpha_fc=0.33\n"))  # TODO Update per soil type
        f.write("%s" % ("Cgw=" + str(dfa.loc[catID]['mean.Coeff'] * 3600 * 1e-6) + "[m/hr]\n"))
        f.write("%s" % ("expon=" + str(dfa.loc[catID]['mode.Expon']) + "[]\n"))
        f.write("%s" % ("giuh_ordinates=0.55, 0.25, 0.2[]\n"))
        f.write("%s" % ("gw_storage=0.05[m/m]\n"))
        f.write("%s" % ("K_lf=0.01[]\n"))
        f.write("%s" % ("K_nash=0.003[1/m]\n"))
        f.write("%s" % ("max_gw_storage=" + str(dfa.loc[catID]['mean.Zmax'] / 1000.) + "[m]\n"))
        f.write("%s" % ("nash_storage=0.0,0.0[]\n"))
        f.write("%s" % ("refkdt=" + str(dfa.loc[catID]['mean.refkdt']) + "[]\n"))
        # f.write("%s" % ("soil_params.b=" + str(dfa.loc[catID]['mode.bexp_soil_layers_stag=1']) + "[]\n"))
        f.write("%s" % ("soil_params.b=" + str(bexp_val) + "[]\n"))
        f.write("%s" % ("soil_params.depth=2.0[m]\n"))
        f.write("%s" % ("soil_params.expon=1[]\n"))
        f.write("%s" % ("soil_params.expon_secondary=1[]\n"))
        f.write("%s" % ("soil_params.satdk=" + str(dfa.loc[catID]['geom_mean.dksat_soil_layers_stag=1']) + "[m/s]\n"))
        # f.write("%s" % ("soil_params.satpsi=" + str(dfa.loc[catID]['geom_mean.psisat_soil_layers_stag=1']) + "[m]\n"))
        f.write("%s" % ("soil_params.satpsi=" + str(satpsi_val) + "[m]\n"))
        f.write("%s" % ("soil_params.slop=" + str(dfa.loc[catID]['mean.slope_1km']) + "[m/m]\n"))
        f.write("%s" % ("soil_params.smcmax=" + str(dfa.loc[catID]['mean.smcmax_soil_layers_stag=1']) + "[m/m]\n"))
        f.write("%s" % ("soil_params.wltsmc=" + str(dfa.loc[catID]['mean.smcwlt_soil_layers_stag=1']) + "[m/m]\n"))
        f.write("%s" % ("soil_storage=0.5[m/m]\n"))

        # add the new parameters for cfex
        # TODO: read these catchment-specific parameters from the NWMv3 model attributes parquet file
        # The current parquet file we have access to was likely based on NWMv2.1 and hence missing these XAJ parameters
        if scheme == 'Xinanjiang':
            f.write("%s" % ("a_Xinanjiang_inflection_point_parameter=-0.212938[]\n"))
            f.write("%s" % ("b_Xinanjiang_shape_parameter=0.666238[]\n"))
            f.write("%s" % ("x_Xinanjiang_shape_parameter=0.02414[]\n"))
            f.write("%s" % ("urban_decimal_fraction=0.0[]\n"))

        f.close()


def create_noah_input(
        catids: List[str],
        time_period: dict,
        dfa: Union[str, Path],
        param_dir_source: Union[str, Path],
        noah_input_dir: Union[str, Path],
        run_type: str
) -> None:
    """ Create BMI configuration file for Noah-OWP-Modular

    Parameters
    ----------
    catids : catchment IDs in the basin
    time_period : simulation and evaluation time period
    dfa: dataframe containing model parameter attributes
    param_dir_source : source directory containing Noah-OWP-Modular parameter files
    noah_input_dir: directory to save configuration files
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

    """

    # Create symlink for parameter directory
    os.makedirs(noah_input_dir, exist_ok=True)
    noah_par_tables = ['SOILPARM.TBL', 'MPTABLE.TBL', 'GENPARM.TBL']
    for par in noah_par_tables:
        src = os.path.join(param_dir_source, par)
        dst = os.path.join(noah_input_dir, par)
        if not os.path.exists(dst):
            os.symlink(src, dst)

    # Files for either the calibration and validation run or the regionalization run
    if run_type == 'calibration':
        run_list = ['calib', 'valid']
    elif run_type == 'regionalization':
        run_list = ['region']
    elif run_type == 'default':
        run_list = ['default']

    for run_name in run_list:
        if time_period['run_time_period'][run_name][0] and time_period['run_time_period'][run_name][1]:
            # Date
            startdate = time_period['run_time_period'][run_name][0]
            startdate = datetime.datetime.strptime(startdate, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=1)
            startdate = startdate.strftime("%Y%m%d%H%M")
            enddate = datetime.datetime.strptime(time_period['run_time_period'][run_name][1], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")

            # Specify options for namelist file
            for catID in catids:
                tslp = dfa.loc[catID]['mean.slope']
                azimuth = dfa.loc[catID]['circ_mean.aspect']
                lat = dfa.loc[catID].geometry.y
                lon = dfa.loc[catID].geometry.x
                isltype = int(dfa.loc[catID]["mode.ISLTYP"])
                vegtype = int(dfa.loc[catID]["mode.IVGTYP"])
                sfctype = 2 if vegtype == 16 else 1
                nom_lst = ['&timing',
                           "  " + "dt".ljust(19) + "= 3600.0" + "                       ! timestep [seconds]",
                           "  " + "startdate".ljust(19) + "= " + "'" + startdate + "'" + "               ! UTC time start of simulation (YYYYMMDDhhmm)",
                           "  " + "enddate".ljust(19) + "= " + "'" + enddate + "'" + "               ! UTC time end of simulation (YYYYMMDDhhmm)",
                           "  " + "forcing_filename".ljust(19) + "= '.'" + "                          ! file containing forcing data",
                           "  " + "output_filename".ljust(19) + "= '.'",
                           '/',
                           "",
                           '&parameters',
                           "  " + "parameter_dir".ljust(19) + "= " + "'" + noah_input_dir + "'",
                           "  " + "general_table".ljust(19) + "= 'GENPARM.TBL'" + "                ! general param tables and misc params",
                           "  " + "soil_table".ljust(19) + "= 'SOILPARM.TBL'" + "               ! soil param table",
                           "  " + "noahowp_table".ljust(19) + "= 'MPTABLE.TBL'" + "                ! model param tables (includes veg)",
                           "  " + "soil_class_name".ljust(19) + "= 'STAS'" + "                       ! soil class data source - 'STAS' or 'STAS-RUC'",
                           "  " + "veg_class_name".ljust(19) + "= 'USGS'" + "                       ! vegetation class data source - 'MODIFIED_IGBP_MODIS_NOAH' or 'USGS'",
                           '/',
                           "",
                           '&location',
                           "  " + "lat".ljust(19) + "= " + str(lat) + "            ! latitude [degrees]  (-90 to 90)",
                           "  " + "lon".ljust(19) + "= " + str(lon) + "           ! longitude [degrees] (-180 to 180)",
                           "  " + "terrain_slope".ljust(19) + "= " + str(tslp) + "           ! terrain slope [degrees]",
                           "  " + "azimuth".ljust(19) + "= " + str(azimuth) + "           ! terrain azimuth or aspect [degrees clockwise from north]",
                           '/',
                           "",
                           "&forcing",
                           "  " + "ZREF".ljust(19) + "= 10.0" + "                         ! measurement height for wind speed (m)",
                           "  " + "rain_snow_thresh".ljust(19) + "= 0.5" + "                          ! rain-snow temperature threshold (degrees Celcius)",
                           "/",
                           "",
                           "&model_options",
                           "  " + "precip_phase_option".ljust(34) + "= 6",
                           "  " + "snow_albedo_option".ljust(34) + "= 1",
                           "  " + "dynamic_veg_option".ljust(34) + "= 4",
                           "  " + "runoff_option".ljust(34) + "= 3",
                           "  " + "drainage_option".ljust(34) + "= 8",
                           "  " + "frozen_soil_option".ljust(34) + "= 1",
                           "  " + "dynamic_vic_option".ljust(34) + "= 1",
                           "  " + "radiative_transfer_option".ljust(34) + "= 3",
                           "  " + "sfc_drag_coeff_option".ljust(34) + "= 1",
                           "  " + "canopy_stom_resist_option".ljust(34) + "= 1",
                           "  " + "crop_model_option".ljust(34) + "= 0",
                           "  " + "snowsoil_temp_time_option".ljust(34) + "= 3",
                           "  " + "soil_temp_boundary_option".ljust(34) + "= 2",
                           "  " + "supercooled_water_option".ljust(34) + "= 1",
                           "  " + "stomatal_resistance_option".ljust(34) + "= 1",
                           "  " + "evap_srfc_resistance_option".ljust(34) + "= 4",
                           "  " + "subsurface_option".ljust(34) + "= 2",
                           "/",
                           "",
                           "&structure",
                           "  " + "isltyp".ljust(17) + "= " + str(isltype) + "              ! soil texture class",
                           "  " + "nsoil".ljust(17) + "= 4              ! number of soil levels",
                           "  " + "nsnow".ljust(17) + "= 3              ! number of snow levels",
                           "  " + "nveg".ljust(17) + "= 27             ! number of vegetation type",
                           "  " + "vegtyp".ljust(17) + "= " + str(vegtype) + "             ! vegetation type",
                           "  " + "croptype".ljust(17) + "= 0              ! crop type (0 = no crops; this option is currently inactive)",
                           "  " + "sfctyp".ljust(17) + "= " + str(sfctype) + "              ! land surface type, 1:soil, 2:lake",
                           "  " + "soilcolor".ljust(17) + "= 4              ! soil color code",
                           "/",
                           "",
                           "&initial_values",
                           "  " + "dzsnso".ljust(10) + "= 0.0, 0.0, 0.0, 0.1, 0.3, 0.6, 1.0      ! level thickness [m]",
                           "  " + "sice".ljust(10) + "= 0.0, 0.0, 0.0, 0.0                     ! initial soil ice profile [m3/m3]",
                           "  " + "sh2o".ljust(10) + "= 0.3, 0.3, 0.3, 0.3                     ! initial soil liquid profile [m3/m3]",
                           "  " + "zwt".ljust(10) + "= -2.0                                   ! initial water table depth below surface [m]",
                           "/",
                           ]

                namelst = os.path.join(noah_input_dir, '{}'.format(catID) + '_' + run_name + '.input')
                with open(namelst, 'w') as outfile:
                    outfile.writelines('\n'.join(nom_lst))
                    outfile.write("\n")


def create_noah_input_template(
        catids: List[str],
        time_period: dict,
        param_dir_source: Union[str, Path],
        input_dir: Union[str, Path],
        template_bmi_dir: Union[str, Path],
        run_type: str
) -> None:
    """ Create BMI configuration files for Noah-OWP-Modular based on template BMI files provided by the user (or NEDS)

    Parameters
    ----------
    catids : catchment IDs in the basin
    time_period : simulation and evaluation time period
    param_dir_source : source directory containing Noah-OWP-Modular parameter files
    input_dir: directory to save configuration files
    template_bmi_dir: directory to template BMI files
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

    """

    # Create symlink for parameter directory
    os.makedirs(input_dir, exist_ok=True)
    noah_par_tables = ['SOILPARM.TBL', 'MPTABLE.TBL', 'GENPARM.TBL']
    for par in noah_par_tables:
        src = os.path.join(param_dir_source, par)
        dst = os.path.join(input_dir, par)

        with open(src) as f:
            # create a symbolic link for each parameter file
            if os.path.exists(dst) or os.path.islink(dst):
                logger.warning(f'File/link {dst} already exists')
            else:
                os.symlink(src, dst)
                logger.info(f'Creating symlink from {src} to {dst}')

    # Files for either the calibration and validation run or the regionalization run
    if run_type == 'calibration':
        run_list = ['calib', 'valid']
    elif run_type == 'regionalization':
        run_list = ['region']
    elif run_type == 'default':
        run_list = ['default']

    for run_name in run_list:
        if time_period['run_time_period'][run_name][0] and time_period['run_time_period'][run_name][1]:

            startdate = time_period['run_time_period'][run_name][0]
            startdate = datetime.datetime.strptime(startdate, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=1)
            startdate = startdate.strftime("%Y%m%d%H%M")
            enddate = datetime.datetime.strptime(time_period['run_time_period'][run_name][1], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")

            # loop through template file for each catchment
            for catID in catids:
                file1 = glob.glob(os.path.join(template_bmi_dir, '*' + catID + '*.input'))
                if len(file1) == 0:
                    try:
                        raise ValueError(f'No template BMI file found for {catID} in {template_bmi_dir}')
                    except ValueError as e:
                        logger.critical(e)
                        raise
                elif len(file1) > 1:
                    try:
                        raise ValueError(f'More than one template BMI file found for {catID} in {template_bmi_dir}')
                    except ValueError as e:
                        logger.critical(e)
                        raise

                with open(file1[0]) as f:
                    lines = f.readlines()

                for i1, l1 in enumerate(lines):
                    if 'startdate' in l1:
                        lines[i1] = "  " + "startdate".ljust(19) + "= " + "'" + startdate + "'" + "               ! UTC time start of simulation (YYYYMMDDhhmm)\n"
                    elif 'enddate' in l1:
                        lines[i1] = "  " + "enddate".ljust(19) + "= " + "'" + enddate + "'" + "               ! UTC time end of simulation (YYYYMMDDhhmm)\n"
                    elif 'parameter_dir' in l1:
                        lines[i1] = "  " + "parameter_dir".ljust(19) + "= " + "'" + input_dir + "'\n"

                namelst = os.path.join(input_dir, '{}'.format(catID) + '_' + run_name + '.input')
                with open(namelst, 'w') as outfile:
                    outfile.writelines(lines)

        logger.info(f'noah-owp-modular BMI config files for regionalization created at {input_dir}/*{run_name}_.input')


def create_sft_smp_input(
        catids: List[str],
        modules: Union[List[str], List[List[str]]],
        attr_parquet: Union[str, Path],
        cfe_dir: Union[str, Path],
        sft_dir: Union[str, Path],
        smp_dir: Union[str, Path],
        run_type: str,
) -> None:
    """ Create BMI configuration file for soil freeze and thaw module, and soil moisture profiles

    Parameters
    ----------
    catids : catchment IDs in the basin
    modules: list of modules in the formulation
    attr_parquet: parquet file containing model attributes
    cfe_dir : directory containing cfe bmi configuration files
    sft_dir : directory for writing sft bmi configuration files
    smp_dir : directory for writing smp bmi configuration files
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

    """

    os.makedirs(sft_dir, exist_ok=True)
    os.makedirs(smp_dir, exist_ok=True)

    # Read hydrofabric attribute file
    df_parquet = pd.read_parquet(attr_parquet)
    df_parquet.set_index("divide_id", inplace=True)

    # Ice fraction scheme
    icefscheme = 'Schaake'
    if run_type != 'regionalization':
        mods = modules
        if ('cfex' in mods):
            icefscheme = 'Xinanjiang'

    # Create bmi config files
    for i in range(len(catids)):

        catID = catids[i]

        # Check if catID is in parquet file (some catchments are missing). This will eventually be replaced when parquet file is not needed for quartz values
        # Set soil_params.quartz value
        if catID in df_parquet.index:
            quartz_val = str(df_parquet.loc[catID][[x for x in df_parquet.columns.to_list() if 'quartz' in x]].mean()) + '[]'  # soil_params.quartz not available in divide-attributes
        else:
            quartz_val = '0[]'

        # Set module list for each catchment during regionalization
        if run_type == 'regionalization':
            mods = modules[i]
            if ('cfex' in mods):
                icefscheme = 'Xinanjiang'

        # Read cfe BMI files
        cfe_bmi_file = os.path.join(cfe_dir, fnmatch.filter(os.listdir(cfe_dir), '*' + catID + '*.txt')[0])
        df = pd.read_table(cfe_bmi_file, delimiter='=', names=["Params", "Values"], index_col=0)

        # Obtain annual mean surface temperature as proxy for initial soil temperature
        # fdf = pd.read_table(os.path.join(forcing_dir, catID + '.csv'), delimiter=',')
        # mtemp = round(fdf['T2D'].mean(), 2)
        # This value is just a reasonable estimate per new direction (Edwin) - HydrofabricAPI
        mtemp = (45 - 32) * 5 / 9 + 273.15  # this is avg soil temp of 45 degrees F converted to Kelvin

        # Create sft list
        sft_lst = ['verbosity=none', 'soil_moisture_bmi=1', 'end_time=1.[d]', 'dt=1.0[h]',
                   'soil_params.smcmax=' + df.loc['soil_params.smcmax'].iloc[0],
                   'soil_params.b=' + df.loc['soil_params.b'].iloc[0],
                   'soil_params.satpsi=' + df.loc['soil_params.satpsi'].iloc[0],
                   'soil_params.quartz=' + quartz_val,
                   'ice_fraction_scheme=' + icefscheme,
                   'soil_z=0.1,0.3,1.0,2.0[m]',
                   'soil_temperature=' + ','.join([str(mtemp)] * 4) + '[K]'
                   ]
        sft_bmi_file = os.path.join(sft_dir, catID + '_bmi_config_sft.txt')
        with open(sft_bmi_file, "w") as f:
            f.writelines('\n'.join(sft_lst))

        # Create smp list
        smp_lst = ['verbosity=none',
                   'soil_params.smcmax=' + df.loc['soil_params.smcmax'].iloc[0],
                   'soil_params.b=' + df.loc['soil_params.b'].iloc[0],
                   'soil_params.satpsi=' + df.loc['soil_params.satpsi'].iloc[0],
                   'soil_z=0.1,0.3,1.0,2.0[m]']
        if 'cfes' in mods or 'cfex' in mods:
            smp_lst += ['soil_storage_model=conceptual', 'soil_storage_depth=2.0']
        elif 'lasam' in mods:
            smp_lst += ['soil_storage_model=layered', 'soil_moisture_profile_option=constant', 'soil_depth_layers=2.0', 'water_table_depth=10[m]']
        smp_bmi_file = os.path.join(smp_dir, catID + '_bmi_config_smp.txt')
        with open(smp_bmi_file, "w") as f:
            f.writelines('\n'.join(smp_lst))


def create_snow17_input(
        catids: List[str],
        dfa: Union[str, Path],
        gpkg_file: Union[str, Path],
        param_dir_source: Union[str, Path],
        snow17_input_dir: str
) -> None:
    """ Create BMI configuration file for Snow17

    Parameters
    ----------
    catids : catchment IDs in the basin
    dfa: dataframe containing model parameter attributes
    gpkg_file: GeoPackage hydrofabric file
    param_dir_source : directory containing snow17 parameter files
    snow17_input_dir : directory for the snow17 bmi configuration files

    Returns
    ----------
    None

   """
    os.makedirs(snow17_input_dir, exist_ok=True)

    # Read geopackage divides
    df_divide = gpd.read_file(gpkg_file, layer="divides")
    df_divide.set_index('divide_id', inplace=True)

    # Read snow17 parameter file
    param_filename = f'{param_dir_source}/snow17_params_2.2.csv'
    params_df = pd.read_csv(param_filename)
    params_df.set_index('divide_id', inplace=True)

    for catID in catids:

        # Set catchment-specific snow17 config parameters
        param_list = ['hru_id ' + catID,
                      'hru_area ' + str(df_divide.loc[catID]['areasqkm']),
                      'latitude ' + str(dfa.loc[catID].geometry.y),
                      'elev ' + str(dfa.loc[catID]['mean.elevation']),
                      'scf 1.100',
                      'mfmax ' + str(params_df.loc[catID]['MFMAX']),
                      'mfmin ' + str(params_df.loc[catID]['MFMIN']),
                      'uadj ' + str(params_df.loc[catID]['UADJ']),
                      'si 500.00',
                      'pxtemp 1.000',
                      'nmf 0.150',
                      'tipm 0.100',
                      'mbase 0.000',
                      'plwhc 0.030',
                      'daygm 0.000',
                      'adc1 0.050',
                      'adc2 0.100',
                      'adc3 0.200',
                      'adc4 0.300',
                      'adc5 0.400',
                      'adc6 0.500',
                      'adc7 0.600',
                      'adc8 0.700',
                      'adc9 0.800',
                      'adc10 0.900',
                      'adc11 1.000']

        input_file = os.path.join(snow17_input_dir, 'snow17-init-' + catID + '.namelist.input')
        param_file = os.path.join(snow17_input_dir, 'snow17_params-' + catID + '.txt')

        with open(param_file, "w") as f:
            f.writelines('\n'.join(param_list))

        # Namelist file is only used when module is run separately from ngen
        input_list = ['&SNOW17_CONTROL',
                      '! === run control file for snow17bmi v. 1.x ===',
                      '',
                      '! -- basin config and path information',
                      'main_id             = "' + catID + '"     ! basin label or gage id',
                      'n_hrus              = 1            ! number of sub-areas in model',
                      'forcing_root        = "extern/snow17/test_cases/ex1/input/forcing/forcing.snow17bmi."',
                      'output_root         = "data/output/output.snow17bmi."',
                      'snow17_param_file   = "' + param_file + '"',
                      'output_hrus         = 1            ! output HRU results? (1=yes; 0=no)',
                      '',
                      '! -- run period information',
                      'start_datehr        = 2017120101   ! start date time, backward looking (check)',
                      'end_datehr          = 2017120123   ! end date time',
                      'model_timestep      = 3600        ! in seconds (86400 seconds = 1 day)',
                      '',
                      '! -- state start/write flags and files',
                      'warm_start_run      = 0  ! is this run started from a state file?  (no=0 yes=1)',
                      "write_states        = 0  ! write restart/state files for 'warm_start' runs (no=0 yes=1)",
                      '',
                      '! -- filenames only needed if warm_start_run = 1',
                      'snow_state_in_root  = "data/state/snow17_states."  ! input state filename root',
                      '',
                      '! -- filenames only needed if write_states = 1',
                      'snow_state_out_root = "data/state/snow17_states."  ! output states filename root',
                      '/',
                      ''
                      ]

        with open(input_file, "w") as f:
            f.writelines('\n'.join(input_list))


def create_ueb_input(
        catids: List[str],
        time_period: dict,
        dfa: Union[str, Path],
        param_dir_source: Union[str, Path],
        ueb_input_dir: str,
        bmi_dir: Union[str, Path],
        run_type: str
) -> None:
    """ Create BMI configuration file for ueb

    Parameters
    ----------
    catids : catchment IDs in the basin
    time_period: simulation time period
    dfa: dataframe containing model parameter attributes
    param_dir_source : directory containing UEB parameter files
    ueb_input_dir : directory for the UEB bmi configuration file
    bmi_dir: directory path containing existing sitevar files (e.g., from EDS)
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

   """
    os.makedirs(ueb_input_dir, exist_ok=True)

    # Create symlink for constant parameter files
    const_file_str = ['inputctr', 'outputctr', 'params']
    const_files = {}
    for par in const_file_str:
        src = Path(param_dir_source, 'ueb_' + par + '.dat').absolute()
        if not os.path.exists(src):
            try:
                raise FileNotFoundError(src)
            except FileNotFoundError as e:
                logger.critical(e)
                raise
        dst = os.path.join(ueb_input_dir, 'ueb_' + par + '.dat')
        const_files.update({par: dst})
        with open(src) as f:
            if not os.path.exists(dst):
                os.symlink(src, dst)
                logger.info(f'Creating symlink from {src} to {dst}')

    # sitevars file
    for catID in catids:
        # Set sitevars file from EDFS BMI dir if it exists
        site_file = os.path.join(ueb_input_dir, 'ueb_sitevars-' + catID + '.dat')
        if bmi_dir != '':
            src = glob.glob(os.path.join(bmi_dir, 'ueb_sitevars*' + catID + '*'))
            if len(src) == 0:
                try:
                    raise ValueError(f'No sitevars file found for {catID} in {bmi_dir}')
                except ValueError as e:
                    logger.critical(e)
                    raise
            elif len(src) > 1:
                try:
                    raise ValueError(f'More than one sitevars file found for {catID} in {bmi_dir}')
                except ValueError as e:
                    logger.critical(e)
                    raise

            with open(src[0]) as f:
                # create a symbolic link
                if os.path.exists(site_file) or os.path.islink(site_file):
                    pass
                    # logger.warning(f'File/link {dst} already exists')
                else:
                    os.symlink(src[0], site_file)
                    logger.info(f'Creating symlink from {src[0]} to {site_file}')

        else:  # create the sitevars file based on a template file

            # retrieve slope, aspect, lat and lon from precomputed attributes file
            tslp = dfa.loc[catID]['mean.slope']
            azimuth = dfa.loc[catID]['circ_mean.aspect']
            lat = dfa.loc[catID].geometry.y
            lon = dfa.loc[catID].geometry.x

            temp_file = Path(param_dir_source, 'ueb_sitevars.dat').resolve(strict=True)
            with open(temp_file) as f:
                lines = f.readlines()
            lines[39] = f'{tslp}\n'
            lines[42] = f'{azimuth}\n'
            lines[45] = f'{lat}\n'
            lines[96] = f'{lon}\n'

            with open(site_file, 'w') as outfile:
                outfile.writelines(lines)

    # ueb-init files need to be created for both calibration and validation runs or regionalization runs
    if run_type == 'calibration':
        run_list = ['calib', 'valid']
    elif run_type == 'regionalization':
        run_list = ['region']
    elif run_type == 'default':
        run_list = ['default']

    for run_name in run_list:
        if time_period['run_time_period'][run_name][0] and time_period['run_time_period'][run_name][1]:
            # Date
            startdate = time_period['run_time_period'][run_name][0]
            startdate = datetime.datetime.strptime(startdate, "%Y-%m-%d %H:%M:%S") + datetime.timedelta(hours=1)
            startdate = startdate.strftime("%Y%m%d%H%M")
            enddate = datetime.datetime.strptime(time_period['run_time_period'][run_name][1], "%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")
            for catID in catids:
                input_file = os.path.join(ueb_input_dir, 'ueb-init-' + catID + '_' + run_name + '.dat')
                site_file = os.path.join(ueb_input_dir, 'ueb_sitevars-' + catID + '.dat')
                input_list = [
                    'UEBGrid Model Driver Test for TWDEF',  # TODO does this need to be updated?
                    const_files['params'],
                    site_file,
                    const_files['inputctr'],
                    const_files['outputctr'],
                    param_dir_source + '/aggout.nc ',
                    param_dir_source + '/watershed_onecell.nc',
                    'watershed y x',
                    f'{startdate[:4]} {startdate[4:6]} {startdate[6:8]} {startdate[8:10]}.0',
                    f'{enddate[:4]} {enddate[4:6]} {enddate[6:8]} {enddate[8:10]}.0',
                    '1.0',
                    '-7.0',
                    '0',
                    '1 15 16',
                    '1 1'
                ]
                with open(input_file, "w") as f:
                    f.writelines('\n'.join(input_list))


def create_sac_input(
        catids: List[str],
        gpkg_file: Union[str, Path],
        param_dir_source: Union[str, Path],
        sac_input_dir: str
) -> None:
    """ Create BMI configuration file for sac-sma

    Parameters
    ----------
    catids : catchment IDs in the basin
    gpkg_file: GeoPackage hydrofabric file
    param_dir_source : directory for sac parameter file
    sac_input_dir : directory for the sac bmi configuration file

    Returns
    ----------
    None

    """
    os.makedirs(sac_input_dir, exist_ok=True)

    # Read geopackage divides
    df_divide = gpd.read_file(gpkg_file, layer="divides")
    df_divide.set_index('divide_id', inplace=True)

    # Read sac-sma parameter file
    param_filename = f'{param_dir_source}/sac_sma_params_2.2.csv'
    params_df = pd.read_csv(param_filename)
    params_df.set_index('divide_id', inplace=True)

    for catID in catids:

        # Set catchment-specific sac-sma config parameters
        param_list = ['hru_id ' + catID,
                      'hru_area ' + str(df_divide.loc[catID]['areasqkm']),
                      'uztwm ' + str(params_df.loc[catID]['UZTWM']),
                      'uzfwm ' + str(params_df.loc[catID]['UZFWM']),
                      'lztwm ' + str(params_df.loc[catID]['LZTWM']),
                      'lzfpm ' + str(params_df.loc[catID]['LZFPM']),
                      'lzfsm ' + str(params_df.loc[catID]['LZFSM']),
                      'adimp 0.0000',
                      'uzk ' + str(params_df.loc[catID]['UZK']),
                      'lzpk ' + str(params_df.loc[catID]['LZPK']),
                      'lzsk ' + str(params_df.loc[catID]['LZSK']),
                      'zperc ' + str(params_df.loc[catID]['ZPERC']),
                      'rexp ' + str(params_df.loc[catID]['REXP']),
                      'pctim 0.0000',
                      'pfree ' + str(params_df.loc[catID]['PFREE']),
                      'riva 0.000',
                      'side 0.0000',
                      'rserv 0.3000']

        input_file = os.path.join(sac_input_dir, 'sac-init-' + catID + '.namelist.input')
        param_file = os.path.join(sac_input_dir, 'sac_params-' + catID + '.txt')

        with open(param_file, "w") as f:
            f.writelines('\n'.join(param_list))

        # Namelist file is only used when module is run separately from ngen
        input_list = ['&SAC_CONTROL',
                      '! === run control file for sacbmi v. 1.x ===',
                      '',
                      '! -- basin config and path information',
                      'main_id             = "' + catID + '"     ! basin label or gage id',
                      'n_hrus              = 1            ! number of sub-areas in model',
                      'forcing_root        = ""',
                      'output_root         = ""',
                      'sac_param_file   = "' + param_file + '"',
                      'output_hrus         = 0            ! output HRU results? (1=yes; 0=no)',
                      '',
                      '! -- run period information',
                      'start_datehr        = 2015120112   ! start date time, backward looking (check)',
                      'end_datehr          = 2015123012   ! end date time',
                      'model_timestep      = 3600        ! in seconds (86400 seconds = 1 day)',
                      '',
                      '! -- state start/write flags and files',
                      'warm_start_run      = 0  ! is this run started from a state file?  (no=0 yes=1)',
                      "write_states        = 0  ! write restart/state files for 'warm_start' runs (no=0 yes=1)",
                      '',
                      '! -- filenames only needed if warm_start_run = 1',
                      'sac_state_in_root  = "../state/sac_states."  ! input state filename root',
                      '',
                      '! -- filenames only needed if write_states = 1',
                      'sac_state_out_root = "../state/sac_states."  ! output states filename root',
                      '/',
                      ''
                      ]
        with open(input_file, "w") as f:
            f.writelines('\n'.join(input_list))


def update_lstm_parameters(
    input_config_path: str,
    output_dir: str,
    params_to_remove=None,
    params_to_update=None
) -> None:

    """
    Reads a YAML config file, removes specified parameters, updates others,
    and writes the result to a new config.yaml in a given output directory.

    :param input_config_path: Path to the original config.yaml
    :param output_dir: Directory where the modified config will be saved
    :param params_to_remove: List of top-level parameters to remove
    :param params_to_update: Dict of parameters to update or add
    """
    import re
    from fnmatch import fnmatch

    params_to_remove = params_to_remove or []
    params_to_update = params_to_update or {}

    # Read the original config
    try:
        with open(input_config_path, 'r') as f:
            config = yaml.safe_load(f)
    except yaml.YAMLError as e:
        logger.critical(f"Error parsing LSTM yaml config at {input_config_path}: {e}")
        raise
    except FileNotFoundError:
        logger.critical(f"LSTM yaml config file not found: {input_config_path}")
        raise

    # Remove specified parameters
    keys_to_remove = set()
    for key in config:
        for pattern in params_to_remove:
            try:
                if fnmatch(key, pattern):
                    keys_to_remove.add(key)
                elif is_probably_regex(pattern) and re.fullmatch(pattern, key):
                    keys_to_remove.add(key)
            except re.error as regex_error:
                logger.info(f"Skipping invalid regex pattern: '{pattern}' - {regex_error}")
    for key in keys_to_remove:
        config.pop(key, None)

    # Update or add new parameters
    config.update(params_to_update)

    # Ensure output directory exists
    os.makedirs(output_dir, exist_ok=True)
    output_config_path = os.path.join(output_dir, "config.yml")

    # Write the modified config
    try:
        with open(output_config_path, 'w') as f:
            yaml.dump(config, f, default_flow_style=False)
    except yaml.YAMLError as e:
        logger.critical(f"Error writing LSTM yaml to {output_config_path}: {e}")
        raise
    except OSError as e:
        logger.critical(f"Error writing LSTM yaml to {output_config_path}: {e}")
        raise

    logger.info(f"LSTM config written to: {output_config_path}")


def create_symlinks(src_file_list, src_dir, dst_dir):

    missing_input_files = list()

    for data_file in src_file_list:
        ffile = os.path.join(src_dir, data_file)
        # Make sure we have the file
        if not os.path.exists(ffile):
            logger.info(f'Input file {ffile} does not exist')
            missing_input_files.append(ffile)
        else:
            target = os.path.join(dst_dir, os.path.basename(ffile))
            if not os.path.exists(target):
                os.symlink(ffile, target)

    if missing_input_files:
        try:
            raise Exception(f'Missing LSTM input files - {missing_input_files}')
        except Exception as e:
            logger.critical(e)
            raise


def create_lstm_config(
        param_dir_source: Union[str, Path],
        lstm_input_dir: Union[str, Path]
) -> None:
    """
    Create LSTM config yaml files from existing static files
    Parameters
    ----------
    param_dir_source: direcetory for static lstm files
    lstm_input_dir: directory for the existing lstm bmi configuration file

    Returns
    ----------
    None

    """
    # create the config files
    lstm_data_dir = os.path.join(param_dir_source, "ngen_files/data/lstm")
    lstm_train_dir = os.path.join(param_dir_source, "trained_neuralhydrology_models/nh_AORC_hourly_slope_elev_precip_temp_seq999_seed101_2801_191806")
    run_dir = lstm_input_dir
    lstm_train_data_dir = os.path.join(lstm_train_dir, 'train_data')
    train_data_dir = os.path.join(run_dir, 'train_data')
    if not os.path.isdir(lstm_train_data_dir):
        try:
            raise ValueError(f"LSTM source path '{lstm_train_data_dir}' must be an existing directory.")
        except Exception as e:
            logger.critical(e)
            raise

    if os.path.islink(train_data_dir) or os.path.exists(train_data_dir):
        os.unlink(train_data_dir)

    os.symlink(lstm_train_data_dir, train_data_dir, target_is_directory=True)

    # Create config.yml
    params_to_remove = ['test_*', 'train_*', 'validation_*', '*_dir']
    params_to_update = {
        "run_dir": run_dir
    }
    update_lstm_parameters(
        input_config_path=os.path.join(lstm_train_dir, 'config.yml'),
        output_dir=lstm_input_dir,
        params_to_remove=params_to_remove,
        params_to_update=params_to_update
    )

    data_files = ['initial_states.csv', 'input_scaling.csv', 'lstm_mean_std.csv', 'sugar_creek_trained.pt']
    create_symlinks(data_files, lstm_data_dir, lstm_input_dir)
    data_files = ['model_epoch009.pt', 'optimizer_state_epoch009.pt']
    create_symlinks(data_files, lstm_train_dir, lstm_input_dir)


def change_lstm_input(
        catids: List[str],
        param_dir_source: Union[str, Path],
        lstm_input_dir: Union[str, Path],
        lstm_bmi_dir: Union[str, Path],

) -> None:

    """
    Create BMI configuration file for LSTM from existing EDFS files
    Parameters
    ----------
    catids: catchment IDs in the basin
    param_dir_source: direcetory for static lstm files
    lstm_input_dir: directory for the existing lstm bmi configuration file
    lstm_bmi_dir: target directory for bmi configuration file output

    Returns
    ----------
    None

    """
    # Create input directory
    os.makedirs(lstm_input_dir, exist_ok=True)

    # Create static LSTM config yaml files
    create_lstm_config(param_dir_source, lstm_input_dir)

    # Create catchment specific BMI config files from EDFS files
    for catID in catids:
        edfs_bmi_file = os.path.join(lstm_bmi_dir, catID + '.yml')
        if not os.path.isfile(edfs_bmi_file):
            try:
                raise FileNotFoundError(f"Required LSTM bmi file not found: {edfs_bmi_file}")
            except Exception as e:
                logger.critical(e)
                raise

        try:
            with open(edfs_bmi_file, 'r') as f:
                config = yaml.safe_load(f)
        except yaml.YAMLError as e:
            logger.critical(f"Error parsing LSTM yaml config at {edfs_bmi_file}: {e}")
            raise
        except FileNotFoundError:
            logger.critical(f"LSTM yaml config file not found: {edfs_bmi_file}")
            raise

        params_to_update = {
            "train_cfg_file": os.path.join(lstm_input_dir, 'config.yml'),
            'time_step': '1 hour',
            'initial_state': 'zero',
            'basin_name': catID,
            'basin_id': catID,
            'verbose': 1,
        }

        config.update(params_to_update)
        input_file = os.path.join(lstm_input_dir, catID + '.yml')
        try:
            with open(input_file, "w") as f:
                yaml.dump(config, f, default_flow_style=False, Dumper=QuotedDumper)
        except yaml.YAMLError as e:
            logger.critical(f"Error writing LSTM yaml to {input_file}: {e}")
            raise
        except OSError as e:
            logger.critical(f"Error writing LSTM yaml to {input_file}: {e}")
            raise


def create_lstm_input(
        catids: List[str],
        dfa: Union[str, Path],
        gpkg_file: Union[str, Path],
        param_dir_source: Union[str, Path],
        lstm_input_dir: Union[str, Path],
        xy_col: List[str]
) -> None:

    """
    Create BMI configuration file for LSTM from existing EDFS files
    Parameters
    ----------
    catids: catchment IDs in the basin
    dfa: dataframe containing model parameter attributes
    gpkg_file: GeoPackage hydrofabric file
    param_dir_source: direcetory for static lstm files
    lstm_input_dir: target directory for bmi configuration file output (lstm_input)
    xy_col: list of centroid column names in divivde-attributes file

    Returns
    ----------
    None

    """
    # Create input directory
    os.makedirs(lstm_input_dir, exist_ok=True)

    # Read geopackage divides
    df_divide = gpd.read_file(gpkg_file, layer="divides")
    df_divide.set_index('divide_id', inplace=True)

    # Create static LSTM config yaml files
    create_lstm_config(param_dir_source, lstm_input_dir)

    # Create catchment specific LSTM bmi config files from scratch
    for catID in catids:

        area = float(df_divide.loc[catID]['areasqkm'])
        slope = float(dfa.loc[catID]['mean.slope'])
        elev = float(dfa.loc[catID]['mean.elevation'])
        lat = float(dfa.loc[catID][xy_col[1]])
        lon = float(dfa.loc[catID][xy_col[0]])

        namelist = {'area_sqkm': area,
                    'basin_id': catID,
                    'basin_name': catID,
                    'elev_mean': elev,
                    'initial_state': 'zero',
                    'lat': lat,
                    'lon': lon,
                    'slope_mean': slope,
                    'time_step': '1 hour',  # There's a disagreement between naming conventions between EDFS and createInputs, unclear which is used
                    'timestep': '1 hour',
                    'train_cfg_file': os.path.join(lstm_input_dir, 'config.yml'),
                    'verbose': '1'}

        # Write config to file
        input_file = os.path.join(lstm_input_dir, catID + '.yml')
        try:
            with open(input_file, "w") as f:
                yaml.dump(namelist, f, default_flow_style=False)
        except yaml.YAMLError as e:
            logger.critical(f"Error writing LSTM yaml to {input_file}: {e}")
            raise
        except OSError as e:
            logger.critical(f"Error writing LSTM yaml to {input_file}: {e}")
            raise


def change_sac_snow17_input(
        module: str,
        catids: List[str],
        input_dir: Union[str, Path],
        bmi_dir: Union[str, Path],
) -> None:
    """ copy existing config files for snow17/sac-sma and change path to sac_param_file in snow17/sac-sma namelist input file

    Parameters
    ----------
    module: "sac" or "snow17"
    catids : catchment IDs
    input_dir : directory for storing new config files
    bmi_dir: directory for existing config files

    Returns
    ----------
    None

    """
    if module not in ['sac', 'snow17']:
        try:
            raise Exception('change_sac_snow17_input: Model must be either "sac" or "snow17"')
        except Exception as e:
            logger.critical(e)
            raise

    # handle parameter file naming convention
    str0 = module + '_params_' if module == 'sac' else module + '_params-'

    # parameter file entry in namelist file
    str1 = module + "_param_file"

    # create input directory for storing new config files
    os.makedirs(input_dir, exist_ok=True)

    # loop through all catchments
    for catID in catids:

        # existing config files
        namelist_file0 = os.path.join(bmi_dir, module + '-init-{}'.format(catID) + '.namelist.input')
        param_file0 = os.path.join(bmi_dir, str0 + '{}'.format(catID) + '.txt')

        # new config files to be created
        namelist_file = os.path.join(input_dir, module + '-init-{}'.format(catID) + '.namelist.input')
        param_file = os.path.join(input_dir, str0 + '{}'.format(catID) + '.txt')

        # create symbolic link to the existing sac parameter file
        if os.path.exists(param_file0):
            os.symlink(param_file0, param_file)
        else:
            try:
                raise Exception(f'Parameter file does not exist: {param_file0}')
            except Exception as e:
                logger.critical(e)
                raise

        # correct the path to sac parameter file in namelist input file
        if not os.path.exists(namelist_file0):
            try:
                raise Exception(f'Namelist file does not exist: {namelist_file0}')
            except Exception as e:
                logger.critical(e)
                raise

        with open(namelist_file0) as f:
            lines0 = f.readlines()
        lines1 = copy.deepcopy(lines0)

        idx = [i for i, s in enumerate(lines0) if str1 in s]
        if len(idx) != 1:
            try:
                raise Exception(f'No entry or more than one entry found for "{str1}" in namelist input file: {namelist_file0}')
            except Exception as e:
                logger.critical(e)
                raise
        lines1[idx[0]] = f'{str1}      = "{param_file}"\n'

        # Save to new namelist file
        if os.path.exists(namelist_file):
            try:
                raise Exception(f'Namelist file {namelist_file} already exists')
            except Exception as e:
                logger.critical(e)
                raise
        with open(namelist_file, 'w') as outfile:
            outfile.writelines(lines1)


def create_pet_input(
        catids: List[str],
        dfa: Union[str, Path],
        pet_input_dir: str
) -> None:
    """ Create BMI configuration file for pet

    Parameters
    ----------
    catids : catchment IDs in the basin
    dfa: dataframe containing model parameter attributes
    pet_input_dir : directory for the pet input files

    Returns
    ----------
    None

    """
    os.makedirs(pet_input_dir, exist_ok=True)

    for catID in catids:

        # Set PET parameters for catchment
        ini_list = ['verbose=0',
                    'pet_method=5',  # Where would this value be supplied in the inputs?
                    'forcing_file=BMI',
                    'run_unit_tests=0',
                    'yes_aorc=1',
                    'yes_wrf=0',
                    'wind_speed_measurement_height_m=10.0',
                    'humidity_measurement_height_m=10.0',
                    'vegetation_height_m=0.12',
                    'zero_plane_displacement_height_m=0.0003',
                    'momentum_transfer_roughness_length=0.0',
                    'heat_transfer_roughness_length_m=0.1',
                    'surface_longwave_emissivity=1.0',
                    'surface_shortwave_albedo=0.22',
                    'cloud_base_height_known=FALSE',
                    'latitude_degrees=' + str(dfa.loc[catID].geometry.y),
                    'longitude_degrees=' + str(dfa.loc[catID].geometry.x),
                    'site_elevation_m=' + str(dfa.loc[catID]['mean.elevation']),
                    'time_step_size_s=3600',
                    'num_timesteps=720',  # This needs to be set from the input files, possibly for calib and valid
                    'shortwave_radiation_provided=0']

        # Write PET bmi config files
        ini_file = os.path.join(pet_input_dir, catID + '_bmi_config.ini')

        with open(ini_file, "w") as f:
            f.writelines('\n'.join(ini_list))


def create_lasam_input(
        catids: List[str],
        modules: Union[List[str], List[List[str]]],
        dfa: Union[str, Path],
        input_dir: Union[str, Path],
        param_dir: Union[str, Path],
        run_type: str
) -> None:
    """ Create BMI configuration file for Lumped Arid and Semi-arid Model

    Parameters
    ----------
    catids : catchment IDs in the basin
    modules: list of modules or a list of formulations for each catchment
    dfa: dataframe containing model parameter attributes
    input_dir : directory for the lasam input configuration file
    param_dir: directory for static lasam parameter files
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

    """

    os.makedirs(input_dir, exist_ok=True)

    # make sure param_dir and parameter files exist
    if param_dir and os.path.exists(param_dir):
        soil_param_file = os.path.join(param_dir, 'vG_default_params.dat')
        if not os.path.exists(soil_param_file):
            try:
                raise Exception(f'Soil params file does not exist: {soil_param_file}')
            except Exception as e:
                logger.critical(e)
                raise
        soil_class_file = os.path.join(param_dir, 'lasam_soil_class.txt')
        if not os.path.exists(soil_class_file):
            try:
                raise Exception(f'Soil class file does not exist: {soil_class_file}')
            except Exception as e:
                logger.critical(e)
                raise
    else:
        try:
            raise Exception(f'lasam_parameter_dir does not exist: {param_dir}')
        except Exception as e:
            logger.critical(e)
            raise

    # Check if sft is in use
    sft_coupled_str = 'true' if 'sft' in modules else 'false'

    # Create lasam list
    lasam_lst = ['verbosity=none',
                 'soil_params_file=' + soil_param_file,
                 'layer_thickness=200.0[cm]',
                 'initial_psi=2000.0[cm]',
                 'timestep=300[sec]',  # Where should this be supplied from?
                 'endtime=1000[hr]',  # Where should this be supplied from?
                 'forcing_resolution=3600[sec]',
                 'ponded_depth_max=1.1[cm]',
                 'use_closed_form_G=false',
                 'layer_soil_type=',
                 'max_soil_types=18',
                 'wilting_point_psi=15495.0[cm]',
                 'giuh_ordinates=0.55,0.25,0.2',  # Where should this be supplied from?
                 'sft_coupled=',
                 'soil_z=10,30,100.0,200.0[cm]',
                 'calib_params=true',
                 'field_capacity_psi=340.9[cm]',
                 ]

    # Set module list for non-regionalization run
    if run_type != 'regionalization':
        mods = modules

    # Create bmi config file
    for i in range(len(catids)):
        catID = catids[i]

        # Set module list for each catchment during regionalization
        if run_type == 'regionalization':
            mods = modules[i]

        # Insert soil type
        lasam_lst_catID = lasam_lst.copy()
        lasam_lst_catID[9] = lasam_lst_catID[9] + str(int(dfa.loc[catID]['mode.ISLTYP']))

        # Check if sft is in use
        sft_coupled_str = 'true' if 'sft' in mods else 'false'
        lasam_lst_catID[13] = 'sft_coupled=' + sft_coupled_str

        # lasam_lst_catID[12] = lasam_lst_catID[12] + df.loc['giuh_ordinates'][0]
        lasam_bmi_file = os.path.join(input_dir, catID + '_bmi_config_lasam.txt')

        with open(lasam_bmi_file, "w") as f:
            f.writelines('\n'.join(lasam_lst_catID))


def change_lasam_input(
        catids: List[str],
        input_dir: Union[str, Path],
        bmi_dir: Union[str, Path],
        param_dir: Union[str, Path],
) -> None:
    """ copy existing config files for lasam and change path to soil_params_file in lasam config file

    Parameters
    ----------
    catids : catchment IDs
    input_dir : directory for storing new config files
    bmi_dir: directory for existing config files
    param_dir: path to lasam parameter files

    Returns
    ----------
    None

    """

    # create input directory for storing new config files
    os.makedirs(input_dir, exist_ok=True)

    # make sure param_dir exists
    if param_dir and os.path.exists(param_dir):
        param_file = os.path.join(param_dir, 'vG_default_params.dat')
        if not os.path.exists(param_file):
            try:
                raise Exception(f'Soil_params_file does not exist: {param_file}')
            except Exception as e:
                logger.critical(e)
                raise
    else:
        try:
            raise Exception(f'lasam_parameter_dir does not exist: {param_dir}')
        except Exception as e:
            logger.critical(e)
            raise

    # loop through all catchments
    for catID in catids:

        # existing config file
        config_file0 = os.path.join(bmi_dir, '{}_bmi_config_lasam'.format(catID) + '.txt')

        # new config file to be created
        config_file = os.path.join(input_dir, '{}_bmi_config_lasam'.format(catID) + '.txt')

        # correct the path to soil_params_file in config file
        if not os.path.exists(config_file0):
            try:
                raise Exception(f'Namelist file does not exist: {config_file0}')
            except Exception as e:
                logger.critical(e)
                raise
        with open(config_file0) as f:
            lines0 = f.readlines()
        lines1 = copy.deepcopy(lines0)
        idx = [i for i, s in enumerate(lines0) if "soil_params_file" in s]
        if len(idx) != 1:
            try:
                raise Exception(f'No entry or more than one entry found for "soil_params_file" in config file: {config_file0}')
            except Exception as e:
                logger.critical(e)
                raise
        lines1[idx[0]] = f'soil_params_file={param_file}\n'

        # Save to new config file
        if os.path.exists(config_file):
            try:
                raise Exception(f'Config file {config_file} already exists')
            except Exception as e:
                logger.critical(e)
                raise
        with open(config_file, 'w') as outfile:
            outfile.writelines(lines1)


def change_smp_input(
        catids: List[str],
        modules: Union[List[str], List[List[str]]],
        input_dir: Union[str, Path],
        bmi_dir: Union[str, Path],
        run_type: str,
        sm_frac_depth: float = 0.4,
        sm_profile_depth: float = 0.1,
) -> float:
    """ copy existing config files for smp and change soil moisture depths as needed

    Parameters
    ----------
    catids : catchment IDs
    modules: list of modules in the formulation
    input_dir : directory for storing new config files
    bmi_dir: directory for existing config files
    run_type: type of run (calib, regionalization, or default)
    sm_frac_depth: depth (m) at which to output soil moisture fraction
    sm_profile_depth: depth (m) at which to output soil moisture (from the first soil layer)

    Returns
    ----------
    sm_profile_depth, since it may be changed here to soil_z[0]

    """

    # create input directory for storing new config files
    os.makedirs(input_dir, exist_ok=True)

    # Retrieve modules
    mods = modules

    # loop through all catchments
    for i in range(len(catids)):

        catID = catids[i]

        # Retrieve modules for regionalization
        if run_type == 'regionalization':
            mods = modules[i]

        # existing config file
        config_file0 = os.path.join(bmi_dir, '{}_bmi_config_smp'.format(catID) + '.txt')

        # new config file to be created
        config_file = os.path.join(input_dir, '{}_bmi_config_smp'.format(catID) + '.txt')

        # read config file
        if not os.path.exists(config_file0):
            try:
                raise Exception(f'Config file for SMP does not exist: {config_file0}')
            except Exception as e:
                logger.critical(e)
                raise
        with open(config_file0) as f:
            lines0 = f.readlines()
        lines1 = copy.deepcopy(lines0)

        # adjust soil_moisture_fraction_depth if needed
        str1 = "soil_moisture_fraction_depth"
        idx = [i for i, s in enumerate(lines0) if str1 in s]
        if len(idx) == 0:
            lines1 = lines1.append(f'{str1}={sm_frac_depth}[m]\n')
        elif len(idx) == 1:
            lines1[idx[0]] = f'{str1}={sm_frac_depth}[m]\n'
        else:
            try:
                raise Exception(f'More than one entry found for {str1} in config file: {config_file0}')
            except Exception as e:
                logger.critical(e)
                raise

        # read soil depths for different layers (currently SMP is limited to 4 layers only)
        idx = [i for i, s in enumerate(lines0) if "soil_z" in s]
        if len(idx) != 1:
            try:
                raise Exception(f'No entry or more than one entry found for "soil_z" in config file: {config_file0}')
            except Exception as e:
                logger.critical(e)
                raise
        depths = lines1[idx[0]].split("=")[1].split("[")[0].split(',')
        depths = list(map(float, depths))

        # make sure depths are in ascending order (since they are accumulative)
        is_ascending = all(earlier <= later for earlier, later in zip(depths, depths[1:]))
        if not is_ascending:
            try:
                raise ValueError(f'Accumulative soil layer depths in soil_z in {config_file0} must be in ascending order: {depths}')
            except ValueError as e:
                logger.critical(e)
                raise

        # convert depths to meters if needed
        unit1 = lines1[idx[0]].split("=")[1].split("[")[1].split(']')[0].lower()
        if unit1 == 'm':
            pass
        elif unit1 == "cm":
            depths = [d / 100 for d in depths]
        elif unit1 == "mm":
            depths = [d / 1000 for d in depths]
        else:
            try:
                raise ValueError(f'Unit {unit1} is not supported for soil_z in {config_file0}; supported units are m, mm, and cm')
            except ValueError as e:
                logger.critical(e)
                raise

        # adjust soil_z for soil_moisture_fraction_depth
        if not any(value == sm_frac_depth for value in depths):
            depths = depths[::-1]
            for i1, d1 in enumerate(depths):
                if d1 < sm_frac_depth:
                    depths[i1] = sm_frac_depth
                    break
            depths = depths[::-1]
            if catID == catids[0]:
                logger.info(f'soil_z in {config_file0} is adjusted to include {str1} {sm_frac_depth}[m]')

        # adjust 1st element of soil_z for soil_moisture_profile output depth (soil_moisture_profile from SMP is an array and
        # currently ngen can only output the first element of arrays)
        if sm_profile_depth != depths[0]:
            if sm_profile_depth > depths[1]:
                if catID == catids[0]:
                    logger.warning(
                        f'sm_profile_depth ({sm_profile_depth}m) is greater than soil_z[1] in {config_file0}; output soil moisture at soil_z[0]({depths[0]}m) instead')
            else:
                depths[0] = sm_profile_depth
                if catID == catids[0]:
                    logger.info(f'soil_z[0] in {config_file0} reset to {sm_profile_depth} to output soil moisture value properly')

        list_depth = ",".join(list(map(str, depths)))
        lines1[idx[0]] = f'soil_z={list_depth}[m]\n'

        # Add soil_storage_model if missing from BMI config file
        if ('cfes' in mods or 'cfex' in mods) and not any('soil_storage_model' in line for line in lines1):
            lines1.extend([
                'soil_storage_model=conceptual\n',
                'soil_storage_depth=2.0\n'
            ])
        elif ('lasam' in mods) and not any('soil_storage_model' in line for line in lines1):
            lines1.extend([
                'soil_storage_model=layered\n',
                'soil_moisture_profile_option=constant\n',
                'soil_depth_layers=2.0\n',
                'water_table_depth=10[m]\n'
            ])

        # Save to new config file
        if os.path.exists(config_file) and catID == catids[0]:
            logger.info(f'Config file {config_file} exists; overwrite it')
        with open(config_file, 'w') as outfile:
            outfile.writelines(lines1)

    return depths[0]


def change_sft_input(
        catids: List[str],
        modules: Union[List[str], List[List[str]]],
        input_dir: Union[str, Path],
        bmi_dir: Union[str, Path],
        run_type: str,
) -> None:
    """ Create BMI configuration file for soil freeze and thaw module, and soil moisture profiles

    Parameters
    ----------
    catids : catchment IDs in the basin
    modules: list of modules in the formulation
    input_dir: directory for writing sft bmi configuration files
    bmi_dir : directory containing bmi configuration files
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None

    """

    # create input directory for storing new config files
    os.makedirs(input_dir, exist_ok=True)

    # Ice fraction scheme
    icefscheme = 'Schaake'
    if run_type != 'regionalization':
        mods = modules
        if ('cfex' in mods):
            icefscheme = 'Xinanjiang'

    # Create bmi config files
    for i in range(len(catids)):

        catID = catids[i]

        # Set module list for each catchment during regionalization
        if run_type == 'regionalization':
            mods = modules[i]
            if ('cfex' in mods):
                icefscheme = 'Xinanjiang'

        # existing config files
        param_file0 = os.path.join(bmi_dir, catID + '_bmi_config_sft.txt')

        # new config files to be created
        param_file = os.path.join(input_dir, catID + '_bmi_config_sft.txt')

        # correct the ice_fraction_scheme depending on the catchments formulation
        if not os.path.exists(param_file0):
            try:
                raise Exception(f'Param file does not exist: {param_file0}')
            except Exception as e:
                logger.critical(e)
                raise
        with open(param_file0) as f:
            lines0 = f.readlines()
        lines1 = copy.deepcopy(lines0)

        # Find index of ice_fraction_scheme line
        idx = [i for i, s in enumerate(lines0) if s.startswith('ice')]
        lines1[idx[0]] = f'ice_fraction_scheme={icefscheme}\n'

        # Save to new parameter file
        with open(param_file, 'w') as outfile:
            outfile.writelines(lines1)


def change_topmodel_input(
        catids: List[str],
        bmi_dir: Union[str, Path],
        inputDir: Union[str, Path],
) -> None:
    """ change options in TOPMODEL input file

    Parameters
    ----------
    catids : catchment IDs in the basin
    bmi_dir : directory containing bmi configuration files
    inputDir : directory for storing input files

    Returns
    ----------
    None

    """

    if not os.path.exists(inputDir):
        os.makedirs(inputDir, exist_ok=True)

    for catID in catids:

        run_file = os.path.join(bmi_dir, '{}_topmodel'.format(catID) + '.run')
        params_file = os.path.join(bmi_dir, '{}_topmodel_params'.format(catID) + '.dat')
        subcat_file = os.path.join(bmi_dir, '{}_topmodel_subcat'.format(catID) + '.dat')

        # Copy
        new_runfile = os.path.join(inputDir, '{}'.format(catID) + '_topmodel.run')
        shutil.copy(run_file, new_runfile)
        new_params = os.path.join(inputDir, '{}'.format(catID) + '_topmodel_params.dat')
        shutil.copy(params_file, new_params)
        new_subcat = os.path.join(inputDir, '{}'.format(catID) + '_topmodel_subcat.dat')
        shutil.copy(subcat_file, new_subcat)

        # read runfile
        with open(new_runfile, 'r') as infile:
            list_lines = infile.readlines()
        lst_lines = copy.deepcopy(list_lines)

        # Change directory in runfile
        topmod_out = os.path.join(os.path.dirname(os.path.dirname(inputDir)), '{}'.format(catID) + '_topmod.out')
        hyd_out = os.path.join(os.path.dirname(os.path.dirname(inputDir)), '{}'.format(catID) + '_hyd.out')
        filePath = [os.path.join(os.path.dirname(inputDir), '{}'.format(catID) + '_forcing.csv'),
                    new_subcat, new_params, topmod_out, hyd_out]

        for i in range(0, 5):
            lst_lines[i + 2] = filePath[i] + '\n'

        # Save file
        with open(new_runfile, 'w') as outfile:
            outfile.writelines(lst_lines)


def create_topmodel_input(
        catids: List[str],
        dfa: Union[str, Path],
        gpkg_file: Union[str, Path],
        inputDir: Union[str, Path],
) -> None:
    """ Create BMI configuration file for Topmodel

    Parameters
    ----------
    catids : catchment IDs in the basin
    dfa: dataframe containing model parameter attributes
    gpkg_file: GeoPackage hydrofabric file
    inputDir: directory for writing topmodel bmi configuration files

    Returns
    ----------
    None

    """

    os.makedirs(inputDir, exist_ok=True)

    # Read geopackage divides file
    df_divide = gpd.read_file(gpkg_file, layer="divides")
    df_divide.set_index('divide_id', inplace=True)

    # Fill Nan lengthkm (coastal divides) with 0
    df_divide['lengthkm'] = df_divide['lengthkm'].fillna(0)

    # Calculate median twi quartiles to fill missing values
    # Extract quartile twi values from divide-attributes
    twi_quart = []
    for val in dfa['dist_4.twi'].dropna():
        try:
            quart = json.loads(val) if isinstance(val, str) else val
        except json.JSONDecodeError:
            continue

        row_v = []
        for q in quart:
            v_val = q.get("v", math.nan)
            row_v.append(v_val)
        twi_quart.append(row_v)

    # Convert all twi values to df
    twi_quart_df = pd.DataFrame(twi_quart)
    med_v = twi_quart_df.median(axis=0, skipna=True)

    # Construct default twi quartiles
    default_twi = [{"v": round(v, 3), "frequency": 0.25} for v in med_v]

    # loop through all catchments
    for catID in catids:

        # Set topmodel parameters
        num_sub_catchments = 1
        imap = 1
        yes_print_output = 1
        area = 1
        twi = json.loads(dfa.loc[catID]['dist_4.twi'])
        # If twi_dist_4 does not have proper values, set to default value
        if not any('v' in d for d in twi):
            twi = default_twi.copy()
        twi_df = pd.DataFrame(twi)
        num_topodex_values = len(twi)

        num_channels = 1
        cum_dist_area_with_dist = 1.0
        dist_from_outlet = round(df_divide.loc[catID]['lengthkm'] * 1000)  # convert km to m

        # Format parameters for output
        subcat_line1 = f"{num_sub_catchments} {imap} {yes_print_output} \n"
        subcat_line2 = f"Extracted study basin:  {catID} \n"
        subcat_line3 = f"{num_topodex_values} {area} \n"
        subcat_line5 = f"{num_channels}\n"
        subcat_line6 = f"{cum_dist_area_with_dist} {dist_from_outlet}\n"

        # Write subcatchment data to file
        cfg_filename_subcat = f'{catID}_topmodel_subcat.dat'
        cfg_filename_subcat_path = os.path.join(inputDir, cfg_filename_subcat)

        with open(cfg_filename_subcat_path, 'w') as outfile:
            outfile.write(subcat_line1)
            outfile.write(subcat_line2)
            outfile.write(subcat_line3)
        try:
            twi_df.to_csv(cfg_filename_subcat_path, mode='a', sep=' ', columns=['frequency', 'v'], index=False, header=False)
        except Exception:
            print(str(catID))
            print(twi_df)
            raise
        with open(cfg_filename_subcat_path, 'a') as outfile:
            outfile.write(subcat_line5)
            outfile.write(subcat_line6)

        # Set topmodel_params.dat
        params = OrderedDict()
        params['szm'] = "0.0125"
        params['t0'] = "0.000075"
        params['td'] = "20"
        params['chv'] = "1000"
        params['rv'] = "1000"
        params['srmax'] = "0.04"
        params['Q0'] = "0.0000328"
        params['sr0'] = "0"
        params['infex'] = "0"
        params['xk0'] = "2"
        params['hf'] = "0.1"
        params['dth'] = "0.1"

        # Format parameters for output
        line1 = catID + '\n'
        line2 = " ".join([str(v) for v in params.values()])

        # Write parameter data to file
        cfg_filename_dat = f'{catID}_topmodel_params.dat'
        cfg_filename_dat_path = os.path.join(inputDir, cfg_filename_dat)
        with open(cfg_filename_dat_path, 'w') as outfile:
            outfile.write(line1)
            outfile.write(line2)

        # Create primary configuration file
        stand_alone = '0\n'  # Set to false for BMI
        title = f'{catID}\n'
        input_fptr = os.path.join(os.path.dirname(os.path.dirname(inputDir)), '{}'.format(catID) + '_forcing.csv\n')
        subcat_fptr = os.path.join(inputDir, '{}'.format(catID) + '_topmodel_subcat.dat\n')
        params_fptr = os.path.join(inputDir, '{}'.format(catID) + '_topmodel_params.dat\n')
        output_fptr = os.path.join(os.path.dirname(os.path.dirname(inputDir)), '{}'.format(catID) + '_topmod.out\n')
        out_hyd_fptr = os.path.join(os.path.dirname(os.path.dirname(inputDir)), '{}'.format(catID) + '_hyd.out\n')

        cfg_filename_run = f'{catID}_topmodel.run'
        cfg_filename_path = os.path.join(inputDir, cfg_filename_run)
        with open(cfg_filename_path, 'w') as outfile:
            outfile.write(stand_alone)
            outfile.write(title)
            outfile.write(input_fptr)
            outfile.write(subcat_fptr)
            outfile.write(params_fptr)
            outfile.write(output_fptr)
            outfile.write(out_hyd_fptr)


def create_troute_config(
        gpkg_file: Union[str, Path],
        rt_cfg_file: Union[str, Path],
        start_date: str,
        nts: int,
        # reformat_dir: Union[str, Path],
) -> None:
    """ Create routing configuration YAML file

    Parameters
    ----------
    gpkg_file :  GeoPackage hydrofabric file
    rt_cfg_file : t-route configuration YAML file
    start_date :  start date for restart run
    nts : number of timesteps
    reformat_dir : directory for the reformatted nexus output files

    Returns
    ----------
    None

    """

    # bmi_parameters
    bmi_param = {"flowpath_columns": ["id", "toid", "lengthkm"],
                 "attributes_columns": ['attributes_id',
                                        # 'rl_gages',
                                        # 'rl_NHDWaterbodyComID',
                                        'gage',
                                        'WaterbodyID',
                                        'MusK',
                                        'MusX',
                                        'n',
                                        'So',
                                        'ChSlp',
                                        'BtmWdth',
                                        'nCC',
                                        'TopWdthCC',
                                        'TopWdth'],
                 "waterbody_columns": ['hl_link',
                                       'ifd',
                                       'LkArea',
                                       'LkMxE',
                                       'OrificeA',
                                       'OrificeC',
                                       'OrificeE',
                                       'WeirC',
                                       'WeirE',
                                       'WeirL'],
                 "network_columns": ['network_id', 'hydroseq', 'hl_uri'],
                 }

    # log_parameters
    log_param = {"showtiming": True, "log_level": 'DEBUG'}

    # network_topology_parameters
    columns = {"key": "id",
               "downstream": "toid",
               "dx": "lengthkm",
               "n": "n",
               "ncc": "nCC",
               "s0": "So",
               "bw": "BtmWdth",
               # "waterbody": "rl_NHDWaterbodyComID",
               # "gages": "rl_gages",
               "waterbody": "WaterbodyID",
               "gages": "gage",
               "tw": "TopWdth",
               "twcc": "TopWdthCC",
               "musk": "MusK",
               "musx": "MusX",
               "cs": "ChSlp",
               "alt": "alt",
               }

    dupseg = ["717696", "1311881", "3133581", "1010832", "1023120", "1813525",
              "1531545", "1304859", "1320604", "1233435", "11816", "1312051",
              "2723765", "2613174", "846266", "1304891", "1233595", "1996602",
              "2822462", "2384576", "1021504", "2360642", "1326659", "1826754",
              "572364", "1336910", "1332558", "1023054", "3133527", "3053788",
              "3101661", "2043487", "3056866", "1296744", "1233515", "2045165",
              "1230577", "1010164", "1031669", "1291638", "1637751",
              ]

    nwtopo_param = {"supernetwork_parameters": {"network_type": "HYFeaturesNetwork",
                                                "geo_file_path": gpkg_file,
                                                "columns": columns,
                                                "duplicate_wb_segments": dupseg},
                    "waterbody_parameters": {"break_network_at_waterbodies": True,
                                             "level_pool": {"level_pool_waterbody_parameter_file_path": gpkg_file}},
                    }

    # compute_parameters
    res_da = {"reservoir_persistence_da": {"reservoir_persistence_usgs": False,
                                           "reservoir_persistence_usace": False},
              "reservoir_rfc_da": {"reservoir_rfc_forecasts": False,
                                   "reservoir_rfc_forecasts_time_series_path": None,
                                   "reservoir_rfc_forecasts_lookback_hours": 28,
                                   "reservoir_rfc_forecasts_offset_hours": 28,
                                   "reservoir_rfc_forecast_persist_days": 11},
              "reservoir_parameter_file": None,
              }

    stream_da = {"streamflow_nudging": False,
                 "diffusive_streamflow_nudging": False,
                 "gage_segID_crosswalk_file": None,
                 }

    comp_param = {"parallel_compute_method": "by-subnetwork-jit-clustered",
                  "subnetwork_target_size": 10000,
                  "cpu_pool": 16,
                  "compute_kernel": "V02-structured",
                  "assume_short_ts": True,
                  "restart_parameters": {"start_datetime": start_date},
                  "forcing_parameters": {"qts_subdivisions": 12,
                                         "dt": 300,
                                         "qlat_input_folder": ".",
                                         "qlat_file_pattern_filter": "nex-*",
                                         "nts": nts,
                                         "max_loop_size": divmod(nts * 300, 3600)[0] + 1},
                  "data_assimilation_parameters": {"usgs_timeslices_folder": None,
                                                   "usace_timeslices_folder": None,
                                                   "timeslice_lookback_hours": 48,
                                                   "qc_threshold": 1,
                                                   "streamflow_da": stream_da,
                                                   "reservoir_da": res_da},
                  }

    # output_parameters
    output_param = {'stream_output': {'stream_output_directory': ".",
                                      'stream_output_time': divmod(nts * 300, 3600)[0] + 1,
                                      'stream_output_type': '.nc',
                                      'stream_output_internal_frequency': 60,
                                      },
                    }

    # Combine all parameters
    config = {"bmi_parameters": bmi_param,
              "log_parameters": log_param,
              "network_topology_parameters": nwtopo_param,
              "compute_parameters": comp_param,
              "output_parameters": output_param,
              }

    # Save configuration into yaml file
    with open(rt_cfg_file, 'w') as file:
        yaml.dump(config, file, sort_keys=False, default_flow_style=False, indent=4)


def update_forcing_config(
        forecast_cycle: str,
        forcing_template: dict,
        geogrid_file: str,
        start_period: str,
        end_period: str,
        forcing_config_dir: Path,
        forcing_config_file: Path
) -> None:
    """ update bmi forcing engine config yaml file

    Parameters
    ----------
    forecast_cycle: name of forecast cycle
    forcing_template : dictionary of forcing bmi config template file
    geogrid_file: path to geogrid file
    start_period: start time for model run
    end_period: end time for model run
    forcing_config_dir: directory path for forcing config file
    forcing_config_dir: output path for forcing config file

    Returns
    ----------
    None
    """
    # Create directory for storing config file
    os.makedirs(forcing_config_dir, exist_ok=True)

    # Format start_period and end_period for config file
    start_dt = datetime.datetime.strptime(start_period, '%Y-%m-%d %H:%M:%S')
    end_dt = datetime.datetime.strptime(end_period, '%Y-%m-%d %H:%M:%S')

    # Shift forecast start date by one hour
    start_shift = start_dt - datetime.timedelta(hours=1)
    start_str = start_shift.strftime('%Y%m%d%H%M')
    end_str = end_dt.strftime('%Y%m%d%H%M')

    # Compute time difference between start and end time
    time_delta = int((end_dt - start_dt).total_seconds() / 60)

    # Update forcing_template with dynamic variables
    if forcing_template['AnAFlag'] == 1:
        forcing_template['RefcstBDateProc'] = end_str
        forcing_template['LookBack'] = time_delta
    elif forcing_template['AnAFlag'] == 0:
        forcing_template['RefcstBDateProc'] = start_str
        forcing_template['ForecastInputHorizons'] = [time_delta] * len(forcing_template['InputForcingDirectories'])

    forcing_template['GeogridIn'] = geogrid_file

    # Write forcing config yaml file
    with open(forcing_config_file, "w") as file:
        yaml.dump(forcing_template, file, Dumper=ForcingDumper, sort_keys=False, default_flow_style=False)


def var_mapping(
        modules: List[str],
        pet_in: str,
        pcp_in: str,
        output_dict: dict,
) -> Dict[str, str]:
    """ create variable name mapping based on modules

    Parameters
    ----------
    modules: list of modules in the formulation
    pet_in: module input variable name for evapotranspiration
    pcp_in: module input variable name for precipitation
    output_dict: dictionary defining which output variables to write out

    Returns
    ----------
    Variable name mapping dictionary (for module inputs and outputs).
    Currently the following outputs are included:
        swe_out: output variable name for SWE (snow water equivalent)
        sm_out: output variable names for soil mositure fraction and soil moisture profile

    """
    var_maps = {'input': {}, 'output': {}}

    # only needed when CFE is not coupled to SFT/SMP
    if ('cfes' in modules or 'cfex' in modules) and ('sft' not in modules) and ('smp' not in modules):
        var_maps['input']["ice_fraction_schaake"] = "sloth_ice_fraction_schaake"
        var_maps['input']["ice_fraction_xinanjiang"] = "sloth_ice_fraction_xinanjiang"
        var_maps['input']["soil_moisture_profile"] = "sloth_smp"

    # PET
    if 'noah' in modules and 'pet' not in modules:
        var_maps['input'][pet_in] = "EVAPOTRANS"

    # snowmelt
    if 'snow17' in modules:
        var_maps['input'][pcp_in] = 'raim'
        if output_dict['output_swe']:
            var_maps['output']['swe_out'] = 'sneqv'
            var_maps['output']['swe_out_header'] = 'SWE_mm'
        else:
            var_maps['output']['swe_out'] = ''
    elif 'ueb' in modules:
        var_maps['input'][pcp_in] = "SWIT"
        if output_dict['output_swe']:
            var_maps['output']['swe_out'] = 'SWE'
            var_maps['output']['swe_out_header'] = 'SWE_m'
        else:
            var_maps['output']['swe_out'] = ''
    elif 'noah' in modules:  # check noah last since it can also be included to provided ET
        var_maps['input'][pcp_in] = "QINSUR"
        if output_dict['output_swe']:
            var_maps['output']['swe_out'] = 'SNEQV'
            var_maps['output']['swe_out_header'] = 'SWE_mm'
        else:
            var_maps['output']['swe_out'] = ''
    else:
        var_maps['output']['swe_out'] = ''

    # soil moisture fraction
    if 'smp' in modules and output_dict['output_sm']:
        var_maps['output']['sm_out'] = ['soil_moisture_fraction', 'soil_moisture_profile']
        var_maps['output']['sm_out_header'] = ['sm_frac_' + str(output_dict['sm_frac_depth']) + 'm',
                                               'sm_profile_' + str(output_dict['sm_profile_depth']) + 'm']
    else:
        var_maps['output']['sm_out'] = ''

    return var_maps


def get_model_type_name(
        module: str
) -> str:
    return settings.modules_all.loc[settings.modules_all['module'] == module, 'name_config'].iloc[0]


def create_reg_realization_file(
        workdir: Union[str, Path],
        lib_file: dict,
        bmi_dir: dict,
        forcing_provider: str,
        forcing_dir: Union[str, Path],
        forcing_config_file: Union[str, Path],
        realization_file: Union[str, Path],
        time_period: dict,
        rt_dict: dict,
        output_dict: dict,
        cat_to_grp: dict,
        grp_to_form: dict,
        grp_params: dict
) -> None:
    """ Create realization file for regionalization for the specified modules by catchment

    Parameters
    ----------
    workdir : basin directory for storing all the files
    lib_file : library files for different modules
    bmi_dir : directory for different model or module to store BMI files
    forcing_provider: forcing provider option (csv or bmi)
    forcing_dir : directory to store forcing files
    forcing_config_file: path to forcing engine configuration file
    realization_file : model realization configuration file
    time_period : simulation and evaluation time period
    rt_dict : routing model source file directory and configuration file
    output_dict: whether to output certain variables (currently SWE and soil moisture)
    cat_to_grp: dictionary mapping catchments to regionalization groups
    grp_to_form: dictionary mapping regionalization groups to formulations
    grp_params: dictionary mapping regionalization groups to modules and their corresponding parameters

    Returns
    ----------
    None
    """

    # Create symlinks for libraries
    lib_mod = {}
    for key, value in lib_file.items():
        lib_mod_link = os.path.join(workdir, 'Input/' + os.path.basename(value))
        lib_mod.update({key: lib_mod_link})
        if not os.path.exists(lib_mod_link):
            os.symlink(value, lib_mod_link)

    # Set model formulations for each regionalization group
    grp_main = {}
    grps = list(grp_to_form.keys())

    for grp in grps:

        model_configs = {}

        # Retrieve modules used in given group
        grp_mod = grp_to_form[grp]

        # noah
        if 'noah' in grp_mod:
            model_configs['noah'] = {"name": "bmi_fortran",
                                     "params": {"name": "bmi_fortran",
                                                "model_type_name": get_model_type_name('noah'),
                                                "main_output_variable": "QINSUR",
                                                "library_file": lib_mod['noah'],
                                                "init_config": os.path.join(bmi_dir['noah'], '{{id}}_region.input'),
                                                "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                                "variables_names_map": {
                                                    "PRCPNONC": "atmosphere_water__liquid_equivalent_precipitation_rate",
                                                    "Q2": "atmosphere_air_water~vapor__relative_saturation",
                                                    "SFCTMP": "land_surface_air__temperature",
                                                    "UU": "land_surface_wind__x_component_of_velocity",
                                                    "VV": "land_surface_wind__y_component_of_velocity",
                                                    "LWDN": "land_surface_radiation~incoming~longwave__energy_flux",
                                                    "SOLDN": "land_surface_radiation~incoming~shortwave__energy_flux",
                                                    "SFCPRS": "land_surface_air__pressure"},
                                                "model_params": grp_params['noah'][grp]}}

        # cfe or cfex
        if 'cfes' in grp_mod or 'cfex' in grp_mod:
            m1 = 'cfes' if 'cfes' in grp_mod else 'cfex'
            model_configs[m1] = {"name": "bmi_c",
                                 "params": {"name": "bmi_c",
                                            "model_type_name": get_model_type_name(m1),
                                            "main_output_variable": "Q_OUT",
                                            "library_file": lib_mod[m1],
                                            "init_config": os.path.join(bmi_dir[m1], '{{id}}_bmi_config_cfe.txt'),
                                            "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                            "registration_function": "register_bmi_cfe",
                                            "model_params": grp_params[m1][grp]}}

            # variable name mapping section
            pet_in = "water_potential_evaporation_flux"
            pcp_in = "atmosphere_water__liquid_equivalent_precipitation_rate"
            var_maps = var_mapping(grp_mod, pet_in, pcp_in, output_dict)

            # module output variable for input to t-route
            main_output_variable = "Q_OUT"

        # topmodel
        if 'topmodel' in grp_mod:
            model_configs['topmodel'] = {"name": "bmi_c",
                                         "params": {"name": "bmi_c",
                                                    "model_type_name": get_model_type_name('topmodel'),
                                                    "main_output_variable": "Qout",
                                                    "library_file": lib_mod['topmodel'],
                                                    "init_config": os.path.join(bmi_dir['topmodel'], '{{id}}_topmodel.run'),
                                                    "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                                    "registration_function": "register_bmi_topmodel",
                                                    "model_params": grp_params['topmodel'][grp]}}
            # variable name mapping section
            pet_in = "water_potential_evaporation_flux"
            pcp_in = "atmosphere_water__liquid_equivalent_precipitation_rate"
            var_maps = var_mapping(grp_mod, pet_in, pcp_in, output_dict)

            # module output variable for input to t-route
            main_output_variable = "Qout"

        # sac-sma
        if 'sac' in grp_mod:
            model_configs['sac'] = {"name": "bmi_fortran",
                                    "params": {
                                        "model_type_name": get_model_type_name('sac'),
                                        "library_file": lib_mod['sac'],
                                        "init_config": os.path.join(bmi_dir['sac'], 'sac-init-' + '{{id}}.namelist.input'),
                                        "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                        "main_output_variable": "tci",
                                        "model_params": grp_params['sac'][grp]}}

            # variable name mapping section
            pet_in = "pet"
            pcp_in = "precip"
            var_maps = var_mapping(grp_mod, pet_in, pcp_in, output_dict)
            var_maps['input']['tair'] = "land_surface_air__temperature"

            # module output variable for input to t-route
            main_output_variable = "tci"

        # snow17
        if 'snow17' in grp_mod:
            model_configs['snow17'] = {"name": "bmi_fortran",
                                       "params": {
                                           "model_type_name": get_model_type_name('snow17'),
                                           "library_file": lib_mod['snow17'],
                                           "init_config": os.path.join(bmi_dir['snow17'], 'snow17-init-' + '{{id}}.namelist.input'),
                                           "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                           "main_output_variable": "raim",
                                           "variables_names_map": {
                                               "precip": "atmosphere_water__liquid_equivalent_precipitation_rate",
                                               "tair": "land_surface_air__temperature"},
                                           "model_params": grp_params['snow17'][grp]}}

        # ueb
        if 'ueb' in grp_mod:
            model_configs['ueb'] = {"name": "bmi_c++",
                                    "params": {
                                        "name": "bmi_c++",
                                        "model_type_name": get_model_type_name('ueb'),
                                        "library_file": lib_mod['ueb'],
                                        "init_config": os.path.join(bmi_dir['ueb'], 'ueb-init-' + '{{id}}_region.dat'),
                                        "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                        "main_output_variable": "SWIT",
                                        "variables_names_map": {
                                            "Prec": "atmosphere_water__liquid_equivalent_precipitation_rate",
                                            "Ta": "land_surface_air__temperature",
                                            "qair": "atmosphere_air_water~vapor__relative_saturation",
                                            "uebu2d": "land_surface_wind__x_component_of_velocity",
                                            "uebv2d": "land_surface_wind__y_component_of_velocity",
                                            "Qli": "land_surface_radiation~incoming~longwave__energy_flux",
                                            "Qsi": "land_surface_radiation~incoming~shortwave__energy_flux",
                                            "AP": "land_surface_air__pressure"},
                                        "model_params": grp_params['ueb'][grp]}}

        # pet
        if 'pet' in grp_mod:
            model_configs['pet'] = {"name": "bmi_c",
                                    "params": {
                                        "model_type_name": get_model_type_name('pet'),
                                        "library_file": lib_mod['pet'],
                                        "init_config": os.path.join(bmi_dir['pet'], '{{id}}_bmi_config.ini'),
                                        "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                        "main_output_variable": "water_potential_evaporation_flux",
                                        "registration_function": "register_bmi_pet"
                                    }}

        # sloth
        if 'sloth' in grp_mod:
            model_configs['sloth'] = {"name": "bmi_c++",
                                      "params": {"name": "bmi_c++",
                                                 "model_type_name": get_model_type_name('sloth'),
                                                 "main_output_variable": "z",
                                                 "library_file": lib_mod['sloth'],
                                                 "init_config": '/dev/null',
                                                 "allow_exceed_end_time": True,
                                                 "fixed_time_step": False,
                                                 "uses_forcing_file": False}}

            if 'cfes' in grp_mod or 'cfex' in grp_mod:
                if 'sft' not in grp_mod:
                    model_params = {
                        "sloth_ice_fraction_schaake(1,double,m,node)": 0.0,
                        "sloth_ice_fraction_xinanjiang(1,double,1,node)": 0.0,
                        "sloth_smp(1,double,1,node)": 0.0}
                else:
                    model_params = {
                        "soil_moisture_wetting_fronts(1,double,1,node)": 0.0,
                        "soil_thickness_layered(1,double,1,node)": 0.0,
                        "soil_depth_wetting_fronts(1,double,1,node)": 0.0,
                        "num_wetting_fronts(1,int,1,node)": 1.0,
                        "Qb_topmodel(1,double,1,node)": 0.0,
                        "Qv_topmodel(1,double,1,node)": 0.0,
                        "global_deficit(1,double,1,node)": 0.0}
            elif 'lasam' in grp_mod:
                if 'sft' not in grp_mod:
                    model_params = {"soil_temperature_profile(1,double,K,node)": 275.15}
                else:
                    model_params = {
                        "sloth_soil_storage(1,double,m,node)": 1.0E-10,
                        "sloth_soil_storage_change(1,double,m,node)": 0.0,
                        "Qb_topmodel(1,double,1,node)": 0.0,
                        "Qv_topmodel(1,double,1,node)": 0.0,
                        "global_deficit(1,double,1,node)": 0.0,
                        "potential_evapotranspiration_rate(1,double,1,node)": 0.0}

            model_configs['sloth']['params']['model_params'] = model_params

        # sft
        if 'sft' in grp_mod:
            model_configs['sft'] = {"name": "bmi_c++",
                                    "params": {"name": "bmi_c++",
                                               "model_type_name": get_model_type_name('sft'),
                                               "main_output_variable": "num_cells",
                                               "library_file": lib_mod['sft'],
                                               "init_config": os.path.join(bmi_dir['sft'], '{{id}}_bmi_config_sft.txt'),
                                               "allow_exceed_end_time": True,
                                               "uses_forcing_file": False,
                                               "variables_names_map": {"ground_temperature": "TGS"}}}

        # smp
        if 'smp' in grp_mod:
            model_configs['smp'] = {"name": "bmi_c++",
                                    "params": {"name": "bmi_c++",
                                               "model_type_name": get_model_type_name('smp'),
                                               "main_output_variable": "soil_water_table",
                                               "library_file": lib_mod['smp'],
                                               "init_config": os.path.join(bmi_dir['smp'], '{{id}}_bmi_config_smp.txt'),
                                               "allow_exceed_end_time": True,
                                               "uses_forcing_file": False,
                                               "variables_names_map": {
                                                   "soil_storage": "SOIL_STORAGE",
                                                   "soil_storage_change": "SOIL_STORAGE_CHANGE"}}}
            if 'lasam' in grp_mod:
                model_configs['smp']['params']["variables_names_map"] = {
                    "soil_storage": "sloth_soil_storage",
                    "soil_storage_change": "sloth_soil_storage_change",
                    "soil_moisture_wetting_fronts": "soil_moisture_wetting_fronts",
                    "soil_depth_wetting_fronts": "soil_depth_wetting_fronts",
                    "num_wetting_fronts": "soil_num_wetting_fronts"}

        # lasam
        if 'lasam' in grp_mod:
            model_configs['lasam'] = {"name": "bmi_c++",
                                      "params": {"name": "bmi_c++",
                                                 "model_type_name": get_model_type_name('lasam'),
                                                 "main_output_variable": "precipitation_rate",
                                                 "library_file": lib_mod['lasam'],
                                                 "init_config": os.path.join(bmi_dir['lasam'], '{{id}}_bmi_config_lasam.txt'),
                                                 "allow_exceed_end_time": True,
                                                 "uses_forcing_file": False,
                                                 "model_params": grp_params['lasam'][grp]}}

            # variable name mapping section
            pet_in = "potential_evapotranspiration_rate"
            pcp_in = "precipitation_rate"
            var_maps = var_mapping(grp_mod, pet_in, pcp_in, output_dict)

            # module output variable for input to t-route
            main_output_variable = "total_discharge"

        if 'lstm' in grp_mod:
            model_configs['lstm'] = {"name": "bmi_python",
                                     "params": {"python_type": "lstm.bmi_lstm.bmi_LSTM",
                                                "model_type_name": get_model_type_name('lstm'),
                                                "main_output_variable": "land_surface_water__runoff_depth",
                                                "init_config": os.path.join(bmi_dir['lstm'], '{{id}}.yml'),
                                                "allow_exceed_end_time": True,
                                                "uses_forcing_file": False}}

            # variable name mapping section
            variables_names_map = dict()
            variables_names_map["streamflow_cms"] = "land_surface_water__runoff_volume_flux",
            variables_names_map["pytorch_model_path"] = os.path.join(bmi_dir['lstm'], "sugar_creek_trained.pt"),
            variables_names_map["normalization_path"] = os.path.join(bmi_dir['lstm'], "input_scaling.csv"),
            variables_names_map["initial_state_path"] = os.path.join(bmi_dir['lstm'], "initial_states.csv"),
            variables_names_map["useGPU"] = False

            var_maps = dict()
            var_maps['input'] = variables_names_map
            var_maps['output'] = dict()
            var_maps['output']['swe_out'] = ''
            var_maps['output']['sm_out'] = ''

            # module output variable for input to t-route
            main_output_variable = "land_surface_water__runoff_depth"

        if 'lstm' in grp_mod:
            model_configs['lstm'] = {"name": "bmi_python",
                                     "params": {"python_type": "lstm.bmi_lstm.bmi_LSTM",
                                                "model_type_name": get_model_type_name('lstm'),
                                                "main_output_variable": "land_surface_water__runoff_depth",
                                                "init_config": os.path.join(bmi_dir['lstm'], '{{id}}.yml'),
                                                "allow_exceed_end_time": True,
                                                "uses_forcing_file": False}}

            # variable name mapping section
            variables_names_map = dict()
            variables_names_map["streamflow_cms"] = "land_surface_water__runoff_volume_flux",
            variables_names_map["pytorch_model_path"] = os.path.join(bmi_dir['lstm'], "sugar_creek_trained.pt"),
            variables_names_map["normalization_path"] = os.path.join(bmi_dir['lstm'], "input_scaling.csv"),
            variables_names_map["initial_state_path"] = os.path.join(bmi_dir['lstm'], "initial_states.csv"),
            variables_names_map["useGPU"] = False

            var_maps = dict()
            var_maps['input'] = variables_names_map
            var_maps['output'] = dict()
            var_maps['output']['swe_out'] = ''
            var_maps['output']['sm_out'] = ''

            # module output variable for input to t-route
            main_output_variable = "land_surface_water__runoff_depth"

        # Store catchment model configs
        model_type_name = "bmi_multi"
        grp_configs = {"name": "bmi_multi",
                       "params": {"name": "bmi_multi", "model_type_name": model_type_name, "init_config": "",
                                  "allow_exceed_end_time": False, "fixed_time_step": False,
                                  "uses_forcing_file": False,
                                  "main_output_variable": main_output_variable}}

        # Output section for each catchment
        output_config = {'output_variables': [], 'output_header_fields': []}
        for key, value in output_dict.items():
            if key == 'output_swe' and var_maps['output']['swe_out'] != '':
                if value:
                    output_config['output_variables'] = output_config['output_variables'] + [var_maps['output']['swe_out']]
                    output_config['output_header_fields'] = output_config['output_header_fields'] + [var_maps['output']['swe_out_header']]

            elif key == 'output_sm' and var_maps['output']['sm_out'] != '':
                if value:
                    output_config['output_variables'] = output_config['output_variables'] + var_maps['output']['sm_out']
                    output_config['output_header_fields'] = output_config['output_header_fields'] + var_maps['output']['sm_out_header']
        if output_config['output_variables'] != []:
            grp_configs['params']['output_variables'] = output_config['output_variables']
        if output_config['output_header_fields'] != []:
            grp_configs['params']['output_header_fields'] = output_config['output_header_fields']

        # determine the RR module in the current formulation
        rr_mod1 = [m1 for m1 in grp_mod if 'Rainfall_runoff' in settings.modules_all.loc[settings.modules_all['module'] == m1, 'process'].values[0]]
        if len(rr_mod1) == 0:
            try:
                raise Exception('No rainfall-runoff module is selected')
            except Exception as e:
                logger.critical(e)
                raise
        elif len(rr_mod1) > 1:
            try:
                raise Exception(f'More than one rainfall-runoff module is selected: {rr_mod1}')
            except Exception as e:
                logger.critical(e)
                raise
        rr_mod1 = rr_mod1[0]

        # modules section
        model_configs[rr_mod1]["params"]["variables_names_map"] = var_maps['input']

        # Group formulation
        grp_configs["params"]["modules"] = [model_configs[m1] for m1 in grp_mod if m1 != 'troute']
        grp_main[grp] = [grp_configs]

    # Initialize global dictionary
    g = {}

    # time object
    t = {"time": {"start_time": time_period['run_time_period']['region'][0],
                  "end_time": time_period['run_time_period']['region'][1], "output_interval": 3600}}
    g.update(t)

    # routing object
    g.update(rt_dict)

    # Set grouped formulations
    g.update({"formulation_groups": grp_main})

    # Set forcing group
    forcing_map = {
        "csv": {"file_pattern": ".*{{id}}.*.csv", "path": forcing_dir, "provider": "CsvPerFeature"},
        "bmi": {"path": forcing_dir, "provider": "ForcingsEngineLumpedDataProvider", "params": {"init_config": str(forcing_config_file)}}
    }
    force_main = {"forcing_grp1": forcing_map[forcing_provider]}
    g.update({"forcing_groups": force_main})

    # Catchment groups
    cat_grps = {cat: {"formulations": grp, "forcing": "forcing_grp1"} for cat, grp in cat_to_grp.items()}
    c = {"catchments": cat_grps}
    g.update(c)

    # save configuration into json file
    with open(realization_file, 'w') as outfile:
        json.dump(g, outfile, indent=4, separators=(", ", ": "), sort_keys=False)
    logger.info(f'Realization file is created at {realization_file}')


def create_realization_file(
        workdir: Union[str, Path],
        lib_file: dict,
        bmi_dir: dict,
        forcing_provider: str,
        forcing_dir: Union[str, Path],
        forcing_config_file: Union[str, Path],
        realization_file: Union[str, Path],
        modules: List[str],
        time_period: dict,
        rt_dict: dict,
        output_dict: dict,
        run_type: str
) -> None:
    """
    Create realization file for the specified model and module

    Parameters
    ----------
    workdir : basin directory for storing all the files
    lib_file : library files for different modules
    bmi_dir : directory for different model or module to store BMI files
    forcing_provider: forcing provider option (csv or bmi)
    forcing_dir : directory to store forcing files
    forcing_config_file: path to forcing engine configuration file
    realization_file : model realization configuration file
    model: model and module combination
    time_period : simulation and evaluation time period
    rt_dict : routing model source file directory and configuration file
    output_dict: whether to output certain variables (currently SWE and soil moisture)
    run_type: type of run (calib, regionalization, or default)

    Returns
    ----------
    None
    """

    # Create symlinks for libraries
    lib_mod = {}
    for key, value in lib_file.items():
        lib_mod_link = os.path.join(workdir, 'Input/' + os.path.basename(value))
        lib_mod.update({key: lib_mod_link})
        if not os.path.exists(lib_mod_link):
            os.symlink(value, lib_mod_link)

    model_configs = {}

    # Abbreviate calibration run_type name for file names/time period
    if run_type == 'calibration':
        run_type = 'calib'

    # noah
    if 'noah' in modules:
        model_configs['noah'] = {"name": "bmi_fortran",
                                 "params": {"name": "bmi_fortran",
                                            "model_type_name": get_model_type_name('noah'),
                                            "main_output_variable": "QINSUR",
                                            "library_file": lib_mod['noah'],
                                            "init_config": os.path.join(bmi_dir['noah'], '{{id}}_' + run_type + '.input'),
                                            "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                            "variables_names_map": {
                                                "PRCPNONC": "atmosphere_water__liquid_equivalent_precipitation_rate",
                                                "Q2": "atmosphere_air_water~vapor__relative_saturation",
                                                "SFCTMP": "land_surface_air__temperature",
                                                "UU": "land_surface_wind__x_component_of_velocity",
                                                "VV": "land_surface_wind__y_component_of_velocity",
                                                "LWDN": "land_surface_radiation~incoming~longwave__energy_flux",
                                                "SOLDN": "land_surface_radiation~incoming~shortwave__energy_flux",
                                                "SFCPRS": "land_surface_air__pressure"}}}

    # cfe or cfex
    if 'cfes' in modules or 'cfex' in modules:
        m1 = 'cfes' if 'cfes' in modules else 'cfex'
        model_configs[m1] = {"name": "bmi_c",
                             "params": {"name": "bmi_c",
                                        "model_type_name": get_model_type_name(m1),
                                        "main_output_variable": "Q_OUT",
                                        "library_file": lib_mod[m1],
                                        "init_config": os.path.join(bmi_dir[m1], '{{id}}_bmi_config_cfe.txt'),
                                        "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                        "registration_function": "register_bmi_cfe"}}

        # variable name mapping section
        pet_in = "water_potential_evaporation_flux"
        pcp_in = "atmosphere_water__liquid_equivalent_precipitation_rate"
        var_maps = var_mapping(modules, pet_in, pcp_in, output_dict)

        # module output variable for input to t-route
        main_output_variable = "Q_OUT"

    # topmodel
    if 'topmodel' in modules:
        model_configs['topmodel'] = {"name": "bmi_c",
                                     "params": {"name": "bmi_c",
                                                "model_type_name": get_model_type_name('topmodel'),
                                                "main_output_variable": "Qout",
                                                "library_file": lib_mod['topmodel'],
                                                "init_config": os.path.join(bmi_dir['topmodel'], '{{id}}_topmodel.run'),
                                                "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                                "registration_function": "register_bmi_topmodel"}}
        # variable name mapping section
        pet_in = "water_potential_evaporation_flux"
        pcp_in = "atmosphere_water__liquid_equivalent_precipitation_rate"
        var_maps = var_mapping(modules, pet_in, pcp_in, output_dict)

        # module output variable for input to t-route
        main_output_variable = "Qout"

    # sac-sma
    if 'sac' in modules:
        model_configs['sac'] = {"name": "bmi_fortran",
                                "params": {
                                    "model_type_name": get_model_type_name('sac'),
                                    "library_file": lib_mod['sac'],
                                    "init_config": os.path.join(bmi_dir['sac'], 'sac-init-{{id}}.namelist.input'),
                                    "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                    "main_output_variable": "tci",
                                }}

        # variable name mapping section
        pet_in = "pet"
        pcp_in = "precip"
        var_maps = var_mapping(modules, pet_in, pcp_in, output_dict)
        var_maps['input']['tair'] = "land_surface_air__temperature"

        # module output variable for input to t-route
        main_output_variable = "tci"

    # snow17
    if 'snow17' in modules:
        model_configs['snow17'] = {"name": "bmi_fortran",
                                   "params": {
                                       "model_type_name": get_model_type_name('snow17'),
                                       "library_file": lib_mod['snow17'],
                                       "init_config": os.path.join(bmi_dir['snow17'], 'snow17-init-{{id}}.namelist.input'),
                                       "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                       "main_output_variable": "raim",
                                       "variables_names_map": {
                                           "precip": "atmosphere_water__liquid_equivalent_precipitation_rate",
                                           "tair": "land_surface_air__temperature"
                                       }}}

    # ueb
    if 'ueb' in modules:
        model_configs['ueb'] = {"name": "bmi_c++",
                                "params": {
                                    "name": "bmi_c++",
                                    "model_type_name": get_model_type_name('ueb'),
                                    "library_file": lib_mod['ueb'],
                                    "init_config": os.path.join(bmi_dir['ueb'], 'ueb-init-{{id}}_' + run_type + '.dat'),
                                    "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                    "main_output_variable": "SWIT",
                                    "variables_names_map": {
                                        "Prec": "atmosphere_water__liquid_equivalent_precipitation_rate",
                                        "Ta": "land_surface_air__temperature",
                                        "qair": "atmosphere_air_water~vapor__relative_saturation",
                                        "uebu2d": "land_surface_wind__x_component_of_velocity",
                                        "uebv2d": "land_surface_wind__y_component_of_velocity",
                                        "Qli": "land_surface_radiation~incoming~longwave__energy_flux",
                                        "Qsi": "land_surface_radiation~incoming~shortwave__energy_flux",
                                        "AP": "land_surface_air__pressure"}}}

    # pet
    if 'pet' in modules:
        model_configs['pet'] = {"name": "bmi_c",
                                "params": {
                                    "model_type_name": get_model_type_name('pet'),
                                    "library_file": lib_mod['pet'],
                                    "init_config": os.path.join(bmi_dir['pet'], '{{id}}_bmi_config.ini'),
                                    "allow_exceed_end_time": True, "fixed_time_step": False, "uses_forcing_file": False,
                                    "main_output_variable": "water_potential_evaporation_flux",
                                    "registration_function": "register_bmi_pet"
                                }}

    # sloth
    if 'sloth' in modules:
        model_configs['sloth'] = {"name": "bmi_c++",
                                  "params": {"name": "bmi_c++",
                                             "model_type_name": get_model_type_name('sloth'),
                                             "main_output_variable": "z",
                                             "library_file": lib_mod['sloth'],
                                             "init_config": '/dev/null',
                                             "allow_exceed_end_time": True,
                                             "fixed_time_step": False,
                                             "uses_forcing_file": False}}

        if 'cfes' in modules or 'cfex' in modules:
            if 'sft' not in modules:
                model_params = {
                    "sloth_ice_fraction_schaake(1,double,m,node)": 0.0,
                    "sloth_ice_fraction_xinanjiang(1,double,1,node)": 0.0,
                    "sloth_smp(1,double,1,node)": 0.0}
            else:
                model_params = {
                    "soil_moisture_wetting_fronts(1,double,1,node)": 0.0,
                    "soil_thickness_layered(1,double,1,node)": 0.0,
                    "soil_depth_wetting_fronts(1,double,1,node)": 0.0,
                    "num_wetting_fronts(1,int,1,node)": 1.0,
                    "Qb_topmodel(1,double,1,node)": 0.0,
                    "Qv_topmodel(1,double,1,node)": 0.0,
                    "global_deficit(1,double,1,node)": 0.0}
        elif 'lasam' in modules:
            if 'sft' not in modules:
                model_params = {"soil_temperature_profile(1,double,K,node)": 275.15}
            else:
                model_params = {
                    "sloth_soil_storage(1,double,m,node)": 1.0E-10,
                    "sloth_soil_storage_change(1,double,m,node)": 0.0,
                    "Qb_topmodel(1,double,1,node)": 0.0,
                    "Qv_topmodel(1,double,1,node)": 0.0,
                    "global_deficit(1,double,1,node)": 0.0,
                    "potential_evapotranspiration_rate(1,double,1,node)": 0.0}

        model_configs['sloth']['params']['model_params'] = model_params

    # sft
    if 'sft' in modules:
        model_configs['sft'] = {"name": "bmi_c++",
                                "params": {"name": "bmi_c++",
                                           "model_type_name": get_model_type_name('sft'),
                                           "main_output_variable": "num_cells",
                                           "library_file": lib_mod['sft'],
                                           "init_config": os.path.join(bmi_dir['sft'], '{{id}}_bmi_config_sft.txt'),
                                           "allow_exceed_end_time": True,
                                           "uses_forcing_file": False,
                                           "variables_names_map": {"ground_temperature": "TGS"}}}

    # smp
    if 'smp' in modules:
        model_configs['smp'] = {"name": "bmi_c++",
                                "params": {"name": "bmi_c++",
                                           "model_type_name": get_model_type_name('smp'),
                                           "main_output_variable": "soil_water_table",
                                           "library_file": lib_mod['smp'],
                                           "init_config": os.path.join(bmi_dir['smp'], '{{id}}_bmi_config_smp.txt'),
                                           "allow_exceed_end_time": True,
                                           "uses_forcing_file": False,
                                           "variables_names_map": {
                                               "soil_storage": "SOIL_STORAGE",
                                               "soil_storage_change": "SOIL_STORAGE_CHANGE"}}}
        if 'lasam' in modules:
            model_configs['smp']['params']["variables_names_map"] = {
                "soil_storage": "sloth_soil_storage",
                "soil_storage_change": "sloth_soil_storage_change",
                "soil_moisture_wetting_fronts": "soil_moisture_wetting_fronts",
                "soil_depth_wetting_fronts": "soil_depth_wetting_fronts",
                "num_wetting_fronts": "soil_num_wetting_fronts"}

    # lasam
    if 'lasam' in modules:
        model_configs['lasam'] = {"name": "bmi_c++",
                                  "params": {"name": "bmi_c++",
                                             "model_type_name": get_model_type_name('lasam'),
                                             "main_output_variable": "precipitation_rate",
                                             "library_file": lib_mod['lasam'],
                                             "init_config": os.path.join(bmi_dir['lasam'], '{{id}}_bmi_config_lasam.txt'),
                                             "allow_exceed_end_time": True,
                                             "uses_forcing_file": False}}

        # variable name mapping section
        pet_in = "potential_evapotranspiration_rate"
        pcp_in = "precipitation_rate"
        var_maps = var_mapping(modules, pet_in, pcp_in, output_dict)

        # module output variable for input to t-route
        main_output_variable = "total_discharge"

    if 'lstm' in modules:
        model_configs['lstm'] = {"name": "bmi_python",
                                 "params": {"python_type": "lstm.bmi_lstm.bmi_LSTM",
                                            "model_type_name": get_model_type_name('lstm'),
                                            "main_output_variable": "land_surface_water__runoff_depth",
                                            "init_config": os.path.join(bmi_dir['lstm'], '{{id}}.yml'),
                                            "allow_exceed_end_time": True,
                                            "uses_forcing_file": False}}

        # variable name mapping section
        variables_names_map = dict()
        variables_names_map["streamflow_cms"] = "land_surface_water__runoff_volume_flux",
        variables_names_map["pytorch_model_path"] = os.path.join(bmi_dir['lstm'], "sugar_creek_trained.pt"),
        variables_names_map["normalization_path"] = os.path.join(bmi_dir['lstm'], "input_scaling.csv"),
        variables_names_map["initial_state_path"] = os.path.join(bmi_dir['lstm'], "initial_states.csv"),
        variables_names_map["useGPU"] = False

        var_maps = dict()
        var_maps['input'] = variables_names_map
        var_maps['output'] = dict()
        var_maps['output']['swe_out'] = ''
        var_maps['output']['sm_out'] = ''

        # module output variable for input to t-route
        main_output_variable = "land_surface_water__runoff_depth"

    # Combine configurations
    model_type_name = "bmi_multi"
    gbmain = {"name": "bmi_multi",
              "params": {"name": "bmi_multi", "model_type_name": model_type_name, "init_config": "",
                         "allow_exceed_end_time": False, "fixed_time_step": False,
                         "uses_forcing_file": False,
                         "main_output_variable": main_output_variable}}

    # Output section
    output_config = {'output_variables': [], 'output_header_fields': []}
    for key, value in output_dict.items():
        if key == 'output_swe' and var_maps['output']['swe_out'] != '':
            if value:
                output_config['output_variables'] = output_config['output_variables'] + [var_maps['output']['swe_out']]
                output_config['output_header_fields'] = output_config['output_header_fields'] + [var_maps['output']['swe_out_header']]

        elif key == 'output_sm' and var_maps['output']['sm_out'] != '':
            if value:
                output_config['output_variables'] = output_config['output_variables'] + var_maps['output']['sm_out']
                output_config['output_header_fields'] = output_config['output_header_fields'] + var_maps['output']['sm_out_header']
    if output_config['output_variables'] != []:
        gbmain['params']['output_variables'] = output_config['output_variables']
    if output_config['output_header_fields'] != []:
        gbmain['params']['output_header_fields'] = output_config['output_header_fields']

    # determine the RR module in the current formulation
    rr_mod1 = [m1 for m1 in modules if 'Rainfall_runoff' in settings.modules_all.loc[settings.modules_all['module'] == m1, 'process'].values[0]]
    if len(rr_mod1) == 0:
        try:
            raise Exception('No rainfall-runoff module is selected')
        except Exception as e:
            logger.critical(e)
            raise
    elif len(rr_mod1) > 1:
        try:
            raise Exception(f'More than one rainfall-runoff module is selected: {rr_mod1}')
        except Exception as e:
            logger.critical(e)
            raise
    rr_mod1 = rr_mod1[0]

    # modules section
    model_configs[rr_mod1]["params"]["variables_names_map"] = var_maps['input']
    gbmain["params"]["modules"] = [model_configs[m1] for m1 in modules if m1 != 'troute']

    # global configuration
    g = {"global": {"formulations": [gbmain],
                    "forcing": {}}}

    # Set forcing configuration
    forcing_map = {
        "csv": {"file_pattern": ".*{{id}}.*.csv", "path": forcing_dir, "provider": "CsvPerFeature"},
        "bmi": {"path": forcing_dir, "provider": "ForcingsEngineLumpedDataProvider", "params": {"init_config": str(forcing_config_file)}}
    }

    g["global"]["forcing"] = forcing_map[forcing_provider]

    # time object
    t = {"time": {"start_time": time_period['run_time_period'][run_type][0],
                  "end_time": time_period['run_time_period'][run_type][1], "output_interval": 3600}}
    g.update(t)

    # routing object
    g.update(rt_dict)

    # save configuration into json file
    with open(realization_file, 'w') as outfile:
        json.dump(g, outfile, indent=4, separators=(", ", ": "), sort_keys=False)
    logger.info(f'Realization file is created at {realization_file}')


def create_calib_config_file(
        par_file: Union[str, Path],
        modules: List[str],
        workdir: Union[str, Path],
        general_dict: dict,
        model_dict: dict,
        config_yaml_file: Union[str, Path],
) -> None:
    """ Create configuration YAML file for calibration run

    Parameters
    ----------
    par_file : file containing min, max and init values of calibration parameters
    modules: list of modules in the formulation
    workdir : basin directory for storing all the files
    general_dict : general settings
    model_dict : model settings
    config_yaml_file : configuration YAML file

    Returns
    ----------
    None

    """

    # Extract calibration params range
    # If par_file (which contains calibration parameters and its initial, min and max values) exists,
    # read from that file directly; otherwise gather this information from predefined calib_params files for
    # individual modules in the directory given by par_file
    calib_modules_config = list(settings.modules_all.loc[settings.modules_all['calibratable'], 'name_config'])
    if os.path.isfile(par_file):
        df_params = pd.read_fwf(par_file).copy()
        df_params = df_params.loc[df_params['model'].isin(calib_modules_config)]
    else:
        if os.path.isdir(par_file):
            df_params = pd.DataFrame()
            for m1 in modules:
                m_ui = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
                m_config = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_config'].iloc[0]
                if m_config in calib_modules_config:
                    f1 = os.path.join(par_file, 'calib_params_' + m_ui + '.csv')
                    if not os.path.exists(f1):
                        logger.warning(f'Folder {par_file} does not contain calibration parameter file for {m_ui}')
                        continue
                    df_tmp = pd.read_csv(f1, sep=None, comment='#', engine='python')
                    df_tmp['model'] = m_config
                    df_params = pd.concat([df_params, df_tmp], ignore_index=True)
        else:
            try:
                raise Exception(f'{par_file} is not a valid file or folder with calibration parameter files for the chosen modules')
            except Exception as e:
                logger.critical(e)
                raise

    params_range_dict = {}
    # Create configuration
    basin_yaml = {'general': general_dict}

    if 'lstm' not in modules:
        if len(df_params) == 0:
            try:
                raise Exception(f'No calibratable parameters found for the list of modules: {modules}')
            except Exception as e:
                logger.critical(e)
                raise

        df_params.set_index('param', inplace=True)
        calib_params = df_params.groupby('model').groups

        params_range_dict = {}
        for k, v in calib_params.items():
            params_range = []
            for m in v:
                params_range.append({'name': m, 'min': float(df_params.query('model==@k').loc[m]['min']),
                                     'max': float(df_params.query('model==@k').loc[m]['max']),
                                     'init': float(df_params.query('model==@k').loc[m]['init'])})
            params_range_dict.update({k: params_range})

        # Create configuration
        basin_yaml = {'general': general_dict}
        basin_yaml.update(params_range_dict)

    # Create symlink for ngen executable
    ngen_file_link = os.path.join(workdir, 'Input/' + os.path.basename(model_dict['binary'])[0:4])
    if os.path.exists(ngen_file_link):
        os.remove(ngen_file_link)
    os.symlink(model_dict['binary'], ngen_file_link)

    model_dict['binary'] = ngen_file_link
    basin_yaml['model'] = model_dict
    if 'lstm' not in modules:
        basin_yaml['model']['params'] = params_range_dict
    else:
        basin_yaml['model']['type'] = 'nocalib'

    # Save configuration into yaml file
    with open(config_yaml_file, 'w') as file:
        yaml.dump(basin_yaml, file, sort_keys=False, default_flow_style=False, indent=2)
    logger.info(f'Calibration config file is created at: {config_yaml_file}')


def create_partition_file(
        partition_generator: str,
        gpkg_file: str,
        nprocs: int,
        work_dir: str,
        basin: str) -> Union[str, Path]:
    """ Create partition file

    Parameters
    ----------
    partition_generator: partition config generator json file
    gpkg_file: GeoPackage hydrofabric file
    nprocs: number of processors
    work_dir : path to working directory
    basin : name of basin

    Returns
    ----------
    None

    """

    partition_file = os.path.join(work_dir, 'Input', f"{basin}_partition_config.json")
    cmd = f"{partition_generator} {gpkg_file} {gpkg_file} {partition_file} {nprocs} '' ''"

    logger.info("Creating partition file for basin %s", basin)
    logger.info(" - Partition generator: %s", partition_generator)
    logger.info(" - Hydrofabric file: %s", gpkg_file)
    logger.info(" - Partition file: %s", partition_file)
    logger.info(" - Number of processors: %s", nprocs)
    logger.info(" - Command: %s", cmd)

    # Run the command and capture output
    try:
        result = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        logger.info("Command output (stdout): %s", result.stdout.decode().strip())
        if result.stderr:
            logger.critical("Command error (stderr): %s", result.stderr.decode().strip())
        result.check_returncode()  # Will raise CalledProcessError if non-zero
    except subprocess.CalledProcessError as e:
        logger.critical("Partition generator command failed with exit code %s", e.returncode)
        raise
    except FileNotFoundError as e:
        logger.critical("Partition generator not found '%s': %s", partition_generator, e)
        raise

    return partition_file
