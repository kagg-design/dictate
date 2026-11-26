from datetime import datetime, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

import numpy as np

from src.history import DictationHistory


class DictationHistoryTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "dictation-history.json"

    def test_complete_unicode_multiline_text_survives_restart_with_bounded_history(self):
        history = DictationHistory(self.path, max_entries=2)
        history.add("old")
        long_text = "Русский & English 😀\nВторая строка. " * 500
        history.add(long_text)
        history.add("latest")
        restored = DictationHistory(self.path, max_entries=2)

        self.assertEqual([entry.text for entry in restored.recent()], ["latest", long_text])
        label = restored.recent()[1].menu_label
        self.assertIn("&&", label)
        self.assertTrue(label.endswith("…"))
        self.assertLess(len(label), 100)

    def test_previous_log_transcripts_are_recovered_oldest_to_newest(self):
        older = self.path.parent / "app.log.1"
        older.write_text(
            "2026-10-02 22:00:00,000 [DEBUG] Worker - dictate - transcriber.py:111 - Resulting transcript text: old\n",
            encoding="utf-8",
        )
        current = self.path.parent / "app.log"
        current.write_text(
            "2026-10-03 10:00:00,000 [DEBUG] Worker - dictate - transcriber.py:111 - Resulting transcript text: recovered Русский 😀\n"
            "2026-10-03 10:01:00,000 [DEBUG] Worker - dictate - transcriber.py:111 - Resulting transcript text: newest\n"
            "2026-10-03 10:01:00,001 [INFO] Worker - dictate - State: IDLE\n",
            encoding="utf-8",
        )
        history = DictationHistory(self.path, max_entries=2)
        self.assertEqual([entry.text for entry in history.recent()], ["newest", "recovered Русский 😀"])
        self.assertTrue(self.path.exists())
        self.assertEqual(history.recent(), DictationHistory(self.path).recent())

    def test_failed_atomic_replace_keeps_previous_file_and_new_text_in_memory(self):
        history = DictationHistory(self.path)
        history.add("saved")
        previous_file = self.path.read_bytes()
        with patch("src.history.os.replace", side_effect=OSError("disk unavailable")):
            history.add("recoverable from memory")

        self.assertEqual(history.recent()[0].text, "recoverable from memory")
        self.assertEqual(self.path.read_bytes(), previous_file)
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_damaged_existing_history_is_preserved_and_new_dictation_remains_available(self):
        self.path.write_text("damaged history", encoding="utf-8")
        history = DictationHistory(self.path)
        history.add("new text")
        self.assertEqual(self.path.read_text(), "damaged history")
        self.assertEqual(history.recent()[0].text, "new text")

    def test_empty_transcripts_do_not_displace_saved_text(self):
        history = DictationHistory(self.path, max_entries=1)
        history.add("saved")
        history.add("")
        history.add(" \n")
        self.assertEqual(history.recent()[0].text, "saved")

    def test_wav_attachment_survives_restart_and_expires_with_its_entry(self):
        history = DictationHistory(self.path, max_entries=1)
        entry = history.add("saved", model="Fluid Speech Mini")
        pcm = np.array([[11], [-1024], [12]], dtype=np.int16)
        history.save_audio(entry, pcm, 16000)

        reopened = DictationHistory(self.path, max_entries=1)
        saved, = reopened.recent()
        self.assertEqual((saved.text, saved.model), ("saved", "Fluid Speech Mini"))
        path = reopened.audio_path(saved)
        with wave.open(str(path), "rb") as audio:
            self.assertEqual((audio.getnchannels(), audio.getsampwidth(), audio.getframerate()), (1, 2, 16000))
            self.assertEqual(audio.readframes(3), pcm.tobytes())
        reopened.add("next")
        self.assertFalse(path.exists())

    def test_history_rejects_audio_paths_outside_the_audio_folder(self):
        self.path.write_text(
            '{"version":1,"entries":[{"created_at":"2026-10-03T10:00:00","text":"old",'
            '"audio_file":"../unrelated.wav"}]}', encoding="utf-8",
        )
        history = DictationHistory(self.path)
        self.assertIsNone(history.audio_path(history.recent()[0]))

    def test_pinned_transcript_and_late_wav_survive_eviction(self):
        history = DictationHistory(self.path, max_entries=1)
        pinned = history.add("keep")
        self.assertTrue(history.set_pinned(pinned, True))
        history.save_audio(pinned, np.array([1, 2], dtype=np.int16), 16000)
        history.add("middle")
        history.add("latest")

        reopened = DictationHistory(self.path, max_entries=1)
        latest, retained = reopened.recent()
        self.assertEqual((latest.text, retained.text), ("latest", "keep"))
        self.assertTrue(retained.pinned)
        self.assertTrue(reopened.audio_path(retained).is_file())
        self.assertTrue(reopened.set_pinned(retained, False))
        self.assertEqual([entry.text for entry in reopened.recent()], ["latest"])

    def test_expiry_keeps_unpinned_text_and_pinned_audio(self):
        history = DictationHistory(self.path)
        pcm = np.array([1, 2], dtype=np.int16)
        first, second = history.add("first"), history.add("second")
        history.save_audio(first, pcm, 16000)
        self.assertTrue(history.set_pinned(second, True))
        history.save_audio(second, pcm, 16000)
        files = {entry.text: history.audio_path(entry) for entry in history.recent()}
        data = json.loads(self.path.read_text(encoding="utf-8"))
        for entry in data["entries"]:
            entry["created_at"] = (datetime.now().astimezone() - timedelta(days=8)).isoformat()
        self.path.write_text(json.dumps(data), encoding="utf-8")

        reopened = DictationHistory(self.path, audio_retention_days=7)
        self.assertEqual([entry.text for entry in reopened.recent()], ["second", "first"])
        self.assertTrue(files["second"].exists())
        self.assertFalse(files["first"].exists())
        self.assertIsNone(reopened.recent()[1].audio_file)


if __name__ == "__main__":
    unittest.main()
