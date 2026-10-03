"""Process/crash diagnostics independent of the tray, Tk, CUDA and audio driver."""

import atexit
import ctypes
from ctypes import wintypes
from datetime import datetime
import faulthandler
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback


POWER_EVENTS = {
    0x0004: "PBT_APMSUSPEND",
    0x0007: "PBT_APMRESUMESUSPEND",
    0x0012: "PBT_APMRESUMEAUTOMATIC",
}
DEVICE_NOTIFY_CALLBACK = 2


class RuntimeDiagnostics:
    def __init__(self, log_dir=None, heartbeat_seconds=60):
        self.log_dir = Path(log_dir or Path(__file__).resolve().parents[1] / "logs")
        self.heartbeat_seconds = heartbeat_seconds
        self.state_provider = None
        self._events = queue.Queue()
        self._thread = None
        self._power_handle = None
        self._power_callback = None
        self._power_parameters = None
        self._user32 = None
        self._closed = False
        self._clean_shutdown = False
        self._started = time.monotonic()

    def install(self):
        """Install before importing third-party/native modules."""
        self.log_dir.mkdir(parents=True, exist_ok=True)
        # Keep this descriptor open for the whole process. Rotating it while
        # faulthandler uses it could send a crash dump to an unrelated file.
        self.crash_file = (self.log_dir / "crash.log").open(
            "a", encoding="utf-8", errors="backslashreplace", buffering=1
        )
        self.crash_file.write(
            f"\n=== Process start {datetime.now().astimezone().isoformat()} "
            f"pid={os.getpid()} python={sys.version.split()[0]} ===\n"
        )
        self._old_stderr = sys.stderr
        if sys.stderr is None:
            sys.stderr = self.crash_file
        self._old_fault_enabled = faulthandler.is_enabled()
        faulthandler.enable(file=self.crash_file, all_threads=True)

        self.log = logging.getLogger(f"dictate.diagnostics.{os.getpid()}.{id(self)}")
        self.log.setLevel(logging.INFO)
        self.log.propagate = False
        self._handler = RotatingFileHandler(
            self.log_dir / "diagnostics.log", maxBytes=1024 * 1024,
            backupCount=3, encoding="utf-8",
        )
        self._handler.setFormatter(logging.Formatter(
            "%(asctime)s [%(levelname)s] pid=%(process)d %(threadName)s - %(message)s"
        ))
        self.log.addHandler(self._handler)
        self._old_excepthook = sys.excepthook
        self._old_thread_hook = threading.excepthook
        self._old_unraisable_hook = sys.unraisablehook
        sys.excepthook = self._exception_hook
        threading.excepthook = self._thread_exception_hook
        sys.unraisablehook = self._unraisable_hook
        atexit.register(self.close)
        self.log.info(
            "Process start: parent_pid=%s executable=%s python=%s timezone=%s cwd=%s",
            os.getppid(), sys.executable, sys.version.split()[0],
            datetime.now().astimezone().tzname(), Path.cwd(),
        )
        return self

    def _record_exception(self, label, exc_type, exc_value, exc_traceback):
        self.log.critical(label, exc_info=(exc_type, exc_value, exc_traceback))
        self.crash_file.write(
            f"\n{datetime.now().astimezone().isoformat()} pid={os.getpid()} {label}\n"
        )
        traceback.print_exception(exc_type, exc_value, exc_traceback, file=self.crash_file)
        self.crash_file.flush()

    def _exception_hook(self, exc_type, exc_value, exc_traceback):
        self._record_exception("Unhandled main-thread exception", exc_type, exc_value, exc_traceback)
        if self._old_stderr is not None:
            self._old_excepthook(exc_type, exc_value, exc_traceback)

    def _thread_exception_hook(self, args):
        name = args.thread.name if args.thread is not None else "unknown"
        self._record_exception(
            f"Unhandled thread exception: {name}",
            args.exc_type, args.exc_value, args.exc_traceback,
        )
        if self._old_stderr is not None:
            self._old_thread_hook(args)

    def _unraisable_hook(self, args):
        self._record_exception(
            f"Unraisable exception: {args.err_msg or 'callback/finalizer'}",
            args.exc_type, args.exc_value, args.exc_traceback,
        )
        if self._old_stderr is not None:
            self._old_unraisable_hook(args)

    def start_monitoring(self, state_provider):
        self.state_provider = state_provider
        self._thread = threading.Thread(
            target=self._monitor_loop, name="DiagnosticsMonitor", daemon=True
        )
        self._thread.start()
        self._register_power_notifications()
        self._events.put("Monitoring started")

    def _register_power_notifications(self):
        if sys.platform != "win32":
            return
        # A callback subscription needs no extra window/Tk loop. Keep both the
        # ctypes callback and its parameters alive until Windows unregisters it.
        callback_type = ctypes.WINFUNCTYPE(
            wintypes.ULONG, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p
        )

        class SubscribeParameters(ctypes.Structure):
            _fields_ = (("Callback", callback_type), ("Context", ctypes.c_void_p))

        try:
            self._user32 = ctypes.WinDLL("user32", use_last_error=True)
            self._user32.RegisterSuspendResumeNotification.argtypes = (
                ctypes.c_void_p, wintypes.DWORD,
            )
            self._user32.RegisterSuspendResumeNotification.restype = wintypes.HANDLE
            self._user32.UnregisterSuspendResumeNotification.argtypes = (wintypes.HANDLE,)
            self._user32.UnregisterSuspendResumeNotification.restype = wintypes.BOOL
            self._power_callback = callback_type(self._on_power_event)
            self._power_parameters = SubscribeParameters(self._power_callback, None)
            self._power_handle = self._user32.RegisterSuspendResumeNotification(
                ctypes.byref(self._power_parameters), DEVICE_NOTIFY_CALLBACK
            )
            if not self._power_handle:
                raise ctypes.WinError(ctypes.get_last_error())
            self.log.info("Windows suspend/resume notifications registered")
        except Exception:
            self.log.exception("Cannot register Windows suspend/resume notifications")

    def _on_power_event(self, context, event_type, setting):
        try:
            event = POWER_EVENTS.get(event_type, f"UNKNOWN_0x{event_type:04x}")
            # Persist the event immediately. Collect state on our own thread,
            # avoiding driver/UI calls inside the Windows suspend callback.
            self.log.info("Power event: %s", event)
            self._events.put(event)
        except Exception:
            self.log.exception("Failed to record a Windows power event")
        return 0

    def record_snapshot(self, reason):
        try:
            state = self.state_provider() if self.state_provider else {}
            self.log.info(
                "%s: uptime_s=%.1f state=%s threads=%s",
                reason, time.monotonic() - self._started,
                json.dumps(state, ensure_ascii=False, sort_keys=True),
                ",".join(thread.name for thread in threading.enumerate()),
            )
        except Exception:
            self.log.exception("Failed to collect diagnostic state: %s", reason)

    def _monitor_loop(self):
        while True:
            try:
                event = self._events.get(timeout=self.heartbeat_seconds)
            except queue.Empty:
                event = "Heartbeat"
            if event is None:
                return
            self.record_snapshot(event)

    def mark_clean_shutdown(self, reason):
        self._clean_shutdown = True
        self.log.info("Clean shutdown: %s", reason)

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._power_handle:
            if not self._user32.UnregisterSuspendResumeNotification(self._power_handle):
                self.log.error("Cannot unregister power notifications: %s", ctypes.get_last_error())
            self._power_handle = None
        self._events.put(None)
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self.log.info("Python process shutdown: clean_shutdown=%s", self._clean_shutdown)
        self.crash_file.write(
            f"=== Python shutdown {datetime.now().astimezone().isoformat()} "
            f"pid={os.getpid()} clean_shutdown={self._clean_shutdown} ===\n"
        )
        if sys.excepthook == self._exception_hook:
            sys.excepthook = self._old_excepthook
        if threading.excepthook == self._thread_exception_hook:
            threading.excepthook = self._old_thread_hook
        if sys.unraisablehook == self._unraisable_hook:
            sys.unraisablehook = self._old_unraisable_hook
        faulthandler.disable()
        if sys.stderr is self.crash_file:
            sys.stderr = self._old_stderr
        if self._old_fault_enabled and sys.stderr is not None:
            faulthandler.enable(file=sys.stderr, all_threads=True)
        self.crash_file.close()
        self.log.removeHandler(self._handler)
        self._handler.close()
        atexit.unregister(self.close)
