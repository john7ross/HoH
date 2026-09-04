from __future__ import annotations

from pathlib import Path
import os
import shutil
import subprocess


def resolve_executable(command: str) -> str:
    """Resolve shell shims before passing a command to subprocess without a shell."""
    candidate = Path(command)
    if candidate.is_absolute() or candidate.parent != Path("."):
        return str(candidate)
    resolved = shutil.which(command)
    return resolved or command


def hidden_process_creation_flags() -> int:
    if os.name != "nt":
        return 0
    return subprocess_creation_flag("CREATE_NO_WINDOW") | subprocess_creation_flag(
        "CREATE_NEW_PROCESS_GROUP"
    )


def subprocess_creation_flag(name: str) -> int:
    # Import lazily so non-Windows Python builds never need Windows-only symbols.
    import subprocess

    return int(getattr(subprocess, name, 0))


def process_group_kwargs() -> dict[str, object]:
    """Popen keyword arguments that make the child killable as a whole tree.

    An agent launcher such as `npx` spawns node; terminating only the direct child
    leaves those descendants holding the attempt worktree. Windows gets its own
    process group so taskkill /T reaches them; POSIX gets its own session so
    killpg does.
    """
    if os.name == "nt":
        return {"creationflags": hidden_process_creation_flags()}
    return {"start_new_session": True}


def terminate_process_tree(process: subprocess.Popen[str], timeout_seconds: float = 5.0) -> None:
    """Terminate the launched process and its descendants on every supported platform."""
    pid = getattr(process, "pid", None)
    if process.poll() is not None:
        return
    if os.name == "nt" and isinstance(pid, int):
        try:
            subprocess.run(
                ("taskkill", "/PID", str(pid), "/T", "/F"),
                text=True,
                capture_output=True,
                check=False,
                timeout=timeout_seconds,
                creationflags=subprocess_creation_flag("CREATE_NO_WINDOW"),
            )
        except (OSError, subprocess.TimeoutExpired):
            process.terminate()
    elif isinstance(pid, int):
        _terminate_posix_group(pid, process, timeout_seconds)
    else:
        process.terminate()
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=timeout_seconds)


def _terminate_posix_group(pid: int, process: subprocess.Popen[str], timeout_seconds: float) -> None:
    import signal

    try:
        group = os.getpgid(pid)
    except (OSError, AttributeError):
        process.terminate()
        return
    if group == os.getpgid(0):
        # The child was not started in its own session; killing the group would
        # take HoH down with it.
        process.terminate()
        return
    try:
        os.killpg(group, signal.SIGTERM)
    except OSError:
        process.terminate()
        return
    try:
        process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(group, signal.SIGKILL)
        except OSError:
            process.kill()
