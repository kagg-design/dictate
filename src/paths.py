"""Paths shared by the source checkout and the per-user Windows installation."""

import os
from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[1]
IS_INSTALLED = (APP_ROOT / "dictate-installed.txt").is_file()
LOCAL_APP_DATA = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
DATA_DIR = LOCAL_APP_DATA / "Dictate" if IS_INSTALLED else APP_ROOT
LOG_DIR = DATA_DIR / "logs"
