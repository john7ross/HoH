from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time
from typing import Any, Callable, Literal
from uuid import uuid4

from .acp import AcpError, JsonRpcStdioClient
from .config import HarnessConfig
from .domain import ModelInvocationEvidence
from .durable_io import atomic_write_json, read_json
from .lifecycle import ProjectPlanMaterialization, materialize_project_plan
from .model_providers import ModelRequest, create_model_provider
from .metrics import UsageContext
from .process_launch import process_group_kwargs, resolve_executable, terminate_process_tree
from .role_profiles import load_effective_config
from .state import HohStateStore, default_state_root
from .supervisor_adapters import create_supervisor_adapter


CHAT_HISTORY_VERSION = "1.0"
CHAT_REPLY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["reply", "ready_to_plan", "requirements_summary"],
    "properties": {
        "reply": {"type": "string", "minLength": 1},
        "ready_to_plan": {"type": "boolean"},
        "requirements_summary": {"type": "string"},
    },
}


class SupervisorChatError(RuntimeError):
    pass


@dataclass(frozen=True)
class SupervisorChatMessage:
    message_id: str
    role: Literal["user", "supervisor"]
    text: str
    created_at_utc: str
    ready_to_plan: bool = False
    requirements_summary: str = ""


@dataclass(frozen=True)
class SupervisorChatTurn:
    user: SupervisorChatMessage
    supervisor: SupervisorChatMessage
    evidence: ModelInvocationEvidence | None = None


def supervisor_chat_path(project_root: Path) -> Path:
    return default_state_root(project_root) / "supervisor-chat.json"


def load_supervisor_chat(project_root: Path) -> tuple[SupervisorChatMessage, ...]:
    path = supervisor_chat_path(project_root)
    payload = read_json(path, default={"version": CHAT_HISTORY_VERSION, "messages": []})
    if not isinstance(payload, dict) or payload.get("version") != CHAT_HISTORY_VERSION:
        raise SupervisorChatError(f"Unsupported or corrupted Supervisor chat history: {path}")
    messages = payload.get("messages")
    if not isinstance(messages, list):
        raise SupervisorChatError(f"Supervisor chat messages must be a JSON array: {path}")
    return tuple(_message_from_payload(item) for item in messages)


def clear_supervisor_chat(project_root: Path) -> None:
    supervisor_chat_path(project_root).unlink(missing_ok=True)


def send_supervisor_message(
    project_root: Path,
    text: str,
    *,
    config: HarnessConfig | None = None,
    invoke: Callable[[HarnessConfig, Path, str], tuple[dict[str, Any], ModelInvocationEvidence | None]] | None = None,
) -> SupervisorChatTurn:
    root = project_root.resolve()
    message = text.strip()
    if not message:
        raise SupervisorChatError("Supervisor chat message must be non-empty.")
    history = load_supervisor_chat(root)
    effective = config or load_effective_config(root)
    prompt = _conversation_prompt(history, message)
    output, evidence = (invoke or _invoke_supervisor_chat)(effective, root, prompt)
    reply, ready, summary = _validate_chat_reply(output)
    now = datetime.now(UTC).isoformat()
    user_message = SupervisorChatMessage(str(uuid4()), "user", message, now)
    supervisor_message = SupervisorChatMessage(
        str(uuid4()), "supervisor", reply, datetime.now(UTC).isoformat(), ready, summary
    )
    updated = (*history, user_message, supervisor_message)
    atomic_write_json(
        supervisor_chat_path(root),
        {"version": CHAT_HISTORY_VERSION, "messages": [_message_payload(item) for item in updated]},
    )
    if evidence is not None:
        try:
            store = HohStateStore(default_state_root(root), coordination=effective.coordination)
            store.metrics.record_model_evidence(
                UsageContext(
                    role="supervisor",
                    agent=effective.three_head.logic.identity,
                    provider=effective.three_head.logic.provider,
                    model=effective.three_head.logic.model,
                    driver=effective.supervisor.driver,
                ),
                evidence,
                effective.metrics.prices,
            )
        except Exception:
            pass
    return SupervisorChatTurn(user_message, supervisor_message, evidence)


def materialize_supervisor_chat_plan(project_root: Path) -> ProjectPlanMaterialization:
    root = project_root.resolve()
    history = load_supervisor_chat(root)
    if not any(item.role == "user" for item in history):
        raise SupervisorChatError("Write at least one task message before creating a plan.")
    config = load_effective_config(root)
    store = HohStateStore(default_state_root(root), coordination=config.coordination)
    planner = create_supervisor_adapter(
        config.supervisor,
        config.supervisor_model,
        config.three_head.logic,
        event_sink=store.journal.adapter_sink(
            config.three_head.logic.identity,
            metric_context=UsageContext(
                role="supervisor",
                agent=config.three_head.logic.identity,
                provider=config.three_head.logic.provider,
                model=config.three_head.logic.model,
                driver=config.supervisor.driver,
            ),
            prices=config.metrics.prices,
        ),
    )
    spec, evidence = planner.plan(root, _requirements_from_history(history))
    result = materialize_project_plan(root, spec, write=True, queue=store)
    store.journal.record(
        "interaction",
        "supervisor",
        "project.spec_generated_from_chat",
        recipient="hoh",
        content={
            "chat_messages": len(history),
            "model_evidence": {
                "provider": evidence.provider,
                "model": evidence.model,
                "endpoint": evidence.endpoint,
                "request_sha256": evidence.request_sha256,
                "response_sha256": evidence.response_sha256,
            },
        },
        metadata={
            "brief_path": str(result.brief_path),
            "roadmap_path": str(result.roadmap_path),
            "enqueued_task_ids": list(result.enqueued_task_ids),
        },
    )
    return result


