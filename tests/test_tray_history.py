import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from src.history import DictationHistory
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

    def test_history_menu_refresh_is_performed_by_the_ui_queue(self):
        self.app.ui_queue.put(REFRESH_HISTORY_MENU)
        self.app.ui_queue.put(None)
        self.app._ui_update_loop()
        self.app.icon.update_menu.assert_called_once()


if __name__ == "__main__":
    unittest.main()
