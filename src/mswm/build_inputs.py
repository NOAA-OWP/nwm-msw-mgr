"""
This module contains functions to manage the initial creation of configuration files

@author: Jeffrey Wade, Xia Feng
"""

from pathlib import Path
import os
import logging
import re
import math
from datetime import datetime
import geopandas as gpd
import pandas as pd
import json
from collections import defaultdict
from pydantic import ValidationError

from mswm.utils import ginputfunc as gfun
from mswm.utils import settings
from mswm.utils.log_level import log_level_set
from mswm.utils.process_forcing import update_forcing_in_realization
from mswm.utils.update_bmi_config import update_noah_ueb, update_troute
from mswm.utils.input_configuration import InputConfig


log_level_set()
logger = logging.getLogger(__name__)
if not logging.getLogger().hasHandlers():
    # When running outside of Django, configure basic logging to stderr
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )


class RealizationBuilder:
    def __init__(self, input_path: str, assign_path: str | None = None, forcing_path: str | None = None, output_folder: str | None = None):
        self.input_path = Path(input_path)
        self.assign_path = Path(assign_path) if assign_path else None
        self.forcing_path = Path(forcing_path) if forcing_path else None
        self.output_folder = output_folder if output_folder else None
        logger.info(f"Initialized RealizationBuilder with {input_path}")

    def _load_config(self):
        """
        Read input.config file
        """
        import configparser
        # Confirm input file exists
        self.input_path = Path(self.input_path).absolute()
        if not self.input_path.exists():
            try:
                raise FileNotFoundError(f'Input file not found: {self.input_path}')
            except FileNotFoundError as e:
                logger.critical(e)
                raise

        # Read input config file
        try:
            self.config = configparser.ConfigParser()
            self.config.read(self.input_path)
        except FileNotFoundError as e:
            logger.critical(f"Input file not found: {self.input_path}\n{e}")
            raise
        except configparser.Error as e:
            logger.critical(f"ConfigParser error reading config file: {self.input_path}\n{e}")
            raise
        except Exception as e:
            logger.critical(f"Unexpected error loading config: {self.input_path}\n{e}")
            raise

        logger.info(f"Input.config file loaded from {self.input_path}")

        # Raise error if config file is empty
        if not {section: dict(self.config[section]) for section in self.config.sections()}:
            try:
                raise ValueError(f'Input.config file is empty or contains no valid sections: {self.input_path}')
            except ValueError as e:
                logger.critical(e)
                raise

    def _validate_config(self):
        """
        Validate input.config file using Pydantic (input_configuration.py)
        """
        # Convert input.config to dictionary
        configs = {}
        for sec in self.config.sections():
            configs[sec] = {}
            for key, val in self.config.items(sec):
                # Strip trailing whitespaces from optional variables
                val_strip = val.strip()
                configs[sec][key] = val_strip if val_strip else None

        # Validate input.config structure and variables using Pydantic
        try:
            self.input_configs = InputConfig(**configs).dict()
        except ValidationError as e:
            logger.critical(f"Input.config Pydantic validation failed: {self.input_path}{e}")
            raise
        logger.info("Input.config validated successfully")

    def _load_yaml(self):
        """
        Read yaml-based configuration file from previous ngen-cal run
        """
        import yaml
        # Confirm config yaml file exists
        self.config_yaml = Path(self.input_path).absolute()
        if not self.config_yaml.exists():
            try:
                raise FileNotFoundError(f'Config file does not exist: {self.config_yaml}')
            except FileNotFoundError as e:
                logger.critical(e)
                raise

        # Read the yaml-based configuration file
        try:
            with open(self.config_yaml) as file:
                self.conf = yaml.safe_load(file)
        except FileNotFoundError as e:
            logger.critical(f'Config file does not exist: {self.config_yaml}\n{e}')
            raise
        except yaml.YAMLError as e:
            logger.critical(f"YAML parsing error in config file: {self.input_path}\n{e}")
            raise
        except Exception as e:
            logger.critical(f"Unexpected error loading config at: {self.input_path}\n{e}")
            raise

        logger.info(f"Configuration yaml file loaded:  {self.config_yaml}")

    def _load_reg_formulation(self):
        """
        Load regionalization formulation CSV file containing formulation groups and parameters
        """
        # Confirm regionalization formulation assignment file exists
        self.assign_file = Path(self.assign_path).absolute()
        if not self.assign_file.exists():
            try:
                raise FileNotFoundError(f'Regionalization formulation file does not exist: {self.assign_file}')
            except FileNotFoundError as e:
                logger.critical(e)
                raise

        # Load regionalization formulation assignment file
        self.reg_df = pd.read_csv(self.assign_file)

        # Check that formulation file is not empty
        if self.reg_df.empty:
            try:
                raise ValueError(f"Regionalization formulation file is empty: {self.assign_file}")
            except ValueError as e:
                logger.critical(e)
                raise

        # Check that formulation file is properly formatted
        req_columns = {'group', 'formulation'}
        if not req_columns.issubset(self.reg_df.columns):
            missing_cols = req_columns - set(self.reg_df.columns)
            try:
                raise ValueError(f"Regionalization formulation file is missing required columns: {missing_cols}")
            except ValueError as e:
                logger.critical(e)
                raise

        logger.info(f"Regionalization formulation file loaded: {self.assign_file}")

    def _load_reg_catchments(self):
        """"
        Load grouped catchment files produced by regionalization and store grouped catchment ids
        """
        # Relate formulation groups to catchment IDS
        self.grp_to_cat = {}
        self.grp_to_cat_path = {}
        for grp in self.reg_df['group']:
            cat_path = (Path(self.assign_path.parent) / (grp + "_catchments.csv")).absolute()
            self.grp_to_cat_path[grp] = str(cat_path)
            if not cat_path.exists():
                try:
                    raise FileNotFoundError(f'Regionalization catchment group file does not exist: {cat_path}')
                except FileNotFoundError as e:
                    logger.critical(e)
                    raise

            # Load catchment group file
            cat_df = pd.read_csv(cat_path)

            # Check that formulation file is properly formatted
            if "divide_id" not in cat_df.columns or cat_df["divide_id"].isnull().all():
                try:
                    raise ValueError(f"Regionalization catchment group file must contain `divide_id` column: {cat_path}")
                except ValueError as e:
                    logger.critical(e)
                    raise

            # Store group id and associated catchments
            self.grp_to_cat[grp] = cat_df.divide_id.tolist()

        logger.info(f"Regionalization catchment files loaded from: {self.assign_file}")

    def _parse_reg_params(self):
        """
        Extract regionalization parameters for each group and module
        """
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

        # Ensure that all calibratable parameters are columns in dataframe
        self.missing_cols = [col for cols in params_dict.values() for col in cols if col not in self.reg_df.columns]
        if self.missing_cols:
            try:
                raise ValueError(f"The following calibratable parameters are missing from the regionalization formulation file: {self.missing_cols}")
            except ValueError as e:
                logger.critical(e)
                raise

        # For each module, retrieve group and corresponding parameter values
        # Raise errors for non-numeric strings, leaving empty parameter values out of realization section
        self.grp_params = {}
        errors = []
        for mod, params in params_dict.items():
            self.grp_params[mod] = {}
            for _, row in self.reg_df.iterrows():
                group = row['group']
                param_values = {}
                for param in params:
                    value = row[param]
                    # If parameter is empty, leave out of parameter dictionary
                    if not math.isnan(value):
                        try:
                            param_values[param] = float(value)
                        except (ValueError, TypeError):
                            errors.append(f"Invalid parameter value in regionalization formulation file at: {mod}: {group}: {param}: {value}")
                self.grp_params[mod][group] = param_values

        # Log and raise errors for bad parameters
        if errors:
            for e in errors:
                logger.critical(e)
            err_message = "\n".join(errors)
            raise ValueError(f"Parameter valdiation failed:\n{err_message}")

        logger.info(f"Regionalization parameters loaded from: {self.assign_file}")

    def _parse_yaml(self):
        """
        Read realization file, hydrofabric gpkg and ngen executable paths from yaml file
        """
        # Set realization file path
        try:
            self.real_input_file = Path(self.conf['model']['realization']).absolute()

            # Get hydrofabric gpkg paths
            self.gpkg_cats = self.conf['model']['catchments']
            self.gpkg_nexus = self.conf['model']['nexus']

            # Get ngen executable path
            self.ngen_exe = self.conf['model']['binary']
        except Exception as e:
            logger.critical(f"Yaml config calib file is missing fields: {self.input_path}\n{e}")
            raise

        logger.info("Yaml file parsed")

    def _load_realization(self):
        """
        Load realization json file
        """
        # Confirm realization file exists
        if not self.real_input_file.exists():
            try:
                raise FileNotFoundError(f'Realization input file does not exist: {self.real_input_file}')
            except FileNotFoundError as e:
                logger.critical(e)
                raise

        # Read realization file
        try:
            with open(self.real_input_file) as fp:
                self.real_config = json.load(fp)
        except FileNotFoundError as e:
            logger.critical(f"Realization input file does not exist: {self.real_input_file}\n{e}")
            raise
        except json.JSONDecodeError as e:
            logger.critical(f"Error parsing json from realization file: {self.real_input_file}\n{e}")
            raise
        except Exception as e:
            logger.critical(f"Unexpected error reading realization file: {self.real_input_file}\n{e}")

    def _create_fcst_output_dir(self):
        """
        Create output directory for forecast run
        """
        # create output directory
        try:
            out_dir0 = Path(self.conf['general']['yaml_file']).parent.parent.resolve(strict=True)
        except KeyError as e:
            logger.critical(f"Yaml file path not found in config calib yaml file: {e}")
            raise
        except FileNotFoundError as e:
            logger.critical(f"Invalid yaml file path: {self.conf['general']['yaml_file']} - {e}")
            raise

        self.out_dir = Path(out_dir0, 'Forecast_Run', self.output_folder)

        try:
            self.out_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.critical(f"Invalid yaml file path: {self.out_dir} - {e}")
            raise

        logger.info(f'New run directory created at: {self.out_dir}')

    def _parse_config(self):
        """
        Parse sections from input.config file
        """
        # reassign config sections for convenience
        self.conf1 = self.input_configs['General']
        self.run_type = self.conf1.get("run_type")

        # Load run_type specific config section
        if self.run_type in ('calibration', 'regionalization'):
            self.conf2 = self.input_configs.get(self.run_type.capitalize())
        self.conf3 = self.input_configs['DataFile']

        # get the parallel section
        self.parallelSec = self.input_configs.get('Parallel')

        # Use parallel ngen only when the number of processors is greater than 1
        if not self.parallelSec or self.parallelSec.get("nprocs", 0) < 2:
            self.parallelSec = None

        # Parse attribute file
        self.attr_parquet = self.conf3['attributes_file']

        logger.info('Input.config sections parsed')

    def _parse_time(self):
        """
        Set run time variables for calibration, regionalization, and default runs
        """
        # Retrieve time period for calibration
        if self.run_type == 'calibration':
            self.time_period = {"run_time_period": {"calib": [self.conf2['calib_start_period'], self.conf2['calib_end_period']],
                                                    "valid": [self.conf2['valid_start_period'], self.conf2['valid_end_period']]},
                                "evaluation_time_period": {"calib": [self.conf2['calib_eval_start_period'], self.conf2['calib_eval_end_period']],
                                                           "valid": [self.conf2['valid_eval_start_period'], self.conf2['valid_eval_end_period']],
                                                           "full": [self.conf2['full_eval_start_period'], self.conf2['full_eval_end_period']]}}
        # Retrieve time period for regionalization
        elif self.run_type == 'regionalization':
            self.time_period = {"run_time_period": {"region": [self.conf1['start_period'], self.conf1['end_period']]}}
        # Retrieve time period for default
        elif self.run_type == 'default':
            self.time_period = {"run_time_period": {"default": [self.conf1['start_period'], self.conf1['end_period']]}}

        # Confirm times are properly formatted and in correct order
        errors = []
        for outer_key, run_dict in self.time_period.items():
            for run_type, times in run_dict.items():
                time_vals = []
                for i, time_str in enumerate(times):
                    try:
                        time_vals.append(datetime.strptime(time_str.strip(), "%Y-%m-%d %H:%M:%S"))
                    except ValueError:
                        errors.append(f"Invalid datetime format: {outer_key}: {run_type}: {time_str}")
                if time_vals[0] >= time_vals[1]:
                    errors.append(f"Start time must be before end time: {outer_key}: {run_type}: {time_vals[0]} >= {time_vals[1]}")

        # Raise time format errors
        if errors:
            for e in errors:
                logger.critical(e)
            err_message = "\n".join(errors)
            raise ValueError(f"Time period valdiation failed:\n{err_message}")

        logger.info('Run time period validated')

    def _parse_calib_settings(self):
        """
        Parse input.config settings for calibration run
        """

        # Retrieve general settings for calibration
        algorithm = (self.conf2.get('optimization_algorithm', "") or "none").lower()
        swarm_size = self.conf2['swarm_size']
        start_iteration = self.conf2.get('start_iteration') or 0
        number_iteration = self.conf2.get('number_iteration') or 0
        restart = self.conf2.get('restart') or 0

        strategy = {'type': 'estimation', 'algorithm': algorithm}
        if algorithm == 'pso':
            strategy.update({'parameters': {'pool': swarm_size, 'particles': swarm_size,
                             'options': {'c1': self.conf2['c1'], 'c2': self.conf2['c2'], 'w': self.conf2['w']}}})
        if algorithm == 'gwo':
            strategy.update({'parameters': {'pool': swarm_size, 'particles': swarm_size}})

        # Set general config
        self.general_cfg = {'strategy': strategy, 'name': 'calib', 'log': True, 'workdir': None, 'yaml_file': None,
                            'start_iteration': start_iteration, 'iterations': number_iteration,
                            'restart': restart}

        logger.info('Calibration settings parsed')

    def _parse_modules(self):
        """
        Read modules from input.config file and ensure formulation is valid
        """

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
            try:
                raise ValueError(f"Invalid module(s) found: {', '.join(invalid_modules)}. Please check your configuration.")
            except ValueError as e:
                logger.critical(e)
                raise

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

        # make sure SMP, SFT, SAC-SMA, and LASAM are not paired with PET, as PET does not provide the required inputs
        if any(m in self.modules for m in ('smp', 'sft', 'sac-sma', 'lasam')) and 'pet' in self.modules:
            try:
                raise ValueError("PET does not supply the required inputs for SMP, SFT, SAC-SMA, and LASAM. Add NOAH-OWP-Modular to formulation.")
            except ValueError as e:
                logger.critical(e)
                raise

        # rearrange modules in order of hydrologic processes
        self.modules = [m1 for m1 in settings.modules_all['module'] if m1 in self.modules]

        # Reorder "sft" and "smp"
        if "sft" in self.modules and "smp" in self.modules:
            smp_index = self.modules.index("smp")
            sft_index = self.modules.index("sft")
            if smp_index > sft_index:
                self.modules.remove("smp")
                self.modules.insert(sft_index, "smp")

        logger.info(f"Final list of modules in formulation: {self.modules}")

    def _parse_reg_modules(self):
        """
        Retrieve modules from regionalization formulation file and ensure formulation is valid
        This could potentially be combined with _parse_modules to not repeat code
        """
        logger.info(f"Available module names: {settings.modules_all['name_ui'].tolist()}")
        self.grp_to_form = {}

        for idx, row in self.reg_df.iterrows():
            modules0 = [x.replace(" ", "") for x in re.split(' ', row['formulation'])]
            modules = []
            invalid_modules = []

            # Ensure modules match possible options provided in settings
            for m1 in modules0:
                filtered = settings.modules_all.loc[settings.modules_all['name_ui'] == m1.lower(), 'module']

                # Add invalid modules to list
                if filtered.empty:
                    invalid_modules.append(m1)

                else:
                    modules.append(filtered.iloc[0])

            # Raise an error if any invalid modules were found
            if invalid_modules:
                try:
                    raise ValueError(f"Invalid module(s) found: {', '.join(invalid_modules)}. Please check your configuration.")
                except ValueError as e:
                    logger.critical(e)
                    raise

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

            # make sure SMP, SFT, SAC-SMA, and LASAM are not paired with PET, as PET does not provide the required inputs
            if any(m in modules for m in ('smp', 'sft', 'sac', 'lasam')) and 'pet' in modules:
                try:
                    raise ValueError("PET does not supply the required inputs for SMP, SFT, SAC-SMA, and LASAM. Add NOAH-OWP-Modular to formulation.")
                except ValueError as e:
                    logger.critical(e)
                    raise

            # rearrange modules in order of hydrologic processes
            modules = [m1 for m1 in settings.modules_all['module'] if m1 in modules]

            # Reorder "sft" and "smp"
            if "sft" in modules and "smp" in modules:
                smp_index = modules.index("smp")
                sft_index = modules.index("sft")
                if smp_index > sft_index:
                    modules.remove("smp")
                    modules.insert(sft_index, "smp")

            # Store with regionalization group id
            self.grp_to_form[row['group']] = modules

            logger.info(f"Final list of modules in formulation for {row['group']}: {modules}")

    def _validate_processes(self):
        """
        Check that formulation has all required hydrological processes
        """
        # check modules selected for each process
        procs = []
        for p1 in settings.modules_all['process']:
            procs = list(set(procs + p1))

        for p1 in procs:
            mods = [m1 for m1 in self.modules if p1 in settings.modules_all.loc[settings.modules_all['module'] == m1, 'process'].values[0]]

            # make sure only one module is selected for each process (except for Soil_moisture and Glacier_snow)
            if len(mods) > 1 and p1 not in ['Soil_moisture', 'Glacier_snow']:
                try:
                    raise Exception(f'Only one module can be selected for {p1} process')
                except Exception as e:
                    logger.critical(e)
                    raise

            # one and only one module must be selected for rainfall-runoff and PET
            if (p1 in ['Evapotranspiration', 'Rainfall_runoff']) and (len(mods) == 0):
                try:
                    raise Exception(f'At least one module must be selected for {p1} process')
                except Exception as e:
                    logger.critical(e)
                    raise

        logger.info("Module processes validated")

    def _validate_reg_processes(self):
        """
        Check that each formulation has all required hydrological processes
        Could be combined with _validate_processes
        """
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
                    try:
                        raise Exception(f'Only one module can be selected for {p1} process: {grp}')
                    except Exception as e:
                        logger.critical(e)
                        raise

                # one and only one module must be selected for rainfall-runoff and PET
                if (p1 in ['Evapotranspiration', 'Rainfall_runoff']) and (len(mods) == 0):
                    try:
                        raise Exception(f'At least one module must be selected for {p1} process: {grp}')
                    except Exception as e:
                        logger.critical(e)
                        raise

            logger.info(f"Module processes validated for {grp}")

    def _map_cat_to_grp(self):
        """
        Map catchments to regionalization groups
        """
        # Relate catchments and their groups
        self.cat_to_grp = {}
        for grp, cats in self.grp_to_cat.items():
            for cat in cats:
                self.cat_to_grp[cat] = grp

    def _map_cat_to_form(self):
        """
        Map catchments to formulations for regionalization
        """
        # Relate catchments and their formulations
        self.cat_to_form = {}
        for cat, grp in self.cat_to_grp.items():
            self.cat_to_form[cat] = self.grp_to_form[grp]

    def _map_mod_to_cat(self):
        """
        Map modules used in each catchment for regionalization
        """
        # Find the catchments that use each module
        self.mod_to_cat = defaultdict(list)
        for cat, modules in self.cat_to_form.items():
            for module in modules:
                self.mod_to_cat[module].append(cat)

    def _set_lib_paths(self):
        """
        Set library files for all modules included in the formulation
        """
        # Set library files
        self.lib_file = {}
        if self.run_type == 'regionalization':
            modules1 = list(set(m1 for form in self.grp_to_form.values() for m1 in form if m1 not in ['troute', 'lstm']))
            self.all_mod = modules1.copy()

            # Add LSTM to all_mod if it's used in a formulation
            if any('lstm' in form for form in self.grp_to_form.values()):
                self.all_mod.append('lstm')
        else:
            modules1 = [m1 for m1 in self.modules if m1 not in ['troute', 'lstm']]

        # Reformat library file paths to match input.config format
        for m1 in modules1:
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
            m2 = m2 if m2 not in ['cfe-s', 'cfe-x'] else 'cfe'
            self.lib_file[m1] = self.conf3[m2.replace("-", "_") + '_lib']

        # Confirm that library paths exist if not using server
        if not hasattr(self, 'conf2') or 'ngen_cerf' not in self.conf2 or self.conf2['ngen_cerf'] is False:
            errors = []
            for mod, lib_path in self.lib_file.items():
                if not Path(lib_path).is_file():
                    errors.append(f"Library file not found for {mod}: {lib_path}")

            # Raise errors
            if errors:
                for e in errors:
                    logger.critical(e)
                err_message = "\n".join(errors)
                raise ValueError(f"Library path valdiation failed:\n{err_message}")

        logger.info("Module libarary paths set")

    def _create_input_dir(self):
        """
        Create input directory to store realization file and BMI config files
        """
        # Set run directory based on run_type
        self.basin = self.conf1['basin']
        obj_fnc = self.conf2.get('objective_function') or "none"
        opt_alg = self.conf2.get('optimization_algorithm') or "none"
        if self.run_type == 'calibration':
            run_dir = os.path.join(self.conf1['main_dir'], '_'.join([obj_fnc, opt_alg]))
        elif self.run_type == 'regionalization':
            run_dir = os.path.join(self.conf1['main_dir'], 'regionalization')
        elif self.run_type == 'default':
            run_dir = os.path.join(self.conf1['main_dir'], 'default')

        # Form input directory paths
        self.work_dir = os.path.join(run_dir, self.conf1['formulation'] + '/' + self.basin)
        self.input_dir = os.path.join(self.work_dir, 'Input/')

        # Create directory
        try:
            os.makedirs(self.input_dir, exist_ok=True)
        except Exception as e:
            logger.critical(f"Invalid input directory: {e}. Check `main_dir` variable")
            raise

        logger.info(f"Input directory created at: {self.input_dir}")

    def _extract_hydrofabric(self):
        """
        Extract hydrofabric geopackage and form catchment, nexus, and crosswalk files
        """
        # Extract hydrofabric files
        self.gpkg_file = self.conf3['hydrofab_file']
        if not os.path.exists(self.gpkg_file):
            try:
                raise Exception(f'Geo package file does not exist: {self.gpkg_file}')
            except Exception as e:
                logger.critical(e)
                raise

        # Set cat, nexus, and walk files
        self.cat_file = os.path.join(self.input_dir, os.path.basename(self.gpkg_file))
        self.nexus_file = os.path.join(self.input_dir, os.path.basename(self.gpkg_file))
        self.walk_file = self.input_dir + '{}'.format(self.basin) + '_crosswalk.json'

        # Symlink gpkg_file to Input directory
        if not os.path.exists(self.cat_file):
            os.symlink(self.gpkg_file, self.cat_file)
            logger.info(f'Symlink created from {self.gpkg_file} to {self.cat_file}')

        # Create crosswalk file between catchments and gages for calibration run
        if self.run_type == 'calibration':
            gfun.create_walk_file(self.basin, self.gpkg_file, self.walk_file)
            logger.info(f"Crosswalk file created at: {self.walk_file}")

        # Read catchment parameter values from geopackage divide-attributes
        try:
            attr_input = gpd.read_file(self.gpkg_file, layer='divide-attributes')
            attr_input.set_index("divide_id", inplace=True)
        except Exception as e:
            logger.critical(f"Error while reading geopackage file: {e}")
            raise

        # Adapt to x,y column name in geopackage
        x_cols = ["centroid_x", "X"]
        y_cols = ["centroid_y", "Y"]
        x_col = next((c for c in x_cols if c in attr_input.columns), None)
        y_col = next((c for c in y_cols if c in attr_input.columns), None)
        self.xy_col = [x_col, y_col]

        if not x_col or y_col:
            try:
                Exception("Could not find coordinate columns in geopackage `divide-attributes`")
            except Exception as e:
                logger.critical(f"Error while reading geopackage file: {e}")
                raise

        # Reproject to WGS84 for X,Y coordinates
        self.attr_file = gpd.GeoDataFrame(attr_input,
                                          geometry=gpd.points_from_xy(attr_input[x_col], attr_input[y_col]),
                                          crs="EPSG:5070")
        self.attr_file = self.attr_file.to_crs("EPSG:4326")

        logger.info(f"Attribute file loaded from: {self.gpkg_file}")

    def _extract_forcing(self):
        """
        Extract forcing files and symlink to input directory
        """
        # Create forcing directory
        missing_catchment_files = []
        self.forcing_path = os.path.join(self.input_dir, 'forcing')
        try:
            os.makedirs(self.forcing_path, exist_ok=True)
        except Exception as e:
            logger.critical(f"Invalid forcing directory: {e}. Check `main_dir` variable")
            raise

        # Read catchment ids from geopackage
        try:
            self.catids = gpd.read_file(self.gpkg_file, layer='divides')['divide_id'].tolist()
        except Exception as e:
            logger.critical(f"Error while reading geopackage file: {e}")
            raise

        # Symlink forcing files
        for catID in self.catids:
            ffile = os.path.join(self.conf3['forcing_dir'], catID + '.csv')
            # Make sure we have the file
            if not os.path.exists(ffile):
                logger.info(f'Forcing file {ffile} does not exist')
                missing_catchment_files.append(ffile)
            else:
                target = os.path.join(self.forcing_path, os.path.basename(ffile))
                if not os.path.exists(target):
                    os.symlink(ffile, target)
        if missing_catchment_files:
            try:
                raise Exception(f"Missing catchment files in forcing data: {self.conf3['forcing_dir']}")
            except Exception as e:
                logger.critical(e)
                raise

        logger.info(f"Extracted forcing data from: {self.conf3['forcing_dir']}")

    def _extract_streamflow_obs(self):
        """
        Extract streamflow gage observations if provided
        """
        # Extract streamflow observation
        if 'obs_dir' in self.conf3.keys() and self.conf3['obs_dir'] is not None:
            try:
                obs = pd.read_csv(os.path.join(self.conf3['obs_dir'], self.basin + '_hourly_discharge.csv'))[['dateTime', 'q_cms']]
                obs = obs.rename(columns={'dateTime': 'value_date', 'q_cms': 'obs_flow'})
            except Exception as e:
                logger.critical(f"Failed to read streamflow observations: {e}")
                raise

            self.obsflow_file = self.input_dir + '{}'.format(self.basin) + '_hourly_discharge.csv'

            try:
                obs.to_csv(self.obsflow_file, index=False)
            except Exception as e:
                logger.critical(f"Failed to write hourly discharge to: {self.obsflow_file} - {e}")
                raise

            logger.info(f"Extracted streamflow observations from: {self.conf3['obs_dir']}")
        else:
            self.obsflow_file = None

    def _set_output_vars(self):
        """
        Set SWE and Soil Moisture output variables
        """
        # whether to output SWE or soil moisture (default to False)
        self.output_dict = dict()
        for s1 in ['output_swe', 'output_sm']:
            if (s1 not in self.conf1.keys()) or (self.conf1[s1] is None) or (self.conf1[s1] == ''):
                self.output_dict[s1] = False
            else:
                self.output_dict[s1] = self.conf1[s1]

        # define depth (in meters) for output soil moisture
        self.output_dict['sm_frac_depth'] = 0.4
        self.output_dict['sm_profile_depth'] = 0.1
        for s1 in ['sm_profile_depth', 'sm_frac_depth']:
            if (self.conf1[s1] is not None) and (self.conf1[s1] != ''):
                self.output_dict[s1] = float(self.conf1[s1])

        logger.info("Set SWE and SM output variables")

    def _update_fcst_realization(self):
        """
        Update forcing and time related info in realization file
        """
        self.real_config = update_forcing_in_realization(Path(self.forcing_path), self.real_config, self.gpkg_cats)
        logger.info("Updated forecast realization file")

    def _update_fcst_noah_ueb(self):
        """
        For UEB and Noah-OWP-Modular, create new BMI config files with new time info, and
        update path to BMI configs in realization file accordingly
        """
        self.real_config = update_noah_ueb(self.real_config, self.out_dir)
        logger.info("Updated noah and ueb config files for forecast if used")

    def _update_fcst_troute(self):
        """
        Update BMI config files for t-route for forecast period
        """
        self.real_config = update_troute(self.real_config, self.out_dir)
        logger.info("Updated noah and ueb config files for forecast")

    def _create_bmi_configs(self):
        """
        Generate BMI config files for modules or link to existing config files
        """
        # always create CFE inputs first since sft/smp need data from CFE inputs if they are selected
        if self.run_type == 'calibration':
            self.run_configs = ['_troute_config_calib.yaml', '_troute_config_valid_control.yaml', '_troute_config_valid_best.yaml']
        elif self.run_type == 'default':
            self.run_configs = ['_troute_config_default.yaml']

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
            bmi_dir = self.conf3.get(m2.replace('-', '_') + '_bmi_dir')

            # Skip config generation for sloth
            if m1 in ['sloth']:
                pass

            # Raise error if bmi_dir is invalid path and not empty
            if bmi_dir is not None and not os.path.isdir(bmi_dir):
                try:
                    raise Exception(f"Invalid BMI directory: {m2.replace('-', '_') + '_bmi_dir'}: `{bmi_dir}`")
                except Exception as e:
                    logger.critical(e)
                    raise

            # Modify existing BMI config files if filepaths provided (ignoring troute for now)
            elif m1 != 'troute' and bmi_dir and os.path.isdir(bmi_dir):

                if not os.listdir(bmi_dir):
                    try:
                        raise ValueError(f'BMI folder {bmi_dir} cannot be empty')
                    except Exception as e:
                        logger.critical(e)
                        raise
                else:

                    # Modify existing BMI config files from EDFS or the user with correct time period and/or paths
                    if m1 == 'noah':
                        gfun.create_noah_input_template(self.catids, self.time_period, self.conf3[m1 + '_parameter_dir'], mod_input_dir, bmi_dir, self.run_type)
                    elif m1 == 'topmodel':
                        gfun.change_topmodel_input(self.catids, bmi_dir, mod_input_dir)
                    elif m1 == 'ueb':
                        gfun.create_ueb_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, bmi_dir, self.run_type)
                    elif m1 in ['sac', 'snow17']:
                        gfun.change_sac_snow17_input(m1, self.catids, mod_input_dir, bmi_dir)
                    elif m1 == 'lasam':
                        gfun.change_lasam_input(self.catids, mod_input_dir, bmi_dir, self.conf3['lasam_parameter_dir'])
                    elif m1 == 'lstm':
                        gfun.change_lstm_input(self.catids, self.conf3['lstm_parameter_dir'], mod_input_dir, bmi_dir)
                    elif m1 == 'smp' and self.output_dict['output_sm']:
                        # For SMP, the depth to output soil moisture may need to be adjusted
                        self.output_dict['sm_profile_depth'] = gfun.change_smp_input(self.catids, self.modules, mod_input_dir, bmi_dir, self.run_type, self.output_dict['sm_frac_depth'],
                                                                                     self.output_dict['sm_profile_depth'])
                    elif m1 == 'sft':
                        # Modify SFT inputs to ensure ice_fraction_scheme matches rainfall_runoff model
                        gfun.change_sft_input(self.catids, modules1, mod_input_dir, bmi_dir, self.run_type)
                    else:
                        # Create symbolic link
                        logger.info(f'{m2}: create symlink from {bmi_dir} to {mod_input_dir}')
                        os.symlink(bmi_dir, mod_input_dir, target_is_directory=True)

            else:
                # Create BMI config files from scratch if paths not provided
                if m1 in ['cfes', 'cfex']:
                    gfun.create_cfe_input(self.catids, self.modules, self.attr_file, mod_input_dir, self.run_type)
                elif m1 == 'topmodel':
                    gfun.create_topmodel_input(self.catids, self.attr_file, self.gpkg_file, mod_input_dir)
                elif m1 == 'ueb':
                    gfun.create_ueb_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, '', self.run_type)
                elif m1 == 'snow17':
                    gfun.create_snow17_input(self.catids, self.attr_file, self.gpkg_file, self.conf3[m2.replace("-", "_") + '_parameter_dir'], mod_input_dir)
                elif m1 == "pet":
                    gfun.create_pet_input(self.catids, self.attr_file, mod_input_dir)
                elif m1 == "sac":
                    gfun.create_sac_input(self.catids, self.gpkg_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir)
                elif m1 == 'noah':
                    gfun.create_noah_input(self.catids, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.run_type)
                elif m1 == 'lstm':
                    gfun.create_lstm_input(self.catids, self.attr_file, self.gpkg_file, self.conf3['lstm_parameter_dir'], mod_input_dir, self.xy_col)
                elif m1 == 'sft':
                    sft_dir = os.path.join(self.input_dir, 'sft_input')
                    smp_dir = os.path.join(self.input_dir, 'smp_input')

                    # Update CFE bmi dir with correct scheme (Schaake/Xinanjiang)
                    if ('cfes' in self.modules):
                        # If bmi_dir not provided by input file, create from input dir
                        if self.conf3['cfe_s_bmi_dir'] is None:
                            cfe_dir = os.path.join(self.input_dir, 'cfe-s_input')
                        # If bmi_dir provided by input file, use that path
                        else:
                            cfe_dir = self.conf3['cfe_s_bmi_dir']
                    elif ('cfex' in self.modules):
                        # If bmi_dir not provided by input file, create from input dir
                        if self.conf3['cfe_x_bmi_dir'] is None:
                            cfe_dir = os.path.join(self.input_dir, 'cfe-x_input')
                        # If bmi_dir provided by input file, use that path
                        else:
                            cfe_dir = self.conf3['cfe_x_bmi_dir']
                    else:
                        # If CFE BMI config files not provided and cfe not in modules, create cfe input files
                        cfe_dir = os.path.join(self.input_dir, 'cfe-s_input')
                        gfun.create_cfe_input(self.catids, ['cfes'] + [self.modules], self.attr_file, cfe_dir, self.run_type)

                    # Create sft input
                    gfun.create_sft_smp_input(self.catids, self.modules, self.attr_parquet, cfe_dir, self.conf3['forcing_dir'], sft_dir, smp_dir, self.run_type)

                elif m1 == 'smp':
                    continue
                elif m1 == 'lasam':
                    gfun.create_lasam_input(self.catids, self.modules, self.attr_file, mod_input_dir, self.conf3['lasam_parameter_dir'], self.run_type)

                elif m1 == 'troute':
                    if self.run_type == 'calibration':
                        run_names = ['calib', 'valid', 'valid']
                    elif self.run_type == 'default':
                        run_names = ['default']

                    for file_name, run_name in zip(self.run_configs, run_names):
                        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + file_name)
                        run_name1 = file_name.replace('_troute_config_', '').replace('.yaml', '')
                        if len(self.time_period['run_time_period'][run_name][0]) != 0 & len(self.time_period['run_time_period'][run_name][0]):
                            run_range = pd.to_datetime(self.time_period['run_time_period'][run_name])
                            nts = len(pd.date_range(start=run_range[0], end=run_range[1], freq='5min')) - 1
                            gfun.create_troute_config(self.gpkg_file, routing_config_file, self.time_period['run_time_period'][run_name][0], nts)
                            logger.info(f'troute config file for {run_name1} is created at: {routing_config_file}')

                if m1 != 'troute':
                    logger.info(f'{m1}: input config files created at: {mod_input_dir}')

        logger.info("Created BMI config files for all modules in the formulation")

    def _create_reg_bmi_configs(self):
        """
        Generate BMI config files for modules for regionalization
        """

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
            bmi_dir = self.conf3.get(m2.replace('-', '_') + '_bmi_dir')

            # Retrieve catchments that use each module
            cat_mod = self.mod_to_cat[m1]

            # If module requires full formulation, retrieve formulation for each catchment
            if m1 in ['cfes', 'cfex', 'sft', 'lasam']:
                form_cat = [self.cat_to_form[cat] for cat in cat_mod]

            # Modify existing BMI config files if filepaths provided (ignoring troute for now)
            # Skip config generation for sloth
            if m1 in ['sloth']:
                pass

            # Raise error if bmi_dir is invalid path and not empty
            if bmi_dir is not None and not os.path.isdir(bmi_dir):
                try:
                    raise Exception(f"Invalid BMI directory: {m2.replace('-', '_') + '_bmi_dir'}: `{bmi_dir}`")
                except Exception as e:
                    logger.critical(e)
                    raise

            elif m1 != 'troute' and bmi_dir and os.path.isdir(bmi_dir):

                if not os.listdir(bmi_dir):
                    try:
                        raise ValueError(f'BMI folder {bmi_dir} cannot be empty')
                    except Exception as e:
                        logger.critical(e)
                        raise
                else:

                    # Modify existing BMI config files from EDFS or the user with correct time period and/or paths
                    if m1 == 'noah':
                        gfun.create_noah_input_template(cat_mod, self.time_period, self.conf3[m1 + '_parameter_dir'], mod_input_dir, bmi_dir, self.run_type)
                    elif m1 == 'topmodel':
                        gfun.change_topmodel_input(cat_mod, bmi_dir, mod_input_dir)
                    elif m1 == 'ueb':
                        gfun.create_ueb_input(cat_mod, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, bmi_dir, self.run_type)
                    elif m1 in ['sac', 'snow17']:
                        gfun.change_sac_snow17_input(m1, cat_mod, mod_input_dir, bmi_dir)
                    elif m1 == 'lasam':
                        gfun.change_lasam_input(cat_mod, mod_input_dir, bmi_dir, self.conf3['lasam_parameter_dir'])
                    elif m1 == 'lstm':
                        gfun.change_lstm_input(cat_mod, self.conf3['lstm_parameter_dir'], mod_input_dir, bmi_dir)
                    elif m1 == "smp" and self.output_dict['output_sm']:
                        # For SMP, the depth to output soil moisture may need to be adjusted
                        self.output_dict['sm_profile_depth'] = gfun.change_smp_input(cat_mod, form_cat, mod_input_dir, bmi_dir, self.run_type,
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
                                    try:
                                        raise Exception('SMP/SFT only implemented when CFE-S, CFE-X, or LASAM are selected')
                                    except Exception as e:
                                        logger.critical(e)
                                        raise

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
                # Create BMI config files from scratch if paths not provided
                if m1 in ['cfes', 'cfex']:
                    gfun.create_cfe_input(cat_mod, form_cat, self.attr_file, mod_input_dir, self.run_type)
                elif m1 == 'topmodel':
                    gfun.create_topmodel_input(cat_mod, self.attr_file, self.gpkg_file, mod_input_dir)
                elif m1 == 'ueb':
                    gfun.create_ueb_input(cat_mod, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, '', self.run_type)
                elif m1 == 'snow17':
                    gfun.create_snow17_input(cat_mod, self.attr_file, self.gpkg_file, self.conf3[m2.replace("-", "_") + '_parameter_dir'], mod_input_dir)
                elif m1 == "pet":
                    gfun.create_pet_input(cat_mod, self.attr_file, mod_input_dir)
                elif m1 == "sac":
                    gfun.create_sac_input(cat_mod, self.gpkg_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir)
                elif m1 == 'noah':
                    gfun.create_noah_input(cat_mod, self.time_period, self.attr_file, self.conf3[m1 + '_parameter_dir'], mod_input_dir, self.run_type)
                elif m1 == 'lstm':
                    gfun.create_lstm_input(cat_mod, self.attr_file, self.gpkg_file, self.conf3['lstm_parameter_dir'], mod_input_dir, self.xy_col)
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
                                try:
                                    raise Exception('SMP/SFT only implemented when CFE-S, CFE-X, or LASAM are selected')
                                except Exception as e:
                                    logger.critical(e)
                                    raise

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
                            gfun.create_sft_smp_input(scheme_cat, scheme_form, self.attr_parquet, cfe_dir, self.conf3['forcing_dir'], sft_dir, smp_dir, self.run_type)

                # Skip smp, inputs created in tandem with sft
                elif m1 == 'smp':
                    continue
                elif m1 == 'lasam':
                    gfun.create_lasam_input(cat_mod, form_cat, self.attr_file, mod_input_dir, self.conf3['lasam_parameter_dir'], self.run_type)

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

        logger.info("Created BMI config files for all modules in each regionalization formulation")

    def _write_realization(self):
        """
        Write realization file for calibration and default runs
        """
        # Set file suffix
        if self.run_type == 'calibration':
            file_suffix = 'calib'
        else:
            file_suffix = self.run_type

        # Set BMI config directories
        self.realization_file = self.work_dir + '/{}'.format(self.basin) + '_realization_config_bmi_' + file_suffix + '.json'
        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + self.run_configs[0])
        bmi_dir = {}
        for m1 in self.modules:
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
            bmi_dir[m1] = os.path.join(self.input_dir, m2 + '_input')
        rt_dict = {"routing": {"t_route_config_file_with_path": routing_config_file}}

        # Write realization file
        gfun.create_realization_file(self.work_dir, self.lib_file, bmi_dir, self.forcing_path, self.realization_file,
                                     self.modules, self.time_period, rt_dict, self.output_dict, self.run_type)

    def _write_region_realization(self):
        """
        Write realization file for regionalization runs
        """
        # Create model realization file for regionalization
        self.realization_file = self.work_dir + '/{}'.format(self.basin) + '_realization_config_bmi_region.json'
        routing_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + self.run_configs[0])

        # Set BMI config directories
        bmi_dir = {}
        for m1 in self.all_mod:
            m2 = settings.modules_all.loc[settings.modules_all['module'] == m1, 'name_ui'].iloc[0]
            bmi_dir[m1] = os.path.join(self.input_dir, m2 + '_input')
        rt_dict = {"routing": {"t_route_config_file_with_path": routing_config_file}}

        # Write realization file
        gfun.create_reg_realization_file(self.work_dir, self.lib_file, bmi_dir, self.forcing_path, self.realization_file,
                                         self.time_period, rt_dict, self.output_dict, self.grp_to_cat_path, self.grp_to_form, self.grp_params)

    def _write_fcst_realization(self):
        """
        Write updated forecast realization file
        """
        # save the new realization file
        self.realization_file = Path(self.out_dir, os.path.basename(self.real_input_file))
        try:
            with open(self.realization_file, 'w') as outfile:
                json.dump(self.real_config, outfile, indent=4, separators=(", ", ": "), sort_keys=False)
        except TypeError as e:
            logger.critical(f"Failed to dump realization data to JSON: {self.realization_file}\n{e}")
            raise
        except OSError as e:
            logger.critical(f"Unexpected error while writing realization data to JSON: {self.realization_file}\n{e}")
            raise

        logger.info(f"Realization file is created at: {self.realization_file}")

    def _write_partition(self):
        """
        Write parallel processing partition file
        """
        self.part_file = gfun.create_partition_file(self.parallelSec['partition_generator_exe'],
                                                    self.gpkg_file,
                                                    self.parallelSec['nprocs'],
                                                    self.work_dir,
                                                    self.basin) if self.parallelSec else None

    def _create_calib_model_dict(self):
        """
        Create calibration model dictionary used to create config yaml file
        """
        # Set site name
        site_name = (f"USGS {self.conf1['basin']}" + (f": {self.conf2['station_name']}" if self.conf2.get('station_name') else ""))
        objective_function = self.conf2.get('objective_function') or "none"
        save_output_iter = self.conf2.get('save_output_iter') or 0
        save_plot_iter = self.conf2.get('save_plot_iter') or 0
        save_plot_iter_freq = self.conf2.get('save_plot_iter_freq') or 0
        streamflow_threshold = self.conf2.get('streamflow_threshold') or 0.0
        user_email = self.conf2.get('user_email') or ''

        # Create calibration configuration file
        self.calib_config_file = os.path.join(self.work_dir + '/Input', '{}'.format(self.basin) + '_config_calib.yaml')
        self.model_dict = {'type': 'ngen', 'binary': self.conf3['ngen_exe_file'], 'realization': self.realization_file,
                           'catchments': self.cat_file, 'nexus': self.nexus_file,
                           'crosswalk': self.walk_file, 'obsflow': self.obsflow_file, 'strategy': 'uniform', 'params': None,
                           'eval_params': {'objective': objective_function,
                                           'evaluation_start': self.time_period['evaluation_time_period']['calib'][0],
                                           'evaluation_stop': self.time_period['evaluation_time_period']['calib'][1],
                                           'valid_start_time': self.time_period['run_time_period']['valid'][0],
                                           'valid_end_time': self.time_period['run_time_period']['valid'][1],
                                           'valid_eval_start_time': self.time_period['evaluation_time_period']['valid'][0],
                                           'valid_eval_end_time': self.time_period['evaluation_time_period']['valid'][1],
                                           'full_eval_start_time': self.time_period['evaluation_time_period']['full'][0],
                                           'full_eval_end_time': self.time_period['evaluation_time_period']['full'][1],
                                           'save_output_iteration': save_output_iter,
                                           'save_plot_iteration': save_plot_iter,
                                           'save_plot_iter_freq': save_plot_iter_freq,
                                           'basinID': self.conf1['basin'],
                                           'threshold': streamflow_threshold,
                                           'site_name': site_name,
                                           'user': user_email},
                           }

        # update the model dict to enable parallel processing
        self.model_dict.update({'partitions': self.part_file}) if self.parallelSec else None
        self.model_dict.update({'parallel': int(self.parallelSec['nprocs'])}) if self.parallelSec else None
        self.model_dict.update({'binary': self.parallelSec['parallel_ngen_exe']}) if self.parallelSec else None

        # Set NWM retrospective
        if 'nwmretro_file' in self.conf3.keys():
            if self.conf3['nwmretro_file'] is not None:
                self.model_dict['nwmflow'] = self.conf3['nwmretro_file']

        logger.info("Formatted calibration configuration settings for output")

    def _write_calib_configuration(self):
        """
        Create calibration configuration yaml file
        """
        # Create general config dictionary for output
        general_dict = self.general_cfg.copy()
        general_dict['workdir'] = self.work_dir
        general_dict['yaml_file'] = self.calib_config_file

        # items related to running from GUI
        for s1 in ['calibration_run_id', 'ngen_cerf', 'auth_token']:
            general_dict[s1] = self.conf2[s1]

        # Create calibration config file
        gfun.create_calib_config_file(self.conf2['calib_parameter_file'], self.modules, self.work_dir, general_dict, self.model_dict, self.calib_config_file)

    def build_calib_realization(self):
        """
        Replicate functionality of create_input.py, saving calibration realization file to output_path and formatting other input files
        Returns output path to realization and calib_config files
        """
        logger.info("Building calibration realization from %s", self.input_path)

        self._load_config()
        self._validate_config()
        self._parse_config()

        if self.run_type != 'calibration':
            try:
                raise ValueError(f"Unexpected run_type {self.run_type} for build_calib_realization. Must be `calibration`.")
            except ValueError as e:
                logging.critical(e)
                raise

        self._parse_time()
        self._parse_calib_settings()
        self._parse_modules()
        self._validate_processes()
        self._set_lib_paths()
        self._create_input_dir()
        self._extract_hydrofabric()
        self._extract_forcing()
        self._extract_streamflow_obs()
        self._set_output_vars()
        self._create_bmi_configs()
        self._write_realization()
        self._write_partition()
        self._create_calib_model_dict()
        self._write_calib_configuration()

        logger.info("Calibration run set up successfully")

    def build_region_realization(self):
        """
        Creating regionalization realization file from formulation_assignment file generated by regionalization
        """
        logger.info("Building regionalization realization from %s", self.input_path)

        self._load_config()
        self._validate_config()
        self._parse_config()

        if self.run_type != 'regionalization':
            try:
                raise ValueError(f"Unexpected run_type {self.run_type} for build_region_realization. Must be `regionalization`.")
            except ValueError as e:
                logging.critical(e)
                raise

        self._load_reg_formulation()
        self._load_reg_catchments()
        self._parse_time()
        self._parse_reg_params()
        self._parse_reg_modules()
        self._validate_reg_processes()
        self._map_cat_to_grp()
        self._map_cat_to_form()
        self._map_mod_to_cat()
        self._set_lib_paths()
        self._create_input_dir()
        self._extract_hydrofabric()
        self._extract_forcing()
        self._set_output_vars()
        self._create_reg_bmi_configs()
        self._write_region_realization()

        logger.info("Regionalization run set up successfully")

    def build_fcst_realization(self):
        """
        Replicate functionality of ngen-fcst, creating realization file from validation yaml file and formatting other input files
        """
        logger.info("Building forecast realization from %s", self.input_path)

        self._load_yaml()
        self._parse_yaml()
        self._load_realization()
        self._create_fcst_output_dir()
        self._update_fcst_realization()
        self._update_fcst_noah_ueb()
        self._update_fcst_troute()
        self._write_fcst_realization()

        logger.info("Forecast run set up successfully")

    def build_default_realization(self):
        """
        Create realization and BMI config files using default parameter values for each catchment
        """

        logger.info("Building default realization from %s", self.input_path)

        self._load_config()
        self._validate_config()
        self._parse_config()

        if self.run_type != 'default':
            try:
                raise ValueError(f"Unexpected run_type {self.run_type} for build_default_realization. Must be `default`.")
            except ValueError as e:
                logging.critical(e)
                raise

        self._parse_time()
        self._parse_modules()
        self._validate_processes()
        self._set_lib_paths()
        self._create_input_dir()
        self._extract_hydrofabric()
        self._extract_forcing()
        self._set_output_vars()
        self._create_bmi_configs()
        self._write_realization()

        logger.info("Default run set up successfully")
