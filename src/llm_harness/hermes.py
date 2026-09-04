from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
import os
from pathlib import Path
import subprocess
from typing import Any, Callable

from .acp import AcpError, JsonRpcStdioClient
from .mcp import RoleMcpPolicyConfig, worker_mcp_policy
from .domain import WorkItem, WorkerPatch
from .jobs import WorkerCompletion, WorkerJob
from .targets import LocalAgentTarget
from .process_launch import terminate_process_tree


class HermesAcpError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesAcpStatus:
    ok: bool
    command: tuple[str, ...]
    stdout: str
    stderr: str
    return_code: int


def check_hermes_acp(command: str = "hermes", timeout_seconds: float = 30.0) -> HermesAcpStatus:
    args = (command, "acp", "--check")
    try:
        completed = subprocess.run(
            args,
            text=True,
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
        return HermesAcpStatus(
            ok=completed.returncode == 0,
            command=args,
            stdout=completed.stdout,
            stderr=completed.stderr,
            return_code=completed.returncode,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return HermesAcpStatus(
            ok=False,
            command=args,
            stdout="",
            stderr=str(exc),
            return_code=124,
        )


@dataclass(frozen=True)
class HermesAcpAdapter:
    target: LocalAgentTarget = LocalAgentTarget(name="hermes", command="hermes", args=("acp",))
    turn_timeout_seconds: float = 300.0
    permission_policy: RoleMcpPolicyConfig = field(default_factory=worker_mcp_policy)
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "hermes-acp"

    def build_prompt(self, repository: Path, work_item: WorkItem) -> str:
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
            "Do not modify files outside the task scope.\n"
            "When the task is complete, stop. HoH will collect the diff from this isolated worktree.\n"
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
            "Supervisor verification commands:\n"
            f"{checks}\n"
        )

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        try:
            branch = _current_branch(repository)
        except HermesAcpError as exc:
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=str(exc),
            )
        if not branch.startswith("hoh/attempt/"):
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error="Hermes ACP execution requires an isolated hoh/attempt/* worktree.",
            )

        args = (self.target.command, *self.target.args)
        environment = os.environ.copy()
        environment.update(dict(self.target.environment))
        environment["HERMES_ACP_SKIP_CONFIGURED_MCP"] = "1"
        environment["HOH_HERMES_WINDOWS_ACP_WORKAROUND"] = "1"
        bootstrap = str(Path(__file__).resolve().with_name("_hermes_bootstrap"))
        inherited_python_path = environment.get("PYTHONPATH", "")
        environment["PYTHONPATH"] = os.pathsep.join(
            item for item in (bootstrap, inherited_python_path) if item
        )
        try:
            process = subprocess.Popen(
                args,
                cwd=repository,
                env=environment,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                **_hermes_launch_kwargs(),
            )
        except OSError as exc:
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=f"Failed to start Hermes ACP: {exc}",
            )

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._run_acp_turn, process, repository, job)
        try:
            return future.result(timeout=self.turn_timeout_seconds)
        except TimeoutError:
            _terminate_process(process)
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=f"Hermes ACP turn timed out after {self.turn_timeout_seconds} seconds.",
            )
        finally:
            _terminate_process(process)
            executor.shutdown(wait=False)

    def _run_acp_turn(
        self,
        process: subprocess.Popen[str],
        repository: Path,
        job: WorkerJob,
    ) -> WorkerCompletion:
        try:
            client = JsonRpcStdioClient(
                process,
                permission_policy=self.permission_policy,
                event_sink=self.event_sink,
            )
            client.initialize()
            session_id = client.new_session(repository)
            response = client.prompt(session_id, self.build_prompt(repository, job.work_item))
        except AcpError as exc:
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=str(exc),
            )

        result = response.get("result")
        stop_reason = result.get("stopReason") if isinstance(result, dict) else None
        if stop_reason not in (None, "complete", "end_turn"):
            return WorkerCompletion(
                job_id=job.id,
                callback_token=job.callback_token,
                patch=None,
                error=f"Hermes ACP stopped with reason: {stop_reason}",
            )

        return WorkerCompletion(
            job_id=job.id,
            callback_token=job.callback_token,
            patch=None,
        )

    def completion_from_patch(self, job: WorkerJob, patch: str, notes: str = "") -> WorkerCompletion:
        return WorkerCompletion(
            job_id=job.id,
            callback_token=job.callback_token,
            patch=WorkerPatch(
                worker_name=self.name,
                work_item_id=job.work_item.id,
                patch=patch,
                notes=notes,
            ),
        )


def _current_branch(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise HermesAcpError(completed.stderr.strip() or "git branch --show-current failed")
    return completed.stdout.strip()


def _terminate_process(process: subprocess.Popen[str]) -> None:
    terminate_process_tree(process)


def _hermes_launch_kwargs() -> dict[str, object]:
    if os.name != "nt":
        # Own session, so terminate_process_tree can reach the node children.
        return {"start_new_session": True}
    return {
        "creationflags": (
            subprocess.CREATE_BREAKAWAY_FROM_JOB
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    }
