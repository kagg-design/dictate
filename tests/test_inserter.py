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
        call_order = []
        with (
            patch.object(inserter.sys, "platform", "win32"),
            patch.object(
                inserter,
                "_wait_for_modifiers_released",
                side_effect=lambda: call_order.append("wait"),
            ) as wait_for_release,
            patch.object(
                inserter,
                "_send_unicode_text",
                side_effect=lambda text: call_order.append("send"),
            ) as send_unicode,
        ):
            inserter.paste_text("Привет")

        wait_for_release.assert_called_once_with()
        send_unicode.assert_called_once_with("Привет ")
        self.assertEqual(call_order, ["wait", "send"])

    def test_consecutive_dictations_have_a_single_space_between_them(self):
        inserted = []
        with (
            patch.object(inserter.sys, "platform", "win32"),
            patch.object(inserter, "_wait_for_modifiers_released"),
            patch.object(
                inserter, "_send_unicode_text", side_effect=inserted.append
            ),
        ):
            inserter.paste_text("Что будет")
            inserter.paste_text("Ага")

        self.assertEqual("".join(inserted), "Что будет Ага ")

    def test_existing_trailing_whitespace_is_not_duplicated(self):
        with (
            patch.object(inserter.sys, "platform", "win32"),
            patch.object(inserter, "_wait_for_modifiers_released"),
            patch.object(inserter, "_send_unicode_text") as send_unicode,
        ):
            inserter.paste_text("Уже есть пробел ")

        send_unicode.assert_called_once_with("Уже есть пробел ")

    def test_modifier_wait_requires_stable_released_state(self):
        fake_get_state = Mock()
        fake_user32 = Mock()
        fake_user32.GetAsyncKeyState = fake_get_state
        modifier_states = iter(
            [
                ("Ctrl",),
                (),
                ("Ctrl",),
                (),
                (),
                (),
            ]
        )

        with (
            patch.object(inserter.ctypes, "WinDLL", return_value=fake_user32),
            patch.object(
                inserter,
                "_pressed_modifier_names",
                side_effect=lambda user32: next(modifier_states),
            ) as pressed_modifiers,
            patch.object(inserter.time, "sleep") as sleep,
        ):
            inserter._wait_for_modifiers_released()

        self.assertEqual(pressed_modifiers.call_count, 6)
        self.assertEqual(sleep.call_count, 5)

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
