import unittest
from queue import SimpleQueue
from threading import Event, Thread
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.hotkey import HotkeyManager
from src.shortcuts import Shortcut


def key_event(name, event_type, scan_code):
    return SimpleNamespace(name=name, event_type=event_type, scan_code=scan_code)


class HotkeyManagerTests(unittest.TestCase):
    def setUp(self):
        keyboard_patcher = patch("src.hotkey.keyboard")
        self.addCleanup(keyboard_patcher.stop)
        self.keyboard = keyboard_patcher.start()
        mask_patcher = patch("src.hotkey.mask_start_menu")
        self.addCleanup(mask_patcher.stop)
        self.mask_menu = mask_patcher.start()
        self.on_start = Mock()
        self.on_stop = Mock()
        self.on_copy = Mock()
        self.on_insert = Mock()
        self.on_cancel = Mock()
        self.manager = HotkeyManager(
            self.on_start, self.on_stop, on_cancel=self.on_cancel, on_copy=self.on_copy, on_insert=self.on_insert,
        )

    def dispatch(self, event):
        return self.manager._handle_event(event)

    def test_insert_last_suppresses_only_the_configured_function_key_and_does_not_repeat(self):
        self.manager.configure("ctrl+win", "hold", True, insert_last_shortcut="ctrl+shift+f9")
        self.assertTrue(self.dispatch(key_event("left ctrl", "down", 29)))
        self.assertTrue(self.dispatch(key_event("left shift", "down", 42)))
        self.assertTrue(self.dispatch(key_event("f10", "down", 68)))
        self.assertTrue(self.dispatch(key_event("f10", "up", 68)))
        self.assertFalse(self.dispatch(key_event("f9", "down", 67)))
        self.assertFalse(self.dispatch(key_event("f9", "down", 67)))
        self.assertTrue(self.dispatch(key_event("left shift", "up", 42)))
        self.assertTrue(self.dispatch(key_event("left ctrl", "up", 29)))
        self.assertFalse(self.dispatch(key_event("f9", "up", 67)))
        self.on_insert.assert_called_once_with()
        self.on_start.assert_not_called()
        self.on_copy.assert_not_called()

        self.manager.configure("ctrl+win", "hold", True, insert_last_shortcut="off")
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left shift", "down", 42))
        self.assertTrue(self.dispatch(key_event("f9", "down", 67)))
        self.assertTrue(self.dispatch(key_event("f9", "up", 67)))
        self.on_insert.assert_called_once_with()

    def test_capture_uses_the_longest_chord_and_works_after_the_layout_changes(self):
        captured = SimpleQueue()
        self.assertTrue(self.manager.begin_capture(captured))
        for name, code in (("left ctrl", 29), ("left alt", 56), ("ф", 30)):
            self.dispatch(key_event(name, "down", code))
        for name, code in (("left alt", 56), ("ф", 30), ("left ctrl", 29)):
            self.dispatch(key_event(name, "up", code))
        shortcut, error = captured.get_nowait()
        self.assertFalse(error)
        self.assertEqual(shortcut.label, "Ctrl+Alt+A")
        self.on_start.assert_not_called()
        self.on_insert.assert_not_called()
        self.manager.configure(Shortcut.from_setting(shortcut.to_setting()), "hold", True)

        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left alt", "down", 56))
        self.assertFalse(self.dispatch(key_event("a", "down", 30)))
        self.assertFalse(self.dispatch(key_event("a", "up", 30)))
        self.on_start.assert_called_once_with()
        self.on_stop.assert_called_once_with()

    def test_saved_cyrillic_label_displays_the_latin_letter_for_the_same_key(self):
        saved = {"modifiers": ["ctrl"], "scan_code": 0x16, "keypad": False, "label": "Г"}
        self.manager.configure(saved, "hold", True)
        self.assertEqual(self.manager.shortcut.label, "Ctrl+U")
        self.assertEqual(self.manager.shortcut.to_setting()["label"], "U")
        self.dispatch(key_event("left ctrl", "down", 29))
        self.assertFalse(self.dispatch(key_event("г", "down", 0x16)))
        self.assertFalse(self.dispatch(key_event("г", "up", 0x16)))
        self.on_start.assert_called_once_with()
        self.on_stop.assert_called_once_with()

    def test_modifier_only_insert_fires_on_release_and_ignores_a_longer_chord(self):
        self.manager.configure("ctrl+win", "hold", True, insert_last_shortcut="ctrl+shift")

        def press_modifiers():
            self.dispatch(key_event("left ctrl", "down", 29))
            self.dispatch(key_event("left shift", "down", 42))

        def release_modifiers():
            self.dispatch(key_event("left shift", "up", 42))
            self.dispatch(key_event("left ctrl", "up", 29))

        press_modifiers()
        self.on_insert.assert_not_called()
        release_modifiers()
        self.on_insert.assert_called_once_with()
        press_modifiers()
        self.assertTrue(self.dispatch(key_event("a", "down", 30)))
        self.assertTrue(self.dispatch(key_event("a", "up", 30)))
        release_modifiers()
        self.on_insert.assert_called_once_with()

    def test_repeated_chords_stop_on_first_modifier_release(self):
        for cycle in range(3):
            self.dispatch(key_event("left ctrl", "down", 29))
            self.dispatch(key_event("left windows", "down", 91))
            self.assertTrue(self.manager.is_active)

            self.dispatch(key_event("left windows", "up", 91))
            self.assertFalse(self.manager.is_active)
            self.dispatch(key_event("left ctrl", "up", 29))

            self.assertEqual(self.on_start.call_count, cycle + 1)
            self.assertEqual(self.on_stop.call_count, cycle + 1)

        self.assertFalse(self.manager._pressed)

    def test_ctrl_v_does_not_trigger_after_dictation(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))
        self.dispatch(key_event("left ctrl", "up", 29))
        self.dispatch(key_event("left windows", "up", 91))

        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("v", "down", 47))
        self.dispatch(key_event("v", "up", 47))
        self.dispatch(key_event("left ctrl", "up", 29))

        self.on_start.assert_called_once_with()
        self.on_stop.assert_called_once_with()

    def test_physical_release_is_processed_even_during_menu_masking(self):
        self.assertTrue(self.dispatch(key_event("left windows", "down", 91)))
        finished = Event()
        hook_progress = []

        def release_keys():
            self.dispatch(key_event("left ctrl", "up", 29))
            hook_progress.append(self.dispatch(key_event("left windows", "up", 91)))
            finished.set()

        key_release = Thread(target=release_keys, daemon=True)

        def mask_menu():
            key_release.start()
            hook_progress.append(finished.wait(timeout=1))

        self.mask_menu.side_effect = mask_menu
        self.dispatch(key_event("left ctrl", "down", 29))
        key_release.join(timeout=1)

        self.assertEqual(hook_progress, [True, True])
        self.assertFalse(self.manager._pressed)
        self.on_stop.assert_called_once_with()
        self.keyboard.send.assert_not_called()

        # Ctrl+Shift changes the layout and must not open another capture.
        for event in (
            key_event("left ctrl", "down", 29), key_event("left shift", "down", 42),
            key_event("left shift", "up", 42), key_event("left ctrl", "up", 29),
        ):
            self.assertTrue(self.dispatch(event))
        self.on_start.assert_called_once_with()

    def test_forwarded_win_keeps_its_key_up_in_either_release_order(self):
        for first, second in (
            (("left ctrl", 29), ("left windows", 91)),
            (("right windows", 92), ("left ctrl", 29)),
        ):
            win, scan_code = next(key for key in (first, second) if "windows" in key[0])
            with self.subTest(release_first=first):
                self.assertTrue(self.dispatch(key_event(win, "down", scan_code)))
                self.assertTrue(self.dispatch(key_event("left ctrl", "down", 29)))
                for name, code in (first, second):
                    self.assertTrue(self.dispatch(key_event(name, "up", code)))
                self.assertFalse(self.manager._pressed)
        self.assertEqual(self.mask_menu.call_count, 2)
        self.assertEqual(self.on_start.call_count, 2)
        self.assertEqual(self.on_stop.call_count, 2)

    def test_releasing_one_of_two_ctrl_keys_keeps_chord_active(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("right ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))
        self.dispatch(key_event("left ctrl", "up", 29))

        self.assertTrue(self.manager.is_active)
        self.on_stop.assert_not_called()

        self.dispatch(key_event("right ctrl", "up", 29))
        self.assertFalse(self.manager.is_active)
        self.on_stop.assert_called_once_with()

    def test_pause_clears_ignored_key_state(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))

        self.manager.set_paused(True)
        self.dispatch(key_event("left ctrl", "up", 29))
        self.dispatch(key_event("left windows", "up", 91))
        self.manager.set_paused(False)

        self.assertFalse(self.manager.is_active)
        self.assertFalse(self.manager._pressed)
        self.on_stop.assert_called_once_with()

    def test_forced_stop_disarms_until_released(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))

        self.manager.cancel_active()

        self.assertFalse(self.manager.is_active)
        self.assertFalse(self.dispatch(key_event("left windows", "down", 91)))
        self.assertFalse(self.dispatch(key_event("left windows", "up", 91)))
        self.dispatch(key_event("left ctrl", "up", 29))
        self.assertFalse(self.manager._pressed)
        self.on_start.assert_called_once_with()
        self.on_stop.assert_not_called()

    def test_win_released_first_does_not_leave_phantom_chord(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        suppress_down = self.dispatch(key_event("left windows", "down", 91))

        suppress_up = self.dispatch(key_event("windows", "up", 91))
        self.assertFalse(self.manager.is_active)
        self.dispatch(key_event("left ctrl", "up", 29))

        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left ctrl", "up", 29))

        self.assertFalse(suppress_down)
        self.assertFalse(suppress_up)
        self.mask_menu.assert_not_called()
        self.on_start.assert_called_once_with()
        self.on_stop.assert_called_once_with()


    def test_copy_chord_abandons_the_provisional_capture(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))
        self.assertFalse(self.dispatch(key_event("м", "down", 47)))
        self.assertFalse(self.dispatch(key_event("v", "down", 47)))
        self.dispatch(key_event("left ctrl", "up", 29))
        self.assertFalse(self.dispatch(key_event("v", "up", 47)))
        self.dispatch(key_event("left windows", "up", 91))
        self.on_copy.assert_called_once_with()
        self.on_cancel.assert_called_once_with(False)
        self.on_stop.assert_not_called()

    def test_copy_masks_a_forwarded_win_press_without_suppressing_its_release(self):
        self.manager.configure("ctrl+alt+space", "hold", True)
        self.assertTrue(self.dispatch(key_event("left ctrl", "down", 29)))
        self.assertTrue(self.dispatch(key_event("left windows", "down", 91)))
        self.assertFalse(self.dispatch(key_event("v", "down", 47)))
        self.assertFalse(self.dispatch(key_event("v", "up", 47)))
        self.assertTrue(self.dispatch(key_event("left windows", "up", 91)))
        self.assertTrue(self.dispatch(key_event("left ctrl", "up", 29)))
        self.on_copy.assert_called_once_with()
        self.on_start.assert_not_called()
        self.mask_menu.assert_called_once_with()

    def test_default_toggle_and_copy_do_not_overlap(self):
        self.manager.configure("ctrl+win", "toggle", True)
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))
        self.dispatch(key_event("left windows", "up", 91))
        self.on_start.assert_called_once()
        self.dispatch(key_event("left windows", "down", 91))
        self.dispatch(key_event("v", "down", 47))
        self.dispatch(key_event("v", "up", 47))
        self.dispatch(key_event("left windows", "up", 91))
        self.on_copy.assert_called_once()
        self.on_stop.assert_not_called()
        self.dispatch(key_event("left windows", "down", 91))
        self.dispatch(key_event("left windows", "up", 91))
        self.on_stop.assert_called_once()

    def test_space_preset_toggles_on_press_and_escape_cancels_to_history(self):
        self.manager.configure("ctrl+alt+space", "toggle", True)
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left alt", "down", 56))
        self.assertFalse(self.dispatch(key_event("space", "down", 57)))
        self.assertFalse(self.dispatch(key_event("space", "down", 57)))
        self.assertFalse(self.dispatch(key_event("space", "up", 57)))
        self.on_start.assert_called_once()
        self.assertFalse(self.dispatch(key_event("escape", "down", 1)))
        self.assertFalse(self.dispatch(key_event("escape", "up", 1)))
        self.on_cancel.assert_called_once_with(True)
        self.on_stop.assert_not_called()
        self.assertTrue(self.dispatch(key_event("escape", "down", 1)))


if __name__ == "__main__":
    unittest.main()
