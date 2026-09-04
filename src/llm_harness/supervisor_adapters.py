from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable, Mapping, Protocol

from .acp import AcpError, JsonRpcStdioClient
from .config import CloudModelEndpoint, RoleIdentityConfig, SupervisorConfig
from .domain import ModelInvocationEvidence
from .lifecycle import ProjectSpec, project_spec_from_mapping
from .model_runtime import PROJECT_SPEC_SCHEMA, _validate_project_spec_shape, generate_project_spec
from .process_launch import process_group_kwargs, resolve_executable, terminate_process_tree


class SupervisorAdapterError(RuntimeError):
    pass


class SupervisorAdapter(Protocol):
    @property
    def name(self) -> str:
        ...

    def plan(self, repository: Path, requirements_text: str) -> tuple[ProjectSpec, ModelInvocationEvidence]:
        ...


@dataclass(frozen=True)
class ModelJsonSupervisorAdapter:
    endpoint: CloudModelEndpoint
    event_sink: Callable[[str, Mapping[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "model-json-supervisor"

    def plan(self, repository: Path, requirements_text: str) -> tuple[ProjectSpec, ModelInvocationEvidence]:
        self._emit("outbound", {"adapter": self.name, "provider": self.endpoint.provider, "model": self.endpoint.model})
        spec, evidence = generate_project_spec(requirements_text, self.endpoint)
        self._emit("inbound", _evidence_payload(evidence))
        return spec, evidence  # type: ignore[return-value]

    def _emit(self, direction: str, payload: Mapping[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(direction, payload)


@dataclass(frozen=True)
class AcpSupervisorAdapter:
    config: SupervisorConfig
    identity: RoleIdentityConfig
    event_sink: Callable[[str, Mapping[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "acp-supervisor"

    def plan(self, repository: Path, requirements_text: str) -> tuple[ProjectSpec, ModelInvocationEvidence]:
        if not requirements_text.strip():
            raise SupervisorAdapterError("Requirements input must be non-empty.")
        prompt = _planning_prompt(requirements_text)
        started = time.monotonic()
        args = (resolve_executable(self.config.command), *self.config.args)
        try:
            with tempfile.TemporaryDirectory(prefix="hoh-acp-supervisor-") as isolated_cwd:
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
                    raise SupervisorAdapterError(f"Failed to start ACP supervisor: {exc}") from exc
                executor = ThreadPoolExecutor(max_workers=1)
                future = executor.submit(self._run_turn, process, Path(isolated_cwd), prompt)
                try:
                    output = future.result(timeout=self.config.timeout_seconds)
                except TimeoutError as exc:
                    _terminate_process(process)
                    raise SupervisorAdapterError(
                        f"ACP supervisor timed out after {self.config.timeout_seconds} seconds."
                    ) from exc
                finally:
                    _terminate_process(process)
                    executor.shutdown(wait=False)
        except AcpError as exc:
            raise SupervisorAdapterError(str(exc)) from exc
        _validate_project_spec_shape(output)
        spec = project_spec_from_mapping(output)
        request_bytes = prompt.encode("utf-8")
        response_bytes = json.dumps(output, ensure_ascii=False, sort_keys=True).encode("utf-8")
        evidence = ModelInvocationEvidence(
            provider=self.identity.provider,
            model=self.identity.model,
            endpoint="acp/stdio",
            request_id=None,
            attempts=1,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            request_sha256=hashlib.sha256(request_bytes).hexdigest(),
            response_sha256=hashlib.sha256(response_bytes).hexdigest(),
        )
        return spec, evidence

    def _run_turn(self, process: subprocess.Popen[str], cwd: Path, prompt: str) -> dict[str, Any]:
        client = JsonRpcStdioClient(process, permission_policy=self.config.mcp, event_sink=self.event_sink)
        client.initialize()
        session_id = client.new_session(cwd)
        model = self.config.model or self.identity.model
        if model.casefold() not in {"agent-default", "supervisor-agent"}:
            client.select_session_option(session_id, "model", model)
        response = client.prompt(session_id, prompt)
        stop_reason = _stop_reason(response)
        if stop_reason not in {None, "complete", "end_turn"}:
            raise SupervisorAdapterError(f"ACP supervisor stopped with reason: {stop_reason}")
        return _strict_json_object(client.agent_text(session_id))


@dataclass(frozen=True)
class CommandJsonSupervisorAdapter:
    config: SupervisorConfig
    identity: RoleIdentityConfig
    event_sink: Callable[[str, Mapping[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "command-json-supervisor"

    def plan(self, repository: Path, requirements_text: str) -> tuple[ProjectSpec, ModelInvocationEvidence]:
        if not requirements_text.strip():
            raise SupervisorAdapterError("Requirements input must be non-empty.")
        prompt = _planning_prompt(requirements_text)
        started = time.monotonic()
        if self.event_sink is not None:
            self.event_sink("outbound", {"adapter": self.name, "model": self.identity.model})
        try:
            with tempfile.TemporaryDirectory(prefix="hoh-command-supervisor-") as isolated_cwd:
                completed = subprocess.run(
                    (resolve_executable(self.config.command), *self.config.args),
                    cwd=isolated_cwd,
                    env={**os.environ, **dict(self.config.environment)},
                    input=prompt,
                    text=True,
                    capture_output=True,
                    check=False,
                    timeout=self.config.timeout_seconds,
                    encoding="utf-8",
                )
        except OSError as exc:
            raise SupervisorAdapterError(f"Failed to start command JSON supervisor: {exc}") from exc
        except subprocess.TimeoutExpired as exc:
            raise SupervisorAdapterError(
                f"Command JSON supervisor timed out after {self.config.timeout_seconds} seconds."
            ) from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()[:1000]
            raise SupervisorAdapterError(
                f"Command JSON supervisor exited with {completed.returncode}: {detail}"
            )
        output = _strict_json_object(completed.stdout)
        _validate_project_spec_shape(output)
        spec = project_spec_from_mapping(output)
        evidence = ModelInvocationEvidence(
            provider=self.identity.provider,
            model=self.identity.model,
            endpoint="process/stdin-json",
            request_id=None,
            attempts=1,
            latency_ms=max(0, int((time.monotonic() - started) * 1000)),
            request_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            response_sha256=hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
        )
        if self.event_sink is not None:
            self.event_sink("inbound", {**_evidence_payload(evidence), "return_code": completed.returncode})
        return spec, evidence


def create_supervisor_adapter(
    config: SupervisorConfig,
    endpoint: CloudModelEndpoint,
    identity: RoleIdentityConfig,
    event_sink: Callable[[str, Mapping[str, Any]], None] | None = None,
) -> SupervisorAdapter:
    if config.driver == "model_json":
        return ModelJsonSupervisorAdapter(endpoint, event_sink)
    if config.driver == "acp":
        return AcpSupervisorAdapter(config, identity, event_sink)
    if config.driver == "command_json":
        return CommandJsonSupervisorAdapter(config, identity, event_sink)
    if config.driver == "a2a":
        from .a2a_adapters import A2ASupervisorAdapter

        return A2ASupervisorAdapter(config, identity, event_sink=event_sink)
    raise SupervisorAdapterError(f"Unsupported supervisor driver: {config.driver}")


def _planning_prompt(requirements_text: str) -> str:
    schema = json.dumps(PROJECT_SPEC_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    return (
        "You are the planning Supervisor in HoH. Convert the customer requirements into one atomic, "
        "dependency-safe project plan. You have no repository, Git, deployment, or state authority. "
        "Do not select agents, models, credentials, or orchestration roles. Return exactly one JSON object "
        "with no prose or Markdown, conforming to this JSON Schema:\n"
        f"{schema}\n\nCustomer requirements:\n{requirements_text}"
    )


def _evidence_payload(evidence: ModelInvocationEvidence) -> dict[str, Any]:
    return {
        "provider": evidence.provider,
        "model": evidence.model,
        "request_id": evidence.request_id,
        "latency_ms": evidence.latency_ms,
        "input_tokens": evidence.input_tokens,
        "output_tokens": evidence.output_tokens,
        "total_tokens": evidence.total_tokens,
        "ok": True,
    }


def _strict_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        output = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SupervisorAdapterError("ACP supervisor did not return one valid project-spec JSON object.") from exc
    if not isinstance(output, dict):
        raise SupervisorAdapterError("ACP supervisor project spec must be a JSON object.")
    return output


def _stop_reason(response: dict[str, Any]) -> object:
    result = response.get("result")
    return result.get("stopReason") if isinstance(result, dict) else None


def _terminate_process(process: subprocess.Popen[str]) -> None:
    terminate_process_tree(process)
