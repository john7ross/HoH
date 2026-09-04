from __future__ import annotations

from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import ipaddress
import json
import os
from pathlib import Path
import ssl
from threading import Thread
from typing import Any, Mapping
from urllib.parse import unquote, urlparse

from . import __version__
from .a2a_push import A2APushError, shared_push_inbox
from .background_scheduler import run_due_projects, run_registered_project, scheduled_result_payload
from .metrics import UsageLedger
from .state import HohStateStore, default_state_root
from .supervisor_chat import load_supervisor_chat, materialize_supervisor_chat_plan, send_supervisor_message
from .workspace import WorkspaceRegistry, summary_payload


MAX_REQUEST_BYTES = 1024 * 1024


class _BoundHTTPServer(ThreadingHTTPServer):
    """A server that refuses a port somebody else is already listening on.

    http.server sets allow_reuse_address, which on POSIX only waives TIME_WAIT.
    On Windows the same option means the bind may succeed on a port another
    socket is actively using, and then the two split the incoming connections.
    That is never what a server wants, and it showed up as a request to a freshly
    started test server being answered by a closed connection.
    """

    allow_reuse_address = os.name != "nt"


class HeadlessServerError(RuntimeError):
    pass


class HeadlessServer:
    def __init__(
        self,
        *,
        host: str = "127.0.0.1",
        port: int = 8765,
        token_env: str = "HOH_SERVER_TOKEN",
        push_token_env: str = "HOH_A2A_PUSH_TOKEN",
        registry_path: Path | None = None,
        tls_cert: Path | None = None,
        tls_key: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        env = environment if environment is not None else os.environ
        token = env.get(token_env, "")
        if len(token) < 24:
            raise HeadlessServerError(f"{token_env} must contain at least 24 characters.")
        push_token = env.get(push_token_env, "")
        if push_token and len(push_token) < 24:
            raise HeadlessServerError(f"{push_token_env} must contain at least 24 characters when set.")
        if not _is_loopback_host(host) and (tls_cert is None or tls_key is None):
            raise HeadlessServerError("A non-loopback headless server requires --tls-cert and --tls-key.")
        if (tls_cert is None) != (tls_key is None):
            raise HeadlessServerError("TLS certificate and key must be supplied together.")
        self.registry = WorkspaceRegistry(registry_path)
        self.token_env = token_env
        self._token = token
        self.push_token_env = push_token_env
        self._push_token = push_token
        self._thread: Thread | None = None
        self._server = _BoundHTTPServer((host, port), self._handler())
        self._server.daemon_threads = True
        if tls_cert is not None and tls_key is not None:
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            context.load_cert_chain(str(tls_cert.resolve()), str(tls_key.resolve()))
            self._server.socket = context.wrap_socket(self._server.socket, server_side=True)
        self.uses_tls = tls_cert is not None

    @property
    def address(self) -> tuple[str, int]:
        host, port = self._server.server_address[:2]
        return str(host), int(port)

    @property
    def url(self) -> str:
        host, port = self.address
        return f"{'https' if self.uses_tls else 'http'}://{host}:{port}"

    def start(self) -> str:
        if self._thread is not None:
            return self.url
        self._thread = Thread(target=self._server.serve_forever, name="hoh-headless-server", daemon=True)
        self._thread.start()
        return self.url

    def serve_forever(self) -> None:
        self._server.serve_forever()

    def stop(self) -> None:
        if self._thread is not None:
            self._server.shutdown()
            self._thread.join(timeout=5)
        self._server.server_close()
        self._thread = None

    def close(self) -> None:
        self._server.server_close()

    def __enter__(self) -> "HeadlessServer":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    def _handler(self) -> type[BaseHTTPRequestHandler]:
        owner = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "HoH"
            sys_version = ""

            def do_GET(self) -> None:  # noqa: N802
                owner._dispatch(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                owner._dispatch(self, "POST")

            def log_message(self, _format: str, *args: object) -> None:
                return

        return Handler

    def _dispatch(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        path = unquote(urlparse(handler.path).path)
        if method == "GET" and path == "/v1/health":
            self._send(handler, 200, {"ok": True, "service": "hoh", "version": __version__})
            return
        if method == "GET" and path == "/v1/openapi.json":
            self._send(handler, 200, openapi_document())
            return
        if method == "POST" and path == "/v1/a2a/push":
            self._receive_a2a_push(handler)
            return
        if not hmac.compare_digest(handler.headers.get("Authorization", ""), f"Bearer {self._token}"):
            self._send(handler, 401, {"ok": False, "error": "unauthorized"})
            return
        try:
            body = self._body(handler) if method == "POST" else {}
            status, payload = self._route(method, path, body)
        except (OSError, RuntimeError, ValueError) as exc:
            status, payload = 400, {"ok": False, "error": str(exc)}
        self._send(handler, status, payload)

    def _receive_a2a_push(self, handler: BaseHTTPRequestHandler) -> None:
        if not self._push_token:
            self._send(handler, 503, {"ok": False, "error": f"{self.push_token_env} is not configured"})
            return
        if not hmac.compare_digest(handler.headers.get("Authorization", ""), f"Bearer {self._push_token}"):
            self._send(handler, 401, {"ok": False, "error": "unauthorized"})
            return
        try:
            task_id = shared_push_inbox().record(self._body(handler))
        except (A2APushError, OSError, ValueError) as exc:
            self._send(handler, 400, {"ok": False, "error": str(exc)})
            return
        self._send(handler, 202, {"ok": True, "task_id": task_id})

    def _route(self, method: str, path: str, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if method == "GET" and path == "/v1/projects":
            return 200, {"ok": True, "projects": [summary_payload(item) for item in self.registry.summaries()]}
        if method == "POST" and path == "/v1/projects":
            root = Path(_required_text(body, "root"))
            name = str(body.get("name") or "").strip() or None
            return 201, {"ok": True, "project": asdict(self.registry.register(root, name))}
        if method == "POST" and path == "/v1/run-due":
            results = run_due_projects(self.registry)
            return 200, {"ok": all(item.status != "failed" for item in results), "results": [scheduled_result_payload(item) for item in results]}

        project_id, suffix = _project_route(path)
        project = self._project(project_id)
        root = Path(project.root)
        if method == "GET" and suffix == "":
            summary = next(item for item in self.registry.summaries() if item.project_id == project_id)
            return 200, {"ok": True, "project": summary_payload(summary)}
        if method == "GET" and suffix == "/queue":
            tasks = HohStateStore(default_state_root(root)).list_tasks()
            return 200, {"ok": True, "tasks": [_queue_payload(item) for item in tasks]}
        if method == "GET" and suffix == "/metrics":
            summary = UsageLedger(default_state_root(root)).summary()
            return 200, {"ok": True, "metrics": summary}
        if method == "GET" and suffix == "/chat":
            return 200, {"ok": True, "messages": [asdict(item) for item in load_supervisor_chat(root)]}
        if method == "POST" and suffix == "/run":
            result = run_registered_project(project)
            self.registry.record_run(project.project_id, result.status, result.stderr.strip() or None)
            return 200, {"ok": result.status != "failed", "result": scheduled_result_payload(result)}
        if method == "POST" and suffix == "/chat":
            turn = send_supervisor_message(root, _required_text(body, "message"))
            return 200, {"ok": True, "turn": {"user": asdict(turn.user), "supervisor": asdict(turn.supervisor)}}
        if method == "POST" and suffix == "/plan":
            result = materialize_supervisor_chat_plan(root)
            return 201, {
                "ok": True,
                "plan": {
                    "brief_path": str(result.brief_path),
                    "roadmap_path": str(result.roadmap_path),
                    "enqueued_task_ids": list(result.enqueued_task_ids),
                },
            }
        return 404, {"ok": False, "error": "not_found"}

    def _project(self, project_id: str):
        project = next((item for item in self.registry.load().projects if item.project_id == project_id), None)
        if project is None:
            raise ValueError(f"Unknown workspace project: {project_id}")
        return project

    @staticmethod
    def _body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
        try:
            size = int(handler.headers.get("Content-Length", "0"))
        except ValueError as exc:
            raise ValueError("Content-Length is invalid.") from exc
        if size == 0:
            return {}
        if size < 2 or size > MAX_REQUEST_BYTES:
            raise ValueError("JSON request body size is outside the allowed range.")
        try:
            payload = json.loads(handler.rfile.read(size))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("Request body is not valid UTF-8 JSON.") from exc
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    @staticmethod
    def _send(handler: BaseHTTPRequestHandler, status: int, payload: Mapping[str, Any]) -> None:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Content-Type-Options", "nosniff")
        handler.end_headers()
        handler.wfile.write(data)


def _project_route(path: str) -> tuple[str, str]:
    prefix = "/v1/projects/"
    if not path.startswith(prefix):
        return "", "/invalid"
    remainder = path[len(prefix):]
    project_id, separator, suffix = remainder.partition("/")
    if not project_id or any(character not in "0123456789abcdef" for character in project_id):
        raise ValueError("Project id is invalid.")
    return project_id, ("/" + suffix if separator else "")


def _required_text(body: Mapping[str, Any], name: str) -> str:
    value = body.get(name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string.")
    return value.strip()


def _is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _queue_payload(task: Any) -> dict[str, Any]:
    return {
        "id": task.work_item.id,
        "title": task.work_item.title,
        "status": task.status,
        "attempts": task.attempts,
        "updated_at_utc": task.updated_at_utc,
        "last_error": task.last_error,
    }


def openapi_document() -> dict[str, Any]:
    bearer = [{"bearerAuth": []}]
    push_bearer = [{"a2aPushBearerAuth": []}]
    return {
        "openapi": "3.1.0",
        "info": {"title": "HoH Headless API", "version": __version__},
        "servers": [{"url": "/"}],
        "components": {
            "securitySchemes": {
                "bearerAuth": {"type": "http", "scheme": "bearer"},
                "a2aPushBearerAuth": {"type": "http", "scheme": "bearer"},
            }
        },
        "paths": {
            "/v1/health": {"get": {"responses": {"200": {"description": "Service health"}}}},
            "/v1/openapi.json": {"get": {"responses": {"200": {"description": "This document"}}}},
            "/v1/projects": {
                "get": {"security": bearer, "responses": {"200": {"description": "Project summaries"}}},
                "post": {"security": bearer, "responses": {"201": {"description": "Project registered"}}},
            },
            "/v1/run-due": {"post": {"security": bearer, "responses": {"200": {"description": "Due runs"}}}},
            "/v1/a2a/push": {
                "post": {
                    "security": push_bearer,
                    "responses": {"202": {"description": "A2A push event accepted"}},
                }
            },
            "/v1/projects/{projectId}": {
                "get": {"security": bearer, "responses": {"200": {"description": "Project summary"}}}
            },
            "/v1/projects/{projectId}/queue": {
                "get": {"security": bearer, "responses": {"200": {"description": "Project queue"}}}
            },
            "/v1/projects/{projectId}/metrics": {
                "get": {"security": bearer, "responses": {"200": {"description": "Usage metrics"}}}
            },
            "/v1/projects/{projectId}/chat": {
                "get": {"security": bearer, "responses": {"200": {"description": "Supervisor chat"}}},
                "post": {"security": bearer, "responses": {"200": {"description": "Supervisor turn"}}},
            },
            "/v1/projects/{projectId}/plan": {
                "post": {"security": bearer, "responses": {"201": {"description": "Plan enqueued"}}}
            },
            "/v1/projects/{projectId}/run": {
                "post": {"security": bearer, "responses": {"200": {"description": "Queue run result"}}}
            },
        },
    }
