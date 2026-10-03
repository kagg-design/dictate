from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
