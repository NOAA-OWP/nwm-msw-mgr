"""
This module contains functions to manage the initial creation of configuration files

@author: Jeffrey Wade, Xia Feng
"""

from pathlib import Path
import os
import logging
import re
import geopandas as gpd
import pandas as pd
import json
from collections import defaultdict

from mswm.utils import ginputfunc as gfun
from mswm.utils import settings
from mswm.utils.log_level import log_level_set
from mswm.utils.process_forcing import update_forcing_in_realization
from mswm.utils.update_bmi_config import update_noah_ueb, update_troute

logger = logging.getLogger(__name__)


class RealizationBuilder:
    def __init__(self, input_path: str, assign_path: str | None = None, forcing_path: str | None = None, output_folder: str | None = None):
        self.input_path = Path(input_path)
        self.assign_path = Path(assign_path) if assign_path else None
        self.forcing_path = Path(forcing_path) if forcing_path else None
        self.output_folder = output_folder if output_folder else None
        logger.info(f"Initialized RealizationBuilder with {input_path}")

    def _load_config(self):
        import configparser
        # Read input config file
        if not os.path.isfile(self.input_path):
            raise ValueError(f'File {self.input_path} does not exist')
        self.config = configparser.ConfigParser()
        self.config.read(self.input_path)

        # Raise error if config file is empty
        if not {section: dict(self.config[section]) for section in self.config.sections()}:
            raise ValueError('Config file is empty')

    def _load_yaml(self):
        import yaml
        # Read the yaml-based configuration file (from a previous ngen-cal validation run)
        self.config_yaml = Path(self.input_path).absolute()
        if not self.config_yaml.exists():
            raise FileNotFoundError(f'Config file {self.config_yaml} does not exist!')
        with open(self.config_yaml) as file:
            self.conf = yaml.safe_load(file)

    def _load_realization(self):
        # Read realization file
        self.real_input_file.resolve(strict=True)
        with open(self.real_input_file) as fp:
            self.real_config = json.load(fp)

    def _load_reg_formulation(self):
        # Read regionalization formulation assignment file
        self.assign_path.resolve(strict=True)
        self.reg_df = pd.read_csv(self.assign_path)

    def _load_reg_catchments(self):
        # Load grouped catchment files produced by regionalization and store grouped catchment ids
        self.grp_to_cat = {}
        for grp in self.reg_df['group']:
            cat_path = os.path.join(self.assign_path.parent, grp + "_catchments.csv")
            cat_df = pd.read_csv(cat_path)

            # Store group id and associated catchments
            self.grp_to_cat[grp] = cat_df.divide_id.tolist()

    def _parse_reg_params(self):
        # Extract regionalization parameters for each group and module
        # Set modules and associated calibratable parameters
        params_dict = {
            'cfes': ['b', 'satdk', 'satpsi', 'slope',
                     'maxsmc', 'wltsmc', 'max_gw_storage', 'Cgw', 'expon',
                     'refkdt', 'Kn', 'Klf'],
            'cfex': ['b', 'satdk', 'satpsi', 'slope',
                     'maxsmc', 'wltsmc', 'max_gw_storage', 'Cgw', 'expon',
                     'refkdt', 'Kn', 'Klf', 'a_Xinanjiang_inflection_point_parameter',
                     'b_Xinanjiang_shape_parameter', 'x_Xinanjiang_shape_parameter'],
            'lasam': ['ponded_depth_max', 'field_capacity', 'smcmin', 'smcmax', 'van_genuchten_alpha', 'van_genuchten_n', 'hydraulic_conductivity'],
            'noah': ['RSURF_EXP', 'CWP', 'VCMX25', 'MP', 'MFSNO', 'RSURF_SNOW', 'SCAMAX'],
            'sac': ['uztwm', 'uzfwm', 'lztwm', 'lzfsm', 'lzfpm', 'adimp', 'uzk', 'lzpk', 'lzsk', 'zperc',
                    'rexp', 'pctim', 'pfree', 'riva', 'side', 'rserv'],
            'snow17': ['scf', 'mfmax', 'mfmin', 'uadj', 'si', 'pxtemp', 'nmf', 'tipm', 'plwhc', 'daygm'],
            'topmodel': ['szm', 't0', 'td', 'chv', 'rv', 'srmax', 'sr0', 'xk0'],
            'ueb': ['ems', 'cg', 'zo', 'rho', 'rhog', 'ks', 'de', 'avo', 'df', 'apr', 'cc', 'hcan', 'lai', 'subalb']
        }

        # For each module, retrieve group and corresponding parameter values
        self.grp_params = {}
        for mod, params in params_dict.items():
            self.grp_params[mod] = {
                row['group']: {param: row[param] for param in params} for _, row in self.reg_df.iterrows()
            }

    def _parse_yaml(self):
        # read realization file path
        self.real_input_file = Path(self.conf['model']['realization'])

        # get hydrofabric gpkg
        self.gpkg_cats = self.conf['model']['catchments']
        self.gpkg_nexus = self.conf['model']['nexus']

        # get ngen executable
        self.ngen_exe = self.conf['model']['binary']

    def _create_fcst_output_dir(self):
        # create output directory
        out_dir0 = Path(self.conf['general']['yaml_file']).parent.parent.resolve(strict=True)
        self.out_dir = Path(out_dir0, 'Forecast_Run', self.output_folder)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f'New run directory created at: {self.out_dir}')

    def _parse_config(self):
        # convert inputs to dictionary
        configs = {}
        for sec in self.config.sections():
            configs[sec] = dict(self.config[sec])

        # reassign config sections for convenience
        self.conf1 = configs['General']
        self.run_type = self.conf1.get("run_type")
        self.conf2 = configs.get({
            "calib": "Calibration",
            "regionalization": "Regionalization"
        }.get(self.run_type))
        self.conf3 = configs['DataFile']

        # get the parallel section
        self.parallelSec = configs['Parallel'] if self.config.has_section("Parallel") else None

        # check the Parallel section values
        if self.parallelSec:
            if 'nprocs' not in self.parallelSec:
                raise ValueError("Parallel section has no nprocs!")
            if 'parallel_ngen_exe' not in self.parallelSec:
                raise ValueError("Parallel section has no parallel_ngen_exe!")
            if 'partition_generator_exe' not in self.parallelSec:
                raise ValueError("Parallel section has no partition_generator_exe!")

        # Use parallel ngen only when the number of processors is greater than 1
        self.parallelSec = configs['Parallel'] if self.config.has_section("Parallel") and int(self.parallelSec['nprocs']) > 1 else None

        # Parse attribute file
        self.attr_file = self.conf3['attributes_file']

    def _parse_time(self):
        # Retrieve time period for calibration
        if self.run_type == 'calib':
            self.time_period = {"run_time_period": {"calib": [self.conf2['calib_start_period'], self.conf2['calib_end_period']],
                                                    "valid": [self.conf2['valid_start_period'], self.conf2['valid_end_period']]},
                                "evaluation_time_period": {"calib": [self.conf2['calib_eval_start_period'], self.conf2['calib_eval_end_period']],
                                                           "valid": [self.conf2['valid_eval_start_period'], self.conf2['valid_eval_end_period']],
                                                           "full": [self.conf2['full_eval_start_period'], self.conf2['full_eval_end_period']]}}
        # Retrieve time period for regionalization
        elif self.run_type == 'regionalization':
            self.time_period = {"run_time_period": {"region": [self.conf2['start_period'], self.conf2['end_period']]}}

    def _parse_calib_settings(self):
        # Retrieve general settings for calibration
        algorithm = self.conf2['optimization_algorithm'].lower()
        swarm_size = int(self.conf2['swarm_size'])
        strategy = {'type': 'estimation', 'algorithm': algorithm}
        if algorithm == 'pso':
            strategy.update({'parameters': {'pool': swarm_size, 'particles': swarm_size,
                             'options': {'c1': float(self.conf2['c1']), 'c2': float(self.conf2['c2']), 'w': float(self.conf2['w'])}}})
        if algorithm == 'gwo':
            strategy.update({'parameters': {'pool': swarm_size, 'particles': swarm_size}})

        # Set general config
        self.general_cfg = {'strategy': strategy, 'name': self.conf1['run_type'], 'log': True, 'workdir': None, 'yaml_file': None,
                            'start_iteration': int(self.conf2['start_iteration']), 'iterations': int(self.conf2['number_iteration']),
                            'restart': int(self.conf2['restart'])}

    def _parse_modules(self):

        if not self.conf1['models']:
            raise Exception('Models must be specified')
        logger.info(f"Available module names: {settings.modules_all['name_ui'].tolist()}")

        # Retrieve modules from config file
        modules0 = [x.replace(" ", "") for x in re.split(',', self.conf1['models'])]
        self.modules = []
        invalid_modules = []

        # Ensure modules match possible options provided in settings
        for m1 in modules0:
            filtered = settings.modules_all.loc[settings.modules_all['name_ui'] == m1.lower(), 'module']

            if filtered.empty:
                invalid_modules.append(m1)

            else:
                self.modules.append(filtered.iloc[0])

        # Raise an error if any invalid modules were found
        if invalid_modules:
            raise ValueError(f"Invalid module(s) found: {', '.join(invalid_modules)}. Please check your configuration.")

        # add sloth if CFE or LASAM is selected
        module_found = [x for x in ['cfes', 'cfex', 'lasam'] if x in self.modules]
        if len(module_found) == 1 and 'sloth' not in self.modules:
            logger.info("CFE or LASAM is used in the formulation. SLOTH added to module list")
            self.modules = ['sloth'] + self.modules

        # make sure SMP and SFT are always selected together
        if 'smp' in self.modules and 'sft' not in self.modules:
            logger.info('SMP and SFT must be selected together. SFT added to module list')
            self.modules = self.modules + ['sft']
        if 'sft' in self.modules and 'smp' not in self.modules:
            logger.info('SMP and SFT must be selected together. SMP added to module list')
            self.modules = self.modules + ['smp']

        # always ensure troute is included
        if 'troute' not in self.modules:
            logger.info("T-Route must be included in the formulation. T-Route added to module list")
            self.modules = self.modules + ['troute']

        # rearrange modules in order of hydrologic processes
        self.modules = [m1 for m1 in settings.modules_all['module'] if m1 in self.modules]

        # Reorder "sft" and "smp"
        if "sft" in self.modules and "smp" in self.modules:
            smp_index = self.modules.index("smp")
            sft_index = self.modules.index("sft")
            if smp_index > sft_index:
                self.modules.remove("smp")
                self.modules.insert(sft_index, "smp")

        logger.info(f"Final list of modules in formulation: {self.modules}\n")

    def _parse_reg_modules(self):
        # Retrieve modules from regionalization formulation assignment file
        # This could potentially be combined with _parse_modules to not repeat code
        logger.info(f"Available module names: {settings.modules_all['name_ui'].tolist()}")
        self.grp_to_form = {}

        for idx, row in self.reg_df.iterrows():
            modules0 = [x.replace(" ", "") for x in re.split('-', row['formulation'])]
            modules = []
            invalid_modules = []

            # Ensure modules match possible options provided in settings
            for m1 in modules0:
                # Catch abbreviations (this could also be performed by modifying settings.modules_all)
                if m1.lower() in ('noah', 'nom'):
                    m1 = 'noah-owp-modular'
                elif m1.lower() == 'cfes':
                    m1 = 'cfe-s'
                elif m1.lower() == 'cfex':
                    m1 = 'cfe-x'
                elif m1.lower() in ('sacsma', 'sac'):
                    m1 = 'sac-sma'
                elif m1.lower() in ('snow17'):
                    m1 = 'snow-17'

                filtered = settings.modules_all.loc[settings.modules_all['name_ui'] == m1.lower(), 'module']

                if filtered.empty:
                    invalid_modules.append(m1)

                else:
                    modules.append(filtered.iloc[0])

            # Raise an error if any invalid modules were found
            if invalid_modules:
                raise ValueError(f"Invalid module(s) found: {', '.join(invalid_modules)}. Please check your configuration.")

            # add sloth if CFE or LASAM is selected
            module_found = [x for x in ['cfes', 'cfex', 'lasam'] if x in modules]
            if len(module_found) == 1 and 'sloth' not in modules:
                logger.info(f"CFE or LASAM is used in the formulation. SLOTH added to module list: {row['group']}")
                modules = ['sloth'] + modules

            # make sure SMP and SFT are always selected together
            if 'smp' in modules and 'sft' not in modules:
                logger.info(f"SMP and SFT must be selected together. SFT added to module list: {row['group']}")
                modules = modules + ['sft']
            if 'sft' in modules and 'smp' not in modules:
                logger.info(f"SMP and SFT must be selected together. SMP added to module list: {row['group']}")
                modules = modules + ['smp']

            # always ensure troute is included
            if 'troute' not in modules:
                logger.info(f"T-Route must be included in the formulation. T-Route added to module list: {row['group']}")
                modules = modules + ['troute']

            # rearrange modules in order of hydrologic processes
            modules = [m1 for m1 in settings.modules_all['module'] if m1 in modules]
            logger.info(f"Final list of modules in formulation for {row['group']}: {modules}\n")

            # Reorder "sft" and "smp"
            if "sft" in modules and "smp" in modules:
                smp_index = modules.index("smp")
                sft_index = modules.index("sft")
                if smp_index > sft_index:
                    modules.remove("smp")
                    modules.insert(sft_index, "smp")

            # Store with regionalization group id
            self.grp_to_form[row['group']] = modules

    def _validate_processes(self):
        # check modules selected for each process
        procs = []
        for p1 in settings.modules_all['process']:
            procs = list(set(procs + p1))

        for p1 in procs:
            mods = [m1 for m1 in self.modules if p1 in settings.modules_all.loc[settings.modules_all['module'] == m1, 'process'].values[0]]

            # make sure only one module is selected for each process (except for Soil_moisture and Glacier_snow)
            if len(mods) > 1 and p1 not in ['Soil_moisture', 'Glacier_snow']:
                raise Exception(f'Only one module can be selected for {p1} process')

            # one and only one module must be selected for rainfall-runoff and PET
            if (p1 in ['Evapotranspiration', 'Rainfall_runoff']) and (len(mods) == 0):
                raise Exception(f'At least one module must be selected for {p1} process')

    def _validate_reg_processes(self):
        # check modules selected for each process
        procs = []
        for p1 in settings.modules_all['process']:
            procs = list(set(procs + p1))

        # Loop through formulation group dictionary
        for grp, form in self.grp_to_form.items():
            for p1 in procs:
                mods = [m1 for m1 in form if p1 in settings.modules_all.loc[settings.modules_all['module'] == m1, 'process'].values[0]]

                # make sure only one module is selected for each process (except for Soil_moisture and Glacier_snow)
                if len(mods) > 1 and p1 not in ['Soil_moisture', 'Glacier_snow']:
                    raise Exception(f'Only one module can be selected for {p1} process: {grp}')

                # one and only one module must be selected for rainfall-runoff and PET
                if (p1 in ['Evapotranspiration', 'Rainfall_runoff']) and (len(mods) == 0):
                    raise Exception(f'At least one module must be selected for {p1} process: {grp}')

    def _map_cat_to_grp(self):
        # Relate catchments and their groups
        self.cat_to_grp = {}
        for grp, cats in self.grp_to_cat.items():
            for cat in cats:
                self.cat_to_grp[cat] = grp

    def _map_cat_to_form(self):
        # Relate catchments and their formulations
        self.cat_to_form = {}
        for cat, grp in self.cat_to_grp.items():
            self.cat_to_form[cat] = self.grp_to_form[grp]

    def _map_mod_to_cat(self):
        # Find the catchments that use each module
        self.mod_to_cat = defaultdict(list)
        for cat, modules in self.cat_to_form.items():
            for module in modules:
                self.mod_to_cat[module].append(cat)

    def _set_lib_paths(self):
        # library files for all modules included in the formulation
        self.lib_file = {}
        if self.run_type == 'calib':
            modules1 = [m1 for m1 in self.modules if m1 != 'troute']
        elif self.run_type == 'regionalization':
            modules1 = list(set(m1 for form in self.grp_to_form.values() for m1 in form if m1 != 'troute'))
            self.all_mod = modules1.copy()

        for m1 in modules1:
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
            m2 = m2 if m2 not in ['cfe-s', 'cfe-x'] else 'cfe'
            self.lib_file[m1] = self.conf3[m2 + '_lib']

    def _create_calib_input_dir(self):
        # Create Input directory
        self.basin = self.conf1['basin']
        run_dir = os.path.join(self.conf1['main_dir'], '_'.join([self.conf2['objective_function'], self.conf2['optimization_algorithm']]))
        self.work_dir = os.path.join(run_dir, self.conf1['formulation'] + '/' + self.basin)
        self.input_dir = os.path.join(self.work_dir, 'Input/')
        os.makedirs(self.input_dir, exist_ok=True)

    def _create_reg_input_dir(self):
        # Create Input directory
        self.basin = self.conf1['basin']
        run_dir = os.path.join(self.conf1['main_dir'], 'regionalization')
        self.work_dir = os.path.join(run_dir, self.conf1['formulation'] + '/' + self.basin)
        self.input_dir = os.path.join(self.work_dir, 'Input/')
        os.makedirs(self.input_dir, exist_ok=True)

    def _extract_hydrofabric(self):
        # Extract hydrofabric files
        self.gpkg_file = self.conf3['hydrofab_file']
        if not os.path.exists(self.gpkg_file):
            raise Exception(f'Geo package file does not exist: {self.gpkg_file}')
        self.cat_file = os.path.join(self.input_dir, os.path.basename(self.gpkg_file))
        self.nexus_file = os.path.join(self.input_dir, os.path.basename(self.gpkg_file))
        self.walk_file = self.input_dir + '{}'.format(self.basin) + '_crosswalk.json'
        if not os.path.exists(self.cat_file):
            logger.info(f'Creating symlink from {self.gpkg_file} to {self.cat_file}')
            os.symlink(self.gpkg_file, self.cat_file)
        gfun.create_walk_file(self.basin, self.gpkg_file, self.walk_file)

    def _extract_forcing(self):
        # Extract forcing files
        missing_catchment_files = []
        self.forcing_path = os.path.join(self.input_dir, 'forcing')
        os.makedirs(self.forcing_path, exist_ok=True)
        self.catids = gpd.read_file(self.gpkg_file, layer='divides')['divide_id'].tolist()
        for catID in self.catids:
            ffile = os.path.join(self.conf3['forcing_dir'], catID + '.csv')
            # Make sure we have the file
            if not os.path.exists(ffile):
                logger.info(f'Forcing file {ffile} does not exist')
                missing_catchment_files.append(ffile)
            else:
                target = os.path.join(self.forcing_path, os.path.basename(ffile))
                if not os.path.exists(target):
                    # print(f'Creating symlink from {ffile} to {target}')
                    os.symlink(ffile, target)
        if missing_catchment_files:
            raise Exception(f'Missing catchment files in forcing data - {missing_catchment_files}')

    def _extract_streamflow_obs(self):
        # Extract streamflow observation
        if 'obs_dir' in self.conf3.keys():
            if self.conf3['obs_dir'] != '':
                obs = pd.read_csv(os.path.join(self.conf3['obs_dir'], self.basin + '_hourly_discharge.csv'))[['dateTime', 'q_cms']]
                obs = obs.rename(columns={'dateTime': 'value_date', 'q_cms': 'obs_flow'})
                self.obsflow_file = self.input_dir + '{}'.format(self.basin) + '_hourly_discharge.csv'
                obs.to_csv(self.obsflow_file, index=False)
        else:
            self.obsflow_file = None

    def _set_output_vars(self):
        # whether to output SWE or soil moisture (default to False)
        self.output_dict = dict()
        for s1 in ['output_swe', 'output_sm']:
            if (s1 not in self.conf2.keys()) or (self.conf2[s1] is None) or (self.conf2[s1] == ''):
                self.output_dict[s1] = False
            elif self.conf2[s1].lower() == 'true':
                self.output_dict[s1] = True
            elif self.conf2[s1].lower() == 'false':
                self.output_dict[s1] = False
            else:
                raise ValueError(f'Invalid value provided for {s1}')

        # define depth (in meters) to output soil moisture at
        self.output_dict['sm_frac_depth'] = 0.4
        self.output_dict['sm_profile_depth'] = 0.1
        if self.output_dict['output_sm']:
            for s1 in ['sm_profile_depth', 'sm_frac_depth']:
                if (s1 in self.conf2.keys()) and (self.conf2[s1] != ''):
                    self.output_dict[s1] = float(self.conf2[s1])

    def _update_fcst_realization(self):
        # Update forcing and time related info in realization file
        self.real_config = update_forcing_in_realization(Path(self.forcing_path), self.real_config, self.gpkg_cats)

    def _update_fcst_noah_ueb(self):
        # For UEB and Noah-OWP-Modular, create new BMI config files with new time info, and
        # update path to BMI configs in realization file accordingly
        self.real_config = update_noah_ueb(self.real_config, self.out_dir)

    def _update_fcst_troute(self):
        # Update BMI config files for t-route
        self.real_config = update_troute(self.real_config, self.out_dir)

    def _init_config_hooks(self):
        from mswm.config_gen.hook_providers import DefaultHookProvider

        # Initialize ngen_config_gen hook provider
        # Load hydrofabric data
        self.hf: gpd.GeoDataFrame = gpd.read_file(self.gpkg_file, layer="divides")
        self.hf_lnk_data: pd.DataFrame = pd.read_parquet(self.attr_file)

        # Subset hydrofabric data to catchments
        self.hf = self.hf[self.hf["divide_id"].isin(self.catids)]
        self.hf_lnk_data = self.hf_lnk_data[self.hf_lnk_data["divide_id"].isin(self.catids)]

        # Initialize hook provider and file writer
        self.hook_provider = DefaultHookProvider(hf=self.hf, hf_lnk_data=self.hf_lnk_data)

    def _get_module_hooks(self):
        from mswm.config_gen.models.cfes import Cfes
        from mswm.config_gen.models.cfex import Cfex
        from mswm.config_gen.models.pet import Pet

        # Translate model names into config_gen hooks
        self.module_to_hook = {
            "sloth": None,
            # "NoahOWP": noah_owp,
            "cfes": Cfes,
            "cfex": Cfex,
            "pet": Pet,
        }

    def _create_bmi_configs_ngen_config_gen(self):

        self.run_configs = ['_troute_config_calib.yaml', '_troute_config_valid_control.yaml', '_troute_config_valid_best.yaml']
        modules1 = self.modules.copy()
        if 'cfes' in self.modules:
            modules1 = ['cfes'] + [m1 for m1 in self.modules if m1 != 'cfes']
        if 'cfex' in self.modules:
            modules1 = ['cfex'] + [m1 for m1 in self.modules if m1 != 'cfex']

        # loop through modules to create input files
        for m1 in modules1:

            # module name used by the UI
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]

            # define module input directory
            mod_input_dir = os.path.join(self.input_dir, m2 + '_input')
            if os.path.isdir(mod_input_dir):
                if os.path.islink(mod_input_dir):
                    os.unlink(mod_input_dir)

            # make symlinks to existing input files or create new input files
            bmi_dir = self.conf3.get(m2 + '_bmi_dir')
            if m1 in ['sloth']:
                pass
            elif m1 in ['topmodel']:
                os.makedirs(mod_input_dir, exist_ok=True)
                if bmi_dir == '' or not os.path.exists(bmi_dir):
                    # Create topmodel input from attribute file
                    gfun.create_topmodel_input(self.catids, self.attr_file, self.gpkg_file, mod_input_dir)
                else:
                    # Modify existing topmodel inputs
                    for catID in self.catids:
                        run_file = os.path.join(bmi_dir, '{}_topmodel'.format(catID) + '.run')
                        params_file = os.path.join(bmi_dir, '{}_topmodel_params'.format(catID) + '.dat')
                        subcat_file = os.path.join(bmi_dir, '{}_topmodel_subcat'.format(catID) + '.dat')
                        gfun.change_topmodel_input(catID, run_file, params_file, subcat_file, mod_input_dir)

            # ignore t-route config files provided via the bmi_dir for now
            elif m1 != 'troute' and bmi_dir and os.path.isdir(bmi_dir):
                if not os.listdir(bmi_dir):
                    raise ValueError(f'BMI folder {bmi_dir} cannot be empty')
                else:

                    # from EDS (or the user) with correct time period and/or paths
                    # For SMP, the depth to output soil moisture may need to be adjusted
                    if m1 == 'noah':
                        gfun.create_noah_input_template(self.catids, self.time_period, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.run_type)
                    elif m1 == 'ueb':
                        gfun.create_ueb_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.run_type)
                    elif m1 in ['sac', 'snow17']:
                        gfun.change_sac_snow17_input(m1, self.catids, mod_input_dir, self.conf3[m2 + "_bmi_dir"])
                    elif m1 == 'lasam':
                        gfun.change_lasam_input(self.catids, mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.conf3['lasam_parameter_dir'])
                    elif m1 == "smp" and self.output_dict['output_sm']:
                        self.output_dict['sm_profile_depth'] = gfun.change_smp_input(self.catids, mod_input_dir, self.conf3[m2 + "_bmi_dir"],
                                                                                     self.output_dict['sm_frac_depth'], self.output_dict['sm_profile_depth'])
                    else:
                        # Create symbolic link to copy existing bmi config files to input directory
                        logger.info(f'{m2}: create symlink from {bmi_dir} to {mod_input_dir}')
                        os.symlink(bmi_dir, mod_input_dir, target_is_directory=True)
            else:
                # If filepath to BMI config file is empty:
                # Initialize config_gen file writer
                from mswm.config_gen.file_writer import DefaultFileWriter
                self.file_writer = DefaultFileWriter(mod_input_dir)

                # Get ngen_config_gen hook objects
                self.hook_objects = [self.module_to_hook.get(m1)]

                # Write BMI config for module
                from mswm.config_gen.generate import generate_configs
                generate_configs(
                    hook_providers=self.hook_provider,
                    hook_objects=self.hook_objects,
                    file_writer=self.file_writer,
                )

                if m1 == 'troute':
                    for file_name, run_name in zip(self.run_configs, ['calib', 'valid', 'valid']):
                        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + file_name)
                        run_name1 = file_name.replace('_troute_config_', '').replace('.yaml', '')
                        if len(self.time_period['run_time_period'][run_name][0]) != 0 & len(self.time_period['run_time_period'][run_name][0]):
                            run_range = pd.to_datetime(self.time_period['run_time_period'][run_name])
                            nts = len(pd.date_range(start=run_range[0], end=run_range[1], freq='5min')) - 1
                            gfun.create_troute_config(self.gpkg_file, routing_config_file, self.time_period['run_time_period'][run_name][0], nts)
                            logger.info(f'troute config file for {run_name1} is created at: {routing_config_file}')

                elif m1 != 'troute':
                    logger.info(f'{m1}: input config files created at: {mod_input_dir}')

    def _create_bmi_configs(self):
        # always create CFE inputs first since sft/smp need data from CFE inputs if they are selected
        self.run_configs = ['_troute_config_calib.yaml', '_troute_config_valid_control.yaml', '_troute_config_valid_best.yaml']
        modules1 = self.modules.copy()
        if 'cfes' in self.modules:
            modules1 = ['cfes'] + [m1 for m1 in self.modules if m1 != 'cfes']
        if 'cfex' in self.modules:
            modules1 = ['cfex'] + [m1 for m1 in self.modules if m1 != 'cfex']

        # loop through modules to create input files
        for m1 in modules1:

            # module name used by the UI
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]

            # define module input directory
            mod_input_dir = os.path.join(self.input_dir, m2 + '_input')
            if os.path.isdir(mod_input_dir):
                if os.path.islink(mod_input_dir):
                    os.unlink(mod_input_dir)

            # make symlinks to existing input files or create new input files
            bmi_dir = self.conf3.get(m2 + '_bmi_dir')

            # Skip config generation for sloth
            if m1 in ['sloth']:
                pass
            # Create bmi configs for topmodel
            elif m1 in ['topmodel']:
                if bmi_dir == '' or not os.path.exists(bmi_dir):
                    # Create topmodel input from attribute file
                    gfun.create_topmodel_input(self.catids, self.attr_file, self.gpkg_file, mod_input_dir)
                else:
                    # Modify existing topmodel inputs
                    for catID in self.catids:
                        run_file = os.path.join(bmi_dir, '{}_topmodel'.format(catID) + '.run')
                        params_file = os.path.join(bmi_dir, '{}_topmodel_params'.format(catID) + '.dat')
                        subcat_file = os.path.join(bmi_dir, '{}_topmodel_subcat'.format(catID) + '.dat')
                        gfun.change_topmodel_input(catID, run_file, params_file, subcat_file, mod_input_dir)

            # Modify existing BMI config files if filepaths provided (ignoring troute for now)
            elif m1 != 'troute' and bmi_dir and os.path.isdir(bmi_dir):

                if not os.listdir(bmi_dir):
                    raise ValueError(f'BMI folder {bmi_dir} cannot be empty')
                else:

                    # Modify existing BMI config files from EDFS or the user with correct time period and/or paths
                    if m1 == 'noah':
                        gfun.create_noah_input_template(self.catids, self.time_period, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.run_type)
                    elif m1 == 'ueb':
                        gfun.create_ueb_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.run_type)
                    elif m1 in ['sac', 'snow17']:
                        gfun.change_sac_snow17_input(m1, self.catids, mod_input_dir, self.conf3[m2 + "_bmi_dir"])
                    elif m1 == 'lasam':
                        gfun.change_lasam_input(self.catids, mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.conf3['lasam_parameter_dir'])
                    elif m1 == 'smp' and self.output_dict['output_sm']:
                        # For SMP, the depth to output soil moisture may need to be adjusted
                        self.output_dict['sm_profile_depth'] = gfun.change_smp_input(self.catids, mod_input_dir, self.conf3[m2 + "_bmi_dir"],
                                                                                     self.output_dict['sm_frac_depth'], self.output_dict['sm_profile_depth'])
                    elif m1 == 'sft':
                        # Modify SFT inputs to ensure ice_fraction_scheme matches rainfall_runoff model
                        gfun.change_sft_input(self.catids, modules1, mod_input_dir, bmi_dir, self.run_type)
                    else:
                        # Create symbolic link
                        logger.info(f'{m2}: create symlink from {bmi_dir} to {mod_input_dir}')
                        os.symlink(bmi_dir, mod_input_dir, target_is_directory=True)

            else:
                if m1 in ['cfes', 'cfex']:
                    gfun.create_cfe_input(self.catids, self.modules, self.attr_file, mod_input_dir, self.run_type)
                elif m1 == 'ueb':
                    gfun.create_ueb_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, '', self.run_type)
                elif m1 == 'snow17':
                    gfun.create_snow17_input(self.catids, self.attr_file, self.gpkg_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir)
                elif m1 == "pet":
                    gfun.create_pet_input(self.catids, self.attr_file, mod_input_dir)
                elif m1 == "sac":
                    gfun.create_sac_input(self.catids, self.attr_file, self.gpkg_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir)
                elif m1 == 'noah':
                    gfun.create_noah_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.run_type)
                elif m1 == 'sft':
                    sft_dir = os.path.join(self.input_dir, 'sft_input')
                    smp_dir = os.path.join(self.input_dir, 'smp_input')

                    # Update CFE bmi dir with correct scheme (Schaake/Xinanjiang)
                    if ('cfes' in self.modules):
                        # If bmi_dir not provided by input file, create from input dir
                        if self.conf3['cfe-s_bmi_dir'] == '':
                            cfe_dir = os.path.join(self.input_dir, 'cfe-s_input')
                        # If bmi_dir provided by input file, use that path
                        else:
                            cfe_dir = self.conf3['cfe-s_bmi_dir']
                    elif ('cfex' in self.modules):
                        # If bmi_dir not provided by input file, create from input dir
                        if self.conf3['cfe-x_bmi_dir'] == '':
                            cfe_dir = os.path.join(self.input_dir, 'cfe-x_input')
                        # If bmi_dir provided by input file, use that path
                        else:
                            cfe_dir = self.conf3['cfe-x_bmi_dir']
                    else:
                        # If CFE BMI config files not provided and cfe not in modules, create cfe input files
                        cfe_dir = os.path.join(self.input_dir, 'cfe-s' + '_input')
                        gfun.create_cfe_input(self.catids, ['cfes'] + [self.modules], self.attr_file, cfe_dir, self.run_type)
                        # raise Exception('Folder for CFE BMI config files needs to be provided, via either cfe-s_bmi_dir or cfe-x_bmi_dir')

                    # Create sft input
                    gfun.create_sft_smp_input(self.catids, self.modules, self.attr_file, cfe_dir, self.conf3['forcing_dir'], sft_dir, smp_dir, self.run_type)

                elif m1 == 'smp':
                    continue
                elif m1 == 'lasam':
                    gfun.create_lasam_input(self.catids, self.modules, mod_input_dir, self.conf3['lasam_parameter_dir'], self.run_type)

                elif m1 == 'troute':
                    for file_name, run_name in zip(self.run_configs, ['calib', 'valid', 'valid']):
                        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + file_name)
                        run_name1 = file_name.replace('_troute_config_', '').replace('.yaml', '')
                        if len(self.time_period['run_time_period'][run_name][0]) != 0 & len(self.time_period['run_time_period'][run_name][0]):
                            run_range = pd.to_datetime(self.time_period['run_time_period'][run_name])
                            nts = len(pd.date_range(start=run_range[0], end=run_range[1], freq='5min')) - 1
                            gfun.create_troute_config(self.gpkg_file, routing_config_file, self.time_period['run_time_period'][run_name][0], nts)
                            logger.info(f'troute config file for {run_name1} is created at: {routing_config_file}')

                if m1 != 'troute':
                    logger.info(f'{m1}: input config files created at: {mod_input_dir}')

    def _create_reg_bmi_configs(self):

        self.run_configs = ['_troute_config_region.yaml']

        # Retrieve unique modules in all formulations, maintaining formulation order
        mod_all = list(dict.fromkeys(item for lst in self.grp_to_form.values() for item in lst))

        # Ensure cfes and cfex are first in mod_all
        if 'cfes' in mod_all:
            mod_all = ['cfes'] + [m1 for m1 in mod_all if m1 != 'cfes']
        if 'cfex' in mod_all:
            mod_all = ['cfex'] + [m1 for m1 in mod_all if m1 != 'cfex']

        # loop through modules to create input files
        for m1 in mod_all:

            # module name used by the UI
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]

            # define and store module input directory
            mod_input_dir = os.path.join(self.input_dir, m2 + '_input')
            if os.path.isdir(mod_input_dir):
                if os.path.islink(mod_input_dir):
                    os.unlink(mod_input_dir)

            # Store input dir in dictionary
            bmi_dir = self.conf3.get(m2 + '_bmi_dir')

            # Retrieve catchments that use each module
            cat_mod = self.mod_to_cat[m1]

            # If module requires full formulation, retrieve formulation for each catchment
            if m1 in ['cfes', 'cfex', 'sft', 'lasam']:
                form_cat = [self.cat_to_form[cat] for cat in cat_mod]

            # Modify existing BMI config files if filepaths provided (ignoring troute for now)
            # Skip config generation for sloth
            if m1 in ['sloth']:
                pass
            # Create bmi configs for topmodel
            elif m1 in ['topmodel']:
                if bmi_dir == '' or not os.path.exists(bmi_dir):
                    # Create topmodel input from attribute file
                    gfun.create_topmodel_input(cat_mod, self.attr_file, self.gpkg_file, mod_input_dir)
                else:
                    # Modify existing topmodel inputs
                    for catID in cat_mod:
                        run_file = os.path.join(bmi_dir, '{}_topmodel'.format(catID) + '.run')
                        params_file = os.path.join(bmi_dir, '{}_topmodel_params'.format(catID) + '.dat')
                        subcat_file = os.path.join(bmi_dir, '{}_topmodel_subcat'.format(catID) + '.dat')
                        gfun.change_topmodel_input(catID, run_file, params_file, subcat_file, mod_input_dir)

            elif m1 != 'troute' and bmi_dir and os.path.isdir(bmi_dir):

                if not os.listdir(bmi_dir):
                    raise ValueError(f'BMI folder {bmi_dir} cannot be empty')
                else:

                    # Modify existing BMI config files from EDFS or the user with correct time period and/or paths
                    if m1 == 'noah':
                        gfun.create_noah_input_template(cat_mod, self.time_period, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.run_type)
                    elif m1 == 'ueb':
                        gfun.create_ueb_input(cat_mod, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.run_type)
                    elif m1 in ['sac', 'snow17']:
                        gfun.change_sac_snow17_input(m1, cat_mod, mod_input_dir, self.conf3[m2 + "_bmi_dir"])
                    elif m1 == 'lasam':
                        gfun.change_lasam_input(cat_mod, mod_input_dir, self.conf3[m2 + "_bmi_dir"], self.conf3['lasam_parameter_dir'])
                    elif m1 == "smp" and self.output_dict['output_sm']:
                        # For SMP, the depth to output soil moisture may need to be adjusted
                        self.output_dict['sm_profile_depth'] = gfun.change_smp_input(cat_mod, mod_input_dir, self.conf3[m2 + "_bmi_dir"],
                                                                                     self.output_dict['sm_frac_depth'], self.output_dict['sm_profile_depth'])
                    # Modify existing SFT inputs to match rainfall runoff model
                    elif m1 == "sft":
                        # Loop through schemes that could be paired with SFT (CFES/CFEX/LASAM)
                        # SFT could be paired with CFES/CFEX/LASAM simulatenously in different formulations, so configs must be generated separately
                        for scheme in ['cfes', 'cfex', 'lasam']:
                            # Retrieve formulation groups where CFES/CFEX/LASAM co-occur with SFT
                            scheme_sft_grps = [grp for grp, mods in self.grp_to_form.items() if scheme in mods and 'sft' in mods]
                            if scheme_sft_grps:
                                # Update CFE bmi dir with correct scheme for formulation (Schaake/Xinanjiang)
                                if scheme == 'cfes' or scheme == 'lasam':
                                    scheme_bmi_var = 'cfe-s'
                                elif scheme == 'cfex':
                                    scheme_bmi_var = 'cfe-x'
                                else:
                                    raise Exception('SMP/SFT only implemented when CFE-S, CFE-X, or LASAM are selected')

                                # Retrieve catchments and formulations corresponding to scheme
                                scheme_cat = [cat for grp in scheme_sft_grps for cat in self.grp_to_cat[grp]]
                                scheme_form = [self.cat_to_form[cat] for cat in scheme_cat]

                                # Form CFE input dir
                                cfe_dir = os.path.join(self.input_dir, scheme_bmi_var + '_input')

                                # If LASAM is selected, create cfe input files required for sft (assume ice_fraction_scheme is cfes)
                                if scheme not in ('cfes', 'cfex'):
                                    scheme_form_cfes = [form + ['cfes'] for form in scheme_form]
                                    gfun.create_cfe_input(scheme_cat, scheme_form_cfes, self.attr_file, cfe_dir, self.run_type)

                                # Create SFT inputs
                                gfun.change_sft_input(scheme_cat, scheme_form, mod_input_dir, bmi_dir, self.run_type)

                    else:
                        # Create symbolic link to catchments with formulation
                        os.makedirs(mod_input_dir, exist_ok=True)
                        logger.info(f'{m2}: create symlink from {bmi_dir} to {mod_input_dir}')

                        # Only link files for required catchments, rather than all files
                        # Could go back to symlinking all files if this causes performance issues
                        for cat in cat_mod:
                            file_match = list(Path(bmi_dir).glob(f"*{cat}*"))
                            for fp in file_match:
                                dest = Path(mod_input_dir) / fp.name
                                if not dest.exists():
                                    os.symlink(fp.resolve(), dest)

            else:
                if m1 in ['cfes', 'cfex']:
                    gfun.create_cfe_input(cat_mod, form_cat, self.attr_file, mod_input_dir, self.run_type)
                elif m1 == 'ueb':
                    gfun.create_ueb_input(cat_mod, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, '', self.run_type)
                elif m1 == 'snow17':
                    gfun.create_snow17_input(cat_mod, self.attr_file, self.gpkg_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir)
                elif m1 == "pet":
                    gfun.create_pet_input(cat_mod, self.attr_file, mod_input_dir)
                elif m1 == "sac":
                    gfun.create_sac_input(cat_mod, self.attr_file, self.gpkg_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir)
                elif m1 == 'noah':
                    gfun.create_noah_input(cat_mod, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.run_type)
                elif m1 == 'sft':
                    sft_dir = os.path.join(self.input_dir, 'sft_input')
                    smp_dir = os.path.join(self.input_dir, 'smp_input')

                    # Loop through schemes that could be paired with SFT (CFES/CFEX/LASAM)
                    # SFT could be paired with CFES/CFEX/LASAM simulatenously in different formulations, so configs must be generated separately
                    for scheme in ['cfes', 'cfex', 'lasam']:
                        # Retrieve formulation groups where CFES/CFEX/LASAM co-occur with SFT
                        scheme_sft_grps = [grp for grp, mods in self.grp_to_form.items() if scheme in mods and 'sft' in mods]
                        if scheme_sft_grps:
                            # Update CFE bmi dir with correct scheme for formulation (Schaake/Xinanjiang)
                            if scheme == 'cfes' or scheme == 'lasam':
                                scheme_bmi_var = 'cfe-s'
                            elif scheme == 'cfex':
                                scheme_bmi_var = 'cfe-x'
                            else:
                                raise Exception('SMP/SFT only implemented when CFE-S, CFE-X, or LASAM are selected')

                            # Retrieve catchments and formulations corresponding to scheme
                            scheme_cat = [cat for grp in scheme_sft_grps for cat in self.grp_to_cat[grp]]
                            scheme_form = [self.cat_to_form[cat] for cat in scheme_cat]

                            # Form CFE input dir
                            cfe_dir = os.path.join(self.input_dir, scheme_bmi_var + '_input')

                            # If LASAM is selected, create cfe input files required for sft (assume ice_fraction_scheme is cfes)
                            if scheme not in ('cfes', 'cfex'):
                                scheme_form_cfes = [form + ['cfes'] for form in scheme_form]
                                gfun.create_cfe_input(scheme_cat, scheme_form_cfes, self.attr_file, cfe_dir, self.run_type)

                            # Create SFT/SMP inputs
                            gfun.create_sft_smp_input(scheme_cat, scheme_form, self.attr_file, cfe_dir, self.conf3['forcing_dir'], sft_dir, smp_dir, self.run_type)

                # Skip smp, inputs created in tandem with sft
                elif m1 == 'smp':
                    continue
                elif m1 == 'lasam':
                    gfun.create_lasam_input(cat_mod, form_cat, mod_input_dir, self.conf3['lasam_parameter_dir'], self.run_type)

                elif m1 == 'troute':
                    for file_name, run_name in zip(self.run_configs, ['region']):
                        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + file_name)
                        run_name1 = file_name.replace('_troute_config_', '').replace('.yaml', '')
                        if len(self.time_period['run_time_period'][run_name][0]) != 0 & len(self.time_period['run_time_period'][run_name][0]):
                            run_range = pd.to_datetime(self.time_period['run_time_period'][run_name])
                            nts = len(pd.date_range(start=run_range[0], end=run_range[1], freq='5min')) - 1
                            gfun.create_troute_config(self.gpkg_file, routing_config_file, self.time_period['run_time_period'][run_name][0], nts)
                            logger.info(f'troute config file for {run_name1} is created at: {routing_config_file}')

                if m1 != 'troute':
                    logger.info(f'{m1}: input config files created at: {mod_input_dir}')

    def _write_calib_realization(self):
        # Create model realization file for calibration
        self.realization_file = self.work_dir + '/{}'.format(self.basin) + '_realization_config_bmi_calib.json'
        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + self.run_configs[0])
        bmi_dir = {}
        for m1 in self.modules:
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
            bmi_dir[m1] = os.path.join(self.input_dir, m2 + '_input')
        rt_dict = {"routing": {"t_route_config_file_with_path": routing_config_file}}

        gfun.create_realization_file(self.work_dir, self.lib_file, bmi_dir, self.forcing_path, self.realization_file,
                                     self.modules, self.time_period, rt_dict, self.output_dict)

    def _write_region_realization(self):
        # Create model realization file for regionalization
        self.realization_file = self.work_dir + '/{}'.format(self.basin) + '_realization_config_bmi_region.json'
        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + self.run_configs[0])

        # Set BMI config directories
        bmi_dir = {}
        for m1 in self.all_mod:
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
            bmi_dir[m1] = os.path.join(self.input_dir, m2 + '_input')
        rt_dict = {"routing": {"t_route_config_file_with_path": routing_config_file}}

        gfun.create_reg_realization_file(self.work_dir, self.lib_file, bmi_dir, self.forcing_path, self.realization_file,
                                         self.time_period, rt_dict, self.output_dict, self.cat_to_grp, self.grp_to_form, self.grp_params)

        # routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + self.run_configs[0])
        # bmi_dir = {}

        # # Skip modules that aren't calibrated
        # if m1 not in ['pet', 'troute', 'smp', 'sft', 'sloth']:
        #     # Retrieve group of each catchment
        #     cat_grp = [self.cat_to_grp[cat] for cat in cat_mod]
        #     # Retrieve parameter dictionary for module
        #     mod_params = self.grp_params[m1]

    def _write_partition(self):
        self.part_file = gfun.create_partition_file(self.parallelSec['partition_generator_exe'],
                                                    self.gpkg_file,
                                                    self.parallelSec['nprocs'],
                                                    self.work_dir,
                                                    self.basin) if self.parallelSec else None

    def _create_calib_model_dict(self):
        # Create calibration configuration file
        self.calib_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + '_config_calib.yaml')
        self.model_dict = {'type': 'ngen', 'binary': self.conf3['ngen_exe_file'], 'realization': self.realization_file,
                           'catchments': self.cat_file, 'nexus': self.nexus_file,
                           'crosswalk': self.walk_file, 'obsflow': self.obsflow_file, 'strategy': 'uniform', 'params': None,
                           'eval_params': {'objective': self.conf2['objective_function'],
                                           'evaluation_start': self.time_period['evaluation_time_period'][self.conf1['run_type']][0],
                                           'evaluation_stop': self.time_period['evaluation_time_period'][self.conf1['run_type']][1],
                                           'valid_start_time': self.time_period['run_time_period']['valid'][0],
                                           'valid_end_time': self.time_period['run_time_period']['valid'][1],
                                           'valid_eval_start_time': self.time_period['evaluation_time_period']['valid'][0],
                                           'valid_eval_end_time': self.time_period['evaluation_time_period']['valid'][1],
                                           'full_eval_start_time': self.time_period['evaluation_time_period']['full'][0],
                                           'full_eval_end_time': self.time_period['evaluation_time_period']['full'][1],
                                           'save_output_iteration': int(self.conf2['save_output_iter']),
                                           'save_plot_iteration': int(self.conf2['save_plot_iter']),
                                           'save_plot_iter_freq': int(self.conf2['save_plot_iter_freq']),
                                           'basinID': self.conf1['basin'],
                                           'threshold': float(self.conf2['streamflow_threshold']),
                                           'site_name': 'USGS ' + self.conf1['basin'] + ": " + self.conf2['station_name'],
                                           'user': self.conf2['user_email']},
                           }

        # update the model dict to enable parallel processing
        self.model_dict.update({'partitions': self.part_file}) if self.parallelSec else None
        self.model_dict.update({'parallel': int(self.parallelSec['nprocs'])}) if self.parallelSec else None
        self.model_dict.update({'binary': self.parallelSec['parallel_ngen_exe']}) if self.parallelSec else None

        # Set NWM retrospective
        if 'nwmretro_file' in self.conf3.keys():
            if self.conf3['nwmretro_file'] != '':
                self.model_dict['nwmflow'] = self.conf3['nwmretro_file']

    def _write_calib_configuration(self):
        # Create general config dictionary for output
        general_dict = self.general_cfg.copy()
        general_dict['workdir'] = self.work_dir
        general_dict['yaml_file'] = self.calib_config_file

        # items related to running from GUI
        for s1 in ['calibration_run_id', 'ngen_cerf', 'auth_token']:
            try:
                general_dict[s1] = self.conf1[s1]
            except KeyError as e:
                logger.error(f"Exception: Key not found: {str(e)}")
                return 1

        general_dict['calibration_run_id'] = int(general_dict['calibration_run_id'])
        general_dict['ngen_cerf'] = True if general_dict['ngen_cerf'].lower() == 'true' else False

        # Create calibration config file
        gfun.create_calib_config_file(self.conf3['calib_parameter_file'], self.modules, self.work_dir, general_dict, self.model_dict, self.calib_config_file)

    def build_calib_realization(self):
        """
        Replicate functionality of create_input.py, saving calibration realization file to output_path and formatting other input files
        Returns output path to realization and calib_config files
        """
        logger = logging.getLogger("create_calibration_input")
        logger.info("Building calibration realization from %s", self.input_path)

        self._load_config()
        self._parse_config()
        self._parse_time()
        self._parse_calib_settings()
        self._parse_modules()
        self._validate_processes()
        self._set_lib_paths()
        self._create_calib_input_dir()
        self._extract_hydrofabric()
        self._extract_forcing()
        self._extract_streamflow_obs()
        self._set_output_vars()
        self._create_bmi_configs()
        self._write_calib_realization()
        self._write_partition()
        self._create_calib_model_dict()
        self._write_calib_configuration()

        return self.realization_file, self.calib_config_file

    def build_region_realization(self):
        """
        Creating regionalization realization file from formulation_assignment file generated by regionalization
        """
        log_level_set()
        logger = logging.getLogger("create_region_input")
        logger.info("Building regionalization realization from %s", self.input_path)

        self._load_config()
        self._load_reg_formulation()
        self._load_reg_catchments()
        self._parse_config()
        self._parse_time()
        self._parse_reg_params()
        self._parse_reg_modules()
        self._validate_reg_processes()
        self._map_cat_to_grp()
        self._map_cat_to_form()
        self._map_mod_to_cat()
        self._set_lib_paths()
        self._create_reg_input_dir()
        self._extract_hydrofabric()
        self._extract_forcing()
        # Eliminated streamflow_obs file, not sure if needed for regionalization
        self._set_output_vars()
        self._create_reg_bmi_configs()
        self._write_region_realization()

    def build_fcst_realization(self):
        """
        Replicate functionality of ngen-fcst, creating realization file from validation yaml file and formatting other input files
        """
        log_level_set()
        logger = logging.getLogger("create_forecast_input")
        logger.info("Building forecast realization from %s", self.input_path)

        self._load_yaml()
        self._parse_yaml()
        self._load_realization()
        self._create_fcst_output_dir()
        self._update_fcst_realization()
        self._update_fcst_noah_ueb()
        self._update_fcst_troute()
        self._write_fcst_realization()

        return self.realization_file

    def run_testing(self):

        self._load_config()
        self._parse_config()
        self._parse_time()
        self._parse_cal_settings()
        self._parse_modules()
        self._validate_processes()
        self._set_lib_paths()
        self._create_calib_input_dir()
        self._extract_hydrofabric()
        self._extract_forcing()
        self._extract_streamflow_obs()
        self._set_output_vars()
        self._init_config_hooks()
        self._get_module_hooks()
        self._create_bmi_configs_ngen_config_gen()
        self._write_calib_realization()
        self._write_partition()
        self._create_calib_model_dict()
        self._write_calib_configuration()
