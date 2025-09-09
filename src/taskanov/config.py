# src/taskanov/config.py
from __future__ import annotations
import os
from pathlib import Path
from typing import Dict, Any
import yaml

#from taskanov.logging_setup import setup_logging

APP_NAME = "taskanov"

def config_dir() -> Path:
    # XDG spec, fallback to ~/.config/taskanov
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / APP_NAME

def state_dir() -> Path:
    # XDG spec, fallback to ~/.local/state/taskanov
    return Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / APP_NAME

def load_config() -> Dict[str, Any]:
    """
    Load YAML config from ~/.config/taskanov/config.yaml (or XDG).
    Returns a dict with defaults when missing.
    """
    cfg_path = config_dir() / "config.yaml"

    config_dir().mkdir(parents=True, exist_ok=True)
    state_dir().mkdir(parents=True, exist_ok=True)

    defaults: Dict[str, Any] = {
        "state_dir": state_dir(), 
        "backend": {
            "type": "localjson",   # available: localjson (default), google, caldav (future)
            "localjson": {
                "filename": "tasks.json",  # stored under state_dir
            },
            # "google": {...},  # placeholder for future
            # "caldav": {...},  # placeholder for future
        },
    }

    if cfg_path.exists():
        try:
            data = yaml.safe_load(cfg_path.read_text()) or {}
            # shallow merge; keep defaults when keys are missing
            def merge(dst, src):
                for k, v in src.items():
                    if isinstance(v, dict) and isinstance(dst.get(k), dict):
                        merge(dst[k], v)
                    else:
                        dst[k] = v
            merge(defaults, data)
        except Exception as e:
            # ignore parse errors and keep defaults
            pass
    else:
        raise("Config is not a YAML")

    return defaults
