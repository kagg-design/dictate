"""Manage the current user's Dictate shortcut in the Windows Startup folder."""

from pathlib import Path
import sys

import comtypes
from comtypes.client import CreateObject

from src.paths import APP_ROOT, IS_INSTALLED


def startup_shortcut():
    comtypes.CoInitialize()
    try:
        shell = CreateObject("WScript.Shell", dynamic=True)
        folder = Path(shell.SpecialFolders.Item("Startup"))
    finally:
        shell = None
        comtypes.CoUninitialize()
    return folder / ("KAGG Dictate.lnk" if IS_INSTALLED else "KAGG Dictate (development).lnk")


def set_start_at_login(path, enabled):
    if not enabled:
        path.unlink(missing_ok=True)
        return
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise FileNotFoundError(pythonw)
    comtypes.CoInitialize()
    shell = shortcut = None
    try:
        shell = CreateObject("WScript.Shell", dynamic=True)
        shortcut = shell.CreateShortcut(str(path))
        shortcut.TargetPath = str(pythonw)
        shortcut.Arguments = "-E -s -B -m src.main" if IS_INSTALLED else "-m src.main"
        shortcut.WorkingDirectory = str(APP_ROOT)
        shortcut.IconLocation = str(APP_ROOT / "icon.ico")
        shortcut.Save()
    finally:
        shortcut = shell = None
        comtypes.CoUninitialize()
