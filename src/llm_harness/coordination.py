from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time
from typing import Any, Iterator
from uuid import uuid4

from .durable_io import DurableIOError, atomic_write_json, read_json


class CoordinationError(RuntimeError):
    pass


class LockContendedError(CoordinationError):
    def __init__(self, lock_path: Path, timeout_seconds: float, owner: dict[str, Any] | None) -> None:
        self.lock_path = lock_path
        self.timeout_seconds = timeout_seconds
        self.owner = owner
        owner_text = json.dumps(owner, sort_keys=True, ensure_ascii=False) if owner else "unknown"
        super().__init__(
            f"Timed out after {timeout_seconds:.3f}s waiting for {lock_path}; owner={owner_text}"
        )


@dataclass(frozen=True)
class CoordinationConfig:
    state_lock_timeout_seconds: float = 5.0
    execution_lock_timeout_seconds: float = 5.0
    poll_interval_seconds: float = 0.05

    def __post_init__(self) -> None:
        if self.state_lock_timeout_seconds <= 0:
            raise ValueError("coordination.state_lock_timeout_seconds must be greater than zero.")
        if self.execution_lock_timeout_seconds <= 0:
            raise ValueError("coordination.execution_lock_timeout_seconds must be greater than zero.")
        if self.poll_interval_seconds <= 0:
            raise ValueError("coordination.poll_interval_seconds must be greater than zero.")


@dataclass(frozen=True)
class LeaseStatus:
    lock_path: Path
    metadata_path: Path
    available: bool
    stale_owner: bool
    owner: dict[str, Any] | None
    metadata_error: str | None = None


