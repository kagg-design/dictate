"""Save and restore the normal bounds of a Windows Tk toplevel."""

import ctypes
from ctypes import wintypes

import yaml

from src.config import save_settings
from src.logger import logger


class _MonitorInfo(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD), ("monitor", wintypes.RECT),
        ("work", wintypes.RECT), ("flags", wintypes.DWORD),
    ]


def _read_position(position):
    if not isinstance(position, (list, tuple)) or len(position) not in (2, 4):
        return None
    if any(type(value) is not int or abs(value) >= 2**30 for value in position):
        return None
    if len(position) == 4 and min(position[2:]) <= 0:
        return None
    return tuple(position)


def _window_handle(window):
    user32 = ctypes.windll.user32
    user32.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
    user32.GetAncestor.restype = wintypes.HWND
    return user32.GetAncestor(window.winfo_id(), 2)  # GA_ROOT gets the Tk window wrapper.


def _is_arranged(hwnd):
    query = getattr(ctypes.windll.user32, "IsWindowArranged", None)
    if not hwnd or query is None:
        return False
    query.argtypes = (wintypes.HWND,)
    query.restype = wintypes.BOOL
    return bool(query(hwnd))


def _window_rectangles(window):
    user32 = ctypes.windll.user32
    hwnd = _window_handle(window)
    if not hwnd:
        return None
    frame, client = wintypes.RECT(), wintypes.RECT()
    for get_rect, rect in ((user32.GetWindowRect, frame), (user32.GetClientRect, client)):
        get_rect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
        get_rect.restype = wintypes.BOOL
        if not get_rect(hwnd, ctypes.byref(rect)):
            return None
    return frame, client


def _work_area(x, y, width, height):
    user32 = ctypes.windll.user32
    user32.MonitorFromRect.argtypes = (ctypes.POINTER(wintypes.RECT), wintypes.DWORD)
    user32.MonitorFromRect.restype = wintypes.HMONITOR
    rectangle = wintypes.RECT(x, y, x + width, y + height)
    monitor = user32.MonitorFromRect(ctypes.byref(rectangle), 2)  # MONITOR_DEFAULTTONEAREST
    info = _MonitorInfo(ctypes.sizeof(_MonitorInfo))
    user32.GetMonitorInfoW.argtypes = (wintypes.HMONITOR, ctypes.POINTER(_MonitorInfo))
    user32.GetMonitorInfoW.restype = wintypes.BOOL
    if monitor and user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return info.work
    return None


class WindowPosition:
    def __init__(self, window, setting, position):
        self.window = window
        self.setting = setting
        self._timer = None
        self._saved = _read_position(position)
        self._position = self._saved
        window.bind("<Configure>", self._moved, add="+")

    def restore(self):
        if self._position is None:
            return
        self.window.update_idletasks()
        x, y, *size = self._position
        rectangles = _window_rectangles(self.window)
        if rectangles is not None:
            frame, client = rectangles
            width, height = size or (client.right, client.bottom)
            minimum_width, minimum_height = self.window.minsize()
            width = max(width, minimum_width)
            height = max(height, minimum_height)
            frame_width = frame.right - frame.left - client.right
            frame_height = frame.bottom - frame.top - client.bottom
            work = _work_area(x, y, width + frame_width, height + frame_height)
            if work is not None:
                # A snapped frame may extend outside the work area by its
                # invisible resize border. Preserve that alignment.
                border = max(0, frame_width // 2)
                left, top = work.left - border, work.top - border
                right, bottom = work.right + border, work.bottom + border
                width = min(width, max(minimum_width, right - left - frame_width))
                height = min(height, max(minimum_height, bottom - top - frame_height))
                x = max(left, min(x, right - width - frame_width))
                y = max(top, min(y, bottom - height - frame_height))
            size = (width, height)
        geometry = f"+{x}+{y}"
        if size:
            geometry = f"{size[0]}x{size[1]}" + geometry
        self.window.geometry(geometry)
        return (x, y, *size) if size else None

    def show(self):
        state = self.window.state()
        if state == "iconic":
            self.window.deiconify()
        elif state == "withdrawn":
            hwnd = _window_handle(self.window)
            if _is_arranged(hwnd):
                # Tk deiconify requests a normal window, which undocks Snap.
                show = ctypes.windll.user32.ShowWindow
                show.argtypes = (wintypes.HWND, ctypes.c_int)
                show.restype = wintypes.BOOL
                show(hwnd, 5)  # SW_SHOW displays the window in its current state.
                if self.window.winfo_viewable():
                    return
            bounds = self.restore()
            self.window.deiconify()
            if bounds is not None:
                self._apply_bounds_after_show(bounds)

    def _apply_bounds_after_show(self, bounds):
        rectangles = _window_rectangles(self.window)
        if rectangles is None:
            return
        frame, client = rectangles
        x, y, width, height = bounds
        if (frame.left, frame.top, client.right, client.bottom) == bounds:
            return
        # Showing the HWND can replay its old floating bounds after a geometry
        # request. Apply the intended rectangle to the now-visible window.
        set_position = ctypes.windll.user32.SetWindowPos
        set_position.argtypes = (
            wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        )
        set_position.restype = wintypes.BOOL
        set_position(
            _window_handle(self.window), None, x, y,
            width + frame.right - frame.left - client.right,
            height + frame.bottom - frame.top - client.bottom,
            0x0014,  # SWP_NOZORDER | SWP_NOACTIVATE
        )

    def _current(self):
        if self.window.state() == "normal" and self.window.winfo_viewable():
            rectangles = _window_rectangles(self.window)
            if rectangles is not None:
                frame, client = rectangles
                return frame.left, frame.top, client.right, client.bottom
            return self.window.winfo_x(), self.window.winfo_y(), self.window.winfo_width(), self.window.winfo_height()
        return self._position

    def _moved(self, event):
        if event.widget is not self.window:
            return
        position = self._current()
        if position == self._position:
            return
        self._position = position
        if self._timer is not None:
            self.window.after_cancel(self._timer)
        self._timer = self.window.after(350, self.save)

    def save(self):
        if self._timer is not None:
            self.window.after_cancel(self._timer)
            self._timer = None
        position = self._position = self._current()
        if position is not None and position != self._saved:
            try:
                save_settings({self.setting: list(position)})
            except (OSError, ValueError, yaml.YAMLError):
                logger.exception("Could not save window bounds for %s", self.setting)
            else:
                self._saved = position
        return position
