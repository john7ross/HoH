from __future__ import annotations

import os
from pathlib import PureWindowsPath
import subprocess
from typing import Any, Callable


def install() -> None:
    if os.name != "nt" or os.environ.get("HOH_HERMES_WINDOWS_ACP_WORKAROUND") != "1":
        return
    original = subprocess.run
    if getattr(original, "_hoh_hermes_windows_patch", False):
        return

    def run(*popenargs: Any, **kwargs: Any) -> subprocess.CompletedProcess[Any]:
        command = popenargs[0] if popenargs else kwargs.get("args")
        if _is_git_bash_health_probe(command):
            text_mode = bool(
                kwargs.get("text")
                or kwargs.get("universal_newlines")
                or kwargs.get("encoding")
                or kwargs.get("errors")
            )
            empty: str | bytes = "" if text_mode else b""
            return subprocess.CompletedProcess(command, 0, empty, empty)
        return original(*popenargs, **kwargs)

    setattr(run, "_hoh_hermes_windows_patch", True)
    subprocess.run = run  # type: ignore[assignment]


def _is_git_bash_health_probe(command: object) -> bool:
    if not isinstance(command, (list, tuple)) or len(command) < 5:
        return False
    executable = command[0]
    if not isinstance(executable, (str, os.PathLike)):
        return False
    if PureWindowsPath(executable).name.casefold() != "bash.exe":
        return False
    arguments = tuple(str(item) for item in command[1:])
    return arguments == (
        "--noprofile",
        "--norc",
        "-c",
        "/usr/bin/true; /usr/bin/cat --version >/dev/null",
    )
