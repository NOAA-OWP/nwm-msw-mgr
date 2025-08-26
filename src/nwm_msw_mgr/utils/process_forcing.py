import logging
from pathlib import Path
import netCDF4
import pandas as pd
import geopandas as gpd


logger = logging.getLogger(__name__)


def update_forcing_in_realization(
        forc_file: Path,
        real_config: dict,
        gpkg_file: Path,
) -> dict:
    """
    Read forcing file(s) to retrieve start time and end time of the forcing data,
    and adjust the realization configuration accordingly:
        1) update forcing information
        2) update start and end times

    Arguments
    ---------
    forc_file: file path to forcing data (a single .nc file or a folder containing a csv file for each catchment)
    real_config: dictionary containing the realization configuration
    gpkg_file: file path the GeoPacakge file

    Returns
    -------
    dictionary containing the adjusted realization config

    """

    # make sure forcing is provided via a single netcdf file or a folder containing a csv file for each catchment
    if forc_file.is_dir():
        # if it is a dir, the folder must contain a csv file for each catchment in the gpkg file
        catids = gpd.read_file(gpkg_file, layer='divides')['divide_id'].tolist()
        cats = [c1 for c1 in catids if not Path(forc_file, c1 + '.csv').exists()]
        if len(cats) > 0:
            try:
                raise FileNotFoundError(f'csv files not found in {forc_file} for these catchments: {cats}')
            except FileNotFoundError as e:
                logger.critical(e)
                raise
        else:
            # get start and end times from one of the csv files
            try:
                df1 = pd.read_csv(Path(forc_file, catids[0] + '.csv'))
                start_time = pd.to_datetime(df1['Time'].iloc[0], format="%Y-%m-%d %H:%M:%S")
                end_time = pd.to_datetime(df1['Time'].iloc[-1], format="%Y-%m-%d %H:%M:%S")
            except Exception as e:
                logger.critical(f"Error loading csv forcing files at {forc_file}\n{e}")
                raise

            # update realization file for forcing
            real_config['global']['forcing'] = dict([('file_pattern', '.*{{id}}.*.csv'),
                                                     ('path', str(forc_file)),
                                                     ('provider', 'CsvPerFeature')])

    elif forc_file.is_file():
        # read start and end times from netcdf file
        try:
            with netCDF4.Dataset(forc_file, 'r') as ncvar:
                t0 = pd.to_datetime(ncvar.model_initialization_time, format="%Y-%m-%d_%H:%M:%S")
                times = [t1 for t1 in ncvar['Time']]
                start_time = t0 + pd.Timedelta(seconds=3600)
                end_time = t0 + pd.Timedelta(seconds=(times[-1] - times[0] + 60) * 60)

                # update forcing in realization file
                real_config['global']['forcing'] = dict([('path', str(forc_file)), ('provider', 'NetCDF')])

        except Exception:
            logger.critical(f'{forc_file} is not a valid NetCDF file')
            raise

    else:
        try:
            raise Exception(f'{forc_file} must be a valid NetCDF file or a folder containing a csv file for each catchment in {gpkg_file}')
        except Exception as e:
            logger.critical(e)
            raise

    logger.info(f'Start time: {start_time}')
    logger.info(f'End time: {end_time}')

    # update time period in realization file
    real_config['time']['start_time'] = str(start_time)
    real_config['time']['end_time'] = str(end_time)

    return real_config
