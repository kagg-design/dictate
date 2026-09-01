import collections
import threading

import numpy as np
import sounddevice as sd

from src.logger import logger


class AudioRecorder:
    """Maintain a warm input stream and capture push-to-talk audio with pre-roll."""

    def __init__(
        self,
        sample_rate=16000,
        max_duration=60,
        min_duration=0.3,
        latency=0.08,
        ring_buffer_duration=0.5,
    ):
        self.sample_rate = sample_rate
        self.max_duration = max_duration
        self.min_duration = min_duration
        self.latency = latency
        self.max_samples = int(sample_rate * max_duration)

        self.stream = None
        self.audio_blocks = []
        self.is_recording = False
        self._session_active = False
        self.active_samples = 0
        self.on_limit_reached = None
        self.on_audio_start = None
        self._has_triggered_audio_start = False

        self.ring_buffer_duration = ring_buffer_duration
        self.ring_buffer_samples = int(sample_rate * ring_buffer_duration)
        self.ring_buffer = collections.deque()
        self.current_ring_samples = 0
        self._lock = threading.RLock()

        self._start_background_stream()

    def _start_background_stream(self):
        """Open the input once so push-to-talk does not pay device startup latency."""
        try:
            logger.info("Initializing background audio stream...")
            stream = sd.InputStream(
                samplerate=self.sample_rate,
                channels=1,
                dtype="int16",
                latency=self.latency,
                callback=self._callback,
            )
            stream.start()
            self.stream = stream
            logger.info("Background audio stream started successfully.")
        except Exception as e:
            self.stream = None
            logger.error(f"Failed to start background audio stream: {e}")
            raise

    def _callback(self, indata, frames, time_info, status):
        if status:
            logger.warning(f"Sounddevice callback status warning: {status}")

        block = indata.copy()
        audio_start_callback = None
        limit_callback = None

        with self._lock:
            self.ring_buffer.append(block)
            self.current_ring_samples += len(block)
            while (
                self.current_ring_samples > self.ring_buffer_samples
                and len(self.ring_buffer) > 1
            ):
                removed_block = self.ring_buffer.popleft()
                self.current_ring_samples -= len(removed_block)

            if not self.is_recording:
                return

            self.audio_blocks.append(block)
            self.active_samples += len(block)

            if not self._has_triggered_audio_start and self.on_audio_start:
                self._has_triggered_audio_start = True
                audio_start_callback = self.on_audio_start

            if self.active_samples >= self.max_samples:
                logger.warning(
                    f"Safety hard cap reached ({self.max_duration}s). Stopping recording."
                )
                # Leave the session available for stop() to finalize, but stop
                # accepting blocks before invoking the callback.
                self.is_recording = False
                limit_callback = self.on_limit_reached

        if audio_start_callback:
            try:
                audio_start_callback()
            except Exception as e:
                logger.error(f"Error in recorder on_audio_start callback: {e}")

        if limit_callback:
            try:
                limit_callback()
            except Exception as e:
                logger.error(f"Error in recorder limit-reached callback: {e}")

    def start(self, on_limit_reached=None, on_audio_start=None):
        """Start an active capture using the current pre-roll snapshot."""
        with self._lock:
            if self._session_active:
                logger.warning("Start called, but recorder is already active.")
                return

        if not self.stream or not self.stream.active:
            self.cleanup()
            self._start_background_stream()

        with self._lock:
            logger.info("Starting active recording capture...")
            self.audio_blocks = list(self.ring_buffer)
            self.active_samples = 0
            self.on_limit_reached = on_limit_reached
            self.on_audio_start = on_audio_start
            self._has_triggered_audio_start = False
            self._session_active = True
            self.is_recording = True

    def stop(self):
        """Finalize the current capture while leaving the warm stream running."""
        with self._lock:
            if not self._session_active:
                logger.info("Stop called, but recorder is not active.")
                return None

            logger.info("Stopping active recording capture...")
            self.is_recording = False
            self._session_active = False
            self.on_audio_start = None
            self.on_limit_reached = None
            audio_blocks = self.audio_blocks
            active_samples = self.active_samples
            self.audio_blocks = []
            self.active_samples = 0

        if not audio_blocks:
            logger.info("No audio data was recorded.")
            return None

        audio = np.concatenate(audio_blocks, axis=0)
        total_duration = len(audio) / self.sample_rate
        active_duration = active_samples / self.sample_rate
        logger.info(
            "Audio record complete. Total duration: %.2fs, Active duration: %.2fs "
            "(%d samples)",
            total_duration,
            active_duration,
            len(audio),
        )

        if active_duration < self.min_duration:
            logger.warning(
                "Active recorded duration (%.2fs) is shorter than minimum allowed "
                "(%ss). Discarding recording to prevent noise hallucinations.",
                active_duration,
                self.min_duration,
            )
            return None

        return audio

    def cleanup(self):
        """Close the warm input stream during application shutdown/recovery."""
        with self._lock:
            stream = self.stream
            self.stream = None
            self.is_recording = False
            self._session_active = False

        if stream:
            try:
                stream.stop()
                stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
