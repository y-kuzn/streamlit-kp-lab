import logging
import sys
import traceback
from datetime import datetime

# Configure logging
logging.basicConfig(
    filename='app_debug.log',
    filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.DEBUG
)

def log_exception(exc_type, exc_value, exc_traceback):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.error("Uncaught exception:", exc_info=(exc_type, exc_value, exc_traceback))
    tb_str = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))
    with open('app_debug.log', 'a') as f:
        f.write(f"\n{'='*40}\n{datetime.now()}\n{tb_str}\n")

# Attach the global exception hook
sys.excepthook = log_exception

def log_info(msg):
    logging.info(msg)

def log_warning(msg):
    logging.warning(msg)

def log_error(msg):
    logging.error(msg)

def log_debug(msg):
    logging.debug(msg)
