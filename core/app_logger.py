import os
import logging
from pathlib import Path
from datetime import datetime

def get_log_path():
    appdata = os.getenv('APPDATA') or os.path.expanduser('~/.config')
    path = Path(appdata) / "SaralGroup" / "PuantajApp"
    path.mkdir(parents=True, exist_ok=True)
    return path / "puantaj.log"

log_path = get_log_path()
logging.basicConfig(
    filename=log_path,
    filemode='a',
    format='%(asctime)s [%(levelname)s] %(message)s',
    level=logging.INFO,
    encoding='utf-8'
)

def log_error(msg):
    logging.error(msg)

def log_info(msg):
    logging.info(msg)
