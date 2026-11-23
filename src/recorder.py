import collections
import math
import threading
import time

import av
import numpy as np
import sounddevice as sd

from src.logger import logger


def list_microphones():
    """Identify inputs by name and host API, independent of PortAudio indices."""
    devices = {}
    host_apis = sd.query_hostapis()
    for index, info in enumerate(sd.query_devices()):
        name = info["name"]
        if (
            info["max_input_channels"] < 1 or info["default_samplerate"] <= 0
            or name in ("Microsoft Sound Mapper - Input", "Primary Sound Capture Driver")
        ):
            continue
        host = host_apis[info["hostapi"]]["name"]
        label = f"{name} · {host}"
        if label in devices:
            label = f"{label} ({index})"
        devices[label] = index
    return devices


class AudioRecorder:
    """Capture push-to-talk audio with an optional warm input and pre-roll."""

    def __init__(
        self,
        sample_rate=16000,
        max_duration=60,
        min_duration=0.3,
        latency=0.08,
        ring_buffer_duration=0.5,
        microphone=None,
    ):
        self.sample_rate = sample_rate
        self.max_duration = max_duration
        self.min_duration = min_duration
        self.latency = latency
        self.max_samples = int(sample_rate * max_duration)

        self.stream = None
        self.microphone = None
        self.input_issue = None
        self._stream_token = None
        self.audio_blocks = []
        self.is_recording = False
        self._session_active = False
        self.active_samples = 0
        self.on_limit_reached = None
        self.on_audio_start = None
        self._has_triggered_audio_start = False

        self.ring_buffer_duration = max(0.0, float(ring_buffer_duration))
        self.ring_buffer_samples = round(sample_rate * self.ring_buffer_duration)
        self.ring_buffer = collections.deque()
        self.current_ring_samples = 0
        self.last_callback_time = None
        self.input_peak = 0.0
        self.capture_interrupted = False
        self._capture_started_at = None
        self._recovering_input = False
        self._last_recovery_attempt = 0.0
        self._input_timeout = max(0.9, 4 * latency)
        self._lock = threading.RLock()
        self._stream_lock = threading.Lock()

        try:
            self.select_microphone(microphone)
        except (OSError, ValueError, RuntimeError, sd.PortAudioError):
            logger.exception("Could not open the selected microphone")
            if microphone is not None:
                try:
                    self.select_microphone(None)
                    self.input_issue = f"{microphone} is unavailable. Using the system default."
                    return
                except (OSError, ValueError, RuntimeError, sd.PortAudioError):
                    logger.exception("Could not open the default microphone")
            self.input_issue = "No microphone is ready. Choose an input in Settings."

    def _open_stream(self, device, token):
        try:
            return self._open_at_rate(device, token, self.sample_rate)
        except sd.PortAudioError:
            native_rate = int(sd.query_devices(device, kind="input")["default_samplerate"])
            if not native_rate or native_rate == self.sample_rate:
                raise
            return self._open_at_rate(device, token, native_rate)

    def _open_at_rate(self, device, token, rate):
        resampler = None
        if rate != self.sample_rate:
            resampler = av.AudioResampler(format="s16", layout="mono", rate=self.sample_rate)

        def capture(indata, frames, time_info, status):
            if resampler is None:
                self._callback(indata, frames, time_info, status, token)
                return
            frame = av.AudioFrame.from_ndarray(np.ascontiguousarray(indata.T), format="s16", layout="mono")
            frame.sample_rate = rate
            for output in resampler.resample(frame):
                self._callback(output.to_ndarray().T, output.samples, time_info, status, token)

        stream = sd.InputStream(
            device=device,
            samplerate=rate,
            channels=1,
            dtype="int16",
            latency=self.latency,
            callback=capture,
        )
        try:
            stream.start()
        except Exception:
            self._close_stream(stream)
            raise
        return stream

    def select_microphone(self, microphone, *, persist=None):
        """Keep the current stream until the new input is usable and saved."""
        with self._stream_lock:
            self._configure_input(microphone, self.ring_buffer_duration, persist, replace=True)

    def set_pre_roll(self, duration, *, persist=None):
        """Apply a new buffer size and release the idle microphone at zero."""
        if not math.isfinite(duration) or duration < 0:
            raise ValueError("Pre-roll duration must be a nonnegative number.")
        with self._stream_lock:
            self._configure_input(self.microphone, duration, persist)

    def _configure_input(self, microphone, duration, persist, *, replace=False):
        samples = round(self.sample_rate * duration)
        with self._lock:
            if self._session_active:
                raise RuntimeError("Finish dictating before changing the microphone settings.")
            previous, token = self.stream, self._stream_token

        stream = previous
        if replace or (samples and (previous is None or not previous.active)):
            device = self._device_index(microphone)
            token = object() if samples else None
            stream = self._open_stream(device, token) if samples else None
        elif not samples:
            stream, token = None, None

        try:
            if persist:
                persist()
        except Exception:
            if stream is not previous:
                self._close_stream(stream)
            raise

        with self._lock:
            self.stream, self._stream_token = stream, token
            self.microphone = microphone
            self.input_issue = None
            self.ring_buffer_duration = duration
            self.ring_buffer_samples = samples
            if replace or stream is not previous or not samples:
                self._reset_pre_roll()
            else:
                self._trim_pre_roll()
        if stream is not previous:
            self._close_stream(previous)
            if stream is not None:
                logger.info("Background audio stream started successfully. Input: %s", microphone or "system default")

    @property
    def microphone_ready(self):
        with self._lock:
            return self.stream is not None or (self.ring_buffer_samples == 0 and self.input_issue is None)

    def _reset_pre_roll(self):
        self.ring_buffer.clear()
        self.current_ring_samples = 0
        self.last_callback_time = None
        self.input_peak = 0.0

    def _trim_pre_roll(self):
        while self.current_ring_samples > self.ring_buffer_samples:
            excess = self.current_ring_samples - self.ring_buffer_samples
            oldest = self.ring_buffer[0]
            if excess < len(oldest):
                self.ring_buffer[0] = oldest[excess:].copy()
                self.current_ring_samples -= excess
            else:
                self.ring_buffer.popleft()
                self.current_ring_samples -= len(oldest)

    @staticmethod
    def _device_index(microphone):
        if microphone is None:
            return None
        device = list_microphones().get(microphone)
        if device is None:
            raise ValueError("The selected microphone is unavailable.")
        return device

    def _capture_callback_gap(self, now):
        if self._capture_started_at is None:
            return 0.0
        return now - max(self._capture_started_at, self.last_callback_time or 0.0)

    def check_capture_input(self):
        """Recover a stalled microphone without blocking recognition or key release."""
        now = time.monotonic()
        with self._lock:
            if (not self.is_recording or self._recovering_input or self._capture_callback_gap(now) < self._input_timeout
                    or now - self._last_recovery_attempt < 2.0):
                return
            self.capture_interrupted = True
            self._recovering_input = True
            self._last_recovery_attempt = now
        threading.Thread(target=self._recover_capture_input, name="MicrophoneRecovery", daemon=True).start()

    def _recover_capture_input(self):
        try:
            with self._stream_lock:
                with self._lock:
                    gap = self._capture_callback_gap(time.monotonic())
                    if not self.is_recording or gap < self._input_timeout:
                        return
                    logger.warning("Microphone delivered no audio for %.2fs during dictation. Reopening input.", gap)
                    previous, self.stream = self.stream, None
                    microphone = self.microphone
                    self._stream_token = None
                    token = object()
                    last_callback_time = self.last_callback_time
                    self._reset_pre_roll()
                    self.last_callback_time = last_callback_time
                self._close_stream(previous)
                stream = self._open_stream(self._device_index(microphone), token)
                with self._lock:
                    still_needed = self.is_recording or bool(self.ring_buffer_samples)
                    if still_needed:
                        self.stream, self._stream_token = stream, token
                if still_needed:
                    logger.info("Microphone reopened during dictation. Captured audio was retained.")
                else:
                    self._close_stream(stream)
        except (OSError, ValueError, RuntimeError, sd.PortAudioError):
            logger.exception("Could not reopen the microphone during dictation")
        finally:
            with self._lock:
                self._recovering_input = False

    def _callback(self, indata, frames, time_info, status, token=None):
        if status:
            logger.warning(f"Sounddevice callback status warning: {status}")

        block = indata.copy()
        if not len(block):
            return
        audio_start_callback = None
        limit_callback = None
        close_on_release = False

        with self._lock:
            if token is not None and token is not self._stream_token:
                return
            self.last_callback_time = time.monotonic()
            self.input_peak = max(abs(int(block.min())), abs(int(block.max()))) / 32768
            if self.ring_buffer_samples:
                self.ring_buffer.append(block)
                self.current_ring_samples += len(block)
                self._trim_pre_roll()

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
                close_on_release = not self.ring_buffer_samples

        if audio_start_callback:
            try:
                if audio_start_callback() is False:
                    with self._lock:
                        if self.on_audio_start is audio_start_callback:
                            self._has_triggered_audio_start = False
            except Exception as e:
                logger.error(f"Error in recorder on_audio_start callback: {e}")

        if limit_callback:
            if close_on_release:
                # PortAudio cannot close its stream from inside its callback.
                threading.Thread(target=self._notify_limit, args=(limit_callback,), name="RecordingLimit", daemon=True).start()
            else:
                self._notify_limit(limit_callback)

    @staticmethod
    def _notify_limit(callback):
        try:
            callback()
        except Exception:
            logger.exception("Error in recorder limit-reached callback")

    def start(self, on_limit_reached=None, on_audio_start=None):
        """Start an active capture using the current pre-roll snapshot."""
        with self._stream_lock:
            with self._lock:
                if self._session_active:
                    logger.warning("Start called, but recorder is already active.")
                    return
                stream, token = self.stream, self._stream_token
            if stream is None or not stream.active:
                token = object()
                try:
                    replacement = self._open_stream(self._device_index(self.microphone), token)
                except (OSError, ValueError, RuntimeError, sd.PortAudioError):
                    with self._lock:
                        self.input_issue = "Could not open the microphone. Check your input in Settings."
                    raise
                self._close_stream(stream)
                stream = replacement
                with self._lock:
                    self._reset_pre_roll()

            with self._lock:
                self.stream, self._stream_token = stream, token
                self.input_issue = None
                logger.info("Starting active recording capture...")
                self.audio_blocks = list(self.ring_buffer)
                self.active_samples = 0
                self.on_limit_reached = on_limit_reached
                self.on_audio_start = on_audio_start
                self._has_triggered_audio_start = False
                self.capture_interrupted = False
                self._capture_started_at = time.monotonic()
                self._session_active = True
                self.is_recording = True

    def stop(self):
        """Finalize the capture and close on-demand input."""
        with self._lock:
            if not self._session_active:
                logger.info("Stop called, but recorder is not active.")
                return None

            logger.info("Stopping active recording capture...")
            gap = self._capture_callback_gap(time.monotonic())
            if gap >= self._input_timeout:
                self.capture_interrupted = True
                logger.warning("The last microphone block arrived %.2fs before release.", gap)
            self._capture_started_at = None
            self.is_recording = False
            self._session_active = False
            self.on_audio_start = None
            self.on_limit_reached = None
            audio_blocks = self.audio_blocks
            active_samples = self.active_samples
            self.audio_blocks = []
            self.active_samples = 0
            stream_to_close = None
            if not self.ring_buffer_samples:
                stream_to_close, self.stream = self.stream, None
                self._stream_token = None
                self._reset_pre_roll()

        self._close_stream(stream_to_close)
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

    def recorded_blocks_from(self, index):
        """Return newly captured blocks, including pre-roll for the first read."""
        with self._lock:
            return self.audio_blocks[index:] if self._session_active else []

    def microphone_level(self):
        with self._lock:
            if self.last_callback_time is None or time.monotonic() - self.last_callback_time > 0.6:
                return 0.0
            return self.input_peak

    def diagnostic_state(self):
        """Report callback liveness without calling PortAudio during suspend."""
        if not self._lock.acquire(timeout=0.05):
            return {"audio_state": "lock_busy"}
        try:
            return {
                "audio_stream_present": self.stream is not None,
                "recording": self.is_recording,
                "active_audio_s": round(self.active_samples / self.sample_rate, 2),
                "last_audio_callback_age_s": (
                    round(time.monotonic() - self.last_callback_time, 2)
                    if self.last_callback_time is not None else None
                ),
            }
        finally:
            self._lock.release()

    def cleanup(self):
        """Close the warm input stream during application shutdown/recovery."""
        with self._stream_lock:
            with self._lock:
                stream = self.stream
                self.stream = None
                self._stream_token = None
                self.is_recording = False
                self._session_active = False
            self._close_stream(stream)

    @staticmethod
    def _close_stream(stream):
        if stream:
            try:
                stream.close()
            except Exception as e:
                logger.error(f"Error closing audio stream: {e}")
