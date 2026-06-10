import os
import sys
import numpy as np

# Attempt to resolve NVIDIA CUDA DLL paths for faster-whisper on Windows before imports
if sys.platform == 'win32':
    try:
        import importlib.metadata
        for pkg in ["nvidia-cublas-cu12", "nvidia-cudnn-cu12"]:
            try:
                files = importlib.metadata.files(pkg)
                if files:
                    dll_dirs = set()
                    for f in files:
                        if f.name.endswith(".dll"):
                            dll_path = f.locate()
                            dll_dirs.add(os.path.dirname(str(dll_path)))
                    for d in dll_dirs:
                        if os.path.exists(d):
                            # Add to DLL directory search path
                            os.add_dll_directory(d)
                            # Also append to PATH just in case some C/C++ loaders require it
                            os.environ['PATH'] = d + os.path.pathsep + os.environ['PATH']
            except Exception:
                pass
    except Exception:
        pass

from faster_whisper import WhisperModel
from src.logger import logger

class WhisperTranscriber:
    def __init__(self, model_name="large-v3-turbo", device="cuda", compute_type="float16"):
        self.model_name = model_name
        self.device = device
        self.compute_type = compute_type
        self.model = None

    def load_model(self):
        """
        Loads the faster-whisper model into memory (VRAM).
        """
        if self.model is not None:
            logger.info("Whisper model is already loaded.")
            return

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
        """
        Normalizes the audio buffer and transcribes it, auto-detecting the language.
        """
        if self.model is None:
            raise RuntimeError("Whisper model is not loaded. Call load_model() first.")

        logger.info("Initiating transcription of recorded audio buffer...")
        
        # Convert audio buffer from int16 to float32 and normalize to [-1.0, 1.0]
        audio_float32 = audio_int16.flatten().astype(np.float32) / 32768.0
        
        try:
            # Transcribe without language parameter to enable auto-detect
            # Russian and English can be auto-detected and mixed
            segments, info = self.model.transcribe(
                audio_float32,
                beam_size=5
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
            logger.info(f"Transcription completed. Characters: {len(transcribed_text)}")
            logger.debug(f"Resulting transcript text: {transcribed_text}")
            
            return transcribed_text
        except Exception as e:
            logger.error(f"Error during audio transcription process: {e}")
            raise e
