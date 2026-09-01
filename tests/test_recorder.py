import unittest
from unittest.mock import Mock, patch

import numpy as np

from src.recorder import AudioRecorder


class FakeInputStream:
    def __init__(self):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        pass


def block(value, samples=2):
    return np.full((samples, 1), value, dtype=np.int16)


class AudioRecorderTests(unittest.TestCase):
    def setUp(self):
        self.stream = FakeInputStream()
        stream_patcher = patch("src.recorder.sd.InputStream", return_value=self.stream)
        self.addCleanup(stream_patcher.stop)
        self.input_stream = stream_patcher.start()

    def make_recorder(self, **overrides):
        settings = {
            "sample_rate": 10,
            "max_duration": 10,
            "min_duration": 0.3,
            "ring_buffer_duration": 0.5,
        }
        settings.update(overrides)
        recorder = AudioRecorder(**settings)
        self.addCleanup(recorder.cleanup)
        return recorder

    def feed(self, recorder, value, samples=2):
        recorder._callback(block(value, samples), samples, None, None)

    def test_capture_contains_pre_roll_once_and_active_audio_once(self):
        recorder = self.make_recorder()
        self.feed(recorder, 1)
        self.feed(recorder, 2)
        self.feed(recorder, 3)

        on_audio_start = Mock()
        recorder.start(on_audio_start=on_audio_start)
        self.feed(recorder, 4)
        self.feed(recorder, 5)
        audio = recorder.stop()

        np.testing.assert_array_equal(
            audio.flatten(), np.array([2, 2, 3, 3, 4, 4, 5, 5], dtype=np.int16)
        )
        on_audio_start.assert_called_once_with()
        self.assertTrue(recorder.stream.active)

    def test_minimum_duration_counts_only_active_audio(self):
        recorder = self.make_recorder(min_duration=0.3)
        self.feed(recorder, 1)
        self.feed(recorder, 2)
        recorder.start()
        self.feed(recorder, 3, samples=2)

        self.assertIsNone(recorder.stop())

    def test_safety_limit_can_finalize_persistent_stream_session(self):
        recorder = self.make_recorder(max_duration=0.4, min_duration=0)
        self.feed(recorder, 1)
        captured = []

        recorder.start(on_limit_reached=lambda: captured.append(recorder.stop()))
        self.feed(recorder, 2)
        self.feed(recorder, 3)

        self.assertEqual(len(captured), 1)
        self.assertIsNotNone(captured[0])
        np.testing.assert_array_equal(
            captured[0].flatten(), np.array([1, 1, 2, 2, 3, 3], dtype=np.int16)
        )
        self.assertFalse(recorder.is_recording)
        self.assertTrue(recorder.stream.active)


if __name__ == "__main__":
    unittest.main()
