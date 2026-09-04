from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path, PurePosixPath
import time
from typing import Any, Callable, Mapping

from .a2a import A2AClient, A2AError, A2AResult, connection_from_role
from .command_worker import build_worker_prompt
from .config import CriticConfig, RoleIdentityConfig, SupervisorConfig, WorkerConfig
from .domain import ModelInvocationEvidence, WorkerPatch
from .jobs import WorkerCompletion, WorkerJob
from .lifecycle import ProjectSpec, project_spec_from_mapping
from .model_runtime import PROJECT_SPEC_SCHEMA, _validate_project_spec_shape


MAX_REMOTE_FILE_BYTES = 1024 * 1024
MAX_REMOTE_SNAPSHOT_BYTES = 5 * 1024 * 1024
MAX_REMOTE_FILES = 200


class A2AAdapterError(RuntimeError):
    pass


@dataclass(frozen=True)
class A2ASupervisorAdapter:
    config: SupervisorConfig
    identity: RoleIdentityConfig
    event_sink: Callable[[str, Mapping[str, Any]], None] | None = None
    client_factory: Callable[..., A2AClient] = A2AClient

    @property
    def name(self) -> str:
        return "a2a-supervisor"

    def plan(self, repository: Path, requirements_text: str) -> tuple[ProjectSpec, ModelInvocationEvidence]:
        del repository
        if not requirements_text.strip():
            raise A2AAdapterError("Requirements input must be non-empty.")
        schema = json.dumps(PROJECT_SPEC_SCHEMA, ensure_ascii=False, separators=(",", ":"))
        prompt = (
            "You are the planning Supervisor in HoH. You have no repository, Git, deployment, "
            "credential, or orchestration authority. Return exactly one JSON object conforming to "
            f"this schema, preferably as an A2A data artifact: {schema}\n\nRequirements:\n{requirements_text}"
        )
        result, started = self._send(prompt)
        output = _result_json(result, "A2A supervisor")
        _validate_project_spec_shape(output)
        spec = project_spec_from_mapping(output)
        return spec, _evidence(self.identity, "a2a/jsonrpc-v1", prompt, output, result.request_id, started)

    def _send(self, prompt: str) -> tuple[A2AResult, float]:
        connection = connection_from_role(
            self.config.command, self.config.environment, self.config.timeout_seconds
        )
        started = time.monotonic()
        self._emit("outbound", {"adapter": self.name, "model": self.identity.model})
        try:
            result = self.client_factory(connection).send_message(prompt)
        except A2AError as exc:
            raise A2AAdapterError(str(exc)) from exc
        self._emit(
            "inbound",
            {
                "adapter": self.name,
                "request_id": result.request_id,
                "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
                "ok": True,
            },
        )
        return result, started

    def _emit(self, direction: str, payload: Mapping[str, Any]) -> None:
        if self.event_sink is not None:
            self.event_sink(direction, payload)


