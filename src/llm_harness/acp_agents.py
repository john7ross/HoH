from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import subprocess
import tempfile
from typing import Any, Callable, Mapping

from .acp import AcpError, JsonRpcStdioClient
from .command_worker import build_worker_prompt, worker_environment
from .config import CriticConfig, RoleIdentityConfig
from .mcp import RoleMcpPolicyConfig, worker_mcp_policy
from .domain import WorkerPatch
from .jobs import WorkerCompletion, WorkerJob
from .process_launch import process_group_kwargs, resolve_executable, terminate_process_tree
from .targets import LocalAgentTarget


class AcpAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class AcpWorkerAdapter:
    target: LocalAgentTarget
    turn_timeout_seconds: float = 300.0
    model: str | None = None
    permission_policy: RoleMcpPolicyConfig = field(default_factory=worker_mcp_policy)
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "acp-worker"

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        try:
            branch = _current_branch(repository)
        except AcpAdapterError as exc:
            return _worker_error(job, str(exc))
        if not branch.startswith("hoh/attempt/"):
            return _worker_error(job, "ACP execution requires an isolated hoh/attempt/* worktree.")

        args = (resolve_executable(self.target.command), *self.target.args)
        environment = worker_environment(repository, job)
        environment.update(dict(self.target.environment))
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
                **process_group_kwargs(),
            )
        except OSError as exc:
            return _worker_error(job, f"Failed to start ACP worker: {exc}")

        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(self._run_turn, process, repository, job)
        try:
            return future.result(timeout=self.turn_timeout_seconds)
        except TimeoutError:
            _terminate_process(process)
            return _worker_error(job, f"ACP worker timed out after {self.turn_timeout_seconds} seconds.")
        finally:
            _terminate_process(process)
            executor.shutdown(wait=False)

    def _run_turn(
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
            if self.model and self.model.casefold() not in {"agent-default", "worker-agent"}:
                client.select_session_option(session_id, "model", self.model)
            response = client.prompt(session_id, build_worker_prompt(repository, job.work_item))
        except AcpError as exc:
            return _worker_error(job, str(exc))
        stop_reason = _stop_reason(response)
        if stop_reason not in {None, "complete", "end_turn"}:
            return _worker_error(job, f"ACP worker stopped with reason: {stop_reason}")
        return WorkerCompletion(job_id=job.id, callback_token=job.callback_token, patch=None)

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


@dataclass(frozen=True)
class AcpCriticAdapter:
    config: CriticConfig
    identity: RoleIdentityConfig
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "acp-critic"

    def review(self, repository: Path, bundle: Mapping[str, Any]) -> dict[str, Any]:
        # Import here to avoid a module cycle: critic_adapters owns the judgment contract.
        from .critic_adapters import CriticAdapterError, build_critic_decision, build_critic_prompt

        args = (resolve_executable(self.config.command), *self.config.args)
        try:
            with tempfile.TemporaryDirectory(prefix="hoh-acp-critic-") as isolated_cwd:
                try:
                    process = subprocess.Popen(
                        args,
                        cwd=isolated_cwd,
                        env={**os.environ, **dict(self.config.environment)},
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        text=True,
                        encoding="utf-8",
                        **process_group_kwargs(),
                    )
                except OSError as exc:
                    raise AcpAdapterError(f"Failed to start ACP critic: {exc}") from exc
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(
                    self._run_review,
                    process,
                    Path(isolated_cwd),
                    build_critic_prompt(bundle),
                )
                try:
                    judgment = future.result(timeout=self.config.timeout_seconds)
                except TimeoutError as exc:
                    _terminate_process(process)
                    raise AcpAdapterError(
                        f"ACP critic timed out after {self.config.timeout_seconds} seconds."
                    ) from exc
                finally:
                    _terminate_process(process)
                    executor.shutdown(wait=False)
        except AcpAdapterError as exc:
            raise CriticAdapterError(str(exc)) from exc
        return build_critic_decision(bundle, self.identity, judgment)

    def _run_review(
        self,
        process: subprocess.Popen[str],
        cwd: Path,
        prompt: str,
    ) -> dict[str, Any]:
        try:
            client = JsonRpcStdioClient(
                process,
                permission_policy=self.config.mcp,
                event_sink=self.event_sink,
            )
            client.initialize()
            session_id = client.new_session(cwd)
            if self.identity.model.casefold() not in {"agent-default", "critic-agent"}:
                client.select_session_option(session_id, "model", self.identity.model)
            response = client.prompt(session_id, prompt)
        except AcpError as exc:
            raise AcpAdapterError(str(exc)) from exc
        stop_reason = _stop_reason(response)
        if stop_reason not in {None, "complete", "end_turn"}:
            raise AcpAdapterError(f"ACP critic stopped with reason: {stop_reason}")
        return _strict_json_object(client.agent_text(session_id))


def _strict_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        value = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise AcpAdapterError("ACP critic did not return one valid judgment JSON object.") from exc
    if not isinstance(value, dict):
        raise AcpAdapterError("ACP critic judgment must be a JSON object.")
    return value


def _stop_reason(response: Mapping[str, Any]) -> object:
    result = response.get("result")
    return result.get("stopReason") if isinstance(result, dict) else None


def _current_branch(repository: Path) -> str:
    completed = subprocess.run(
        ["git", "branch", "--show-current"],
        cwd=repository,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise AcpAdapterError(completed.stderr.strip() or "git branch --show-current failed")
    return completed.stdout.strip()


def _worker_error(job: WorkerJob, message: str) -> WorkerCompletion:
    return WorkerCompletion(
        job_id=job.id,
        callback_token=job.callback_token,
        patch=None,
        error=message,
    )


def _terminate_process(process: subprocess.Popen[str]) -> None:
    terminate_process_tree(process)
