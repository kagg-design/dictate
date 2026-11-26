from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.hotkey import HotkeyManager
from src.postprocess import installed_models, polish


class PostprocessTests(unittest.TestCase):
    @patch("src.postprocess._request")
    def test_model_picker_lists_only_local_models(self, request):
        request.return_value = {"models": [
            {"name": "gemma4:e2b"},
            {"name": "remote", "remote_host": "https://ollama.com"},
            {"name": "gemma3:4b"},
        ]}
        self.assertEqual(installed_models(), ("gemma3:4b", "gemma4:e2b"))
        request.assert_called_once_with("/api/tags", None, timeout=2)

    @patch("src.postprocess._request")
    def test_only_sends_transcript_after_confirming_a_local_model(self, request):
        request.side_effect = [
            {"details": {"family": "gemma4"}},
            {"done": True, "done_reason": "stop", "message": {"content": "  Первый абзац.\n\nВторой.  "}},
        ]
        self.assertEqual(polish("Эм, первый абзац, второй.", "gemma4:e2b"), "Первый абзац.\n\nВторой.")
        self.assertEqual(request.call_args_list[0].args, ("/api/show", {"model": "gemma4:e2b"}))
        path, body = request.call_args.args
        self.assertEqual(path, "/api/chat")
        self.assertEqual(body["messages"][-1], {"role": "user", "content": "Эм, первый абзац, второй."})
        self.assertFalse(body["stream"])

    @patch("src.postprocess._request")
    def test_refuses_remote_model_before_sending_transcript(self, request):
        request.return_value = {"details": {}, "remote_host": "https://ollama.com"}
        with self.assertRaisesRegex(ValueError, "local Ollama model"):
            polish("Private transcript", "remote-model")
        request.assert_called_once()

    @patch("src.postprocess._request")
    def test_rejects_partial_response(self, request):
        request.side_effect = [{"details": {}}, {"done": True, "done_reason": "length", "message": {"content": "partial"}}]
        with self.assertRaisesRegex(ValueError, "complete transcript"):
            polish("Long dictation", "gemma4:e2b")

    def test_editing_hotkey_toggles_and_regular_hotkey_still_holds(self):
        start, edit, stop, cancel = Mock(), Mock(), Mock(), Mock()
        manager = HotkeyManager(
            start, stop, on_cancel=cancel, on_postprocess_start=edit, postprocess_shortcut="ctrl+alt+f10",
        )

        def key(name, code, event_type):
            manager._handle_event(SimpleNamespace(name=name, scan_code=code, event_type=event_type))

        def tap_edit():
            keys = [("left ctrl", 29), ("left alt", 56), ("f10", 68)]
            for name, code in keys:
                key(name, code, "down")
            for name, code in reversed(keys):
                key(name, code, "up")

        tap_edit()
        edit.assert_called_once_with()
        stop.assert_not_called()
        tap_edit()
        stop.assert_called_once_with()
        start.assert_not_called()
        tap_edit()
        key("esc", 1, "down")
        key("esc", 1, "up")
        cancel.assert_called_once_with(True)
        self.assertFalse(manager.is_active)
        key("left ctrl", 29, "down")
        key("left windows", 91, "down")
        key("left windows", 91, "up")
        key("left ctrl", 29, "up")
        start.assert_called_once_with()
        self.assertEqual(stop.call_count, 2)
