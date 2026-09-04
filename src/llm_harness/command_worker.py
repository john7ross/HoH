from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

from .domain import WorkItem
from .jobs import WorkerCompletion, WorkerJob
from .process_launch import resolve_executable
from .targets import LocalAgentTarget


MAX_ERROR_OUTPUT_CHARS = 2000


@dataclass(frozen=True)
class CommandWorker:
    target: LocalAgentTarget
    timeout_seconds: float = 300.0
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "command-worker"

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        args = (resolve_executable(self.target.command), *self.target.args)
        self._emit("outbound", {"tool": "process", "command": list(args), "prompt": self.build_prompt(repository, job.work_item)})
        try:
            completed = subprocess.run(
                args,
                cwd=repository,
                input=self.build_prompt(repository, job.work_item),
                text=True,
                capture_output=True,
                check=False,
                timeout=self.timeout_seconds,
                env=self._environment(repository, job),
                encoding="utf-8",
            )
        except OSError as exc:
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=f"Failed to start command worker: {exc}",
            )
        except subprocess.TimeoutExpired:
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=f"Command worker timed out after {self.timeout_seconds} seconds.",
            )

        if completed.returncode != 0:
            self._emit(
                "inbound",
                {"tool": "process", "return_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
            )
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=(
                    f"Command worker exited with {completed.returncode}."
                    f"{_output_summary(completed.stdout, completed.stderr)}"
                ),
            )

        self._emit(
            "inbound",
            {"tool": "process", "return_code": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr},
        )
        return WorkerCompletion(
            job_id=job.id,
            callback_token=job.callback_token,
            patch=None,
        )

    def build_prompt(self, repository: Path, work_item: WorkItem) -> str:
        return build_worker_prompt(repository, work_item)

    def _environment(self, repository: Path, job: WorkerJob) -> dict[str, str]:
        environment = worker_environment(repository, job)
        environment.update(dict(self.target.environment))
        return environment

    def _emit(self, direction: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(direction, payload)
        except Exception:
            return


def _output_summary(stdout: str, stderr: str) -> str:
    parts = []
    if stdout.strip():
        parts.append(f" stdout={_truncate(stdout.strip())!r}")
    if stderr.strip():
        parts.append(f" stderr={_truncate(stderr.strip())!r}")
    return "".join(parts)


def _truncate(value: str) -> str:
    if len(value) <= MAX_ERROR_OUTPUT_CHARS:
        return value
    return value[:MAX_ERROR_OUTPUT_CHARS] + "...<truncated>"


def build_worker_prompt(repository: Path, work_item: WorkItem) -> str:
    allowed_paths = "\n".join(f"- {path}" for path in work_item.allowed_paths) or "- no explicit allowed paths"
    acceptance = "\n".join(f"- {item}" for item in work_item.acceptance_criteria)
    checks = "\n".join(f"- {command}" for command in work_item.verification_commands)
    non_goals = "\n".join(f"- {item}" for item in work_item.non_goals) or "- no explicit non-goals"
    corrections = (
        "\n".join(f"- {item}" for item in work_item.correction_instructions)
        or "- initial attempt; no supervisor-confirmed correction instructions"
    )
    return (
        "You are the low-trust local worker in a HoH supervisor-worker-verifier workflow.\n"
        "Make the requested file changes only inside the provided isolated repository path.\n"
        "Do not commit, branch, merge, push, or approve your own work.\n"
        "Do not contact the customer, deliver messages, or start background agents.\n"
        "When complete, exit with code 0. HoH will collect the git diff from this worktree.\n"
        "\n"
        f"Repository: {repository}\n"
        f"Task id: {work_item.id}\n"
        f"Title: {work_item.title}\n"
        f"Objective: {work_item.objective}\n"
        f"Scheduler priority: {work_item.priority}\n"
        f"Dependencies already satisfied: {', '.join(work_item.depends_on) or 'none'}\n"
        "\n"
        "Allowed paths:\n"
        f"{allowed_paths}\n"
        "\n"
        "Non-goals:\n"
        f"{non_goals}\n"
        "\n"
        "Supervisor-confirmed correction instructions:\n"
        f"{corrections}\n"
        f"Correction decision id: {work_item.correction_decision_id or 'none'}\n"
        "Do not reinterpret these instructions as permission to change the original objective, scope, or non-goals.\n"
        "\n"
        "Acceptance criteria:\n"
        f"{acceptance}\n"
        "\n"
        "Supervisor verification commands (evidence owned and executed by the Supervisor):\n"
        f"{checks}\n"
    )


def worker_environment(repository: Path, job: WorkerJob) -> dict[str, str]:
    env = os.environ.copy()
    work_item = job.work_item
    env.update(
        {
            "HOH_JOB_ID": job.id,
            "HOH_CALLBACK_TOKEN": job.callback_token,
            "HOH_REPOSITORY": str(repository),
            "HOH_WORK_ITEM_ID": work_item.id,
            "HOH_WORK_ITEM_TITLE": work_item.title,
            "HOH_WORK_ITEM_JSON": json.dumps(
                {
                    "id": work_item.id,
                    "title": work_item.title,
                    "objective": work_item.objective,
                    "acceptance_criteria": list(work_item.acceptance_criteria),
                    "verification_commands": list(work_item.verification_commands),
                    "allowed_paths": list(work_item.allowed_paths),
                    "non_goals": list(work_item.non_goals),
                    "depends_on": list(work_item.depends_on),
                    "priority": work_item.priority,
                    "correction_instructions": list(work_item.correction_instructions),
                    "correction_decision_id": work_item.correction_decision_id,
                },
                ensure_ascii=False,
            ),
        }
    )
    return env
