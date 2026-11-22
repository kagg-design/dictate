"""Run Fluid Speech Mini in a persistent process with its own CUDA runtime."""

import ctypes
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from time import perf_counter

import numpy as np

from src.paths import APP_ROOT, LOG_DIR


_FRAME = struct.Struct("<I")
_TIMESTAMPS = 1 << 31
_MODEL_FILE = Path("models/fluid-speech-mini/fluid-speech-mini-q2k-q4k.gguf")
_CUDA_DLL = Path("FluidVoice/Runtimes/parakeet-cpp/parakeet-v0.4.0/cuda/parakeet.dll")


def _installed_file(appdata, subdirectory, relative_path):
    default = Path.home() / "AppData" / subdirectory
    path = Path(os.environ.get(appdata, default)) / relative_path
    return path if path.is_file() else default / relative_path


class FluidMiniTranscriber:
    device = "cuda"
    clean_text = staticmethod(str.strip)

    def __init__(self, sample_rate=16000, model_path=None, dll_path=None):
        self.sample_rate = sample_rate
        self.model_path = Path(model_path) if model_path else _installed_file(
            "APPDATA", "Roaming", Path("com.altic.fluidvoice.windows") / _MODEL_FILE
        )
        self.dll_path = Path(dll_path) if dll_path else _installed_file(
            "LOCALAPPDATA", "Local", _CUDA_DLL
        )
        self._process = None

    def load_model(self):
        if self._process and self._process.poll() is None:
            return
        self.close()
        for path in (self.model_path, self.dll_path):
            if not path.is_file():
                raise FileNotFoundError(f"Fluid Speech Mini requires the installed CUDA runtime and model: {path}")

        python = Path(sys.executable)
        if python.name.lower() == "pythonw.exe":
            python = python.with_name("python.exe")
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with (LOG_DIR / "fluid-mini.log").open("ab") as stderr:
            self._process = subprocess.Popen(
                [
                    str(python), "-m", "src.fluid_mini", str(self.dll_path.resolve()),
                    str(self.model_path.resolve()), str(self.sample_rate),
                ],
                cwd=APP_ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=stderr,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        try:
            self._read_reply()
        except Exception:
            self.close()
            raise

    def _read_reply(self):
        line = self._process.stdout.readline()
        if not line:
            raise RuntimeError("Fluid Speech Mini stopped unexpectedly. Check logs/fluid-mini.log.")
        reply = json.loads(line)
        if "error" in reply:
            raise RuntimeError(reply["error"])
        return reply

    def transcribe(self, audio):
        if not len(audio):
            return ""
        audio = np.pad(np.asarray(audio).reshape(-1), (0, self.sample_rate // 4))
        return self._request(audio, timestamps=False).get("text", "")

    def transcribe_words(self, audio):
        return self._request(audio, timestamps=True).get("words", [])

    def _request(self, audio, *, timestamps):
        payload = np.asarray(audio, dtype="<i2").tobytes()
        if not payload:
            return {}
        if not self._process or self._process.poll() is not None:
            self.load_model()
        try:
            flags = _TIMESTAMPS if timestamps else 0
            self._process.stdin.write(_FRAME.pack(flags | len(payload)))
            self._process.stdin.write(payload)
            self._process.stdin.flush()
        except OSError as exc:
            raise RuntimeError("Fluid Speech Mini stopped while receiving audio.") from exc
        return self._read_reply()

    transcribe_chunk = transcribe

    def close(self):
        process, self._process = self._process, None
        if process is None:
            return
        try:
            process.stdin.close()
        except OSError:
            pass
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
        finally:
            process.stdout.close()


def _load_library(path):
    with os.add_dll_directory(str(path.parent)):
        library = ctypes.CDLL(str(path))
    float_pointer = ctypes.POINTER(ctypes.c_float)
    signatures = {
        "abi_version": (ctypes.c_int, ()),
        "load": (ctypes.c_void_p, (ctypes.c_char_p,)),
        "free": (None, (ctypes.c_void_p,)),
        "free_string": (None, (ctypes.c_void_p,)),
        "last_error": (ctypes.c_char_p, (ctypes.c_void_p,)),
        "transcribe_pcm": (
            ctypes.c_void_p,
            (ctypes.c_void_p, float_pointer, ctypes.c_int, ctypes.c_int, ctypes.c_int),
        ),
        "transcribe_pcm_batch_json": (
            ctypes.c_void_p,
            (ctypes.c_void_p, float_pointer, ctypes.POINTER(ctypes.c_int), ctypes.c_int, ctypes.c_int, ctypes.c_int),
        ),
    }
    for name, (result, arguments) in signatures.items():
        function = getattr(library, "parakeet_capi_" + name)
        function.restype, function.argtypes = result, arguments
    if library.parakeet_capi_abi_version() != 5:
        raise RuntimeError("Fluid Speech Mini requires the parakeet.cpp v0.4.0 CUDA runtime (ABI 5).")
    return library


def _decode(library, context, pcm, sample_rate, *, timestamps=False):
    audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0
    samples = audio.ctypes.data_as(ctypes.POINTER(ctypes.c_float))
    if timestamps:
        count = ctypes.c_int(len(audio))
        result = library.parakeet_capi_transcribe_pcm_batch_json(
            context, samples, ctypes.byref(count), 1, sample_rate, 0
        )
    else:
        result = library.parakeet_capi_transcribe_pcm(context, samples, len(audio), sample_rate, 0)
    if not result:
        error = library.parakeet_capi_last_error(context) or b"Fluid Speech Mini could not decode the audio."
        raise RuntimeError(error.decode("utf-8", errors="replace"))
    try:
        output = ctypes.string_at(result).decode("utf-8", errors="replace")
        if timestamps:
            return {"words": json.loads(output)[0]["words"]}
        return {"text": output.strip()}
    finally:
        library.parakeet_capi_free_string(result)


def _serve(library_path, model_path, sample_rate):
    # Keep native library messages out of the pipe used for transcription results.
    with os.fdopen(os.dup(sys.stdout.fileno()), "wb") as protocol:
        os.dup2(sys.stderr.fileno(), sys.stdout.fileno())

        def reply(**data):
            protocol.write(json.dumps(data).encode("ascii") + b"\n")
            protocol.flush()

        try:
            library = _load_library(library_path)
            context = library.parakeet_capi_load(str(model_path).encode("utf-8"))
            if not context:
                raise RuntimeError(f"Could not load Fluid Speech Mini from {model_path}.")
        except Exception as exc:
            reply(error=str(exc))
            return

        try:
            _decode(library, context, bytes(sample_rate // 2), sample_rate)
            reply(text="")
            source = sys.stdin.buffer
            while header := source.read(_FRAME.size):
                request, = _FRAME.unpack(header)
                timestamps = bool(request & _TIMESTAMPS)
                length = request & ~_TIMESTAMPS
                pcm = source.read(length)
                if len(pcm) != length:
                    break
                try:
                    started = perf_counter()
                    result = _decode(library, context, pcm, sample_rate, timestamps=timestamps)
                    print(f"Decoded {length // 2} samples in {perf_counter() - started:.3f}s", file=sys.stderr)
                    reply(**result)
                except Exception as exc:
                    reply(error=str(exc))
        finally:
            library.parakeet_capi_free(context)
            # Unload parakeet before Windows tears down the CUDA DLLs it uses.
            free_library = ctypes.windll.kernel32.FreeLibrary
            free_library.argtypes = (ctypes.c_void_p,)
            free_library(library._handle)


if __name__ == "__main__":
    _serve(Path(sys.argv[1]), Path(sys.argv[2]), int(sys.argv[3]))
