import ctypes
import unittest
from unittest.mock import Mock, patch

from src import inserter


class FakeSendInput:
    def __init__(self):
        self.argtypes = None
        self.restype = None
        self.scan_codes = []
        self.packet_sizes = []

    def __call__(self, count, events, struct_size):
        self.packet_sizes.append(count)
        self.scan_codes.extend(events[index].ki.wScan for index in range(count))
        return count


class TextInserterTests(unittest.TestCase):
    def setUp(self):
        native_patcher = patch.object(inserter, "_try_insert_native_text", return_value=False)
        native_patcher.start()
        self.addCleanup(native_patcher.stop)

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

        fake_user32.GetForegroundWindow.return_value = 123
        with (
            patch.object(inserter.ctypes, "WinDLL", return_value=fake_user32),
            patch.object(inserter.time, "sleep"),
        ):
            inserter._send_unicode_text("A😀")

        self.assertEqual(
            fake_send_input.scan_codes,
            [0x0041, 0x0041, 0xD83D, 0xD83D, 0xDE00, 0xDE00],
        )
        self.assertEqual(fake_send_input.packet_sizes, [2, 4])

    def test_long_text_is_paced_without_dropping_or_repeating_characters(self):
        text = "Это проверка. English 123! " * 60
        fake_send_input = FakeSendInput()
        fake_user32 = Mock()
        fake_user32.SendInput = fake_send_input
        fake_user32.GetForegroundWindow.return_value = 123

        with (
            patch.object(inserter.ctypes, "WinDLL", return_value=fake_user32),
            patch.object(inserter.time, "sleep") as sleep,
        ):
            inserter._send_unicode_text(text)

        decoded = b"".join(
            scan_code.to_bytes(2, "little")
            for scan_code in fake_send_input.scan_codes[::2]
        ).decode("utf-16-le")
        self.assertEqual(decoded, text)
        self.assertEqual(fake_send_input.packet_sizes, [2] * len(text))
        self.assertEqual(sleep.call_count, len(text) - 1)
        sleep.assert_called_with(inserter.UNICODE_CHARACTER_INTERVAL_SECONDS)

    def test_insertion_stops_when_the_foreground_window_changes(self):
        fake_send_input = FakeSendInput()
        fake_user32 = Mock()
        fake_user32.SendInput = fake_send_input
        fake_user32.GetForegroundWindow.side_effect = [123, 123, 456]

        with (
            patch.object(inserter.ctypes, "WinDLL", return_value=fake_user32),
            patch.object(inserter.time, "sleep"),
        ):
            with self.assertRaisesRegex(RuntimeError, "after 1/3 characters"):
                inserter._send_unicode_text("ABC")

        self.assertEqual(fake_send_input.scan_codes, [ord("A")] * 2)

    def test_partial_send_is_reported_without_retrying_the_text(self):
        fake_user32 = Mock()
        fake_user32.GetForegroundWindow.return_value = 123
        fake_user32.SendInput.return_value = 1

        with patch.object(inserter.ctypes, "WinDLL", return_value=fake_user32):
            with self.assertRaisesRegex(OSError, "accepted 1/2 events"):
                inserter._send_unicode_text("ABC")

        fake_user32.SendInput.assert_called_once()

    def test_paste_text_reports_insertion_failure_to_the_worker(self):
        with (
            patch.object(inserter.sys, "platform", "win32"),
            patch.object(inserter, "_wait_for_modifiers_released"),
            patch.object(inserter, "_send_unicode_text", side_effect=OSError("blocked")),
        ):
            with self.assertRaisesRegex(OSError, "blocked"):
                inserter.paste_text("Привет")


class NativeTextInsertionTests(unittest.TestCase):
    def make_user32(self, control_class):
        user32 = Mock()

        def get_focus(thread, target):
            target._obj.hwndFocus = 123
            return 1

        def get_class(window, buffer, size):
            buffer.value = control_class
            return len(control_class)

        user32.GetGUIThreadInfo.side_effect = get_focus
        user32.GetClassNameW.side_effect = get_class
        user32.SendMessageTimeoutW.return_value = 1
        return user32

    def test_native_editor_receives_entire_text_in_one_undoable_message(self):
        user32 = self.make_user32("RichEditD2DPT")
        delivered = []

        def receive(window, message, wparam, lparam, flags, timeout, result):
            self.assertEqual(window, 123)
            self.assertEqual(message, inserter.EM_REPLACESEL)
            self.assertEqual(wparam, 1)
            delivered.append(ctypes.wstring_at(lparam))
            return 1

        user32.SendMessageTimeoutW.side_effect = receive
        text = "Русский текст, English и emoji 😀. " * 50
        with patch.object(inserter.ctypes, "WinDLL", return_value=user32):
            self.assertTrue(inserter._try_insert_native_text(text))

        self.assertEqual(delivered, [text])
        user32.SendMessageTimeoutW.assert_called_once()
        user32.SendInput.assert_not_called()

    def test_chromium_receives_literal_utf16_messages(self):
        for control_class in ("Chrome_RenderWidgetHostHWND", "Chrome_WidgetWin_1"):
            with self.subTest(control_class=control_class):
                user32 = self.make_user32(control_class)
                with patch.object(inserter.ctypes, "WinDLL", return_value=user32):
                    self.assertTrue(inserter._try_insert_native_text("Я😀"))

                self.assertEqual(
                    [call.args[2] for call in user32.SendMessageTimeoutW.call_args_list],
                    [0x042F, 0xD83D, 0xDE00],
                )
                self.assertTrue(all(
                    call.args[1] == inserter.WM_CHAR
                    for call in user32.SendMessageTimeoutW.call_args_list
                ))
                user32.SendInput.assert_not_called()

    def test_unknown_control_falls_back_without_sending_a_text_message(self):
        user32 = self.make_user32("CustomEditor")
        with patch.object(inserter.ctypes, "WinDLL", return_value=user32):
            self.assertFalse(inserter._try_insert_native_text("ABC"))

        user32.SendMessageTimeoutW.assert_not_called()

    def test_chromium_stops_when_focus_changes(self):
        user32 = self.make_user32("Chrome_RenderWidgetHostHWND")
        handles = iter([123, 123, 456])

        def get_focus(thread, target):
            target._obj.hwndFocus = next(handles)
            return 1

        user32.GetGUIThreadInfo.side_effect = get_focus
        with patch.object(inserter.ctypes, "WinDLL", return_value=user32):
            with self.assertRaisesRegex(RuntimeError, "after 1 UTF-16"):
                inserter._try_insert_native_text("ABC")

        user32.SendMessageTimeoutW.assert_called_once()

    def test_timeout_does_not_retry_with_keyboard_input(self):
        user32 = self.make_user32("RichEditD2DPT")
        user32.SendMessageTimeoutW.return_value = 0
        with (
            patch.object(inserter.ctypes, "WinDLL", return_value=user32),
            patch.object(inserter, "_wait_for_modifiers_released"),
            patch.object(inserter, "_send_unicode_text") as keyboard_input,
        ):
            with self.assertRaisesRegex(OSError, "failed or timed out"):
                inserter.paste_text("Привет")

        keyboard_input.assert_not_called()


if __name__ == "__main__":
    unittest.main()
