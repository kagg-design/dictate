import ctypes
import sys
import time
from ctypes import wintypes

from src.logger import logger


INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
UNICODE_CHARACTER_INTERVAL_SECONDS = 0.005
EM_REPLACESEL = 0x00C2
WM_CHAR = 0x0102
SMTO_BLOCK = 0x0001
SMTO_ABORTIFHUNG = 0x0002
TEXT_MESSAGE_TIMEOUT_MS = 2000
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


class GUITHREADINFO(ctypes.Structure):
    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    )


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
        if not _try_insert_native_text(text_to_insert):
            _send_unicode_text(text_to_insert)
    except Exception as e:
        # Do not fall back to a temporary clipboard value: that is precisely
        # what allowed unrelated Ctrl+V operations to paste dictation text.
        logger.error(f"Failed to insert text into the focused Windows control: {e}")
        raise


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


def _try_insert_native_text(text):
    """Use editor messages for native Edit/RichEdit and Chromium controls."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetGUIThreadInfo.argtypes = (wintypes.DWORD, ctypes.POINTER(GUITHREADINFO))
    user32.GetGUIThreadInfo.restype = wintypes.BOOL
    user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
    user32.GetClassNameW.restype = ctypes.c_int
    user32.SendMessageTimeoutW.argtypes = (
        wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
        wintypes.UINT, wintypes.UINT, ctypes.POINTER(ctypes.c_size_t),
    )
    user32.SendMessageTimeoutW.restype = wintypes.LPARAM

    target = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not user32.GetGUIThreadInfo(0, ctypes.byref(target)) or not target.hwndFocus:
        return False
    class_name = ctypes.create_unicode_buffer(256)
    if not user32.GetClassNameW(target.hwndFocus, class_name, len(class_name)):
        return False

    control_class = class_name.value.lower()
    native_edit = control_class == "edit" or control_class.startswith("richedit")
    chromium_edit = control_class in (
        "chrome_renderwidgethosthwnd", "chrome_widgetwin_1",
    )
    if not (native_edit or chromium_edit):
        logger.info("Text input control '%s': using paced keyboard input.", class_name.value)
        return False

    started = time.perf_counter()
    if native_edit:
        # EM_REPLACESEL inserts at the caret or replaces the selection, keeping
        # existing text and undo support. Windows marshals this system message
        # across processes; the clipboard and VK_PACKET state are not involved.
        replacement = ctypes.create_unicode_buffer(text)
        _send_text_message(
            user32, target.hwndFocus, EM_REPLACESEL, 1, ctypes.addressof(replacement)
        )
        method = "EM_REPLACESEL"
    else:
        # Chromium consumes WM_CHAR from its focused window. Put the
        # actual UTF-16 unit in each message instead of relying on VK_PACKET's
        # mutable keyboard state while the editor processes a delayed queue.
        encoded = text.encode("utf-16-le", errors="surrogatepass")
        for offset in range(0, len(encoded), 2):
            current = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
            if (
                not user32.GetGUIThreadInfo(0, ctypes.byref(current))
                or current.hwndFocus != target.hwndFocus
            ):
                raise RuntimeError(
                    "Focused control changed; stopped insertion after "
                    f"{offset // 2} UTF-16 code units."
                )
            code_unit = int.from_bytes(encoded[offset:offset + 2], "little")
            _send_text_message(user32, target.hwndFocus, WM_CHAR, code_unit, 1)
        method = "WM_CHAR"

    logger.info(
        "Text delivered via %s to '%s': %d characters in %.3fs (control=%s).",
        method, class_name.value, len(text), time.perf_counter() - started,
        target.hwndFocus,
    )
    return True


def _send_text_message(user32, window, message, wparam, lparam):
    result = ctypes.c_size_t()
    ctypes.set_last_error(0)
    accepted = user32.SendMessageTimeoutW(
        window, message, wparam, lparam,
        SMTO_BLOCK | SMTO_ABORTIFHUNG, TEXT_MESSAGE_TIMEOUT_MS, ctypes.byref(result),
    )
    if not accepted:
        raise OSError(
            ctypes.get_last_error(),
            f"Text message 0x{message:04x} failed or timed out; insertion stopped.",
        )


def _send_unicode_text(text):
    """Submit one Unicode character at a time, allowing the receiver to keep up."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SendInput.argtypes = (
        wintypes.UINT,
        ctypes.POINTER(INPUT),
        ctypes.c_int,
    )
    user32.SendInput.restype = wintypes.UINT
    user32.GetForegroundWindow.argtypes = ()
    user32.GetForegroundWindow.restype = wintypes.HWND
    target_window = user32.GetForegroundWindow()
    if not target_window:
        raise RuntimeError("There is no foreground window for text insertion.")

    sent_code_units = 0
    started = time.perf_counter()
    for character_index, character in enumerate(text):
        if user32.GetForegroundWindow() != target_window:
            raise RuntimeError(
                "Foreground window changed; stopped insertion after "
                f"{character_index}/{len(text)} characters."
            )

        # A large SendInput call floods modern text controls with VK_PACKET
        # messages. Submission can succeed even when the editor drops or
        # misinterprets characters. Keep each character's down/up events
        # together, including both UTF-16 units of a supplementary character.
        encoded = character.encode("utf-16-le", errors="surrogatepass")
        code_units = [
            int.from_bytes(encoded[index:index + 2], "little")
            for index in range(0, len(encoded), 2)
        ]
        events = []
        for code_unit in code_units:
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
        sent_code_units += len(code_units)
        if character_index + 1 < len(text):
            time.sleep(UNICODE_CHARACTER_INTERVAL_SECONDS)

    logger.info(
        "Unicode keyboard events submitted: %d characters in %.3fs "
        "(window=%s). Display in the target application is not verified.",
        len(text),
        time.perf_counter() - started,
        target_window,
    )
