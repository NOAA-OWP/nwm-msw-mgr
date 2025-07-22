import logging
import os
import time
import sys
from datetime import datetime, timezone
from pathlib import Path


def create_timestamp() -> str:
    now = datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%d")


def log_level_set():
    '''
    Set logging level and specify logger configuration.

    Arguments
    ---------
    input_parameters (dict): User input logging parameters

    Returns
    -------
    None

    Notes
    -----
    In the absense of user-specified logging level, level defaults to DEBUG
    See also https://docs.python.org/3/library/logging.html

    '''

    log_level = 'INFO'
    if True:
        BASE_DIR = Path(__file__).resolve().parent.parent

        if Path("/ngencerf/data").exists():
            log_file_dir = Path(f'/ngencerf/data/run-logs/mswm_{create_timestamp()}/')
        else:
            log_file_dir = Path(BASE_DIR) / f'run-logs/mswm_{create_timestamp()}/'

        log_file_name = "mswm.log"
        os.makedirs(log_file_dir, exist_ok=True)
        logFilePath = os.path.join(log_file_dir, log_file_name)
        try:
            logFile = open(logFilePath, "w")
            print(f"Logging into: {logFilePath}")
        except IOError:
            print(f"Can't Open local directory Log File: {logFilePath}", file=sys.stderr)

        logging.Formatter.converter = time.gmtime
        logging.basicConfig(
            force=True,
            level=log_level,
            format='%(asctime)s.%(msecs)03d MSWM %(levelname)s    %(message)s',
            datefmt='%Y-%m-%dT%H:%M:%S',
            handlers=[
                logging.FileHandler(logFilePath, mode='a'),  # Log to a file
                # logging.StreamHandler(sys.stdout)
            ])
    else:
        # Unclear when this block is intended to be used
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)s - %(funcName)s]: %(message)s',
            stream=sys.stderr,
        )
