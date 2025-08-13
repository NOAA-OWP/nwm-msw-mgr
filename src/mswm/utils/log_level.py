import logging
from datetime import datetime, timezone
import sys
from pathlib import Path

log_level = logging.INFO
MODULE_NAME = "MSWM"
LOG_MODULE_NAME_LEN = 8


class CustomFormatter(logging.Formatter):
    LEVEL_NAME_MAP = {
        logging.DEBUG: "DEBUG",
        logging.INFO: "INFO",
        logging.WARNING: "WARNING",
        logging.ERROR: "SEVERE",
        logging.CRITICAL: "FATAL"
    }

    def format(self, record):
        original_levelname = record.levelname
        record.levelname = self.LEVEL_NAME_MAP.get(record.levelno, original_levelname)
        record.levelname_padded = record.levelname.ljust(7)[:7]  # Exactly 7 chars
        formatted = super().format(record)
        record.levelname = original_levelname  # Restore original in case it's reused
        return formatted


def create_timestamp(date_only: bool = False, iso: bool = False, append_ms: bool = False) -> str:
    now = datetime.now(timezone.utc)

    if date_only:
        ts_base = now.strftime("%Y%m%d")
    elif iso:
        ts_base = now.strftime("%Y-%m-%dT%H:%M:%S")
    else:
        ts_base = now.strftime("%Y%m%dT%H%M%S")

    if append_ms:
        ms_str = f".{now.microsecond // 1000:03d}"
        return ts_base + ms_str
    else:
        return ts_base


def log_level_set():
    '''
    Set logging level and specify logger configuration.

    Arguments
    ---------
    None

    Returns
    -------
    None

    Notes
    -----
    In the absence of user-specified logging level, level defaults to INFO.
    See also https://docs.python.org/3/library/logging.html
    '''

    BASE_DIR = Path(__file__).resolve().parent.parent

    # Define log directory, defaulting to BASE_DIR if the custom path does not exist
    log_file_dir = Path('/ngencerf/data/run-logs/mswm/') if Path("/ngencerf/data").exists() else BASE_DIR / 'run-logs/mswm/'

    # Create log file name with timestamp
    log_file_name = f"mswm_{create_timestamp()}.log"
    log_file_path = log_file_dir / log_file_name

    # Ensure the log directory exists
    log_file_dir.mkdir(parents=True, exist_ok=True)

    # Format module name with a maximum length of 8 characters
    formatted_module = MODULE_NAME.upper().ljust(LOG_MODULE_NAME_LEN)[:LOG_MODULE_NAME_LEN]

    try:
        logger = logging.getLogger()

        # Clear existing handlers to avoid duplicates (Only once)
        if not logger.hasHandlers():
            handler = logging.FileHandler(log_file_path, mode='a')
            formatter = CustomFormatter(
                fmt=f"%(asctime)s.%(msecs)03d {formatted_module} %(levelname_padded)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S"
            )
            handler.setFormatter(formatter)
            logger.addHandler(handler)
            logger.setLevel(log_level)

        # Only print the message if logging is enabled for INFO level
        if logger.isEnabledFor(logging.INFO):
            print(f"Logging into: {log_file_path}")

    except OSError as e:
        print(f"Can't open local directory log file: {log_file_path} - {e}", file=sys.stderr)