def _invoke_supervisor_chat(
    config: HarnessConfig,
    repository: Path,
    prompt: str,
) -> tuple[dict[str, Any], ModelInvocationEvidence | None]:
    driver = config.supervisor.driver
    if driver == "model_json":
        response = create_model_provider(config.supervisor_model).invoke(
            ModelRequest(
                instructions=_chat_instructions(),
                input_text=prompt,
                output_schema_name="hoh_supervisor_chat_reply",
                output_schema=CHAT_REPLY_SCHEMA,
            )
        )
        return response.output, response.evidence
    if driver == "acp":
        return _acp_chat(config, prompt)
    if driver == "command_json":
        return _command_chat(config, prompt)
    if driver == "a2a":
        return _a2a_chat(config, prompt)
    raise SupervisorChatError(f"Supervisor driver does not support chat: {driver}")


def _acp_chat(config: HarnessConfig, prompt: str) -> tuple[dict[str, Any], ModelInvocationEvidence]:
    started = time.monotonic()
    args = (resolve_executable(config.supervisor.command), *config.supervisor.args)
    try:
        with tempfile.TemporaryDirectory(prefix="hoh-acp-supervisor-chat-") as isolated_cwd:
            process = subprocess.Popen(
                args,
                cwd=isolated_cwd,
                env={**os.environ, **dict(config.supervisor.environment)},
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                **process_group_kwargs(),
            )
            executor = ThreadPoolExecutor(max_workers=1)
            future = executor.submit(_run_acp_chat_turn, process, Path(isolated_cwd), config, prompt)
            try:
                output = future.result(timeout=config.supervisor.timeout_seconds)
            except TimeoutError as exc:
                terminate_process_tree(process)
                raise SupervisorChatError(
                    f"ACP Supervisor chat timed out after {config.supervisor.timeout_seconds:g} seconds."
                ) from exc
            finally:
                terminate_process_tree(process)
                executor.shutdown(wait=False)
    except (OSError, AcpError) as exc:
        raise SupervisorChatError(f"ACP Supervisor chat failed: {exc}") from exc
    response_bytes = json.dumps(output, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return output, ModelInvocationEvidence(
        provider=config.three_head.logic.provider,
        model=config.three_head.logic.model,
        endpoint="acp/stdio/chat",
        request_id=None,
        attempts=1,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        request_sha256=hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        response_sha256=hashlib.sha256(response_bytes).hexdigest(),
    )


def _a2a_chat(config: HarnessConfig, prompt: str) -> tuple[dict[str, Any], ModelInvocationEvidence]:
    from .a2a import A2AClient, A2AError, connection_from_role
    from .a2a_adapters import _result_json

    full_prompt = _chat_instructions() + "\n\n" + prompt
    started = time.monotonic()
    try:
        result = A2AClient(
            connection_from_role(
                config.supervisor.command,
                config.supervisor.environment,
                config.supervisor.timeout_seconds,
            )
        ).send_message(full_prompt)
        output = _result_json(result, "A2A Supervisor chat")
    except (A2AError, ValueError) as exc:
        raise SupervisorChatError(f"A2A Supervisor chat failed: {exc}") from exc
    response_bytes = json.dumps(output, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return output, ModelInvocationEvidence(
        provider=config.three_head.logic.provider,
        model=config.three_head.logic.model,
        endpoint="a2a/jsonrpc-v1/chat",
        request_id=result.request_id,
        attempts=1,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        request_sha256=hashlib.sha256(full_prompt.encode("utf-8")).hexdigest(),
        response_sha256=hashlib.sha256(response_bytes).hexdigest(),
    )


def _run_acp_chat_turn(
    process: subprocess.Popen[str],
    cwd: Path,
    config: HarnessConfig,
    prompt: str,
) -> dict[str, Any]:
    client = JsonRpcStdioClient(process, permission_policy=config.supervisor.mcp)
    client.initialize()
    session_id = client.new_session(cwd)
    model = config.supervisor.model or config.three_head.logic.model
    if model.casefold() not in {"agent-default", "supervisor-agent"}:
        client.select_session_option(session_id, "model", model)
    response = client.prompt(session_id, _chat_instructions() + "\n\n" + prompt)
    stop_reason = response.get("result", {}).get("stopReason") if isinstance(response.get("result"), dict) else None
    if stop_reason not in {None, "complete", "end_turn"}:
        raise SupervisorChatError(f"ACP Supervisor stopped with reason: {stop_reason}")
    return _strict_json_object(client.agent_text(session_id))


def _command_chat(config: HarnessConfig, prompt: str) -> tuple[dict[str, Any], ModelInvocationEvidence]:
    started = time.monotonic()
    full_prompt = _chat_instructions() + "\n\n" + prompt
    try:
        with tempfile.TemporaryDirectory(prefix="hoh-command-supervisor-chat-") as isolated_cwd:
            completed = subprocess.run(
                (resolve_executable(config.supervisor.command), *config.supervisor.args),
                cwd=isolated_cwd,
                env={**os.environ, **dict(config.supervisor.environment)},
                input=full_prompt,
                text=True,
                capture_output=True,
                check=False,
                timeout=config.supervisor.timeout_seconds,
                encoding="utf-8",
            )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise SupervisorChatError(f"Command Supervisor chat failed: {exc}") from exc
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[:1000]
        raise SupervisorChatError(f"Command Supervisor chat exited with {completed.returncode}: {detail}")
    output = _strict_json_object(completed.stdout)
    return output, ModelInvocationEvidence(
        provider=config.three_head.logic.provider,
        model=config.three_head.logic.model,
        endpoint="process/stdin-json/chat",
        request_id=None,
        attempts=1,
        latency_ms=max(0, int((time.monotonic() - started) * 1000)),
        request_sha256=hashlib.sha256(full_prompt.encode("utf-8")).hexdigest(),
        response_sha256=hashlib.sha256(completed.stdout.encode("utf-8")).hexdigest(),
    )


def _chat_instructions() -> str:
    return (
        "You are the selected HoH Supervisor. Discuss the customer's software project and clarify the task. "
        "Do not modify files, run tools, select orchestration roles, or claim completion. Ask focused questions "
        "when requirements or acceptance criteria are unclear. Reply in the customer's language. Return exactly "
        "one JSON object with reply, ready_to_plan, and requirements_summary matching this schema: "
        + json.dumps(CHAT_REPLY_SCHEMA, ensure_ascii=False, separators=(",", ":"))
    )


def _conversation_prompt(history: tuple[SupervisorChatMessage, ...], new_message: str) -> str:
    bounded = history[-30:]
    lines = ["Conversation so far:"]
    lines.extend(f"{item.role.upper()}: {item.text}" for item in bounded)
    lines.append(f"USER: {new_message}")
    return "\n\n".join(lines)


def _requirements_from_history(history: tuple[SupervisorChatMessage, ...]) -> str:
    summary = next(
        (item.requirements_summary for item in reversed(history) if item.role == "supervisor" and item.requirements_summary),
        "",
    )
    transcript = "\n\n".join(f"{item.role.upper()}: {item.text}" for item in history)
    return f"Agreed requirements summary:\n{summary or 'Not yet summarized.'}\n\nConversation transcript:\n{transcript}"


def _validate_chat_reply(output: dict[str, Any]) -> tuple[str, bool, str]:
    if set(output) != {"reply", "ready_to_plan", "requirements_summary"}:
        raise SupervisorChatError("Supervisor chat reply contains unexpected or missing fields.")
    reply = output.get("reply")
    ready = output.get("ready_to_plan")
    summary = output.get("requirements_summary")
    if not isinstance(reply, str) or not reply.strip():
        raise SupervisorChatError("Supervisor chat reply must contain non-empty reply text.")
    if not isinstance(ready, bool) or not isinstance(summary, str):
        raise SupervisorChatError("Supervisor chat readiness fields have invalid types.")
    return reply.strip(), ready, summary.strip()


def _strict_json_object(text: str) -> dict[str, Any]:
    candidate = text.strip()
    if candidate.startswith("```json") and candidate.endswith("```"):
        candidate = candidate[7:-3].strip()
    try:
        output = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise SupervisorChatError("Supervisor chat did not return valid structured JSON.") from exc
    if not isinstance(output, dict):
        raise SupervisorChatError("Supervisor chat output must be a JSON object.")
    return output


def _message_payload(message: SupervisorChatMessage) -> dict[str, Any]:
    return {
        "message_id": message.message_id,
        "role": message.role,
        "text": message.text,
        "created_at_utc": message.created_at_utc,
        "ready_to_plan": message.ready_to_plan,
        "requirements_summary": message.requirements_summary,
    }


def _message_from_payload(value: Any) -> SupervisorChatMessage:
    if not isinstance(value, dict) or value.get("role") not in {"user", "supervisor"}:
        raise SupervisorChatError("Supervisor chat contains an invalid message record.")
    required = ("message_id", "text", "created_at_utc")
    if any(not isinstance(value.get(key), str) for key in required):
        raise SupervisorChatError("Supervisor chat message identity fields are invalid.")
    return SupervisorChatMessage(
        message_id=value["message_id"],
        role=value["role"],
        text=value["text"],
        created_at_utc=value["created_at_utc"],
        ready_to_plan=bool(value.get("ready_to_plan", False)),
        requirements_summary=str(value.get("requirements_summary", "")),
    )
