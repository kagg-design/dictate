"""Temporarily lower other Windows audio sessions on the default output."""

from contextlib import contextmanager
import ctypes as C
from os import getpid
from uuid import UUID

from src.logger import logger


CLSID_DEVICE_ENUMERATOR = UUID("bcde0395-e52f-467c-8e3d-c4579291692e")
IID_DEVICE_ENUMERATOR = UUID("a95664d2-9614-4f35-a746-de8db63617e6")
IID_SESSION_MANAGER = UUID("77aa99a0-1bd6-484f-8bc7-2c654c9a9b6f")
IID_SESSION_CONTROL = UUID("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d")
IID_SESSION_VOLUME = UUID("87ce5498-68d6-44e5-9215-6da47ef883d8")


def audio_method(instance, slot, result, *arguments):
    vtable = C.cast(instance, C.POINTER(C.POINTER(C.c_void_p))).contents
    return C.WINFUNCTYPE(result, C.c_void_p, *arguments)(vtable[slot])


def check_audio_result(result, operation):
    if result < 0:
        raise OSError(f"{operation} returned 0x{result & 0xffffffff:08x}")


def _release(instance):
    audio_method(instance, 2, C.c_ulong)(instance)


@contextmanager
def _interface(operation, *args):
    pointer = C.c_void_p()
    check_audio_result(operation(*args, C.byref(pointer)), "Core Audio")
    try:
        yield pointer
    finally:
        _release(pointer)


def _query(instance, iid):
    query = audio_method(instance, 0, C.c_long, C.c_void_p, C.POINTER(C.c_void_p))
    return _interface(query, instance, iid.bytes_le)


class BackgroundAudio:
    def __init__(self):
        self._sessions = []

    def lower(self):
        uint, ptr, pointer = C.c_uint32, C.c_void_p, C.POINTER(C.c_void_p)
        create = C.WinDLL("ole32").CoCreateInstance
        create.argtypes = (ptr, ptr, uint, ptr, pointer)
        create.restype = C.c_long
        try:
            with _interface(create, CLSID_DEVICE_ENUMERATOR.bytes_le, None, 1, IID_DEVICE_ENUMERATOR.bytes_le) as devices:
                default = audio_method(devices, 4, C.c_long, C.c_int, C.c_int, pointer)
                with _interface(default, devices, 0, 1) as device:  # eRender, eMultimedia
                    activate = audio_method(device, 3, C.c_long, ptr, uint, ptr, pointer)
                    with _interface(activate, device, IID_SESSION_MANAGER.bytes_le, 1, None) as manager:
                        self._lower_sessions(manager)
        except OSError as exc:
            logger.debug("Could not lower background audio: %s", exc)

    def _lower_sessions(self, manager):
        pointer = C.POINTER(C.c_void_p)
        enumerate_sessions = audio_method(manager, 5, C.c_long, pointer)
        with _interface(enumerate_sessions, manager) as sessions:
            get_count = audio_method(sessions, 3, C.c_long, C.POINTER(C.c_int))
            count = C.c_int()
            check_audio_result(get_count(sessions, C.byref(count)), "GetCount")
            get_session = audio_method(sessions, 4, C.c_long, C.c_int, pointer)
            for index in range(count.value):
                try:
                    with _interface(get_session, sessions, index) as session:
                        with _query(session, IID_SESSION_CONTROL) as control:
                            if self._is_background(control):
                                self._lower_session(session)
                except OSError:
                    logger.debug("An audio session disappeared while starting dictation.")

    @staticmethod
    def _is_background(control):
        get_pid = audio_method(control, 14, C.c_long, C.POINTER(C.c_uint32))
        pid = C.c_uint32()
        status = get_pid(control, C.byref(pid))
        return status == 0 and pid.value not in (0, getpid())

    @staticmethod
    def _get(volume):
        current = C.c_float()
        read = audio_method(volume, 4, C.c_long, C.POINTER(C.c_float))
        check_audio_result(read(volume, C.byref(current)), "GetMasterVolume")
        return current.value

    @staticmethod
    def _set(volume, value):
        write = audio_method(volume, 3, C.c_long, C.c_float, C.c_void_p)
        check_audio_result(write(volume, value, None), "SetMasterVolume")

    def _lower_session(self, session):
        with _query(session, IID_SESSION_VOLUME) as volume:
            original = self._get(volume)
            if original <= 0.001:
                return
            lowered = original * 0.2
            self._set(volume, lowered)
            audio_method(volume, 1, C.c_ulong)(volume)
            self._sessions.append((volume, original, lowered))

    def restore(self):
        sessions, self._sessions = self._sessions, []
        for volume, original, lowered in sessions:
            try:
                if abs(self._get(volume) - lowered) < 0.001:
                    self._set(volume, original)
            except OSError:
                logger.debug("An audio session disappeared while finishing dictation.")
            finally:
                _release(volume)
