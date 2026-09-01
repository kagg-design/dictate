import unittest
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np

from src.config import DEFAULT_CONFIG
from src.transcriber import WhisperTranscriber


class WhisperTranscriberTests(unittest.TestCase):
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
