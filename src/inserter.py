import ctypes
import sys
import time
from ctypes import wintypes

from src.logger import logger


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
MAX_CODE_UNITS_PER_BATCH = 1024
MODIFIER_KEYS = (
    (0x10, "Shift"),
    (0x11, "Ctrl"),
    (0x12, "Alt"),
    (0x5B, "Left Win"),
    (0x5C, "Right Win"),
)
MODIFIER_RELEASE_POLL_SECONDS = 0.01
MODIFIER_RELEASE_STABLE_CHECKS = 3


class KEYBDINPUT(ctypes.Structure):
    _fields_ = (
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    )


class MOUSEINPUT(ctypes.Structure):
    _fields_ = (
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", wintypes.WPARAM),
    )


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = (
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    )


class INPUT_UNION(ctypes.Union):
    _fields_ = (
        ("ki", KEYBDINPUT),
        ("mi", MOUSEINPUT),
        ("hi", HARDWAREINPUT),
    )


class INPUT(ctypes.Structure):
    _anonymous_ = ("value",)
    _fields_ = (("type", wintypes.DWORD), ("value", INPUT_UNION))


def paste_text(text):
    """Insert text into the focused Windows control without using the clipboard."""
    if not text:
        logger.info("No text to insert. Skipping text insertion.")
        return

    # Leave the caret ready for a following dictation or typed word. Placing
    # the separator after this insertion avoids a leading space if the user
    # moves focus to a different field before the next dictation completes.
    text_to_insert = text if text[-1].isspace() else text + " "
    logger.info(
        f"Inserting text directly: '{text_to_insert[:40]}...' "
        f"(total length: {len(text_to_insert)})"
    )

    if sys.platform != "win32":
        logger.error("Direct text insertion is only supported on Windows.")
        return

    try:
        _wait_for_modifiers_released()
        _send_unicode_text(text_to_insert)
    except Exception as e:
        # Do not fall back to a temporary clipboard value: that is precisely
        # what allowed unrelated Ctrl+V operations to paste dictation text.
        logger.error(f"Failed to insert text through Windows SendInput: {e}")


def _wait_for_modifiers_released():
    """Wait until physical modifiers are released for several consecutive polls."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetAsyncKeyState.argtypes = (ctypes.c_int,)
    user32.GetAsyncKeyState.restype = wintypes.SHORT

    stable_checks = 0
    waiting_logged = False
    while stable_checks < MODIFIER_RELEASE_STABLE_CHECKS:
        pressed = _pressed_modifier_names(user32)
        if pressed:
            stable_checks = 0
            if not waiting_logged:
                logger.info(
                    "Deferring text insertion until modifiers are released: %s",
                    ", ".join(pressed),
                )
                waiting_logged = True
        else:
            stable_checks += 1

        if stable_checks < MODIFIER_RELEASE_STABLE_CHECKS:
            time.sleep(MODIFIER_RELEASE_POLL_SECONDS)

    if waiting_logged:
        logger.info("Modifiers released; continuing deferred text insertion.")


def _pressed_modifier_names(user32):
    return tuple(
        name
        for virtual_key, name in MODIFIER_KEYS
        if user32.GetAsyncKeyState(virtual_key) & 0x8000
    )


def _send_unicode_text(text):
    """Send UTF-16 keyboard packets in bounded batches via Win32 SendInput."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    )
    user32.SendInput.restype = wintypes.UINT

    encoded = text.encode("utf-16-le", errors="surrogatepass")
    code_units = [
        int.from_bytes(encoded[index:index + 2], "little")
        for index in range(0, len(encoded), 2)
    ]

    sent_code_units = 0
    for offset in range(0, len(code_units), MAX_CODE_UNITS_PER_BATCH):
        batch = code_units[offset:offset + MAX_CODE_UNITS_PER_BATCH]
        events = []
        for code_unit in batch:
            events.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE, 0, 0),
                )
            )
            events.append(
                INPUT(
                    type=INPUT_KEYBOARD,
                    ki=KEYBDINPUT(
                        0,
                        code_unit,
                        KEYEVENTF_UNICODE | KEYEVENTF_KEYUP,
                        0,
                        0,
                    ),
                )
            )

        event_array = (INPUT * len(events))(*events)
        ctypes.set_last_error(0)
        sent_events = user32.SendInput(
            len(event_array), event_array, ctypes.sizeof(INPUT)
        )
        if sent_events != len(event_array):
            error_code = ctypes.get_last_error()
            raise OSError(
                error_code,
                "SendInput accepted "
                f"{sent_events}/{len(event_array)} events after "
                f"{sent_code_units} UTF-16 code units",
            )
        sent_code_units += len(batch)

    logger.debug("Direct Unicode text insertion completed successfully.")
