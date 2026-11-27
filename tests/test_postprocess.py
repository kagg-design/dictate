from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from src.gemma import LocalGemma
from src.hotkey import HotkeyManager, validate_postprocess_shortcut
from src.postprocess import KEEP_ALIVE, installed_models, polish, warm_model
from src.shortcuts import Shortcut


class PostprocessTests(unittest.TestCase):
    def test_gemma_download_resumes_and_verifies_before_publishing_weights(self):
        weights = b"test weights"
        response = BytesIO(weights[4:])
        response.status = 206
        response.headers = {"Content-Range": f"bytes 4-{len(weights) - 1}/{len(weights)}"}
        with TemporaryDirectory() as directory:
            path = Path(directory) / "model.gguf"
            partial = path.with_suffix(".part")
            partial.write_bytes(weights[:4])
            engine = LocalGemma(weights=path)
            with (
                patch("src.gemma.GEMMA_BYTES", len(weights)),
                patch("src.gemma.GEMMA_SHA256", sha256(weights).hexdigest()),
                patch("src.gemma.urlopen", return_value=response) as download,
            ):
                engine._download(Mock())
            self.assertEqual(download.call_args.args[0].get_header("Range"), "bytes=4-")
            self.assertEqual(path.read_bytes(), weights)
            self.assertFalse(partial.exists())

    @patch("src.postprocess._request")
    def test_warms_only_a_local_model_with_no_dictation(self, request):
        request.side_effect = [{"details": {}}, {"done": True}]
        warm_model("gemma4:e2b")
        request.assert_called_with(
            "/api/generate", {"model": "gemma4:e2b", "keep_alive": KEEP_ALIVE, "stream": False}, timeout=120
        )

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
            {"done": True, "done_reason": "stop", "message": {"content": "<dictation>\nПервый абзац.\n\nВторой.\n</dictation>"}},
        ]
        self.assertEqual(polish("Эм, первый абзац, второй.", "gemma4:e2b"), "Первый абзац.\n\nВторой.")
        self.assertEqual(request.call_args_list[0].args, ("/api/show", {"model": "gemma4:e2b"}))
        path, body = request.call_args.args
        self.assertEqual(path, "/api/chat")
        self.assertIn("<dictation>\nЭм, первый абзац, второй.\n</dictation>", body["messages"][-1]["content"])
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

    @patch("src.postprocess._request")
    def test_rejects_language_switch_or_changed_version_number(self, request):
        for output in ["Prepare version 1.3.3.", "Подготовь версию 1.3.4."]:
            with self.subTest(output=output):
                request.side_effect = [{"details": {}}, {"done": True, "message": {"content": output}}]
                with self.assertRaisesRegex(ValueError, "changed languages, numbers or length"):
                    polish("Подготовь версию 1.3.3.", "gemma3:4b")

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

    @patch("src.hotkey.mask_start_menu")
    def test_modifier_only_editing_shortcut_uses_release_and_keeps_copy_shortcut(self, mask):
        start, edit, stop, copy = Mock(), Mock(), Mock(), Mock()
        manager = HotkeyManager(
            start, stop, on_copy=copy, on_postprocess_start=edit, shortcut="shift+win",
            postprocess_shortcut="ctrl+win",
        )

        def key(name, code, event_type):
            return manager._handle_event(SimpleNamespace(name=name, scan_code=code, event_type=event_type))

        self.assertTrue(key("left windows", 91, "down"))
        self.assertTrue(key("left ctrl", 29, "down"))
        edit.assert_not_called()
        mask.assert_called_once()
        self.assertTrue(key("left windows", 91, "up"))
        key("left ctrl", 29, "up")
        edit.assert_called_once_with()

        key("left ctrl", 29, "down")
        key("left windows", 91, "down")
        self.assertFalse(key("м", 47, "down"))
        key("м", 47, "up")
        self.assertTrue(key("left ctrl", 29, "up"))
        self.assertTrue(key("left windows", 91, "up"))
        copy.assert_called_once_with()
        stop.assert_not_called()

        key("left ctrl", 29, "down")
        key("left windows", 91, "down")
        self.assertTrue(key("left ctrl", 29, "up"))
        self.assertTrue(key("left windows", 91, "up"))
        stop.assert_called_once_with()
        start.assert_not_called()

    def test_editing_binding_rejects_an_overlapping_modifier_only_dictation(self):
        with self.assertRaisesRegex(ValueError, "separate shortcut"):
            validate_postprocess_shortcut(
                Shortcut.from_setting("ctrl+win+shift"), Shortcut.from_setting("ctrl+win"), None,
            )
