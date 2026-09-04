"""Provider credentials entered in the GUI, kept out of project configuration.

Until now a key could only be supplied as an environment variable set outside the
application, which is a wall for anyone who has not read the docs: the interface
asked for a *variable name* and then silently did nothing useful until that
variable existed.

Values are stored per user, one file per name, DPAPI-protected on Windows and
0600 elsewhere — the same treatment the A2A OAuth token cache already gets.
Nothing is written to the project, so a repository still cannot leak a key, and
`config` keeps holding names only.

The environment still wins. Anything already exported keeps working, and CI or a
scheduled run needs no store at all.
"""

from __future__ import annotations

from base64 import b64decode, b64encode
from hashlib import sha256
import json
import os
from pathlib import Path
import re
from typing import Mapping

from .durable_io import atomic_write_text
from .oauth_store import _protect, _unprotect


ENVIRONMENT_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class SecretStoreError(RuntimeError):
    pass


def secret_store_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_SECRET_STORE")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "secrets"
    root = Path(env["XDG_DATA_HOME"]) if env.get("XDG_DATA_HOME") else Path.home() / ".local" / "share"
    return root / "hoh" / "secrets"


class SecretStore:
    """User-scoped credential values, addressed by the environment-variable name they stand in for."""

    def __init__(self, root: Path | None = None) -> None:
        self.root = (root or secret_store_path()).resolve()

    def set(self, name: str, value: str) -> Path:
        normalized = self._normalize(name)
        if not value.strip():
            raise SecretStoreError("A stored credential cannot be empty.")
        protection = "windows-dpapi-user" if os.name == "nt" else "user-file-0600"
        raw = value.encode("utf-8")
        payload = _protect(raw) if os.name == "nt" else raw
        path = self._path(normalized)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            path,
            json.dumps(
                {
                    "schema": 1,
                    "name": normalized,
                    "protection": protection,
                    "payload": b64encode(payload).decode("ascii"),
                },
                separators=(",", ":"),
            )
            + "\n",
        )
        try:
            path.chmod(0o600)
        except OSError:
            pass
        return path

    def get(self, name: str) -> str | None:
        path = self._path(self._normalize(name))
        if not path.exists():
            return None
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
            payload = b64decode(envelope["payload"])
            raw = _unprotect(payload) if envelope.get("protection") == "windows-dpapi-user" else payload
            return raw.decode("utf-8")
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise SecretStoreError(f"Stored credential could not be read: {name}") from exc

    def delete(self, name: str) -> bool:
        path = self._path(self._normalize(name))
        if not path.exists():
            return False
        path.unlink()
        return True

    def names(self) -> tuple[str, ...]:
        if not self.root.is_dir():
            return ()
        found: list[str] = []
        for path in sorted(self.root.glob("*.json")):
            try:
                envelope = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            name = envelope.get("name")
            if isinstance(name, str) and ENVIRONMENT_NAME_RE.fullmatch(name):
                found.append(name)
        return tuple(found)

    def _normalize(self, name: str) -> str:
        candidate = str(name).strip()
        if not ENVIRONMENT_NAME_RE.fullmatch(candidate):
            raise SecretStoreError(
                f"A credential is addressed by an environment-variable name: {name!r}"
            )
        return candidate

    def _path(self, name: str) -> Path:
        return self.root / f"{sha256(name.encode('utf-8')).hexdigest()}.json"


def resolve_credential(
    name: str,
    environment: Mapping[str, str] | None = None,
    store: SecretStore | None = None,
) -> str | None:
    """The environment wins; the store is the fallback for what the GUI saved."""
    env = environment if environment is not None else os.environ
    value = env.get(name)
    if value:
        return value
    try:
        return (store or SecretStore()).get(name)
    except SecretStoreError:
        return None
