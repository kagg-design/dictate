import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from src.config import DEFAULT_CONFIG
from src.transcriber import WhisperTranscriber


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


if __name__ == "__main__":
    unittest.main()
