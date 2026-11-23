import unittest
from threading import Event, Thread
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
        self.on_copy = Mock()
        self.on_cancel = Mock()
        self.manager = HotkeyManager(self.on_start, self.on_stop, on_cancel=self.on_cancel, on_copy=self.on_copy)

    def dispatch(self, event):
        return self.manager._handle_event(event)

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

    def test_ctrl_release_allows_win_hook_to_finish_before_synthetic_release(self):
        self.dispatch(key_event("left windows", "down", 91))
        self.dispatch(key_event("left ctrl", "down", 29))
        finished = Event()
        hook_progress = []

        def win_key_up():
            self.dispatch(key_event("left windows", "up", 91))
            finished.set()

        win_hook = Thread(target=win_key_up, daemon=True)

        def inject_ctrl_down(_):
            # Windows may wait for the pending physical hook to return
            # before the injected key press can complete.
            win_hook.start()
            hook_progress.append(finished.wait(timeout=1))

        self.keyboard.send.side_effect = lambda keys, **kwargs: inject_ctrl_down(keys) if keys == "ctrl" else None
        self.dispatch(key_event("left ctrl", "up", 29))
        win_hook.join(timeout=1)

        self.assertEqual(hook_progress, [True])
        self.assertFalse(self.manager._pressed)
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

        suppress_up = self.dispatch(key_event("left windows", "up", 91))
        self.assertFalse(self.manager.is_active)
        self.dispatch(key_event("left ctrl", "up", 29))

        self.dispatch(key_event("left ctrl", "down", 29))
        self.dispatch(key_event("left ctrl", "up", 29))

        self.assertFalse(suppress_down)
        self.assertFalse(suppress_up)
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
