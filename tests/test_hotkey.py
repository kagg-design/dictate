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

    def dispatch(self, event):
        if event.name in self.manager.WIN_NAMES:
            return self.manager._on_win_key_event(event)
        return self.manager._on_ctrl_key_event(event)

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

        self.assertFalse(self.manager._pressed_ctrl_keys)
        self.assertFalse(self.manager._pressed_win_keys)

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
        self.assertFalse(self.manager._pressed_ctrl_keys)
        self.assertFalse(self.manager._pressed_win_keys)
        self.on_stop.assert_called_once_with()

    def test_forced_cancel_unblocks_windows_key(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left windows", "down", 91))

        self.manager.cancel_active()

        self.assertFalse(self.manager.is_active)
        self.assertFalse(self.manager.win_blocked)
        self.assertFalse(self.manager._pressed_ctrl_keys)
        self.assertFalse(self.manager._pressed_win_keys)
        self.keyboard.release.assert_any_call("left windows")
        self.keyboard.release.assert_any_call("right windows")

    def test_win_released_first_does_not_leave_phantom_chord(self):
        self.dispatch(key_event("left ctrl", "down", 29))
        suppress_down = self.dispatch(key_event("left windows", "down", 91))

        suppress_up = self.dispatch(key_event("left windows", "up", 91))
        self.assertFalse(self.manager.is_active)
        self.dispatch(key_event("left ctrl", "up", 29))

        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left ctrl", "up", 29))

        self.assertFalse(suppress_down)
        self.assertFalse(suppress_up)
        self.on_start.assert_called_once_with()
        self.on_stop.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
