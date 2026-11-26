"""Preload the recording cues and play them outside the Tk and microphone threads."""

import ctypes as C
from array import array
from io import BytesIO
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic
import wave
import winsound

from src.background_audio import BackgroundAudio, default_output_id
from src.background_audio import audio_method as _method, check_audio_result as _check
from src.logger import logger
from src.paths import APP_ROOT


RECORDING_CUES = {
    True: APP_ROOT / "assets" / "sounds" / "recording-start.wav",
    False: APP_ROOT / "assets" / "sounds" / "recording-stop.wav",
}


class _WaveFormat(C.Structure):
    _pack_ = 1
    _fields_ = [
        ("tag", C.c_uint16), ("channels", C.c_uint16), ("sample_rate", C.c_uint32),
        ("byte_rate", C.c_uint32), ("block_size", C.c_uint16), ("bits", C.c_uint16),
        ("extra_size", C.c_uint16),
    ]


class _AudioBuffer(C.Structure):
    _pack_ = 1
    _fields_ = [
        ("flags", C.c_uint32), ("size", C.c_uint32), ("data", C.c_void_p),
        ("play_begin", C.c_uint32), ("play_length", C.c_uint32),
        ("loop_begin", C.c_uint32), ("loop_length", C.c_uint32), ("loop_count", C.c_uint32),
        ("context", C.c_void_p),
    ]


class _VoiceState(C.Structure):
    _fields_ = [("context", C.c_void_p), ("queued", C.c_uint32), ("played", C.c_uint64)]


class _AudioVoice:
    def __init__(self):
        self.engine, self.master, self.source = C.c_void_p(), C.c_void_p(), C.c_void_p()
        self.samples = {}
        self.buffers = {}
        self.dll = C.WinDLL("xaudio2_9.dll", winmode=0x800)  # LOAD_LIBRARY_SEARCH_SYSTEM32
        try:
            create = self.dll.XAudio2Create
            pointer, uint, hresult = C.POINTER(C.c_void_p), C.c_uint32, C.c_long
            create.argtypes = (pointer, uint, uint)
            create.restype = hresult
            _check(create(C.byref(self.engine), 0, 1), "XAudio2Create")
            # IXAudio2::CreateMasteringVoice, default format and virtual default output.
            master = _method(self.engine, 7, hresult, pointer, uint, uint, uint, C.c_wchar_p, C.c_void_p, C.c_int)
            _check(master(self.engine, C.byref(self.master), 0, 0, 0, None, None, 0), "CreateMasteringVoice")
            sample_rate = None
            for recording, path in RECORDING_CUES.items():
                with wave.open(str(path), "rb") as cue:
                    rate = cue.getframerate()
                    if cue.getnchannels() != 1 or cue.getsampwidth() != 2 or sample_rate not in (None, rate):
                        raise ValueError("The recording cues must use the same mono 16-bit PCM format.")
                    sample_rate = rate
                    pcm = cue.readframes(cue.getnframes())
                samples = C.create_string_buffer(pcm)
                self.samples[recording] = samples  # XAudio2 borrows this memory during playback.
                self.buffers[recording] = _AudioBuffer(0x40, len(pcm), C.addressof(samples))
            fmt = _WaveFormat(1, 1, sample_rate, 2 * sample_rate, 2, 16, 0)
            # IXAudio2::CreateSourceVoice and IXAudio2SourceVoice::Start / SubmitSourceBuffer.
            make_source = _method(
                self.engine, 5, hresult, pointer, C.POINTER(_WaveFormat), uint, C.c_float,
                C.c_void_p, C.c_void_p, C.c_void_p,
            )
            _check(make_source(self.engine, C.byref(self.source), C.byref(fmt), 0, 2.0, None, None, None),
                   "CreateSourceVoice")
            _check(_method(self.source, 19, hresult, uint, uint)(self.source, 0, 0), "Start")
            self.set_volume = _method(self.source, 12, hresult, C.c_float, uint)
            self.submit = _method(self.source, 21, hresult, C.POINTER(_AudioBuffer), C.c_void_p)
            self.get_state = _method(self.source, 25, None, C.POINTER(_VoiceState), uint)
            self.last_submission = None
        except BaseException:
            self.close()
            raise

    def play(self, recording, volume):
        _check(self.set_volume(self.source, volume, 0), "SetVolume")
        _check(self.submit(self.source, C.byref(self.buffers[recording]), None), "SubmitSourceBuffer")
        self.last_submission = monotonic()

    def stalled(self):
        if self.last_submission is None or monotonic() - self.last_submission < 0.3:
            return False
        state = _VoiceState()
        self.get_state(self.source, C.byref(state), 0x100)  # XAUDIO2_VOICE_NOSAMPLESPLAYED
        return bool(state.queued)

    def close(self):
        for voice in (self.source, self.master):
            if voice:
                _method(voice, 18, None)(voice)  # IXAudio2Voice::DestroyVoice
                voice.value = None
        if self.engine:
            _method(self.engine, 2, C.c_ulong)(self.engine)  # IUnknown::Release
            self.engine.value = None


