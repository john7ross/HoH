from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
from shutil import which
import subprocess
import tempfile
from typing import Any, Callable

from .command_worker import build_worker_prompt, worker_environment
from .config import WorkerConfig
from .jobs import WorkerCompletion, WorkerJob
from .targets import LocalAgentTarget


EventSink = Callable[[str, dict[str, Any]], None]
CLAUDE_FILE_TOOLS = "Read,Edit,Write,Glob,Grep"
CLAUDE_FORBIDDEN_ARGUMENTS = {
    "--dangerously-skip-permissions",
    "--allow-dangerously-skip-permissions",
    "--permission-mode",
    "--tools",
    "--allowedtools",
    "--allowed-tools",
    "--disallowedtools",
    "--disallowed-tools",
    "--add-dir",
    "--worktree",
    "--continue",
    "--resume",
    "--mcp-config",
    "--plugin-dir",
    "--plugin-url",
    "--settings",
    "--setting-sources",
    "--agent",
    "--agents",
    "--chrome",
    "--remote-control",
    "--background",
    "--bg",
}
OPENCLAW_FORBIDDEN_ARGUMENTS = {
    "agent",
    "--deliver",
    "--to",
    "--channel",
    "--reply-to",
    "--reply-channel",
    "--reply-account",
    "--session-key",
    "--session-id",
    "--agent",
    "--message",
    "--message-file",
    "--local",
    "--timeout",
    "--json",
}


@dataclass(frozen=True)
class ProviderWorkerProbe:
    worker_type: str
    command: str
    available: bool
    safe: bool
    version: str | None
    detail: str

    @property
    def ok(self) -> bool:
        return self.available and self.safe


@dataclass(frozen=True)
class ClaudeCodeWorker:
    target: LocalAgentTarget
    timeout_seconds: float
    model: str | None = None
    max_budget_usd: float | None = None
    event_sink: EventSink | None = None

    @property
    def name(self) -> str:
        return "claude-code-worker"

    def command_args(self) -> tuple[str, ...]:
        args = [
            self.target.command,
            *self.target.args,
            "-p",
            "--input-format",
            "text",
            "--output-format",
            "json",
            "--no-session-persistence",
            "--safe-mode",
            "--disable-slash-commands",
            "--no-chrome",
            "--permission-mode",
            "dontAsk",
            "--tools",
            CLAUDE_FILE_TOOLS,
            "--allowedTools",
            CLAUDE_FILE_TOOLS,
        ]
        if self.model:
            args.extend(["--model", self.model])
        if self.max_budget_usd is not None:
            args.extend(["--max-budget-usd", str(self.max_budget_usd)])
        return tuple(args)

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        isolation_error = _attempt_isolation_error(repository)
        if isolation_error:
            return _failure(job, isolation_error, "isolation")
        prompt = build_worker_prompt(repository, job.work_item)
        args = self.command_args()
        self._emit(
            "outbound",
            _outbound_evidence(
                self.name,
                _redact_configured_args(args, len(self.target.args)),
                prompt,
                job.work_item.id,
            ),
        )
        environment = worker_environment(repository, job)
        environment.update(dict(self.target.environment))
        try:
            completed = subprocess.run(
                args,
                cwd=repository,
                input=prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                env=environment,
                encoding="utf-8",
            )
        except OSError:
            return _failure(job, "Claude Code worker executable is unavailable.", "unavailable")
        except subprocess.TimeoutExpired:
            return _failure(
                job,
                f"Claude Code worker timed out after {self.timeout_seconds:g} seconds.",
                "timeout",
                retryable=True,
            )
        evidence = _process_evidence(self.name, completed)
        envelope, parse_error = _json_object(completed.stdout)
        if envelope is not None:
            usage = envelope.get("usage")
            evidence.update(
                {
                    "session_id": _safe_scalar(envelope.get("session_id")),
                    "duration_ms": _safe_scalar(envelope.get("duration_ms")),
                    "num_turns": _safe_scalar(envelope.get("num_turns")),
                    "total_cost_usd": _safe_scalar(envelope.get("total_cost_usd")),
                    "input_tokens": (
                        _safe_scalar(usage.get("input_tokens")) if isinstance(usage, dict) else None
                    ),
                    "output_tokens": (
                        _safe_scalar(usage.get("output_tokens")) if isinstance(usage, dict) else None
                    ),
                    "is_error": bool(envelope.get("is_error", False)),
                }
            )
        self._emit("inbound", evidence)
        if completed.returncode != 0:
            kind, retryable = _classify_process_error(completed.stdout, completed.stderr)
            return _failure(
                job,
                f"Claude Code worker exited with code {completed.returncode} ({kind}).",
                kind,
                retryable=retryable,
            )
        if parse_error:
            return _failure(job, "Claude Code worker returned invalid JSON output.", "invalid_output")
        if envelope and envelope.get("is_error"):
            kind, retryable = _classify_process_error(
                str(envelope.get("result", "")),
                completed.stderr,
            )
            return _failure(
                job,
                f"Claude Code worker reported an API error ({kind}).",
                kind,
                retryable=retryable,
            )
        return WorkerCompletion(job.id, job.callback_token, patch=None)

    def _emit(self, direction: str, payload: dict[str, Any]) -> None:
        _emit(self.event_sink, direction, payload)


