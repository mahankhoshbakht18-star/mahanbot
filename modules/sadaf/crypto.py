# -*- coding: utf-8 -*-
"""Windows DPAPI wrappers. Secrets stay bound to this Windows user."""
import ctypes
import sys
from ctypes import wintypes


class _Blob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]


_UI_FORBIDDEN = 0x01


def _protect(data: bytes, encrypt: bool) -> bytes:
    if sys.platform != "win32":
        raise OSError("رمزنگاری مشتری فقط روی ویندوز در دسترس است.")
    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data)
    incoming = _Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    outgoing = _Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    args = [ctypes.byref(incoming), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(outgoing)]
    if not encrypt:
        args = [ctypes.byref(incoming), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(outgoing)]
    if not fn(*args):
        raise OSError(ctypes.get_last_error() or 1, "DPAPI")
    try:
        return ctypes.string_at(outgoing.pbData, outgoing.cbData)
    finally:
        kernel32.LocalFree(outgoing.pbData)


def seal(text: str) -> bytes:
    return _protect(text.encode("utf-8"), True)


def open_secret(blob: bytes) -> str:
    return _protect(blob, False).decode("utf-8")
