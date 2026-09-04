from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable

from .command_worker import build_worker_prompt, worker_environment
from .config import ProcessProfileConfig, validate_process_arguments
from .jobs import WorkerCompletion, WorkerJob
from .process_launch import resolve_executable
from .targets import LocalAgentTarget


MAX_EVIDENCE_CHARS = 4000


@dataclass(frozen=True)
class ProfiledProcessWorker:
    target: LocalAgentTarget
    profile: ProcessProfileConfig
    timeout_seconds: float = 300.0
    model: str | None = None
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return f"process-{self.profile.profile_id}-worker"

    def command_args(self, prompt_path: Path | None = None) -> tuple[str, ...]:
        validate_process_worker_config(self.target.args, self.profile)
        args = [
            resolve_executable(self.target.command),
            *self.target.args,
            *self.profile.required_args,
        ]
        if self.model is not None:
            if self.profile.model_argument is None:
                raise ValueError(
                    f"Process profile '{self.profile.profile_id}' does not declare model_argument."
                )
            args.extend((self.profile.model_argument, self.model))
        if self.profile.prompt_transport == "file":
            if prompt_path is None:
                raise ValueError("File process profiles require a temporary prompt path.")
            args.extend((self.profile.prompt_argument or "", str(prompt_path)))
        return tuple(args)

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        prompt = build_worker_prompt(repository, job.work_item)
        prompt_path = _temporary_prompt(prompt) if self.profile.prompt_transport == "file" else None
        try:
            try:
                args = self.command_args(prompt_path)
            except (OSError, ValueError) as exc:
                return _failure(job, f"Invalid process profile invocation: {exc}")
            self._emit(
                "outbound",
                {
                    "tool": "process",
                    "profile_id": self.profile.profile_id,
                    "prompt_transport": self.profile.prompt_transport,
                    "command": _redacted_args(args, prompt_path),
                    "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                    "work_item_id": job.work_item.id,
                },
            )
            environment = worker_environment(repository, job)
            environment.update(dict(self.target.environment))
            try:
                completed = subprocess.run(
                    args,
                    cwd=repository,
                    input=prompt if self.profile.prompt_transport == "stdin" else None,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.timeout_seconds,
                    env=environment,
                    encoding="utf-8",
                )
            except OSError as exc:
                return _failure(job, f"Failed to start process-profile worker: {exc}")
            except subprocess.TimeoutExpired:
                return _failure(
                    job,
                    f"Process-profile worker timed out after {self.timeout_seconds:g} seconds.",
                )
            self._emit(
                "inbound",
                {
                    "tool": "process",
                    "profile_id": self.profile.profile_id,
                    "return_code": completed.returncode,
                    "stdout": _truncate(completed.stdout),
                    "stderr": _truncate(completed.stderr),
                },
            )
            if completed.returncode != 0:
                return _failure(
                    job,
                    f"Process-profile worker exited with {completed.returncode}."
                    f"{_output_summary(completed.stdout, completed.stderr)}",
                )
            return WorkerCompletion(job.id, job.callback_token, patch=None)
        finally:
            if prompt_path is not None:
                prompt_path.unlink(missing_ok=True)

    def _emit(self, direction: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(direction, payload)
        except Exception:
            return


def validate_process_worker_config(
    configured_args: tuple[str, ...],
    profile: ProcessProfileConfig,
) -> None:
    validate_process_arguments(configured_args, profile)


def _temporary_prompt(prompt: str) -> Path:
    handle = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        suffix=".txt",
        prefix="hoh-process-prompt-",
        delete=False,
    )
    try:
        handle.write(prompt)
        return Path(handle.name)
    finally:
        handle.close()


def _argument_key(value: str) -> str:
    return value.split("=", 1)[0].strip().casefold()


def _redacted_args(args: tuple[str, ...], prompt_path: Path | None) -> list[str]:
    prompt = str(prompt_path) if prompt_path is not None else None
    return ["<temporary-prompt-file>" if prompt is not None and item == prompt else item for item in args]


def _output_summary(stdout: str, stderr: str) -> str:
    parts: list[str] = []
    if stdout.strip():
        parts.append(f" stdout={_truncate(stdout.strip())!r}")
    if stderr.strip():
        parts.append(f" stderr={_truncate(stderr.strip())!r}")
    return "".join(parts)


def _truncate(value: str) -> str:
    if len(value) <= MAX_EVIDENCE_CHARS:
        return value
    return value[:MAX_EVIDENCE_CHARS] + "...<truncated>"


def _failure(job: WorkerJob, message: str) -> WorkerCompletion:
    return WorkerCompletion(
        job_id=job.id,
        callback_token=job.callback_token,
        patch=None,
        error=message,
    )
