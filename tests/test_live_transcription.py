import unittest
from unittest.mock import Mock

import numpy as np

from src.fluid_mini import FluidMiniTranscriber
from src.live_transcription import LiveTranscription, RollingTranscription


class RollingTranscriptionTests(unittest.TestCase):
    def test_empty_mini_decode_at_a_pause_keeps_audio_for_the_final_pass(self):
        engine = Mock(transcribe_chunk=Mock(return_value=""), clean_text=FluidMiniTranscriber.clean_text)
        ending = "Спасибо. Спасибо. Спасибо."
        engine.transcribe.return_value = ending
        audio = np.full(24000, 1, dtype=np.int16)
        live = LiveTranscription(16000, rolling=True)
        live._find_pause = Mock(return_value=20000)
        live.feed([audio], engine)

        self.assertEqual(live.finish(audio, engine), ending)
        np.testing.assert_array_equal(engine.transcribe.call_args.args[0], audio)

    def test_final_decode_has_a_silent_margin_after_the_last_sample(self):
        engine = Mock(clean_text=str.strip)
        engine.transcribe_words.side_effect = [
            [{"w": "First", "start": 0.2, "end": 0.8}],
            [{"w": "First", "start": 0.2, "end": 0.8}, {"w": "ending.", "start": 1.9, "end": 2.1}],
        ]
        source = np.ones(2150, dtype=np.int16)
        live = RollingTranscription(1000)
        live.feed([source[:2000]], engine)

        self.assertEqual(live.finish(source, engine), "First ending.")
        final_samples = engine.transcribe_words.call_args.args[0]
        np.testing.assert_array_equal(final_samples[:len(source)], source)
        np.testing.assert_array_equal(final_samples[len(source):], np.zeros(250, dtype=np.int16))

    def test_continuous_audio_is_decoded_early_and_repeated_words_survive_overlap(self):
        words = (
            "we say yes yes yes and then we keep talking without a pause for a while "
            "as we explain how parts of the same sentence can span many windows of audio"
        ).split()
        source = np.arange(len(words) * 350, dtype=np.int16)
        decoded_lengths = []

        def decode(pcm):
            start = int(pcm[0])
            end = start + len(pcm)
            decoded_lengths.append(len(pcm))
            return [
                {"w": text, "start": (i * 350 - start) / 1000, "end": ((i + 1) * 350 - start) / 1000}
                for i, text in enumerate(words) if i * 350 >= start and (i + 1) * 350 <= end
            ]

        engine = Mock(transcribe_words=Mock(side_effect=decode), clean_text=str.strip)
        live = RollingTranscription(1000)
        live.feed([source[:450]], engine)
        live.feed([source[450:2000]], engine)
        self.assertEqual(len(decoded_lengths), 1)
        self.assertTrue(live._confirmed)

        for start in range(2000, len(source) - 100, 220):
            live.feed([source[start:min(start + 220, len(source) - 100)]], engine)

        self.assertGreater(len(decoded_lengths), 2)
        self.assertLess(max(decoded_lengths), len(source))
        self.assertEqual(live.finish(source, engine), " ".join(words))
        engine.transcribe.assert_not_called()

    def test_later_context_can_correct_the_last_provisional_word(self):
        engine = Mock(clean_text=str.strip)
        engine.transcribe_words.side_effect = [
            [
                {"w": "I", "start": 0.1, "end": 0.28},
                {"w": "recognize", "start": 0.30, "end": 1.30},
            ],
            [
                {"w": "I", "start": 0.1, "end": 0.28},
                {"w": "recognized", "start": 0.30, "end": 1.43},
                {"w": "it.", "start": 1.52, "end": 1.83},
            ],
        ]
        live = RollingTranscription(1000)
        source = np.ones(3000, dtype=np.int16)
        live.feed([source[:2000]], engine)
        live.feed([source[2000:]], engine)

        self.assertEqual(live.finish(source, engine), "I recognized it.")
        self.assertEqual(engine.transcribe_words.call_count, 2)
        engine.transcribe.assert_not_called()

    def test_an_unreliable_final_join_recovers_from_the_full_recording(self):
        engine = Mock()
        engine.transcribe_words.side_effect = [
            [{"w": "First", "start": 4.1, "end": 5.0}],
            [{"w": "Different", "start": 2.1, "end": 3.0}],
        ]
        engine.transcribe.return_value = "Recovered text."
        live = RollingTranscription(1000)
        source = np.ones(6900, dtype=np.int16)
        live.feed([source[:6600]], engine)

        self.assertEqual(live.finish(source, engine), "Recovered text.")
        engine.transcribe.assert_called_once_with(source)

    def test_rolling_join_can_recover_while_audio_is_still_arriving(self):
        engine = Mock(clean_text=str.strip)
        engine.transcribe_words.side_effect = [
            [{"w": "First", "start": 4.1, "end": 5.0}],
            [{"w": "Different", "start": 2.1, "end": 3.0}],
            [
                {"w": "First", "start": 4.1, "end": 5.0},
                {"w": "sentence.", "start": 7.0, "end": 8.0},
            ],
        ]
        live = RollingTranscription(1000)
        source = np.ones(9800, dtype=np.int16)
        live.feed([source[:6600]], engine)
        live.feed([source[6600:]], engine, lambda: source)

        np.testing.assert_array_equal(engine.transcribe_words.call_args.args[0], source)
        self.assertEqual(live.finish(source, engine), "First sentence.")
        engine.transcribe.assert_not_called()
