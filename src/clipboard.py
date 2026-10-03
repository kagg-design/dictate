"""Explicit user-requested copying; automatic dictation never calls this."""

import ctypes
from ctypes import wintypes
import sys
import time


CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002


def copy_text(text, owner_window):
    if sys.platform != "win32":
        raise RuntimeError("Clipboard copying requires Windows")
    if not owner_window:
        raise RuntimeError("The tray window is not ready for clipboard ownership")
    if "\0" in text:
        raise ValueError("Clipboard text cannot contain embedded null characters")

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.OpenClipboard.argtypes = (wintypes.HWND,)
    user32.OpenClipboard.restype = wintypes.BOOL
    user32.CloseClipboard.argtypes = ()
    user32.CloseClipboard.restype = wintypes.BOOL
    user32.EmptyClipboard.argtypes = ()
    user32.EmptyClipboard.restype = wintypes.BOOL
    user32.SetClipboardData.argtypes = (wintypes.UINT, wintypes.HANDLE)
    user32.SetClipboardData.restype = wintypes.HANDLE
    kernel32.GlobalAlloc.argtypes = (wintypes.UINT, ctypes.c_size_t)
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalUnlock.restype = wintypes.BOOL
    kernel32.GlobalFree.argtypes = (wintypes.HGLOBAL,)
    kernel32.GlobalFree.restype = wintypes.HGLOBAL

    # Allocate and fill the text before opening/clearing the clipboard.
    encoded = text.encode("utf-16-le") + b"\0\0"
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(encoded))
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    opened = False
    try:
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            ctypes.memmove(pointer, encoded, len(encoded))
        finally:
            kernel32.GlobalUnlock(handle)

        # Another app can briefly own the clipboard lock. Bound retries to
        # 250 ms so selecting a history entry cannot freeze the tray menu.
        for attempt in range(6):
            if user32.OpenClipboard(owner_window):
                opened = True
                break
            if attempt < 5:
                time.sleep(0.05)
        if not opened:
            raise OSError(ctypes.get_last_error(), "Clipboard is busy; try copying again")
        if not user32.EmptyClipboard():
            raise ctypes.WinError(ctypes.get_last_error())
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            raise ctypes.WinError(ctypes.get_last_error())
        handle = None  # Windows owns this allocation after SetClipboardData.
    finally:
        if opened:
            user32.CloseClipboard()
        if handle:
            kernel32.GlobalFree(handle)
