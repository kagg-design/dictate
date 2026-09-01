import ctypes
import unittest
from unittest.mock import Mock, patch

from src import inserter


class FakeSendInput:
    def __init__(self):
        self.argtypes = None
        self.restype = None
        self.scan_codes = []

    def __call__(self, count, events, struct_size):
        self.scan_codes.extend(events[index].ki.wScan for index in range(count))
        return count


class TextInserterTests(unittest.TestCase):
    def test_input_structure_matches_native_windows_size(self):
        expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28
        self.assertEqual(ctypes.sizeof(inserter.INPUT), expected_size)

    def test_paste_text_uses_direct_insertion(self):
        with patch.object(inserter.sys, "platform", "win32"), patch.object(
            inserter, "_send_unicode_text"
        ) as send_unicode:
            inserter.paste_text("Привет")

        send_unicode.assert_called_once_with("Привет")

    def test_utf16_surrogate_pairs_are_sent_as_key_down_up_packets(self):
        fake_send_input = FakeSendInput()
        fake_user32 = Mock()
        fake_user32.SendInput = fake_send_input

        with patch.object(inserter.ctypes, "WinDLL", return_value=fake_user32):
            inserter._send_unicode_text("A😀")

        self.assertEqual(
            fake_send_input.scan_codes,
            [0x0041, 0x0041, 0xD83D, 0xD83D, 0xDE00, 0xDE00],
        )


if __name__ == "__main__":
    unittest.main()