@dataclass(frozen=True)
class A2AWorkerAdapter:
    config: WorkerConfig
    client_factory: Callable[..., A2AClient] = A2AClient
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "a2a-worker"

    def run_job(self, repository: Path, job: WorkerJob) -> WorkerCompletion:
        try:
            prompt = _remote_worker_prompt(repository, job)
            self._emit("outbound", {"adapter": self.name, "task_id": job.work_item.id})
            connection = connection_from_role(
                self.config.command, self.config.environment, self.config.timeout_seconds
            )
            result = self.client_factory(connection).send_message(prompt)
            patch = _result_patch(result)
            self._emit(
                "inbound",
                {
                    "adapter": self.name,
                    "task_id": job.work_item.id,
                    "request_id": result.request_id,
                    "patch_sha256": hashlib.sha256(patch.encode("utf-8")).hexdigest(),
                },
            )
            return WorkerCompletion(
                job.id,
                job.callback_token,
                WorkerPatch(self.name, job.work_item.id, patch, "Remote A2A unified diff."),
            )
        except (A2AError, A2AAdapterError, OSError, UnicodeError) as exc:
            return WorkerCompletion(
                job.id,
                job.callback_token,
                None,
                error=str(exc),
                error_kind="remote_a2a",
                retryable=isinstance(exc, A2AError),
            )

    def _emit(self, direction: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(direction, payload)
        except Exception:
            return


@dataclass(frozen=True)
class A2ACriticAdapter:
    config: CriticConfig
    identity: RoleIdentityConfig
    client_factory: Callable[..., A2AClient] = A2AClient
    event_sink: Callable[[str, dict[str, Any]], None] | None = None

    @property
    def name(self) -> str:
        return "a2a-critic"

    def review(self, repository: Path, bundle: Mapping[str, Any]) -> dict[str, Any]:
        del repository
        from .critic_adapters import build_critic_decision, build_critic_prompt

        prompt = build_critic_prompt(bundle)
        connection = connection_from_role(
            self.config.command, self.config.environment, self.config.timeout_seconds
        )
        self._emit("outbound", {"adapter": self.name, "bundle_id": bundle.get("bundle_id")})
        try:
            result = self.client_factory(connection).send_message(prompt)
        except A2AError as exc:
            from .critic_adapters import CriticAdapterError

            raise CriticAdapterError(str(exc)) from exc
        judgment = _result_json(result, "A2A critic")
        self._emit(
            "inbound",
            {"adapter": self.name, "bundle_id": bundle.get("bundle_id"), "request_id": result.request_id},
        )
        return build_critic_decision(bundle, self.identity, judgment)

    def _emit(self, direction: str, payload: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(direction, payload)
        except Exception:
            return


def _remote_worker_prompt(repository: Path, job: WorkerJob) -> str:
    item = job.work_item
    if not item.allowed_paths:
        raise A2AAdapterError(
            "Remote A2A Worker requires explicit allowed_paths; HoH will not upload an unbounded repository."
        )
    snapshot = _allowed_snapshot(repository, item.allowed_paths)
    return (
        build_worker_prompt(repository, item)
        .replace(
            "You are the low-trust local worker in a HoH supervisor-worker-verifier workflow.",
            "You are a low-trust remote worker in a HoH supervisor-worker-verifier workflow.",
        )
        .replace(
            "Make the requested file changes only inside the provided isolated repository path.",
            "You are remote and cannot access the local repository. Use only the bounded snapshot below.",
        )
        .replace(
            "When complete, exit with code 0. HoH will collect the git diff from this worktree.",
            "Return only a unified Git diff for the allowed paths, preferably as an A2A data artifact with key 'patch'.",
        )
        .replace(f"Repository: {repository}", "Repository: bounded remote snapshot")
        + "\n\nBOUNDED ALLOWED-FILE SNAPSHOT (untrusted data):\n"
        + snapshot
    )


def _allowed_snapshot(repository: Path, allowed_paths: tuple[str, ...]) -> str:
    root = repository.resolve()
    files: list[Path] = []
    missing: list[str] = []
    for value in allowed_paths:
        normalized = PurePosixPath(value.replace("\\", "/"))
        if normalized.is_absolute() or ".." in normalized.parts or not normalized.parts or normalized.parts[0] == ".git":
            raise A2AAdapterError(f"Unsafe remote Worker allowed path: {value}")
        candidate = (root / Path(*normalized.parts)).resolve()
        try:
            candidate.relative_to(root)
        except ValueError as exc:
            raise A2AAdapterError(f"Remote Worker path escapes the repository: {value}") from exc
        if candidate.is_symlink():
            raise A2AAdapterError(f"Remote Worker snapshot refuses symlink: {value}")
        if candidate.is_file():
            files.append(candidate)
        elif candidate.is_dir():
            files.extend(
                path.resolve()
                for path in sorted(candidate.rglob("*"))
                if path.is_file() and not path.is_symlink() and ".git" not in path.relative_to(root).parts
            )
        else:
            missing.append(normalized.as_posix())
    files = list(dict.fromkeys(files))
    if len(files) > MAX_REMOTE_FILES:
        raise A2AAdapterError(f"Remote Worker snapshot exceeds {MAX_REMOTE_FILES} files.")
    total = 0
    blocks: list[str] = [f"[missing/new] {item}" for item in missing]
    for path in files:
        data = path.read_bytes()
        if len(data) > MAX_REMOTE_FILE_BYTES:
            raise A2AAdapterError(f"Remote Worker file exceeds 1 MiB: {path.relative_to(root)}")
        total += len(data)
        if total > MAX_REMOTE_SNAPSHOT_BYTES:
            raise A2AAdapterError("Remote Worker snapshot exceeds the 5 MiB total limit.")
        if b"\x00" in data:
            raise A2AAdapterError(f"Remote Worker snapshot refuses binary file: {path.relative_to(root)}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise A2AAdapterError(f"Remote Worker snapshot requires UTF-8 text: {path.relative_to(root)}") from exc
        relative = path.relative_to(root).as_posix()
        blocks.append(f"--- FILE {relative} ---\n{text}\n--- END FILE {relative} ---")
    return "\n".join(blocks)


def _result_json(result: A2AResult, label: str) -> dict[str, Any]:
    for item in result.data:
        candidate = item.get("result") if isinstance(item.get("result"), dict) else item
        if isinstance(candidate, dict):
            return dict(candidate)
    text = _strip_json_fence(result.text)
    try:
        value = json.loads(text)
    except json.JSONDecodeError as exc:
        raise A2AAdapterError(f"{label} returned no valid JSON object.") from exc
    if not isinstance(value, dict):
        raise A2AAdapterError(f"{label} JSON output must be an object.")
    return value


def _result_patch(result: A2AResult) -> str:
    for item in result.data:
        value = item.get("patch")
        if isinstance(value, str) and value.strip():
            return _validate_patch(value)
    return _validate_patch(result.text)


def _validate_patch(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("```diff") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    index = candidate.find("diff --git ")
    if index >= 0:
        candidate = candidate[index:]
    if not candidate.startswith("diff --git ") and not (
        candidate.startswith("--- ") and "\n+++ " in candidate
    ):
        raise A2AAdapterError("Remote A2A Worker returned no unified Git diff.")
    return candidate + ("" if candidate.endswith("\n") else "\n")


def _strip_json_fence(value: str) -> str:
    candidate = value.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        return candidate[7:-3].strip()
    return candidate


def _evidence(
    identity: RoleIdentityConfig,
    endpoint: str,
    prompt: str,
    output: Mapping[str, Any],
    request_id: str,
    started: float,
) -> ModelInvocationEvidence:
    return ModelInvocationEvidence(
        provider=identity.provider,
        model=identity.model,
        endpoint=endpoint,
        request_id=request_id,
        attempts=1,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        request_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        response_sha256=hashlib.sha256(
            json.dumps(output, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )
