import unittest
from unittest.mock import Mock, patch

from src.background_audio import BackgroundAudio


class BackgroundAudioTests(unittest.TestCase):
    def test_restores_lowered_volume_and_leaves_later_manual_changes(self):
        audio = BackgroundAudio()
        audio._sessions = [("first", 0.8, 0.16), ("second", 0.5, 0.1)]
        audio._get = Mock(side_effect=[0.160000011, 0.6])
        audio._set = Mock()
        with patch("src.background_audio._release") as release:
            audio.restore()
        audio._set.assert_called_once_with("first", 0.8)
        self.assertEqual([call.args[0] for call in release.call_args_list], ["first", "second"])
        self.assertEqual(audio._sessions, [])
