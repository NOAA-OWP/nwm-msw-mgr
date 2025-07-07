# mswm



## Name
mswm - Model Setup Workflow Manager

## Description
The Model Setup Workflow Manager generates realization and configuration filesfor running ngen in calibration, validation, and forecast modes. mswm can either be run from the command line or called directly by ngen model runs.

## Installation

### Clone mswm

cd [NGEN_REG_ROOT]
git clone -b development --recurse-submodules https://gitlab.sh.nextgenwaterprediction.com/NGWPC/nwm-ngen/mswm.git

### Build the environment

To run the program, one would need an environment for successfully running ngen.

If you already have an environment for running ngen, you can use the same venv.

Once the venv is activated, run:

1) cd mswm
2) pip install .

## Usage

The two primary functionalities of mswm, generating realization files to run ngen (`build\_inputs.py`) and editing validation realization files during ngen-cal iterations (`edit\_configs.py`), are orchestrated by `manager.py`. Users will most directly use mswm from the command line to create realization files for calibration and forecasting.

To generate the model realization file and config_calib yaml file for a calibration run of ngen:

1. source [VENV_ROOT]/env.ngen/bin/activate
2. python -m mswm.manager build_calib ~/ngwpc/run_ngen/input.config
3. python calibration.py ~/ngwpc/run_ngen/kge_DDS/noah_cfes/01123000/Input/01123000_config_calib.yaml

The mswm.manager script in calibration mode takes two command line arguments:
1) Command for calibration mode (build_calib)
2) Path to the user generated input.config file

To generate the model realization file for a forecast run on ngen:

1. source [VENV_ROOT]/env.ngen/bin/activate
2. python -m mswm.manager build_fcst ~/ngwpc/run_ngen/kge_DDS/noah_cfes/01123000/Output/Validation_Run/01123000_config_valid_best.yaml ~/ngwpc/ngen-fcst/test_data/forcing.nc fcst_run1
3. python ~/ngwpc/ngen-fcst/python/run_ngen_fcst.py ~/ngwpc/ngen-fcst/test_data/forcing.nc ~/ngwpc/run_ngen/kge_DDS/noah_cfes/01123000/Output/Validation_Run/01123000_config_valid_best.yaml fcst_run1

The mswm.manager script in calibration mode takes two command line arguments:
1) Command for forecast mode (build_fcst)
2) Path to the config validation yaml file from a prior run of ngen-cal
3) Path to the NetCDF forcing file or folder containing csv forcing files for all catchments in the basin
4) Relative path to the folder to be created for storing inputs/outputs from running ngen, relative to the Output directory of the calibration run as indicated in the config yaml file. For example, if "fcst_run1" is the 4th argument, and "yaml_file" in the "general" section of the config file is '~/ngwpc/run_ngen/kge_DDS/noah_cfes/01123000/Output/Validation_Run/01123000_config_valid_best.yaml', then the new output directory to be created for the ngen-fcst run would be '~/ngwpc/run_ngen/kge_DDS/noah_cfes/01123000/Output/Forecast_Run/fcst_run1'

The forecast realization file can also be used to run ngen directly from the command line:

1. ~/ngwpc/ngen/cmake_build/ngen ~/s3/ngwpc-hydrofabric/2.2/CONUS/01123000/GEOPACKAGE/USGS/2025_Mar_14_21_14_37/gauge_01123000.gpkg 'all' ~/s3/ngwpc-hydrofabric/2.2/CONUS/01123000/GEOPACKAGE/USGS/2025_Mar_14_21_14_37/gauge_01123000.gpkg 'all' ~/ngwpc/run_ngen/kge_DDS/noah_cfes/01123000/Output/Forecast_Run/fcst_run1/01123000_realization_config_bmi_valid_best.json


## Docker container

### Requirements


### Build


### Running



## Contributing
State if you are open to contributions and what your requirements are for accepting them.

For people who want to make changes to your project, it's helpful to have some documentation on how to get started. Perhaps there is a script that they should run or some environment variables that they need to set. Make these steps explicit. These instructions could also be useful to your future self.

You can also document commands to lint the code or run tests. These steps help to ensure high code quality and reduce the likelihood that the changes inadvertently break something. Having instructions for running tests is especially helpful if it requires external setup, such as starting a Selenium server for testing in a browser.

## Authors and acknowledgment
Show your appreciation to those who have contributed to the project.

## License
For open source projects, say how it is licensed.

## Project status
If you have run out of energy or time for your project, put a note at the top of the README saying that development has slowed down or stopped completely. Someone may choose to fork your project or volunteer to step in as a maintainer or owner, allowing your project to keep going. You can also make an explicit request for maintainers.
