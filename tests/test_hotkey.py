import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.hotkey import HotkeyManager


def key_event(name, event_type, scan_code):
    return SimpleNamespace(name=name, event_type=event_type, scan_code=scan_code)


class HotkeyManagerTests(unittest.TestCase):
    def setUp(self):
        keyboard_patcher = patch("src.hotkey.keyboard")
        self.addCleanup(keyboard_patcher.stop)
        self.keyboard = keyboard_patcher.start()
        self.on_start = Mock()
        self.on_stop = Mock()
        self.manager = HotkeyManager(self.on_start, self.on_stop)

    def test_repeated_chords_stop_on_first_modifier_release(self):
        for cycle in range(3):
            self.manager._on_key_event(key_event("left ctrl", "down", 29))
            self.manager._on_key_event(key_event("left windows", "down", 91))
            self.assertTrue(self.manager.is_active)

            self.manager._on_key_event(key_event("left windows", "up", 91))
            self.assertFalse(self.manager.is_active)
            self.manager._on_key_event(key_event("left ctrl", "up", 29))

            self.assertEqual(self.on_start.call_count, cycle + 1)
            self.assertEqual(self.on_stop.call_count, cycle + 1)

        self.assertFalse(self.manager._pressed_ctrl_keys)
        self.assertFalse(self.manager._pressed_win_keys)

    def test_ctrl_v_does_not_trigger_after_dictation(self):
        self.manager._on_key_event(key_event("left ctrl", "down", 29))
        self.manager._on_key_event(key_event("left windows", "down", 91))
        self.manager._on_key_event(key_event("left ctrl", "up", 29))
        self.manager._on_key_event(key_event("left windows", "up", 91))

        self.manager._on_key_event(key_event("left ctrl", "down", 29))
        self.manager._on_key_event(key_event("v", "down", 47))
        self.manager._on_key_event(key_event("v", "up", 47))
        self.manager._on_key_event(key_event("left ctrl", "up", 29))

        self.on_start.assert_called_once_with()
        self.on_stop.assert_called_once_with()

    def test_releasing_one_of_two_ctrl_keys_keeps_chord_active(self):
        self.manager._on_key_event(key_event("left ctrl", "down", 29))
        self.manager._on_key_event(key_event("right ctrl", "down", 29))
        self.manager._on_key_event(key_event("left windows", "down", 91))
        self.manager._on_key_event(key_event("left ctrl", "up", 29))

        self.assertTrue(self.manager.is_active)
        self.on_stop.assert_not_called()

        self.manager._on_key_event(key_event("right ctrl", "up", 29))
        self.assertFalse(self.manager.is_active)
        self.on_stop.assert_called_once_with()

    def test_pause_clears_ignored_key_state(self):
        self.manager._on_key_event(key_event("left ctrl", "down", 29))
        self.manager._on_key_event(key_event("left windows", "down", 91))

        self.manager.set_paused(True)
        self.manager._on_key_event(key_event("left ctrl", "up", 29))
        self.manager._on_key_event(key_event("left windows", "up", 91))
        self.manager.set_paused(False)

        self.assertFalse(self.manager.is_active)
        self.assertFalse(self.manager._pressed_ctrl_keys)
        self.assertFalse(self.manager._pressed_win_keys)
        self.on_stop.assert_called_once_with()

    def test_forced_cancel_unblocks_windows_key(self):
        self.manager._on_key_event(key_event("left ctrl", "down", 29))
        self.manager._on_key_event(key_event("left windows", "down", 91))

        self.manager.cancel_active()

        self.assertFalse(self.manager.is_active)
        self.assertFalse(self.manager.win_blocked)
        self.assertFalse(self.manager._pressed_ctrl_keys)
        self.assertFalse(self.manager._pressed_win_keys)
        self.keyboard.unblock_key.assert_any_call("left windows")
        self.keyboard.unblock_key.assert_any_call("right windows")


if __name__ == "__main__":
    unittest.main()
