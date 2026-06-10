import numpy as np
import sounddevice as sd
from src.logger import logger

class AudioRecorder:
    def __init__(self, sample_rate=16000, max_duration=60, min_duration=0.3):
        self.sample_rate = sample_rate
        self.max_duration = max_duration
        self.min_duration = min_duration
        self.stream = None
        self.audio_blocks = []
        self.is_recording = False
        self.max_samples = int(sample_rate * max_duration)
        self.total_samples = 0
        self.on_limit_reached = None

    def _callback(self, indata, frames, time, status):
        if status:
            logger.warning(f"Sounddevice callback status warning: {status}")
        
        if not self.is_recording:
            return

        # Check safety cap (e.g. 60 seconds)
        if self.total_samples >= self.max_samples:
            logger.warning(f"Safety hard cap reached ({self.max_duration}s). Stopping recording.")
            self.is_recording = False
            # Execute limit-reached callback (ensure it handles threading safely if needed)
            if self.on_limit_reached:
                try:
                    self.on_limit_reached()
                except Exception as e:
                    logger.error(f"Error in recorder limit-reached callback: {e}")
            return

        # Store copy of audio data block
        block = indata.copy()
        self.audio_blocks.append(block)
        self.total_samples += len(block)

    def start(self, on_limit_reached=None):
        """
        Starts the audio recording stream.
        """
        if self.is_recording:
            logger.warning("Start called, but recorder is already active.")
            return
            
        logger.info("Initializing audio recording stream...")
        self.audio_blocks = []
        self.total_samples = 0
        self.is_recording = True
        self.on_limit_reached = on_limit_reached
        
        try:
            self.stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype='int16',
                callback=self._callback
            )
            self.stream.start()
            logger.info("Audio stream started successfully.")
        except Exception as e:
            logger.error(f"Failed to start audio input stream: {e}")
            self.is_recording = False
            self.stream = None
            raise e

    def stop(self):
        """
        Stops the recording stream and returns the concatenated numpy array or None.
        """
        if not self.is_recording and not self.stream:
            logger.info("Stop called, but recorder is not active.")
            return None

        logger.info("Stopping audio recording stream...")
        self.is_recording = False
        
        if self.stream:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
            finally:
                self.stream = None

        if not self.audio_blocks:
            logger.info("No audio data was recorded.")
            return None

        # Concatenate audio blocks into a single array
        audio = np.concatenate(self.audio_blocks, axis=0)
        
        duration = len(audio) / self.sample_rate
        logger.info(f"Audio record complete. Recorded duration: {duration:.2f} seconds ({len(audio)} samples)")
        
        if duration < self.min_duration:
            logger.warning(
                f"Recorded duration ({duration:.2f}s) is shorter than minimum allowed "
                f"({self.min_duration}s). Discarding recording."
            )
            return None

        return audio
