import os
import sys
import time
import wave
import numpy as np

# Adjust python path to allow importing from the parent folder (src)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.transcriber import WhisperTranscriber
from src.logger import logger

def generate_silent_wav(filename, duration_seconds=1.0, sample_rate=16000):
    """
    Generates a silent 16-bit mono WAV file.
    """
    logger.info(f"Generating temporary silent WAV file '{filename}'...")
    num_samples = int(sample_rate * duration_seconds)
    audio_data = np.zeros(num_samples, dtype=np.int16)
    
    with wave.open(filename, 'wb') as wf:
        wf.setnchannels(1)             # Mono
        wf.setsampwidth(2)             # 16-bit
        wf.setframerate(sample_rate)   # 16 kHz
        wf.writeframes(audio_data.tobytes())
    logger.info(f"Temporary file '{filename}' generated successfully.")

def main():
    print("=" * 60)
    print("   NVIDIA GPU & CUDA DICTATION CHAIN DIAGNOSTIC TEST   ")
    print("=" * 60)
    
    # 1. Environment and package diagnostics
    print(f"Python version: {sys.version}")
    print(f"Operating System: {sys.platform}")
    
    try:
        import ctranslate2
        print(f"ctranslate2 version: {ctranslate2.__version__}")
        print("Supported compute types on CPU:", ctranslate2.get_supported_compute_types("cpu"))
        try:
            cuda_types = ctranslate2.get_supported_compute_types("cuda")
            print("Supported compute types on CUDA:", cuda_types)
        except Exception as e:
            print("CUDA support check failed. Error:", e)
            print("[ERROR] CUDA might not be configured correctly, or drivers are missing.")
    except Exception as e:
        print("[WARNING] Failed to query ctranslate2 directly:", e)

    # 2. Generate silent WAV
    temp_wav = "temp_diagnostic_silence.wav"
    try:
        generate_silent_wav(temp_wav)
    except Exception as e:
        print(f"[CRITICAL] Failed to generate temporary WAV file: {e}")
        sys.exit(1)

    # 3. Initialize Whisper Transcriber (loads CUDA DLLs)
    transcriber = WhisperTranscriber(
        model_name="large-v3-turbo",
        device="cuda",
        compute_type="float16"
    )
    
    print("\n[1/3] Loading Whisper model (large-v3-turbo) into VRAM...")
    load_start = time.time()
    try:
        transcriber.load_model()
        load_duration = time.time() - load_start
        print(f"[SUCCESS] Model loaded successfully in {load_duration:.2f} seconds.")
    except Exception as e:
        print(f"\n[CRITICAL] Failed to load Whisper model on CUDA: {e}")
        print("\nTroubleshooting tips:")
        print("1. Ensure you have an NVIDIA GPU and the latest game-ready or studio driver installed.")
        print("2. Ensure nvidia-cublas-cu12 and nvidia-cudnn-cu12 are installed in your virtual environment.")
        print("3. Check for typical Windows DLL errors. Make sure you run scripts from an Administrator terminal.")
        
        # Clean up
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
        sys.exit(1)

    # 4. Read the generated WAV file
    print("\n[2/3] Reading temporary WAV file into memory buffer...")
    try:
        with wave.open(temp_wav, 'rb') as wf:
            frames = wf.readframes(wf.getnframes())
            audio_buffer = np.frombuffer(frames, dtype=np.int16)
        print(f"[SUCCESS] Loaded {len(audio_buffer)} samples from WAV file.")
    except Exception as e:
        print(f"[CRITICAL] Failed to read generated WAV: {e}")
        if os.path.exists(temp_wav):
            os.remove(temp_wav)
        sys.exit(1)

    # 5. Run transcription
    print("\n[3/3] Running transcription on silent audio buffer...")
    transcribe_start = time.time()
    try:
        result = transcriber.transcribe(audio_buffer)
        transcribe_duration = time.time() - transcribe_start
        print(f"[SUCCESS] Transcription complete in {transcribe_duration:.2f} seconds.")
        print(f"Transcription result text: '{result}' (expected empty or minimal noise)")
    except Exception as e:
        print(f"\n[CRITICAL] Error occurred during transcription step: {e}")
        sys.exit(1)
    finally:
        # Clean up temporary WAV file
        if os.path.exists(temp_wav):
            try:
                os.remove(temp_wav)
                logger.info(f"Removed temporary silent WAV file '{temp_wav}'.")
            except Exception as e:
                logger.warning(f"Failed to delete temporary WAV: {e}")

    print("\n" + "=" * 60)
    print("   DIAGNOSTIC TEST PASSED: CUDA transcription chain is fully working!   ")
    print("=" * 60)

if __name__ == "__main__":
    main()
