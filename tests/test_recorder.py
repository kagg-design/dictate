import unittest
from threading import Event
from unittest.mock import Mock, patch

import numpy as np
import sounddevice as sd

from src.recorder import AudioRecorder


class FakeInputStream:
    def __init__(self):
        self.active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def close(self):
        self.active = False


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
            audio.flatten(), np.array([1, 2, 2, 3, 3, 4, 4, 5, 5], dtype=np.int16)
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

    def test_zero_pre_roll_opens_input_only_for_the_capture(self):
        recorder = self.make_recorder(ring_buffer_duration=0)
        self.input_stream.assert_not_called()
        self.assertTrue(recorder.microphone_ready)
        self.feed(recorder, 1)
        self.assertEqual(recorder.current_ring_samples, 0)

        recorder.start()
        callback = self.input_stream.call_args.kwargs["callback"]
        callback(block(2, 4), 4, None, None)
        np.testing.assert_array_equal(recorder.stop().flatten(), [2, 2, 2, 2])
        self.assertFalse(self.stream.active)
        self.assertIsNone(recorder.stream)
        self.assertTrue(recorder.microphone_ready)
        self.assertEqual(recorder.current_ring_samples, 0)
        self.assertEqual(recorder.microphone_level(), 0)

    def test_resizing_pre_roll_keeps_only_the_latest_samples_and_releases_at_zero(self):
        recorder = self.make_recorder()
        recorder._callback(np.arange(1, 9, dtype=np.int16).reshape(-1, 1), 8, None, None)
        np.testing.assert_array_equal(np.concatenate(recorder.ring_buffer).flatten(), [4, 5, 6, 7, 8])

        recorder.set_pre_roll(0.2)
        self.input_stream.assert_called_once()
        np.testing.assert_array_equal(np.concatenate(recorder.ring_buffer).flatten(), [7, 8])
        recorder.set_pre_roll(0)
        self.assertFalse(self.stream.active)
        self.assertFalse(recorder.ring_buffer)
        self.assertIsNone(recorder.stream)

        recorder.set_pre_roll(0.5)
        self.assertTrue(recorder.stream.active)
        self.assertFalse(recorder.ring_buffer)

    def test_pre_roll_change_preserves_the_previous_input_when_saving_fails(self):
        recorder = self.make_recorder()
        self.feed(recorder, 2)
        with self.assertRaisesRegex(OSError, "disk unavailable"):
            recorder.set_pre_roll(0, persist=Mock(side_effect=OSError("disk unavailable")))
        self.assertEqual(recorder.ring_buffer_duration, 0.5)
        self.assertEqual(recorder.current_ring_samples, 2)
        self.assertTrue(self.stream.active)

    def test_zero_pre_roll_safety_limit_can_close_the_input(self):
        recorder = self.make_recorder(ring_buffer_duration=0, max_duration=0.4, min_duration=0)
        stopped = Event()

        def finish():
            recorder.stop()
            stopped.set()

        recorder.start(on_limit_reached=finish)
        self.feed(recorder, 2, samples=4)
        self.assertTrue(stopped.wait(timeout=2))
        self.assertIsNone(recorder.stream)
        self.assertFalse(self.stream.active)

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

    def test_diagnostics_measure_audio_callback_age_without_querying_driver(self):
        recorder = self.make_recorder()
        recorder.stream = Mock()
        with patch("src.recorder.time.monotonic", return_value=100):
            self.feed(recorder, 1)
        with patch("src.recorder.time.monotonic", return_value=107):
            state = recorder.diagnostic_state()

        self.assertEqual(state["last_audio_callback_age_s"], 7)
        self.assertTrue(state["audio_stream_present"])
        self.assertFalse(state["recording"])
        recorder.stream.assert_not_called()
        self.assertEqual(recorder.stream.mock_calls, [])

    def test_switching_input_clears_pre_roll_and_ignores_old_callbacks(self):
        recorder = self.make_recorder()
        old_callback = self.input_stream.call_args.kwargs["callback"]
        old_callback(block(8), 2, None, None)
        self.input_stream.return_value = FakeInputStream()
        with patch("src.recorder.list_microphones", return_value={"USB mic": 6}):
            recorder.select_microphone("USB mic")
        new_callback = self.input_stream.call_args.kwargs["callback"]

        old_callback(block(8), 2, None, None)
        new_callback(block(9), 2, None, None)
        recorder.start()
        new_callback(block(10, samples=4), 4, None, None)
        np.testing.assert_array_equal(recorder.stop().flatten(), [9, 9, 10, 10, 10, 10])
        self.assertEqual(self.input_stream.call_args.kwargs["device"], 6)
        self.assertEqual(recorder.microphone, "USB mic")

    def test_stalled_input_reopens_without_losing_the_capture(self):
        recorder = self.make_recorder()
        old_callback = self.input_stream.call_args.kwargs["callback"]
        with patch("src.recorder.time.monotonic", return_value=100):
            recorder.start()
            old_callback(block(1), 2, None, None)
        self.input_stream.return_value = FakeInputStream()

        with (
            patch("src.recorder.time.monotonic", return_value=101),
            patch("src.recorder.threading.Thread") as recovery,
        ):
            recorder.check_capture_input()
            recorder.check_capture_input()
            recovery.assert_called_once()
            recovery.call_args.kwargs["target"]()
            new_callback = self.input_stream.call_args.kwargs["callback"]
            old_callback(block(3), 2, None, None)
            new_callback(block(2, samples=4), 4, None, None)
            np.testing.assert_array_equal(recorder.stop().flatten(), [1, 1, 2, 2, 2, 2])

        self.assertTrue(recorder.capture_interrupted)
        self.assertIs(recorder.stream, self.input_stream.return_value)

    def test_zero_pre_roll_closes_a_recovery_stream_if_released_while_opening(self):
        recorder = self.make_recorder(ring_buffer_duration=0)
        with patch("src.recorder.time.monotonic", return_value=100):
            recorder.start()
            self.feed(recorder, 1, samples=4)
        replacement = FakeInputStream()

        def reopen(*args):
            np.testing.assert_array_equal(recorder.stop().flatten(), [1, 1, 1, 1])
            replacement.start()
            return replacement

        with (
            patch("src.recorder.time.monotonic", return_value=101),
            patch.object(recorder, "_open_stream", side_effect=reopen),
        ):
            recorder._recover_capture_input()

        self.assertFalse(replacement.active)
        self.assertIsNone(recorder.stream)

    def test_failed_preference_write_keeps_the_current_microphone(self):
        recorder = self.make_recorder()
        replacement = Mock()
        self.input_stream.return_value = replacement
        with (
            patch("src.recorder.list_microphones", return_value={"USB mic": 6}),
            self.assertRaisesRegex(OSError, "disk unavailable"),
        ):
            recorder.select_microphone("USB mic", persist=Mock(side_effect=OSError("disk unavailable")))
        self.assertIs(recorder.stream, self.stream)
        self.assertIsNone(recorder.microphone)
        replacement.close.assert_called_once_with()

    def test_native_rate_input_is_resampled_to_the_dictation_rate(self):
        recorder = self.make_recorder(sample_rate=16000, min_duration=0.01)
        self.input_stream.side_effect = [sd.PortAudioError("Unsupported sample rate"), FakeInputStream()]
        with (
            patch("src.recorder.list_microphones", return_value={"USB mic": 6}),
            patch("src.recorder.sd.query_devices", return_value={"default_samplerate": 48000}),
        ):
            recorder.select_microphone("USB mic")
        self.assertEqual(self.input_stream.call_args.kwargs["samplerate"], 48000)
        callback = self.input_stream.call_args.kwargs["callback"]
        recorder.start()
        callback(block(1000, samples=4800), 4800, None, None)
        audio = recorder.stop()
        self.assertEqual(audio.dtype, np.int16)
        self.assertTrue(1550 <= len(audio) <= 1600)
        self.assertEqual(int(audio[100, 0]), 1000)


if __name__ == "__main__":
    unittest.main()
