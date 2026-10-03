import ctypes
from ctypes import wintypes
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from src.diagnostics import RuntimeDiagnostics


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class RuntimeDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.log_dir = Path(temporary.name)
        self.diagnostics = RuntimeDiagnostics(self.log_dir).install()
        self.addCleanup(self.diagnostics.close)

    def test_windowless_main_exception_is_preserved_before_process_exit(self):
        log_dir = self.log_dir / "child"
        result = subprocess.run(
            [sys.executable, "-c", "\n".join([
                "import sys",
                "sys.stderr = None",
                "from src.diagnostics import RuntimeDiagnostics",
                f"diagnostics = RuntimeDiagnostics({str(log_dir)!r}).install()",
                "raise RuntimeError('windowless startup failure')",
            ])],
            cwd=PROJECT_ROOT, capture_output=True, timeout=15,
        )
        self.assertNotEqual(result.returncode, 0)
        for filename in ("crash.log", "diagnostics.log"):
            text = (log_dir / filename).read_text(encoding="utf-8")
            self.assertIn("windowless startup failure", text)
            self.assertIn("Unhandled main-thread exception", text)
        self.assertIn("clean_shutdown=False", (log_dir / "crash.log").read_text())

    def test_thread_and_callback_finalizer_exceptions_are_preserved(self):
        log_dir = self.log_dir / "child"
        result = subprocess.run(
            [sys.executable, "-c", "\n".join([
                "import sys, threading",
                "sys.stderr = None",
                "from src.diagnostics import RuntimeDiagnostics",
                f"diagnostics = RuntimeDiagnostics({str(log_dir)!r}).install()",
                "def fail(): raise RuntimeError('worker failure')",
                "thread = threading.Thread(target=fail, name='TestWorker')",
                "thread.start(); thread.join()",
                "class BrokenFinalizer:",
                "    def __del__(self): raise ValueError('finalizer failure')",
                "obj = BrokenFinalizer(); del obj",
                "diagnostics.mark_clean_shutdown('test completed')",
            ])],
            cwd=PROJECT_ROOT, capture_output=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        text = (log_dir / "crash.log").read_text(encoding="utf-8")
        self.assertIn("Unhandled thread exception: TestWorker", text)
        self.assertIn("worker failure", text)
        self.assertIn("Unraisable exception", text)
        self.assertIn("finalizer failure", text)
        self.assertIn("clean_shutdown=True", text)

    def test_fatal_traceback_descriptor_stays_open_and_is_usable(self):
        import faulthandler

        self.assertTrue(faulthandler.is_enabled())
        faulthandler.dump_traceback(file=self.diagnostics.crash_file, all_threads=True)
        text = (self.log_dir / "crash.log").read_text(encoding="utf-8")
        self.assertIn("test_fatal_traceback_descriptor", text)
        self.assertFalse(self.diagnostics.crash_file.closed)

    def test_power_callback_records_events_and_queues_state_without_calling_provider(self):
        user32 = Mock()
        handle = 0x12345678ABC if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x1234
        user32.RegisterSuspendResumeNotification.return_value = handle
        user32.UnregisterSuspendResumeNotification.return_value = 1
        self.diagnostics.state_provider = Mock(side_effect=AssertionError("driver/UI call"))

        with patch("src.diagnostics.ctypes.WinDLL", return_value=user32):
            self.diagnostics._register_power_notifications()
        self.assertEqual(user32.RegisterSuspendResumeNotification.restype, wintypes.HANDLE)
        self.assertEqual(user32.RegisterSuspendResumeNotification.call_args.args[1], 2)
        for event in (4, 18, 7):
            self.assertEqual(self.diagnostics._power_parameters.Callback(None, event, None), 0)
        self.diagnostics.state_provider.assert_not_called()
        self.assertEqual(
            [self.diagnostics._events.get_nowait() for _ in range(3)],
            ["PBT_APMSUSPEND", "PBT_APMRESUMEAUTOMATIC", "PBT_APMRESUMESUSPEND"],
        )
        self.diagnostics.close()
        user32.UnregisterSuspendResumeNotification.assert_called_once_with(handle)
        text = (self.log_dir / "diagnostics.log").read_text(encoding="utf-8")
        self.assertIn("Power event: PBT_APMSUSPEND", text)
        self.assertIn("Power event: PBT_APMRESUMEAUTOMATIC", text)

    def test_heartbeat_reports_liveness_and_survives_state_provider_failure(self):
        self.diagnostics.state_provider = Mock(side_effect=[RuntimeError("state unavailable"), {"worker_alive": True}])
        self.diagnostics.record_snapshot("first")
        self.diagnostics.record_snapshot("second")
        self.assertIn("state unavailable", (self.log_dir / "diagnostics.log").read_text())

        heartbeat_received = threading.Event()
        calls = []

        def state():
            calls.append(True)
            if len(calls) >= 2:
                heartbeat_received.set()
            return {"worker_alive": True, "last_audio_callback_age_s": 0.03}

        self.diagnostics.heartbeat_seconds = 0.02
        with patch.object(self.diagnostics, "_register_power_notifications"):
            self.diagnostics.start_monitoring(state)
        self.assertTrue(heartbeat_received.wait(timeout=2))
        self.diagnostics.close()
        text = (self.log_dir / "diagnostics.log").read_text(encoding="utf-8")
        self.assertIn("Heartbeat:", text)
        self.assertIn('"last_audio_callback_age_s": 0.03', text)
        self.assertIn('"worker_alive": true', text)


if __name__ == "__main__":
    unittest.main()
