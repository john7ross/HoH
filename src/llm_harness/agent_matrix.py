"""What each agent can do on this machine, and which version of it is here.

This replaces an earlier certification scheme that kept signed records bound to
a hash of the controller's own source. That machinery suited a vendor making
public compatibility claims; it did not suit this project. It also created the
failure it was meant to prevent: the records outlived the code they attested,
and the documentation went on claiming a certification the build no longer had.

What is left is what an operator actually needs: is the agent here, which
version, and which roles its driver covers. To find out whether a role really
works, run it — `role-conformance` does exactly that in a throwaway repository
and tells you, without writing a record anybody could later misread.
"""

from __future__ import annotations

import platform
import re
import subprocess
from typing import Any, Mapping, Sequence

from .process_launch import resolve_executable


VERSION_ARGUMENT_RE = re.compile(r"@(\d[\w.\-+]*)$")
VERSION_OUTPUT_RE = re.compile(r"(\d+\.\d+(?:\.\d+)?(?:[\w.\-+]*)?)")
AGENT_STATUSES = ("available", "unavailable")


def current_platform() -> str:
    return "-".join(
        part.strip().casefold().replace(" ", "_")
        for part in (platform.system(), platform.machine(), f"python-{platform.python_version()}")
        if part.strip()
    )


def infer_declared_version(args: Sequence[str]) -> str | None:
    """Read the version a launch argument pins, as in `@agentclientprotocol/codex-acp@1.7.0`."""
    for argument in args:
        match = VERSION_ARGUMENT_RE.search(str(argument))
        if match:
            return match.group(1)
    return None


def probe_command_version(command: str, args: Sequence[str] = ("--version",)) -> str | None:
    """Ask the executable itself. Returns None when it is absent or says nothing useful."""
    if not command:
        return None
    try:
        completed = subprocess.run(
            [resolve_executable(command), *args],
            capture_output=True,
            check=False,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (completed.stdout or b"") + b" " + (completed.stderr or b"")
    match = VERSION_OUTPUT_RE.search(output.decode("utf-8", "replace"))
    return match.group(1) if match else None


def agent_matrix(catalog: Mapping[str, Any]) -> dict[str, Any]:
    """One row per agent and role its driver covers."""
    entries: list[dict[str, Any]] = []
    for agent in catalog.get("agents", []):
        if not isinstance(agent, dict):
            continue
        for role in ("supervisor", "worker", "critic"):
            driver = agent.get(f"{role}_driver")
            if not driver:
                continue
            available = bool(agent.get("available"))
            entries.append(
                {
                    "agent": agent.get("name"),
                    "display_name": agent.get("display_name"),
                    "role": role,
                    "driver": driver,
                    "version": str(agent.get("version") or "unknown"),
                    "available": available,
                    "status": "available" if available else "unavailable",
                    "detail": agent.get("detail"),
                }
            )
    return {
        "platform": current_platform(),
        "statuses": list(AGENT_STATUSES),
        "entries": sorted(entries, key=lambda item: (str(item["agent"]), item["role"])),
        "available_count": sum(1 for item in entries if item["available"]),
    }
