from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

from src.hotkey import HotkeyManager
from src.recording_cues import RecordingCuePlayer


class RecordingCueTests(unittest.TestCase):
    @patch("src.recording_cues._output_id", return_value="speakers")
    @patch("src.recording_cues.C.WinDLL")
    @patch("src.recording_cues._AudioVoice")
    @patch("src.recording_cues._play_fallback")
    def test_hold_start_and_stop_use_fallback_if_xaudio_loses_the_device(self, fallback, factory, dll, output):
        dll.return_value.CoInitializeEx.return_value = 0
        voice = factory.return_value
        voice.stalled.return_value = False
        voice.play.side_effect = OSError("device removed")
        player = RecordingCuePlayer(lambda: True, lambda: 0.75, lambda: False)
        manager = HotkeyManager(lambda: player.set_recording(True), lambda: player.set_recording(False))
        try:
            for name, code, event_type in (
                ("left ctrl", 29, "down"), ("left windows", 91, "down"),
                ("left windows", 91, "up"), ("left ctrl", 29, "up"),
            ):
                manager._handle_event(SimpleNamespace(name=name, scan_code=code, event_type=event_type))
        finally:
            player.close()
        voice.play.assert_called_once_with(True, 0.75)
        self.assertEqual(fallback.call_args_list, [call(True, 0.75), call(False, 0.75)])

    @patch("src.recording_cues._output_id", side_effect=["speakers", "headphones", "headphones", "headphones"])
    @patch("src.recording_cues.C.WinDLL")
    @patch("src.recording_cues._AudioVoice")
    @patch("src.recording_cues._play_fallback")
    def test_reopens_xaudio_after_default_output_changes(self, fallback, factory, dll, output):
        dll.return_value.CoInitializeEx.return_value = 0
        old, new = Mock(), Mock()
        old.stalled.return_value = new.stalled.return_value = False
        factory.side_effect = [old, new]
        player = RecordingCuePlayer(lambda: True, lambda: 1, lambda: False)
        try:
            player.set_recording(True)
            player.set_recording(False)
        finally:
            player.close()
        old.play.assert_not_called()
        old.close.assert_called_once_with()
        fallback.assert_called_once_with(True, 1)
        new.play.assert_called_once_with(False, 1)