def _play_fallback(recording, volume):
    with wave.open(str(RECORDING_CUES[recording]), "rb") as original:
        samples = array("h", original.readframes(original.getnframes()))
        scaled = array("h", (round(sample * volume) for sample in samples))
        buffer = BytesIO()
        with wave.open(buffer, "wb") as cue:
            cue.setparams(original.getparams())
            cue.writeframes(scaled.tobytes())
    winsound.PlaySound(buffer.getvalue(), winsound.SND_MEMORY | winsound.SND_NODEFAULT)


def _output_id():
    try:
        return default_output_id()
    except OSError:
        return None


class RecordingCuePlayer:
    def __init__(self, enabled, volume, duck_audio):
        self._enabled = enabled
        self._volume = volume
        self._duck_audio = duck_audio
        self._events = SimpleQueue()
        self._thread = Thread(target=self._run, name="RecordingCues", daemon=True)
        self._thread.start()

    def set_recording(self, active):
        self._events.put(active)

    def close(self):
        self._events.put(None)
        self._thread.join(timeout=1)

    def _run(self):
        ole32 = C.WinDLL("ole32")
        ole32.CoInitializeEx.argtypes = (C.c_void_p, C.c_uint32)
        ole32.CoInitializeEx.restype = C.c_long
        initialized = ole32.CoInitializeEx(None, 0) >= 0
        voice = None
        voice_output = None
        other_audio = BackgroundAudio()
        retry_at = 0.0
        recording = False
        try:
            while True:
                if self._enabled():
                    if voice is None and monotonic() >= retry_at:
                        try:
                            voice = _AudioVoice()
                            voice_output = _output_id() if initialized else None
                            logger.info("Recording sounds ready on XAudio2.")
                        except (OSError, ValueError, EOFError, wave.Error, AttributeError) as error:
                            logger.debug("Could not prepare recording sounds: %s", error)
                            retry_at = monotonic() + 5
                elif voice is not None:
                    voice.close()
                    voice = None
                    retry_at = 0.0
                try:
                    active = self._events.get(timeout=0.25)
                except Empty:
                    continue
                if active is None:
                    break
                if active == recording:
                    continue
                recording = active
                if not recording:
                    other_audio.restore()
                volume = self._volume()
                if self._enabled() and volume:
                    current_output = _output_id() if initialized else None
                    if voice is not None and (voice.stalled() or current_output and current_output != voice_output):
                        voice.close()
                        voice = None
                        retry_at = 0.0
                    if voice is not None:
                        try:
                            voice.play(recording, volume)
                            logger.debug("Recording cue queued on XAudio2: recording=%s", recording)
                        except OSError as exc:
                            logger.warning("XAudio2 recording cue was not queued: %s", exc)
                            voice.close()
                            voice = None
                            retry_at = monotonic() + 5
                    try:
                        if voice is None:
                            _play_fallback(recording, volume)
                            logger.debug("Recording cue played through Windows sound: recording=%s", recording)
                    except (OSError, RuntimeError, EOFError, wave.Error):
                        logger.exception("Could not play the Dictate recording sound")
                if recording and initialized and self._duck_audio():
                    other_audio.lower()
        finally:
            other_audio.restore()
            if voice is not None:
                voice.close()
            if initialized:
                ole32.CoUninitialize()