@dataclass(frozen=True)
class OpenClawWorker:
    target: LocalAgentTarget
    timeout_seconds: float
    model: str
    thinking: str | None = None
    event_sink: EventSink | None = None

    @property
    def name(self) -> str:
        return "openclaw-worker"

    def command_args(
        self,
        job: WorkerJob,
        message_path: Path,
    ) -> tuple[str, ...]:
        args = [
            self.target.command,
            *self.target.args,
            "agent",
            "--local",
            "--session-key",
            f"hoh-{job.id}",
            "--message-file",
            str(message_path),
            "--model",
            self.model,
            "--timeout",
            str(max(1, int(self.timeout_seconds))),
            "--json",
        ]
        if self.thinking:
            args.extend(["--thinking", self.thinking])
        return tuple(args)

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        isolation_error = _attempt_isolation_error(repository)
        if isolation_error:
            return _failure(job, isolation_error, "isolation")
        prompt = build_worker_prompt(repository, job.work_item)
        config_path = _temporary_text(_openclaw_safe_config())
        message_path = _temporary_text(prompt)
        args = self.command_args(job, message_path)
        environment = worker_environment(repository, job)
        environment.update(dict(self.target.environment))
        environment.update(
            {
                "OPENCLAW_CONFIG_PATH": str(config_path),
                "OPENCLAW_WORKSPACE_DIR": str(repository.resolve()),
            }
        )
        self._emit(
            "outbound",
            _outbound_evidence(
                self.name,
                _redact_temporary_arg(
                    _redact_configured_args(args, len(self.target.args)),
                    message_path,
                ),
                prompt,
                job.work_item.id,
                extra={
                    "config_sha256": _sha256_text(_openclaw_safe_config()),
                    "workspace": str(repository.resolve()),
                    "local": True,
                    "delivery": False,
                },
            ),
        )
        try:
            try:
                completed = subprocess.run(
                    args,
                    cwd=repository,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout_seconds + 15,
                    env=environment,
                    encoding="utf-8",
                )
            except OSError:
                return _failure(job, "OpenClaw worker executable is unavailable.", "unavailable")
            except subprocess.TimeoutExpired:
                return _failure(
                    job,
                    f"OpenClaw worker timed out after {self.timeout_seconds:g} seconds.",
                    "timeout",
                    retryable=True,
                )
        finally:
            config_path.unlink(missing_ok=True)
            message_path.unlink(missing_ok=True)
        evidence = _process_evidence(self.name, completed)
        envelope, parse_error = _json_object(completed.stdout)
        if envelope is not None:
            meta = envelope.get("meta")
            evidence.update(
                {
                    "status": _safe_scalar(envelope.get("status")),
                    "transport": (
                        _safe_scalar(meta.get("transport"))
                        if isinstance(meta, dict)
                        else None
                    ),
                    "duration_ms": (
                        _safe_scalar(meta.get("durationMs"))
                        if isinstance(meta, dict)
                        else None
                    ),
                }
            )
        self._emit("inbound", evidence)
        if completed.returncode != 0:
            kind, retryable = _classify_process_error(completed.stdout, completed.stderr)
            return _failure(
                job,
                f"OpenClaw worker exited with code {completed.returncode} ({kind}).",
                kind,
                retryable=retryable,
            )
        if parse_error:
            return _failure(job, "OpenClaw worker returned invalid JSON output.", "invalid_output")
        status = envelope.get("status") if envelope else None
        if status in {"error", "failed", "in_flight"}:
            kind = "session_busy" if status == "in_flight" else "agent_error"
            return _failure(
                job,
                f"OpenClaw worker reported status={status}.",
                kind,
                retryable=status == "in_flight",
            )
        return WorkerCompletion(job.id, job.callback_token, patch=None)

    def _emit(self, direction: str, payload: dict[str, Any]) -> None:
        _emit(self.event_sink, direction, payload)


def validate_provider_worker_config(config: WorkerConfig) -> None:
    worker_type = config.type.casefold()
    if worker_type in {"claude_code", "claude"}:
        _reject_forbidden_args(config.args, CLAUDE_FORBIDDEN_ARGUMENTS, "Claude Code")
    elif worker_type == "openclaw":
        _reject_forbidden_args(config.args, OPENCLAW_FORBIDDEN_ARGUMENTS, "OpenClaw")
        if not config.model:
            raise ValueError("OpenClaw worker requires worker.model for deterministic routing.")


