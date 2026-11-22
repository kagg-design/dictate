import os
import re
import sys
import time
from importlib import metadata

import numpy as np

from src.logger import logger
from src.paths import APP_ROOT, IS_INSTALLED


_CUDA_PACKAGES = ("nvidia-cublas-cu12", "nvidia-cudnn-cu12", "nvidia-cuda-nvrtc-cu12")
_cuda_dll_directories = []
_available_cuda_packages = set()

# Register the private CUDA directories before importing CTranslate2.
if sys.platform == "win32":
    installed_packages = {
        distribution.metadata["Name"]: distribution
        for distribution in metadata.distributions(path=[str(APP_ROOT / "cuda")])
    } if IS_INSTALLED else {}
    for package in _CUDA_PACKAGES:
        try:
            distribution = installed_packages.get(package) if IS_INSTALLED else metadata.distribution(package)
        except metadata.PackageNotFoundError:
            continue
        files = (distribution.files or ()) if distribution else ()
        paths = {file.locate().parent for file in files if file.name.endswith(".dll") and file.locate().is_file()}
        for path in sorted(paths):
            try:
                _cuda_dll_directories.append(os.add_dll_directory(str(path)))
            except OSError:
                logger.warning("Could not register CUDA DLL directory %s", path)
                continue
            os.environ["PATH"] = str(path) + os.pathsep + os.environ.get("PATH", "")
            _available_cuda_packages.add(package)

import ctranslate2
from faster_whisper import WhisperModel


# Match stock Whisper endings only after recognition has joined all segments.
_WHISPER_TAIL = re.compile(
    r"""
    (?:
        (?<![\w«“"])
        субтитры (?:\s+(?:создавал|создал|сделал|добавил|подготовил|подогнал)[аи]?)?
        [\s:–—-]+ [«“"]? (?:дима[\s_.-]*тор[жз]ок|dima[\s_.-]*tor(?:zh|z|j)ok|симон) [»”"]?
        |
        (?:\A|(?<=[.!?…\r\n])\s*)
        (?:
            редактор\s+субтитров\s+[а-яё]\.\s*[а-яё-]+\s+корректор\s+[а-яё]\.\s*[а-яё-]+
            | продолжение\s+следует
            | спасибо\s+за\s+(?:просмотр|субтитры)
            | (?:thank\s+you|thanks)\s+for\s+watching
            | to\s+be\s+continued
            | подпиш(?:ись|итесь)\s+на\s+канал
        )
        |
        (?<![\w«“"]) (?P<thanks>спасибо|thank\ you) (?:[\s.,!?…;:]+(?P=thanks)){2,}
    )
    [.!?…]* \Z
    """,
    re.IGNORECASE | re.VERBOSE,
)


def _strip_whisper_tail(text):
    text = text.strip()
    while artifact := _WHISPER_TAIL.search(text):
        text = text[:artifact.start()].rstrip()
    return text


class WhisperTranscriber:
    @staticmethod
    def clean_text(text):
        text = _strip_whisper_tail(text)
        logger.debug("Resulting transcript text: %s", text)
        return text

    def __init__(self, model_name="large-v3-turbo", device="cuda", compute_type="float16", vad_filter=True):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.vad_filter = vad_filter
        self.model = None

    def load_model(self):
        """
        Loads the faster-whisper model into memory (VRAM).
        """
        if self.model is not None:
            logger.info("Whisper model is already loaded.")
            return

        if self.device == "auto":
            use_cuda = _available_cuda_packages.issuperset(_CUDA_PACKAGES) and ctranslate2.get_cuda_device_count() > 0
            self.device = "cuda" if use_cuda else "cpu"
            self.compute_type = "float16" if use_cuda else "int8"

        elif self.device == "cuda" and ctranslate2.get_cuda_device_count() == 0:
            logger.warning(
                "NVIDIA GPU is unavailable. Falling back to CPU int8 for this session."
            )
            self.device = "cpu"
            self.compute_type = "int8"

        logger.info(
            f"Loading faster-whisper model '{self.model_name}' on "
            f"'{self.device}' with compute type '{self.compute_type}'..."
        )
        try:
            self.model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type
            )
            logger.info("Whisper model loaded successfully and ready.")
        except Exception as e:
            logger.critical(f"Failed to load Whisper model: {e}")
            raise e

    def transcribe(self, audio_int16):
        return self.clean_text(self.transcribe_chunk(audio_int16))

    def close(self):
        self.model = None

    def transcribe_chunk(self, audio_int16):
        """
        Normalizes the audio buffer and transcribes it, auto-detecting the language.
        """
        if self.model is None:
            raise RuntimeError("Whisper model is not loaded. Call load_model() first.")

        logger.info("Initiating transcription of recorded audio buffer...")
        started = time.perf_counter()
        
        # Convert audio buffer from int16 to float32 and normalize to [-1.0, 1.0]
        audio_float32 = audio_int16.flatten().astype(np.float32) / 32768.0
        
        try:
            # Transcribe without language parameter to enable auto-detect
            # Russian and English can be auto-detected and mixed
            segments, info = self.model.transcribe(
                audio_float32,
                beam_size=5,
                vad_filter=self.vad_filter
            )
            
            logger.info(
                f"Auto-detected language: '{info.language}' with "
                f"probability {info.language_probability:.2f}"
            )
            
            text_segments = []
            for segment in segments:
                logger.info(f"Segment [{segment.start:.2f}s -> {segment.end:.2f}s]: {segment.text}")
                text_segments.append(segment.text)
                
            transcribed_text = "".join(text_segments).strip()
            logger.info(
                "Transcription completed. Characters: %d, elapsed: %.3fs, device: %s",
                len(transcribed_text),
                time.perf_counter() - started,
                self.device,
            )
            
            return transcribed_text
        except Exception as e:
            logger.error(f"Error during audio transcription process: {e}")
            raise e
