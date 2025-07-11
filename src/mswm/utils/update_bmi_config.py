import glob
import os
from pathlib import Path
import pandas as pd
import yaml


def update_noah_ueb(
        real_config: dict,
        out_dir: Path,
) -> dict:
    """
    For noah-owp-modular & UEB, create new BMI config files with adjusted start/end times, and then
        update path to BMI config files in realization file accordingly

    Arguments
    ---------
    real_config: dictionary containing the realization configuration
    out_dir: folder for the new BMI config files

    Returns
    -------
    dictionary containing adjusted realization config

    """
    module_config = real_config['global']['formulations'][0]['params']['modules']

    start_time = real_config['time']['start_time']
    startdate = pd.to_datetime(start_time, format="%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")
    end_time = real_config['time']['end_time']
    enddate = pd.to_datetime(end_time, format="%Y-%m-%d %H:%M:%S").strftime("%Y%m%d%H%M")

    mod_dict = {'NoahOWP': 'noah-owp-modular', 'UEB': 'ueb'}

    for i1, m1 in enumerate(module_config):
        if m1['params']['model_type_name'] in ['NoahOWP', 'UEB']:

            # read the BMI config files from the source directory in the realization file
            src0 = m1['params']['init_config']
            src = Path(src0.replace('{{id}}', '*'))
            dst = Path(out_dir, mod_dict[m1['params']['model_type_name']] + '_input')
            dst.mkdir(parents=True, exist_ok=True)
            for f1 in glob.glob(f'{src}'):
                with open(f1) as f:
                    lines = f.readlines()

                # update start/end times
                for i2, l1 in enumerate(lines):
                    if m1['params']['model_type_name'] == 'NoahOWP':
                        if 'startdate' in l1:
                            lines[i2] = "  " + "startdate".ljust(19) + "= " + "'" + startdate + "'" + "               ! UTC time start of simulation (YYYYMMDDhhmm)\n"
                        elif 'enddate' in l1:
                            lines[i2] = "  " + "enddate".ljust(19) + "= " + "'" + enddate + "'" + "               ! UTC time end of simulation (YYYYMMDDhhmm)\n"
                    elif m1['params']['model_type_name'] == 'UEB':
                        lines[8] = f'{startdate[:4]} {startdate[4:6]} {startdate[6:8]} {startdate[8:10]}.0\n'
                        lines[9] = f'{enddate[:4]} {enddate[4:6]} {enddate[6:8]} {enddate[8:10]}.0\n'

                        # write to new BMI config files
                with open(Path(dst, os.path.basename(f1)), 'w') as outfile:
                    outfile.writelines(lines)

            # replace path to BMI config file in realization file
            module_config[i1]['params']['init_config'] = str(Path(dst, os.path.basename(src0)))
            real_config['global']['formulations'][0]['params']['modules'] = module_config

    return real_config


def update_troute(
        real_config: dict,
        out_dir: Path,
) -> dict:
    """
    For t-route, create new BMI config file with adjusted start/end times, and then
        update path to BMI config files in realization file accordingly

    Arguments
    ---------
    real_config: dictionary containing the realization configuration
    out_dir: folder for the new BMI config files

    Returns
    -------
    dictionary containing adjusted realization config

    """

    # make sure the source t-route config exists
    src = Path(real_config['routing']['t_route_config_file_with_path']).absolute()
    if not src.exists():
        raise FileNotFoundError(src)

    with open(src) as fp1:
        rt_config = yaml.safe_load(fp1)

    # compute number of time steps and max_loop_size
    start_time = pd.to_datetime(real_config['time']['start_time'], format="%Y-%m-%d %H:%M:%S")
    end_time = pd.to_datetime(real_config['time']['end_time'], format="%Y-%m-%d %H:%M:%S")
    nts = len(pd.date_range(start=start_time, end=end_time, freq='5min')) - 1
    max_loop_size = divmod(nts * 300, 3600)[0] + 1
    stream_output_time = divmod(nts * 300, 3600)[0] + 1

    # update t-route config
    rt_config['compute_parameters']['restart_parameters']['start_datetime'] = str(start_time)
    rt_config['compute_parameters']['forcing_parameters']['nts'] = nts
    rt_config['compute_parameters']['forcing_parameters']['max_loop_size'] = max_loop_size
    rt_config['output_parameters']['stream_output']['stream_output_time'] = stream_output_time

    # write to new t-route config file
    new_file = Path(out_dir, os.path.basename(src))
    with open(new_file, 'w') as file:
        yaml.dump(rt_config, file, sort_keys=False, default_flow_style=False, indent=4)

    # update path to new t-route config in realization
    real_config['routing']['t_route_config_file_with_path'] = str(new_file)

    return real_config
