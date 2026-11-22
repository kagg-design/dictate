import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.config import DEFAULT_CONFIG
from src.transcriber import WhisperTranscriber, _strip_whisper_tail


class WhisperTranscriberTests(unittest.TestCase):
    @patch("src.transcriber.WhisperModel")
    @patch("src.transcriber.ctranslate2.get_cuda_device_count", return_value=0)
    def test_falls_back_to_cpu_when_nvidia_is_unavailable(self, device_count, model):
        transcriber = WhisperTranscriber()

        transcriber.load_model()

        model.assert_called_once_with("large-v3-turbo", device="cpu", compute_type="int8")
        self.assertEqual(transcriber.device, "cpu")
        self.assertEqual(transcriber.compute_type, "int8")

    @patch("src.transcriber.WhisperModel")
    @patch("src.transcriber.ctranslate2.get_cuda_device_count", return_value=1)
    def test_uses_cuda_when_nvidia_is_available(self, device_count, model):
        transcriber = WhisperTranscriber()

        transcriber.load_model()

        model.assert_called_once_with("large-v3-turbo", device="cuda", compute_type="float16")

    def test_vad_is_enabled_by_default_and_forwarded_to_model(self):
        transcriber = WhisperTranscriber()
        transcriber.model = Mock()
        transcriber.model.transcribe.return_value = (
            [],
            SimpleNamespace(language="ru", language_probability=1.0),
        )

        result = transcriber.transcribe(np.zeros((16000, 1), dtype=np.int16))

        self.assertEqual(result, "")
        self.assertTrue(DEFAULT_CONFIG["vad_filter"])
        self.assertTrue(transcriber.model.transcribe.call_args.kwargs["vad_filter"])

    def test_transcript_is_cleaned_after_joining_segments(self):
        transcriber = WhisperTranscriber()
        transcriber.model = Mock()
        transcriber.model.transcribe.return_value = (
            [
                SimpleNamespace(start=0, end=1, text=" Отправь отчёт."),
                SimpleNamespace(start=1, end=2, text=" Субтитры создал"),
                SimpleNamespace(start=2, end=3, text=" Дима Торжок."),
                SimpleNamespace(start=3, end=4, text=" Спасибо, спасибо, спасибо!"),
            ],
            SimpleNamespace(language="ru", language_probability=1.0),
        )

        with self.assertLogs("dictate", level="DEBUG") as logs:
            result = transcriber.transcribe(np.zeros((16000, 1), dtype=np.int16))

        self.assertEqual(result, "Отправь отчёт.")
        self.assertEqual(logs.output[-1], "DEBUG:dictate:Resulting transcript text: Отправь отчёт.")

    def test_filters_known_endings_and_preserves_ordinary_speech(self):
        cases = (
            ("Фраза. Субтитры создавал DimaTorzok", "Фраза."),
            ("Фраза СУБТИТРЫ СДЕЛАЛ Дима-Торзок!", "Фраза"),
            ("Субтитры подогнал «Симон»!", ""),
            ("Фраза. Редактор субтитров Е.Воинова Корректор А.Кулакова", "Фраза."),
            ("Фраза. Продолжение следует... Спасибо за просмотр!", "Фраза."),
            ("Фраза. Подпишитесь на канал.", "Фраза."),
            ("Thank you for watching!", ""),
            ("Done. To be continued… Thanks for watching.", "Done."),
            ("Фраза. Спасибо. СПАСИБО. Спасибо.", "Фраза."),
            ("Фраза Спасибо, спасибо, спасибо, спасибо!", "Фраза"),
            ("Фраза. Спасибо\nСпасибо\nСпасибо…", "Фраза."),
            ("Done. Thank you, thank you, Thank you!", "Done."),
            ("Спасибо!", "Спасибо!"),
            ("Спасибо, спасибо!", "Спасибо, спасибо!"),
            ("Спасибо за помощь.", "Спасибо за помощь."),
            ("Напиши спасибо три раза.", "Напиши спасибо три раза."),
            ("Напиши «Спасибо за просмотр».", "Напиши «Спасибо за просмотр»."),
            ("Субтитры сделал Иван.", "Субтитры сделал Иван."),
            ("Субтитры сделал DimaTorzok, проверь перевод.", "Субтитры сделал DimaTorzok, проверь перевод."),
            ("Продолжение следует в пятницу.", "Продолжение следует в пятницу."),
            ("Спасибо за просмотр. Остались вопросы?", "Спасибо за просмотр. Остались вопросы?"),
        )
        for text, expected in cases:
            with self.subTest(text=text):
                self.assertEqual(_strip_whisper_tail(text), expected)


if __name__ == "__main__":
    unittest.main()
