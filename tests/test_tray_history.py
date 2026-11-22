import json
from pathlib import Path
from threading import Event, Thread
import tempfile
import unittest
from unittest.mock import Mock, patch

import numpy as np
import yaml

from src.config import save_recognition_model
from src.history import DictationHistory
from src.live_transcription import CompletedCapture
from src.recorder import AudioRecorder
from src.tray import REFRESH_HISTORY_MENU, SystemTrayApp


class TrayHistoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "dictation-history.json"
        self.history = DictationHistory(self.path)
        self.transcriber = Mock()
        with patch("src.tray.pystray.Icon"):
            self.app = SystemTrayApp(
                self.transcriber, Mock(), Mock(), None, history=self.history,
            )
        self.app.icon._hwnd = 123
        self.app.show_notification = Mock()

    def test_failed_insertion_keeps_full_text_and_worker_continues_without_copying(self):
        texts = ["Rejected by numeric field 😀", "next dictation"]
        self.transcriber.transcribe.side_effect = texts
        for audio in ("first audio", "second audio", None):
            self.app.task_queue.put(audio)

        delivered = []

        def insert(text):
            persisted = json.loads(self.path.read_text(encoding="utf-8"))
            self.assertEqual(persisted["entries"][-1]["text"], text)
            delivered.append(text)
            if len(delivered) == 1:
                raise OSError("insertion failed")

        with (
            patch("src.tray.paste_text", side_effect=insert),
            patch("src.tray.copy_text") as copy,
        ):
            self.app._worker_loop()
        self.assertEqual(delivered, texts)
        self.assertEqual([entry.text for entry in self.history.recent()], list(reversed(texts)))
        self.assertEqual(self.app.state, "idle")
        copy.assert_not_called()

    def test_menu_copies_the_selected_full_entry_even_after_a_new_dictation(self):
        older = "older full text 😀 " * 100
        self.history.add(older)
        self.history.add("latest")
        items = self.app._history_menu_items()
        self.history.add("arrived while menu was open")
        with patch("src.tray.copy_text") as copy:
            items[-1](self.app.icon)
        copy.assert_called_once_with(older, 123)
        self.assertEqual(self.history.recent()[0].text, "arrived while menu was open")
        self.app.show_notification.assert_called_once()

    def test_empty_history_is_visible_with_a_disabled_placeholder(self):
        items = self.app._history_menu_items()
        self.assertEqual(len(items), 1)
        self.assertFalse(items[0].enabled)

    def test_left_click_opens_the_desktop_window(self):
        open_window = Mock()
        with patch("src.tray.pystray.Icon"):
            app = SystemTrayApp(Mock(), Mock(), Mock(), None, history=self.history, open_window=open_window)
        app.menu(app.icon)
        open_window.assert_called_once()
        self.assertTrue(next(iter(app.menu)).default)

    def test_opted_in_audio_is_written_after_text_is_inserted(self):
        capture = Mock()
        capture.finish.return_value = "dictated text"
        pcm = np.array([[1], [2]], dtype=np.int16)
        self.app.recorder.sample_rate = 16000
        self.app._pending_captures = 1

        def insert(text):
            entry, = self.history.recent()
            self.assertEqual(entry.text, text)
            self.assertIsNone(self.history.audio_path(entry))

        with patch("src.tray.paste_text", side_effect=insert) as paste:
            self.app._deliver_transcript(CompletedCapture(capture, pcm, save_wav=True))
        paste.assert_called_once_with("dictated text")
        self.assertIsNotNone(self.history.audio_path(self.history.recent()[0]))

    def test_history_menu_refresh_is_performed_by_the_ui_queue(self):
        self.app.ui_queue.put(REFRESH_HISTORY_MENU)
        self.app.ui_queue.put(None)
        self.app._ui_update_loop()
        self.app.icon.update_menu.assert_called_once()

    def test_live_capture_includes_pre_roll_and_releases_only_one_complete_transcript(self):
        with patch("src.recorder.sd.InputStream"):
            recorder = AudioRecorder()
        self.app.recorder = recorder
        pre_roll = np.full((8000, 1), 1, dtype=np.int16)
        first_phrase = np.full((12000, 1), 2, dtype=np.int16)
        pause = np.zeros((8000, 1), dtype=np.int16)
        final_phrase = np.full((5000, 1), 3, dtype=np.int16)
        first_decode_started = Event()
        finish_first_decode = Event()
        decoded = []

        def decode(samples):
            decoded.append(samples.copy())
            if len(decoded) == 1:
                first_decode_started.set()
                finish_first_decode.wait(timeout=5)
                return "Beginning."
            return "Ending."

        self.transcriber.transcribe_chunk.side_effect = decode
        self.transcriber.clean_text.side_effect = str.strip
        recorder._callback(pre_roll, len(pre_roll), None, None)
        self.app.start_capture(Mock(), Mock())
        for block in (first_phrase, pause):
            recorder._callback(block, len(block), None, None)

        worker = Thread(target=self.app._worker_loop, daemon=True)
        with (
            patch("src.live_transcription.get_speech_timestamps", return_value=[{"start": 800, "end": 20000}]),
            patch("src.tray.paste_text") as insert,
        ):
            worker.start()
            try:
                self.assertTrue(first_decode_started.wait(timeout=5))
                insert.assert_not_called()
                self.assertEqual(self.history.recent(), ())
                recorder._callback(final_phrase, len(final_phrase), None, None)
                self.app.finish_capture()
                insert.assert_not_called()
            finally:
                self.app.task_queue.put(None)
                finish_first_decode.set()
                worker.join(timeout=5)
            self.assertFalse(worker.is_alive())
            insert.assert_called_once_with("Beginning. Ending.")

        self.assertEqual(len(decoded), 2)
        self.assertEqual(len(decoded[0]), 23200)
        self.transcriber.transcribe.assert_not_called()
        np.testing.assert_array_equal(
            np.concatenate(decoded, axis=0),
            np.concatenate((pre_roll, first_phrase, pause, final_phrase), axis=0),
        )

    def test_model_switch_is_persisted_only_after_successful_load(self):
        config = self.path.with_name("config.yaml")
        config.write_text("hotkey: ctrl+win\nrecognition_model: whisper\n", encoding="utf-8")
        mini = Mock(device="cuda")
        self.app.models = {"fluid-speech-mini": ("Fluid Speech Mini", lambda: mini)}
        self.app.model_name = "whisper"
        self.app.select_model("fluid-speech-mini")
        self.app.task_queue.put(None)
        with patch("src.tray.save_recognition_model", side_effect=lambda model: save_recognition_model(model, config)):
            self.app._worker_loop()

        self.assertEqual(self.app.model_name, "fluid-speech-mini")
        self.assertIs(self.app.transcriber, mini)
        mini.load_model.assert_called_once_with()
        self.transcriber.close.assert_called_once_with()
        self.assertEqual(yaml.safe_load(config.read_text(encoding="utf-8")), {
            "hotkey": "ctrl+win", "recognition_model": "fluid-speech-mini",
        })

    def test_model_switch_failure_keeps_the_active_engine(self):
        mini = Mock()
        mini.load_model.side_effect = RuntimeError("CUDA unavailable")
        self.app.models = {"fluid-speech-mini": ("Fluid Speech Mini", lambda: mini)}
        self.app.model_name = "whisper"
        self.app.select_model("fluid-speech-mini")
        self.app.task_queue.put(None)
        with patch("src.tray.save_recognition_model") as save:
            self.app._worker_loop()

        save.assert_not_called()
        self.assertEqual(self.app.model_name, "whisper")
        self.assertIs(self.app.transcriber, self.transcriber)
        self.assertFalse(self.app._loading_model)


if __name__ == "__main__":
    unittest.main()
