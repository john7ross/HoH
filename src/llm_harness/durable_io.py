from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import time
from typing import Any


class DurableIOError(OSError):
    pass


def read_authored_text(path: Path) -> str:
    """Read a file a person wrote, tolerating a byte order mark.

    Windows editors and PowerShell's own Set-Content routinely save UTF-8 with a
    BOM. Strict UTF-8 keeps it as a leading \\ufeff, and a config.toml written that
    way failed with "Invalid statement (at line 1, column 1)", which says nothing
    about the real cause. utf-8-sig drops the mark when it is there and is
    otherwise identical.
    """
    return path.read_text(encoding="utf-8-sig")


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temporary_path, path)
        temporary_path = None
        _sync_directory(path.parent)
    except OSError as exc:
        raise DurableIOError(f"Could not durably write {path}: {exc}") from exc
    finally:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def atomic_write_json(path: Path, payload: Any) -> None:
    atomic_write_text(path, json.dumps(payload, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise DurableIOError(f"Corrupted or unreadable JSON state at {path}: {exc}") from exc


def read_jsonl(path: Path) -> tuple[Any, ...]:
    if not path.exists():
        return ()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise DurableIOError(f"Could not read JSONL state at {path}: {exc}") from exc
    records: list[Any] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise DurableIOError(
                f"Corrupted JSONL state at {path}:{line_number}: {exc}"
            ) from exc
    return tuple(records)


def atomic_append_jsonl(path: Path, payload: Any) -> None:
    records = list(read_jsonl(path))
    records.append(payload)
    text = "".join(
        json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n"
        for record in records
    )
    atomic_write_text(path, text)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace_with_retry(source: Path, destination: Path) -> None:
    deadline = time.monotonic() + 0.5
    while True:
        try:
            os.replace(source, destination)
            return
        except PermissionError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
