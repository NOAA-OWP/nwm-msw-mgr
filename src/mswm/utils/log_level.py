import logging
import os
import time
import sys
from datetime import datetime, timezone
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


def log_level_set(log_file_dir: str):
    """
    Initialize logging once. If already initialized, does nothing.
    Prints 'Logging into:' exactly once, on first initialization.
    """

    # Prevent reinitializing logging if already set up
    if getattr(log_level_set, "_initialized", False):
        return

    # Try to create log in run logs folder
    try:
        os.makedirs(log_file_dir, exist_ok=True)
        log_file_name = "mswm.log"
    except OSError:
        # If creating the log directory fails, use default fallback
        if Path("/ngencerf/data").exists():
            log_file_dir = Path('/ngencerf/data/run-logs/mswm/')
        else:
            BASE_DIR = Path(__file__).resolve().parent.parent
            log_file_dir = Path(BASE_DIR) / 'run-logs/mswm/'
        log_file_name = f"mswm_{create_timestamp()}.log"
    logFilePath = os.path.join(log_file_dir, log_file_name)

    formatted_module = MODULE_NAME.upper().ljust(LOG_MODULE_NAME_LEN)[:LOG_MODULE_NAME_LEN]

    try:
        handler = logging.FileHandler(logFilePath, mode='a')
        formatter = CustomFormatter(
            fmt=f"%(asctime)s.%(msecs)03d {formatted_module} %(levelname_padded)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S"
        )
        handler.setFormatter(formatter)

        logging.Formatter.converter = time.gmtime

        logger = logging.getLogger()
        logger.setLevel(log_level)
        logger.handlers.clear()
        logger.addHandler(handler)

        # Only prints once because function never runs again
        print(f"Logging into: {logFilePath}")

        # Mark that we've done setup once
        log_level_set._initialized = True
    except OSError:
        print(f"Can't open local directory Log File: {logFilePath}", file=sys.stderr)
