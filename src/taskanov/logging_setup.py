# logging_setup.py
import logging
from pathlib import Path

from .config import state_dir

def setup_logging(level: str = "INFO"):
    log_dir = state_dir() 
    log_dir.mkdir(parents=True, exist_ok=True)
    logfile = log_dir / "taskanov.log"

    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(logfile, encoding="utf-8"),
            #logging.StreamHandler(),
        ],
    )
    logging.getLogger("googleapiclient.discovery_cache").setLevel(logging.ERROR)
    return logging.getLogger("taskanov")