def probe_provider_worker(
    config: WorkerConfig,
    *,
    resolver: Callable[[str], str | None] | None = None,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> ProviderWorkerProbe:
    try:
        validate_provider_worker_config(config)
    except ValueError as exc:
        return ProviderWorkerProbe(config.type, config.command, False, False, None, str(exc))
    resolve = resolver or which
    executable = resolve(config.command)
    if executable is None:
        return ProviderWorkerProbe(
            config.type,
            config.command,
            False,
            True,
            None,
            f"missing command={config.command}",
        )
    try:
        completed = runner(
            [config.command, *config.args, "--version"],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
            encoding="utf-8",
        )
    except (OSError, subprocess.TimeoutExpired):
        return ProviderWorkerProbe(
            config.type,
            config.command,
            False,
            True,
            None,
            "version probe failed",
        )
    version_text = (completed.stdout or completed.stderr).strip().splitlines()
    version = version_text[0][:200] if completed.returncode == 0 and version_text else None
    return ProviderWorkerProbe(
        config.type,
        config.command,
        completed.returncode == 0,
        True,
        version,
        f"path={executable}" if completed.returncode == 0 else f"version exit={completed.returncode}",
    )


def _openclaw_safe_config() -> str:
    payload = {
        "agents": {
            "defaults": {
                "workspace": "${OPENCLAW_WORKSPACE_DIR}",
                "skipBootstrap": True,
            }
        },
        "tools": {
            "allow": ["read", "write", "edit", "apply_patch"],
            "deny": [
                "exec",
                "process",
                "browser",
                "canvas",
                "message",
                "sessions_spawn",
                "sessions_send",
                "cron",
                "nodes",
                "gateway",
                "group:web",
                "group:messaging",
                "group:runtime",
            ],
            "fs": {"workspaceOnly": True},
            "exec": {
                "mode": "deny",
                "applyPatch": {
                    "enabled": True,
                    "workspaceOnly": True,
                },
            },
            "elevated": {"enabled": False},
        },
    }
    return json.dumps(payload, indent=2, ensure_ascii=False) + "\n"


def _attempt_isolation_error(repository: Path) -> str | None:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        return "Worker could not verify the isolated attempt branch."
    if not completed.stdout.strip().startswith("hoh/attempt/"):
        return "Provider worker execution requires an isolated hoh/attempt/* worktree."
    return None


def _failure(
    job: WorkerJob,
    message: str,
    kind: str,
    *,
    retryable: bool = False,
) -> WorkerCompletion:
    return WorkerCompletion(
        job_id=job.id,
        callback_token=job.callback_token,
        patch=None,
        error=message,
        error_kind=kind,
        retryable=retryable,
    )


def _outbound_evidence(
    adapter: str,
    args: tuple[str, ...] | list[str],
    prompt: str,
    work_item_id: str,
    *,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "tool": "process",
        "adapter": adapter,
        "command": list(args),
        "work_item_id": work_item_id,
        "prompt_sha256": _sha256_text(prompt),
        "prompt_chars": len(prompt),
    }
    payload.update(extra or {})
    return payload


def _process_evidence(
    adapter: str,
    completed: subprocess.CompletedProcess[str],
) -> dict[str, Any]:
    return {
        "tool": "process",
        "adapter": adapter,
        "return_code": completed.returncode,
        "stdout_sha256": _sha256_text(completed.stdout),
        "stdout_chars": len(completed.stdout),
        "stderr_sha256": _sha256_text(completed.stderr),
        "stderr_chars": len(completed.stderr),
    }


def _json_object(value: str) -> tuple[dict[str, Any] | None, bool]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return None, True
    return (parsed, False) if isinstance(parsed, dict) else (None, True)


def _classify_process_error(stdout: str, stderr: str) -> tuple[str, bool]:
    text = (stdout + "\n" + stderr).casefold()
    if "429" in text or "rate limit" in text:
        return "rate_limit", True
    if "session_limit" in text or "session limit" in text:
        return "session_limit", True
    if "timeout" in text or "timed out" in text:
        return "timeout", True
    if any(token in text for token in ("authentication", "unauthorized", "api key", "login required")):
        return "authentication", False
    if "insufficient" in text and ("credit" in text or "quota" in text or "balance" in text):
        return "quota", False
    return "process_exit", False


def _reject_forbidden_args(args: tuple[str, ...], forbidden: set[str], label: str) -> None:
    hits = sorted(
        {
            token
            for token in (item.strip().casefold() for item in args)
            if token in forbidden
        }
    )
    if hits:
        raise ValueError(f"{label} worker args contain HoH-owned or unsafe flags: {', '.join(hits)}")


def _temporary_text(value: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".json" if value.lstrip().startswith("{") else ".txt",
        prefix="hoh-worker-",
        delete=False,
    )
    try:
        handle.write(value)
        return Path(handle.name)
    finally:
        handle.close()


def _redact_temporary_arg(args: tuple[str, ...], message_path: Path) -> tuple[str, ...]:
    return tuple("<temporary-prompt-file>" if item == str(message_path) else item for item in args)


def _redact_configured_args(args: tuple[str, ...], count: int) -> tuple[str, ...]:
    if count <= 0:
        return args
    return (
        args[0],
        *("<configured-arg>" for _ in range(count)),
        *args[1 + count :],
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _safe_scalar(value: Any) -> str | int | float | bool | None:
    return value if isinstance(value, (str, int, float, bool)) else None


def _emit(sink: EventSink | None, direction: str, payload: dict[str, Any]) -> None:
    if sink is None:
        return
    try:
        sink(direction, payload)
    except Exception:
        return
