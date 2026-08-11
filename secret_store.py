"""Tenant-bound secret protection for the Windows desktop application.

Secrets are encrypted with Windows DPAPI for the current OS user and additional
tenant-specific entropy.  Ciphertext can be copied or backed up, but it cannot
be decrypted by another Windows account or silently reused by another tenant.
"""

from __future__ import annotations

import base64
import ctypes
from ctypes import wintypes
import hashlib
import os


PREFIX = "dpapi:v1:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class SecretStoreError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD),
                ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def is_protected(value):
    return isinstance(value, str) and value.startswith(PREFIX)


def _blob(value):
    raw = bytes(value)
    buffer = ctypes.create_string_buffer(raw, max(1, len(raw)))
    blob = _DataBlob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    return blob, buffer


def _entropy(scope):
    return hashlib.sha256(("wenmo-secret-v1:" + str(scope)).encode("utf-8")).digest()


def _crypt32():
    if os.name != "nt":
        raise SecretStoreError("DPAPI secret storage is only available on Windows")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), wintypes.LPCWSTR, ctypes.POINTER(_DataBlob),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p,
        wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    return crypt32, kernel32


def protect_secret(value, scope):
    value = str(value or "")
    if not value or is_protected(value):
        return value
    crypt32, kernel32 = _crypt32()
    source, source_buffer = _blob(value.encode("utf-8"))
    entropy, entropy_buffer = _blob(_entropy(scope))
    output = _DataBlob()
    # Keep buffers alive until CryptProtectData returns.
    _ = source_buffer, entropy_buffer
    if not crypt32.CryptProtectData(
            ctypes.byref(source), "Wenmo tenant credential", ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output)):
        raise SecretStoreError("DPAPI encryption failed: %s" % ctypes.get_last_error())
    try:
        encrypted = ctypes.string_at(output.pbData, output.cbData)
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)
    return PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")


def reveal_secret(value, scope):
    value = str(value or "")
    if not value or not is_protected(value):
        return value
    try:
        encrypted = base64.urlsafe_b64decode(value[len(PREFIX):].encode("ascii"))
    except Exception as exc:
        raise SecretStoreError("invalid DPAPI ciphertext") from exc
    crypt32, kernel32 = _crypt32()
    source, source_buffer = _blob(encrypted)
    entropy, entropy_buffer = _blob(_entropy(scope))
    output = _DataBlob()
    description = wintypes.LPWSTR()
    _ = source_buffer, entropy_buffer
    if not crypt32.CryptUnprotectData(
            ctypes.byref(source), ctypes.byref(description), ctypes.byref(entropy),
            None, None, _CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output)):
        raise SecretStoreError("DPAPI decryption failed: %s" % ctypes.get_last_error())
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        if output.pbData:
            kernel32.LocalFree(output.pbData)
        if description:
            kernel32.LocalFree(description)