class FileLease:
    def __init__(
        self,
        lock_path: Path,
        *,
        kind: str,
        default_timeout_seconds: float,
        poll_interval_seconds: float,
    ) -> None:
        self.lock_path = lock_path
        self.metadata_path = lock_path.with_suffix(lock_path.suffix + ".owner.json")
        self.kind = kind
        self.default_timeout_seconds = default_timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds
        self._thread_lock = threading.RLock()
        self._local = threading.local()
        self._handle: Any = None
        self._owner_payload: dict[str, Any] | None = None

    @contextmanager
    def hold(
        self,
        action: str,
        *,
        timeout_seconds: float | None = None,
        command: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        owner = self.acquire(
            action,
            timeout_seconds=timeout_seconds,
            command=command,
        )
        try:
            yield owner
        finally:
            self.release()

    def acquire(
        self,
        action: str,
        *,
        timeout_seconds: float | None = None,
        command: str | None = None,
    ) -> dict[str, Any]:
        timeout = self.default_timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout < 0:
            raise ValueError("Lock timeout must be zero or greater.")
        deadline = time.monotonic() + timeout
        if not self._thread_lock.acquire(timeout=timeout):
            owner, _ = self._read_metadata()
            raise LockContendedError(self.lock_path, timeout, owner)
        depth = getattr(self._local, "depth", 0)
        if depth:
            self._local.depth = depth + 1
            assert self._owner_payload is not None
            return self._owner_payload

        handle = None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            handle = self.lock_path.open("a+b", buffering=0)
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            while True:
                if _try_lock(handle):
                    break
                if time.monotonic() >= deadline:
                    owner, _ = self._read_metadata()
                    raise LockContendedError(self.lock_path, timeout, owner)
                time.sleep(min(self.poll_interval_seconds, max(0.0, deadline - time.monotonic())))

            previous, previous_error = self._read_metadata()
            recovered_from = (
                previous
                if previous is not None and previous.get("status") == "active"
                else None
            )
            owner = {
                "version": 1,
                "lease_id": str(uuid4()),
                "kind": self.kind,
                "status": "active",
                "pid": os.getpid(),
                "hostname": socket.gethostname(),
                "command": command or _default_command(),
                "action": action,
                "acquired_at_utc": _utc_now(),
            }
            if recovered_from is not None:
                owner["recovered_from"] = recovered_from
            if previous_error is not None:
                owner["recovered_metadata_error"] = previous_error
            atomic_write_json(self.metadata_path, owner)
            self._handle = handle
            self._owner_payload = owner
            self._local.depth = 1
            return owner
        except Exception:
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
            self._thread_lock.release()
            raise

    def release(self) -> None:
        depth = getattr(self._local, "depth", 0)
        if depth < 1:
            raise CoordinationError(f"Lease {self.lock_path} is not held by this thread.")
        if depth > 1:
            self._local.depth = depth - 1
            self._thread_lock.release()
            return
        assert self._handle is not None
        handle = self._handle
        try:
            released = dict(self._owner_payload or {})
            released["status"] = "released"
            released["released_at_utc"] = _utc_now()
            try:
                atomic_write_json(self.metadata_path, released)
            except OSError:
                # The kernel lock is authoritative. A failed release-marker write
                # remains visible as stale metadata on the next status/recovery.
                pass
            try:
                _unlock(handle)
            finally:
                handle.close()
        finally:
            self._handle = None
            self._owner_payload = None
            self._local.depth = 0
            self._thread_lock.release()

    def status(self) -> LeaseStatus:
        owner, metadata_error = self._read_metadata()
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+b", buffering=0)
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
                os.fsync(handle.fileno())
            available = _try_lock(handle)
            if available:
                _unlock(handle)
            return LeaseStatus(
                lock_path=self.lock_path,
                metadata_path=self.metadata_path,
                available=available,
                stale_owner=bool(
                    available and owner is not None and owner.get("status") == "active"
                ),
                owner=owner,
                metadata_error=metadata_error,
            )
        finally:
            handle.close()

    def recover(self, *, command: str | None = None) -> dict[str, Any]:
        with self.hold("operator.recover", timeout_seconds=0, command=command) as owner:
            return dict(owner)

    def _read_metadata(self) -> tuple[dict[str, Any] | None, str | None]:
        try:
            payload = read_json(self.metadata_path, default=None)
        except DurableIOError as exc:
            return None, str(exc)
        if payload is None:
            return None, None
        if not isinstance(payload, dict):
            return None, f"Invalid lease metadata object at {self.metadata_path}"
        return payload, None


_LEASES_LOCK = threading.Lock()
_LEASES: dict[str, FileLease] = {}


def shared_file_lease(
    lock_path: Path,
    *,
    kind: str,
    default_timeout_seconds: float,
    poll_interval_seconds: float,
) -> FileLease:
    key = os.path.normcase(str(lock_path.resolve()))
    with _LEASES_LOCK:
        lease = _LEASES.get(key)
        if lease is None:
            lease = FileLease(
                lock_path,
                kind=kind,
                default_timeout_seconds=default_timeout_seconds,
                poll_interval_seconds=poll_interval_seconds,
            )
            _LEASES[key] = lease
        else:
            lease.default_timeout_seconds = default_timeout_seconds
            lease.poll_interval_seconds = poll_interval_seconds
        return lease


def state_lease(root: Path, config: CoordinationConfig = CoordinationConfig()) -> FileLease:
    return shared_file_lease(
        root / ".coordination" / "state.lock",
        kind="state",
        default_timeout_seconds=config.state_lock_timeout_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
    )


def repository_execution_lease(
    repository: Path,
    config: CoordinationConfig = CoordinationConfig(),
) -> FileLease:
    root = repository_coordination_root(repository)
    return shared_file_lease(
        root / "execution.lock",
        kind="repository-execution",
        default_timeout_seconds=config.execution_lock_timeout_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
    )


def repository_coordination_root(repository: Path) -> Path:
    resolved = repository.resolve()
    digest = hashlib.sha256(str(resolved).casefold().encode("utf-8")).hexdigest()[:12]
    return resolved.parent / ".hoh-leases" / f"{resolved.name}-{digest}"


def named_state_operation_lease(
    root: Path,
    name: str,
    config: CoordinationConfig = CoordinationConfig(),
) -> FileLease:
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789-" for character in name):
        raise ValueError("Operation lease name must use lowercase letters, digits, or hyphens.")
    return shared_file_lease(
        root / ".coordination" / f"{name}.lock",
        kind=f"state-operation:{name}",
        default_timeout_seconds=config.execution_lock_timeout_seconds,
        poll_interval_seconds=config.poll_interval_seconds,
    )


def _try_lock(handle: Any) -> bool:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl

    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock(handle: Any) -> None:
    handle.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _default_command() -> str:
    executable = Path(sys.argv[0]).name if sys.argv else "python"
    subcommand = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else ""
    return f"{executable} {subcommand}".strip()
