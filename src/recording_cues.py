"""Preload the recording cues and play them outside the Tk and microphone threads."""

import ctypes as C
from queue import Empty, SimpleQueue
from threading import Thread
from time import monotonic
import wave
import winsound

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


def _method(instance, slot, result, *arguments):
    vtable = C.cast(instance, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *arguments)(vtable[slot])


def _check(result, operation):
    if result < 0:
        raise OSError(f"{operation} returned 0x{result & 0xffffffff:08x}")


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
            self.submit = _method(self.source, 21, hresult, C.POINTER(_AudioBuffer), C.c_void_p)
        except BaseException:
            self.close()
            raise

    def play(self, recording):
        _check(self.submit(self.source, C.byref(self.buffers[recording]), None), "SubmitSourceBuffer")

    def close(self):
        for voice in (self.source, self.master):
            if voice:
                _method(voice, 18, None)(voice)  # IXAudio2Voice::DestroyVoice
                voice.value = None
        if self.engine:
            _method(self.engine, 2, C.c_ulong)(self.engine)  # IUnknown::Release
            self.engine.value = None


class RecordingCuePlayer:
    def __init__(self, enabled):
        self._enabled = enabled
        self._events = SimpleQueue()
        self._thread = Thread(target=self._run, name="RecordingCues", daemon=True)
        self._thread.start()

    def set_recording(self, active):
        self._events.put((active, monotonic()))

    def close(self):
        self._events.put(None)
        self._thread.join(timeout=1)

    def _run(self):
        ole32 = C.WinDLL("ole32")
        ole32.CoInitializeEx.argtypes = (C.c_void_p, C.c_uint32)
        ole32.CoInitializeEx.restype = C.c_long
        initialized = ole32.CoInitializeEx(None, 0) >= 0
        voice = None
        retry_at = 0.0
        recording = False
        try:
            while True:
                if self._enabled():
                    if voice is None and monotonic() >= retry_at:
                        try:
                            voice = _AudioVoice()
                            logger.info("Recording sounds ready on XAudio2.")
                        except (OSError, ValueError, EOFError, wave.Error, AttributeError) as error:
                            logger.debug("Could not prepare recording sounds: %s", error)
                            retry_at = monotonic() + 5
                elif voice is not None:
                    voice.close()
                    voice = None
                    retry_at = 0.0
                try:
                    event = self._events.get(timeout=0.25)
                except Empty:
                    continue
                if event is None:
                    break
                active, received_at = event
                if active == recording:
                    continue
                recording = active
                if not self._enabled() or monotonic() - received_at > 0.25:
                    continue
                try:
                    if voice is not None:
                        voice.play(recording)
                    else:
                        winsound.PlaySound(str(RECORDING_CUES[recording]),
                                           winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
                except (OSError, RuntimeError):
                    logger.exception("Could not play the Dictate recording sound")
                    if voice is not None:
                        voice.close()
                        voice = None
        finally:
            if voice is not None:
                voice.close()
            if initialized:
                ole32.CoUninitialize()
