from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Callable, Literal, Protocol, TextIO

from . import __version__
from .mcp import (
    ACP_TOOL_KINDS,
    RoleMcpPolicyConfig,
    build_mcp_server_payloads,
    redact_acp_message,
)


class AcpError(RuntimeError):
    pass


class AcpProcess(Protocol):
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO

    def terminate(self) -> None:
        ...

    def wait(self, timeout: float | None = None) -> int:
        ...


@dataclass
class JsonRpcStdioClient:
    process: AcpProcess
    next_id: int = 1
    permission_policy: RoleMcpPolicyConfig | Literal["allow_once", "reject"] = "reject"
    event_sink: Callable[[str, dict[str, Any]], None] | None = None
    notifications: list[dict[str, Any]] = field(default_factory=list)
    session_config_options: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    agent_mcp_capabilities: dict[str, bool] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.permission_policy == "allow_once":
            self.permission_policy = RoleMcpPolicyConfig("allow_once", ACP_TOOL_KINDS)
        elif self.permission_policy == "reject":
            self.permission_policy = RoleMcpPolicyConfig()

    def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        request_id = self.next_id
        self.next_id += 1
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write_message(message)
        return self._read_response(request_id)

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        message: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
        }
        if params is not None:
            message["params"] = params
        self._write_message(message)

    def initialize(
        self,
        client_name: str = "hoh",
        client_version: str = __version__,
        *,
        terminal_auth: bool = False,
    ) -> dict[str, Any]:
        response = self.request(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "auth": {
                        "terminal": terminal_auth,
                    },
                    "fs": {
                        "readTextFile": False,
                        "writeTextFile": False,
                    },
                    "terminal": False,
                },
                "clientInfo": {
                    "name": client_name,
                    "title": "HoH",
                    "version": client_version,
                },
            },
        )
        result = response.get("result")
        capabilities = result.get("agentCapabilities") if isinstance(result, dict) else None
        mcp = capabilities.get("mcpCapabilities") if isinstance(capabilities, dict) else None
        self.agent_mcp_capabilities = {
            "http": bool(mcp.get("http", False)),
            "sse": bool(mcp.get("sse", False)),
        } if isinstance(mcp, dict) else {}
        return response

    def authenticate(self, method_id: str) -> dict[str, Any]:
        if not method_id.strip():
            raise AcpError("ACP authentication method id must be non-empty.")
        return self.request("authenticate", {"methodId": method_id})

    def logout(self) -> dict[str, Any]:
        return self.request("logout", {})

    def new_session(self, cwd: Path) -> str:
        policy = self.permission_policy
        if not isinstance(policy, RoleMcpPolicyConfig):
            raise AcpError("Invalid ACP permission policy.")
        try:
            servers = build_mcp_server_payloads(
                policy,
                agent_capabilities=self.agent_mcp_capabilities,
            )
        except ValueError as exc:
            raise AcpError(str(exc)) from exc
        response = self.request("session/new", {"cwd": str(cwd.resolve()), "mcpServers": servers})
        result = response.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("sessionId"), str):
            raise AcpError("ACP session/new response did not contain sessionId.")
        session_id = result["sessionId"]
        options = result.get("configOptions")
        self.session_config_options[session_id] = [
            item for item in options if isinstance(item, dict)
        ] if isinstance(options, list) else []
        return session_id

    def select_session_option(self, session_id: str, category: str, value: str) -> None:
        options = self.session_config_options.get(session_id, [])
        selected = next(
            (
                item
                for item in options
                if item.get("category") == category or item.get("id") == category
            ),
            None,
        )
        if selected is None:
            raise AcpError(f"ACP agent does not expose a session {category!r} option.")
        config_id = selected.get("id")
        if not isinstance(config_id, str) or not config_id:
            raise AcpError(f"ACP session {category!r} option has no id.")
        resolved_value = _resolve_config_value(selected.get("options"), value)
        response = self.request(
            "session/set_config_option",
            {"sessionId": session_id, "configId": config_id, "value": resolved_value},
        )
        result = response.get("result")
        updated = result.get("configOptions") if isinstance(result, dict) else None
        if isinstance(updated, list):
            self.session_config_options[session_id] = [
                item for item in updated if isinstance(item, dict)
            ]

    def prompt(self, session_id: str, text: str) -> dict[str, Any]:
        return self.request(
            "session/prompt",
            {
                "sessionId": session_id,
                "prompt": [{"type": "text", "text": text}],
            },
        )

    def cancel(self, session_id: str) -> None:
        self.notify("session/cancel", {"sessionId": session_id})

    def agent_text(self, session_id: str) -> str:
        chunks: list[str] = []
        for message in self.notifications:
            params = message.get("params")
            if not isinstance(params, dict) or params.get("sessionId") != session_id:
                continue
            update = params.get("update")
            if not isinstance(update, dict) or update.get("sessionUpdate") != "agent_message_chunk":
                continue
            content = update.get("content")
            if isinstance(content, dict) and content.get("type") == "text":
                text = content.get("text")
                if isinstance(text, str):
                    chunks.append(text)
        return "".join(chunks)

    def _write_message(self, message: dict[str, Any]) -> None:
        self._emit("outbound", message)
        encoded = json.dumps(message, ensure_ascii=False, separators=(",", ":"))
        self.process.stdin.write(encoded + "\n")
        self.process.stdin.flush()

    def _read_response(self, expected_id: int) -> dict[str, Any]:
        while True:
            line = self.process.stdout.readline()
            if line == "":
                raise AcpError("ACP server closed stdout before responding.")
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError as exc:
                raise AcpError(f"Invalid ACP JSON-RPC line: {line}") from exc
            self._emit("inbound", message)
            if "method" in message and "id" in message:
                self._respond_to_server_request(message)
                continue
            if "method" in message:
                self.notifications.append(message)
                continue
            if message.get("id") != expected_id:
                continue
            if "error" in message:
                raise AcpError(f"ACP request failed: {message['error']}")
            return message

    def _respond_to_server_request(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        request_id = message.get("id")
        if method == "session/request_permission":
            outcome = self._permission_outcome(message)
            self._write_message(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {
                        "outcome": outcome,
                    },
                }
            )
            return

        self._write_message(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {
                    "code": -32601,
                    "message": f"Unsupported ACP client request: {method}",
                },
            }
        )

    def _permission_outcome(self, message: dict[str, Any]) -> dict[str, str]:
        params = message.get("params")
        options = params.get("options", ()) if isinstance(params, dict) else ()
        tool_call = params.get("toolCall") if isinstance(params, dict) else None
        kind = tool_call.get("kind") if isinstance(tool_call, dict) else None
        policy = self.permission_policy
        if isinstance(policy, RoleMcpPolicyConfig) and policy.allows(kind):
            option = _first_option_by_kind(options, "allow_once")
            if option is not None:
                return {
                    "outcome": "selected",
                    "optionId": option,
                }

        option = _first_option_by_kind(options, "reject_once") or _first_option_by_kind(options, "reject_always")
        if option is not None:
            return {
                "outcome": "selected",
                "optionId": option,
            }
        return {
            "outcome": "cancelled",
        }

    def _emit(self, direction: str, message: dict[str, Any]) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(direction, redact_acp_message(message))
        except Exception:
            # Observability must never alter the agent protocol outcome.
            return


def _first_option_by_kind(options: object, kind: str) -> str | None:
    if not isinstance(options, list):
        return None
    for option in options:
        if not isinstance(option, dict):
            continue
        if option.get("kind") == kind and isinstance(option.get("optionId"), str):
            return option["optionId"]
    return None


def _resolve_config_value(options: object, requested: str) -> str:
    values: list[tuple[str, str]] = []

    def collect(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            if isinstance(item.get("options"), list):
                collect(item["options"])
                continue
            value = item.get("value")
            name = item.get("name")
            if isinstance(value, str):
                values.append((value, str(name) if name is not None else value))

    collect(options)
    exact = next((value for value, _ in values if value == requested), None)
    if exact is not None:
        return exact
    folded = requested.casefold()
    matches = [value for value, name in values if value.casefold() == folded or name.casefold() == folded]
    if len(matches) == 1:
        return matches[0]
    available = ", ".join(value for value, _ in values[:20]) or "none"
    raise AcpError(f"ACP session option value {requested!r} is unavailable. Available values: {available}")
