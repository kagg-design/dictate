import ctypes
import unittest
from unittest.mock import Mock, patch

from src.clipboard import copy_text


class ClipboardCopyTests(unittest.TestCase):
    def setUp(self):
        self.memory = ctypes.create_string_buffer(50000)
        self.handle = 0x12345678ABC if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x1234
        self.user32 = Mock()
        self.kernel32 = Mock()
        self.kernel32.GlobalAlloc.return_value = self.handle
        self.kernel32.GlobalLock.return_value = ctypes.addressof(self.memory)
        self.user32.SetClipboardData.return_value = self.handle
        self.user32.OpenClipboard.return_value = 1
        self.user32.EmptyClipboard.return_value = 1
        library = patch("src.clipboard.ctypes.WinDLL", side_effect=lambda name, **kw: {
            "user32": self.user32, "kernel32": self.kernel32,
        }[name])
        library.start()
        self.addCleanup(library.stop)

    def test_copy_transfers_complete_unicode_text_and_ownership_to_windows(self):
        text = "Русский 😀\nEnglish " * 100
        copy_text(text, owner_window=123)
        encoded = text.encode("utf-16-le") + b"\0\0"
        self.assertEqual(ctypes.string_at(ctypes.addressof(self.memory), len(encoded)), encoded)
        self.user32.OpenClipboard.assert_called_once_with(123)
        self.user32.SetClipboardData.assert_called_once_with(13, self.handle)
        self.user32.CloseClipboard.assert_called_once()
        self.kernel32.GlobalFree.assert_not_called()

    def test_busy_clipboard_is_not_cleared_and_allocated_memory_is_freed(self):
        self.user32.OpenClipboard.return_value = 0
        with patch("src.clipboard.time.sleep"):
            with self.assertRaisesRegex(OSError, "Clipboard is busy"):
                copy_text("saved dictation", owner_window=123)
        self.user32.EmptyClipboard.assert_not_called()
        self.user32.SetClipboardData.assert_not_called()
        self.user32.CloseClipboard.assert_not_called()
        self.kernel32.GlobalFree.assert_called_once_with(self.handle)

    def test_allocation_lock_failure_does_not_touch_the_clipboard(self):
        self.kernel32.GlobalLock.return_value = 0
        with self.assertRaises(OSError):
            copy_text("saved dictation", owner_window=123)
        self.user32.OpenClipboard.assert_not_called()
        self.kernel32.GlobalFree.assert_called_once_with(self.handle)

    def test_set_data_failure_closes_clipboard_and_frees_untransferred_memory(self):
        self.user32.SetClipboardData.return_value = 0
        with self.assertRaises(OSError):
            copy_text("saved dictation", owner_window=123)
        self.user32.CloseClipboard.assert_called_once()
        self.kernel32.GlobalFree.assert_called_once_with(self.handle)


if __name__ == "__main__":
    unittest.main()
