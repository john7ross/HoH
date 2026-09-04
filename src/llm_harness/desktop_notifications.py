from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import html
import os
from pathlib import Path
import platform
import shutil
import subprocess
from typing import Callable
from uuid import uuid4

from .durable_io import atomic_append_jsonl


@dataclass(frozen=True)
class DesktopNotificationRecord:
    notification_id: str
    created_at_utc: str
    title: str
    text: str
    platform: str
    delivered: bool
    error: str | None = None


def desktop_notification_log_path(environment: dict[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_NOTIFICATION_LOG")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "desktop-notifications.jsonl"
    state_home = Path(env["XDG_STATE_HOME"]) if env.get("XDG_STATE_HOME") else Path.home() / ".local" / "state"
    return state_home / "hoh" / "desktop-notifications.jsonl"


class DesktopNotifier:
    def __init__(
        self,
        log_path: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self.log_path = log_path or desktop_notification_log_path()
        self.runner = runner

    def notify(self, title: str, text: str) -> DesktopNotificationRecord:
        normalized_title = _bounded_text(title, 120)
        normalized_text = _bounded_text(text, 1000)
        system = platform.system()
        delivered = False
        error = None
        try:
            command, stdin = _notification_command(system, normalized_title, normalized_text)
            completed = self.runner(
                command,
                input=stdin,
                text=True,
                capture_output=True,
                check=False,
                timeout=15,
            )
            delivered = completed.returncode == 0
            if not delivered:
                error = (completed.stderr or completed.stdout or f"exit {completed.returncode}").strip()
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            error = str(exc)
        record = DesktopNotificationRecord(
            str(uuid4()),
            datetime.now(UTC).isoformat(),
            normalized_title,
            normalized_text,
            system,
            delivered,
            error,
        )
        atomic_append_jsonl(self.log_path, dict(record.__dict__))
        return record


def _notification_command(system: str, title: str, text: str) -> tuple[list[str], str | None]:
    if system == "Windows":
        executable = shutil.which("powershell.exe") or shutil.which("powershell")
        if not executable:
            raise ValueError("Windows PowerShell is unavailable for desktop notifications.")
        xml = (
            "<toast><visual><binding template=\"ToastGeneric\"><text>"
            + html.escape(title)
            + "</text><text>"
            + html.escape(text)
            + "</text></binding></visual></toast>"
        )
        script = "\n".join(
            (
                "$ErrorActionPreference = 'Stop'",
                "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null",
                "[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null",
                "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument",
                f"$doc.LoadXml('{_powershell_single_quote(xml)}')",
                "$toast = New-Object Windows.UI.Notifications.ToastNotification $doc",
                "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('HoH.AgentHarness').Show($toast)",
            )
        )
        return [executable, "-NoProfile", "-NonInteractive", "-Command", "-"], script
    if system == "Darwin":
        executable = shutil.which("osascript")
        if not executable:
            raise ValueError("osascript is unavailable for desktop notifications.")
        script = f'display notification "{_apple_script(text)}" with title "{_apple_script(title)}"'
        return [executable, "-e", script], None
    executable = shutil.which("notify-send")
    if not executable:
        raise ValueError("notify-send is unavailable for desktop notifications.")
    return [executable, "--app-name=HoH", title, text], None


def _bounded_text(value: str, limit: int) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("Notification title and text must be non-empty.")
    return normalized if len(normalized) <= limit else normalized[: limit - 1] + "…"


def _powershell_single_quote(value: str) -> str:
    return value.replace("'", "''")


def _apple_script(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')
