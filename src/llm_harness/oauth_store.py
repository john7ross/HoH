from __future__ import annotations

from base64 import b64decode, b64encode
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .durable_io import atomic_write_text


class OAuthTokenStoreError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredOAuthToken:
    access_token: str
    refresh_token: str | None
    expires_at: float
    token_type: str = "Bearer"
    scope: str | None = None


class OAuthTokenStore:
    """User-scoped OAuth cache; DPAPI-protected on Windows and mode 0600 elsewhere."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or oauth_token_store_path()).expanduser().resolve()

    def load(self, key: str) -> StoredOAuthToken | None:
        path = self._path(key)
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(envelope, dict) or envelope.get("schema") != 1:
                raise OAuthTokenStoreError("OAuth token cache has an unsupported schema.")
            encoded = envelope.get("payload")
            if not isinstance(encoded, str):
                raise OAuthTokenStoreError("OAuth token cache payload is invalid.")
            protected = b64decode(encoded, validate=True)
            raw = _unprotect(protected) if envelope.get("protection") == "windows-dpapi-user" else protected
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise OAuthTokenStoreError("OAuth token cache record is invalid.")
            return StoredOAuthToken(
                access_token=_required_string(payload, "access_token"),
                refresh_token=_optional_string(payload.get("refresh_token")),
                expires_at=float(payload.get("expires_at", 0)),
                token_type=_required_string(payload, "token_type"),
                scope=_optional_string(payload.get("scope")),
            )
        except OAuthTokenStoreError:
            raise
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise OAuthTokenStoreError("OAuth token cache could not be read.") from exc

    def save(self, key: str, token: StoredOAuthToken) -> Path:
        raw = json.dumps(
            {
                "access_token": token.access_token,
                "refresh_token": token.refresh_token,
                "expires_at": token.expires_at,
                "token_type": token.token_type,
                "scope": token.scope,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        protection = "windows-dpapi-user" if os.name == "nt" else "user-file-0600"
        protected = _protect(raw) if os.name == "nt" else raw
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps(
                {"schema": 1, "protection": protection, "payload": b64encode(protected).decode("ascii")},
                separators=(",", ":"),
            )
            + "\n",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def delete(self, key: str) -> bool:
        path = self._path(key)
        if not path.exists():
            return False
        path.unlink()
        return True

    def _path(self, key: str) -> Path:
        return self.root / f"{sha256(key.encode('utf-8')).hexdigest()}.json"


def oauth_token_store_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    if env.get("HOH_OAUTH_TOKEN_DIR"):
        return Path(env["HOH_OAUTH_TOKEN_DIR"])
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "oauth-tokens"
    state = Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    return state / "hoh" / "oauth-tokens"


def _required_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise OAuthTokenStoreError(f"OAuth token cache is missing {key}.")
    return value


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


if os.name == "nt":
    class _DataBlob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _protect(payload: bytes) -> bytes:
    if os.name != "nt":
        return payload
    source_buffer = ctypes.create_string_buffer(payload)
    source = _DataBlob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_wchar_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    if not crypt32.CryptProtectData(ctypes.byref(source), "HoH OAuth token", None, None, None, 1, ctypes.byref(target)):
        raise OAuthTokenStoreError(f"Windows DPAPI protection failed ({ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)


def _unprotect(payload: bytes) -> bytes:
    if os.name != "nt":
        return payload
    source_buffer = ctypes.create_string_buffer(payload)
    source = _DataBlob(len(payload), ctypes.cast(source_buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _DataBlob()
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    if not crypt32.CryptUnprotectData(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(target)):
        raise OAuthTokenStoreError(f"Windows DPAPI decryption failed ({ctypes.get_last_error()}).")
    try:
        return ctypes.string_at(target.pbData, target.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(target.pbData)
