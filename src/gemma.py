"""Download Gemma's text weights and run the bundled llama.cpp server on Windows."""

from hashlib import file_digest
from http.client import HTTPConnection, HTTPException
import json
import os
from secrets import token_hex
from socket import socket
from subprocess import CREATE_NO_WINDOW, Popen, TimeoutExpired
from threading import Event, Lock
from time import monotonic
from urllib.request import Request, urlopen

from src.paths import APP_ROOT, DATA_DIR


GEMMA_MODEL = "Gemma 4 E2B (built-in)"
GEMMA_SHA256 = "6dd7ac24fe13a238898e16e253ebc7a72fa27fb83f2658c2f6c241df3af5937d"
GEMMA_BYTES = 3_502_493_472
GEMMA_URL = f"https://registry.ollama.ai/v2/library/gemma4/blobs/sha256:{GEMMA_SHA256}"
GEMMA_WEIGHTS = DATA_DIR / "models" / f"gemma-4-e2b-it-q4_k_m-{GEMMA_SHA256[:8]}.gguf"


def gemma_executable():
    installed = APP_ROOT / "assets" / "gemma" / "llama-server.exe"
    if installed.is_file():
        return installed
    return APP_ROOT / "artifacts" / "windows-build" / "gemma-runtime" / "runtime" / "llama-server.exe"


class LocalGemma:
    def __init__(self, executable=None, weights=GEMMA_WEIGHTS):
        self.executable = executable or gemma_executable()
        self.weights = weights
        self._closed = Event()
        self._lock = Lock()
        self._server = None
        self._port = None
        self._key = token_hex(32)

    def prepare(self, report):
        if not self.executable.is_file():
            raise ValueError("The Gemma engine is missing. Reinstall Dictate.")
        if not self.weights.is_file() or self.weights.stat().st_size != GEMMA_BYTES:
            self._download(report)
        report("Loading Gemma 4…")
        self._start_server()

        deadline = monotonic() + 90
        while not self._closed.is_set() and monotonic() < deadline:
            if self._server.poll() is not None:
                raise ValueError("The Gemma engine exited during startup. See gemma.log.")
            try:
                if self._request("/health", None, timeout=2).get("status") == "ok":
                    return
            except (OSError, HTTPException, ValueError):
                pass
            self._closed.wait(0.25)
        self._stop_server()
        raise ValueError("Gemma did not finish loading.")

    def _download(self, report):
        self.weights.parent.mkdir(parents=True, exist_ok=True)
        partial = self.weights.with_suffix(".part")
        downloaded = partial.stat().st_size if partial.is_file() else 0
        if downloaded > GEMMA_BYTES:
            partial.unlink()
            downloaded = 0

        if downloaded < GEMMA_BYTES:
            headers = {"Range": f"bytes={downloaded}-"} if downloaded else {}
            request = Request(GEMMA_URL, headers=headers)
            report(f"Downloading Gemma 4… {downloaded * 100 // GEMMA_BYTES}%")
            with urlopen(request, timeout=20) as response:
                if response.status == 206:
                    expected = f"bytes {downloaded}-{GEMMA_BYTES - 1}/{GEMMA_BYTES}"
                    if response.headers.get("Content-Range") != expected:
                        raise ValueError("Invalid response while resuming the Gemma download.")
                else:
                    downloaded = 0
                with partial.open("ab" if downloaded else "wb") as output:
                    while chunk := response.read(1024 * 1024):
                        if self._closed.is_set():
                            raise ValueError("Gemma download paused.")
                        downloaded += len(chunk)
                        if downloaded > GEMMA_BYTES:
                            raise ValueError("The Gemma download exceeded the expected size.")
                        output.write(chunk)
                        report(f"Downloading Gemma 4… {downloaded * 100 // GEMMA_BYTES}%")

        if downloaded != GEMMA_BYTES:
            raise ValueError("Gemma download was interrupted. Retrying will resume it.")
        report("Verifying Gemma download…")
        with partial.open("rb") as model:
            digest = file_digest(model, "sha256").hexdigest()
        if digest != GEMMA_SHA256:
            partial.unlink()
            raise ValueError("Gemma download failed its integrity check.")
        partial.replace(self.weights)

    def _start_server(self):
        with self._lock:
            if self._closed.is_set():
                raise ValueError("Gemma is turned off.")
            if self._server is not None and self._server.poll() is None:
                return
            with socket() as listener:
                listener.bind(("127.0.0.1", 0))
                self._port = listener.getsockname()[1]
            command = [
                str(self.executable), "--host", "127.0.0.1", "--port", str(self._port),
                "--api-key", self._key, "--model", str(self.weights), "--alias", "dictate-gemma",
                "--ctx-size", "4096", "--parallel", "1", "--reasoning", "off", "--no-webui",
                "--gpu-layers", "auto", "--fit", "on",
            ]
            environment = {key: value for key, value in os.environ.items() if not key.startswith("LLAMA_ARG_")}
            with self.weights.with_name("gemma.log").open("wb") as log:
                self._server = Popen(command, cwd=self.executable.parent, env=environment, stdout=log,
                                     stderr=log, creationflags=CREATE_NO_WINDOW)

    def edit(self, messages):
        if self._closed.is_set() or self._server is None or self._server.poll() is not None:
            raise ValueError("Gemma is not running.")
        response = self._request("/v1/chat/completions", {
            "model": "dictate-gemma", "temperature": 0, "max_tokens": 2048, "messages": messages,
        }, timeout=20)
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ValueError("Gemma returned no edited dictation.")
        choice = choices[0]
        message = choice.get("message")
        if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
            raise ValueError("Gemma did not finish editing the dictation.")
        return message.get("content")

    def _request(self, path, data, *, timeout):
        connection = HTTPConnection("127.0.0.1", self._port, timeout=timeout)
        headers = {"Authorization": f"Bearer {self._key}", "Content-Type": "application/json"}
        try:
            connection.request("GET" if data is None else "POST", path,
                               None if data is None else json.dumps(data).encode("utf-8"), headers)
            response = connection.getresponse()
            if response.status != 200:
                raise ValueError(f"The Gemma engine returned HTTP {response.status}.")
            payload = response.read(1_048_577)
            if len(payload) > 1_048_576:
                raise ValueError("The Gemma response is too large.")
            result = json.loads(payload)
            if not isinstance(result, dict):
                raise ValueError("Invalid Gemma response.")
            return result
        finally:
            connection.close()

    def close(self):
        self._closed.set()
        self._stop_server()

    def _stop_server(self):
        with self._lock:
            if self._server is not None and self._server.poll() is None:
                self._server.terminate()
                try:
                    self._server.wait(timeout=2)
                except TimeoutExpired:
                    pass
