from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hmac
import json
import os
from threading import Condition, Lock, Thread
import time
from typing import Any, Mapping


MAX_PUSH_BYTES = 10 * 1024 * 1024
_EVENT_KEYS = {"task", "message", "statusUpdate", "artifactUpdate"}


class A2APushError(RuntimeError):
    pass


class A2APushInbox:
    def __init__(self, *, max_events_per_task: int = 1000, max_tasks: int = 1000) -> None:
        self.max_events_per_task = max_events_per_task
        self.max_tasks = max_tasks
        self._condition = Condition()
        self._events: dict[str, list[Mapping[str, Any]]] = {}

    def record(self, event: Mapping[str, Any]) -> str:
        present = [key for key in _EVENT_KEYS if isinstance(event.get(key), dict)]
        if len(present) != 1:
            raise A2APushError("A2A push payload must contain exactly one StreamResponse event.")
        nested = event[present[0]]
        task_id = nested.get("taskId") or nested.get("id") or event.get("taskId")
        if not isinstance(task_id, str) or not task_id:
            raise A2APushError("A2A push event does not identify a task.")
        with self._condition:
            if task_id not in self._events and len(self._events) >= self.max_tasks:
                self._events.pop(next(iter(self._events)))
            target = self._events.setdefault(task_id, [])
            target.append(dict(event))
            if len(target) > self.max_events_per_task:
                del target[: len(target) - self.max_events_per_task]
            self._condition.notify_all()
        return task_id

    def wait(self, task_id: str, *, after: int = 0, timeout_seconds: float = 30.0) -> tuple[Mapping[str, Any], ...]:
        deadline = time.monotonic() + timeout_seconds
        with self._condition:
            while len(self._events.get(task_id, ())) <= after:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return ()
                self._condition.wait(remaining)
            return tuple(self._events[task_id][after:])

    def discard(self, task_id: str) -> None:
        with self._condition:
            self._events.pop(task_id, None)


_SHARED_PUSH_INBOX = A2APushInbox()


def shared_push_inbox() -> A2APushInbox:
    """Return the process-wide inbox used by the headless callback route and A2A clients."""

    return _SHARED_PUSH_INBOX


class A2APushReceiver:
    def __init__(
        self,
        inbox: A2APushInbox,
        *,
        token_env: str,
        host: str = "127.0.0.1",
        port: int = 0,
        path: str = "/a2a/push",
        environment: Mapping[str, str] | None = None,
    ) -> None:
        if not path.startswith("/") or "?" in path or "#" in path:
            raise A2APushError("A2A push path must be an absolute URL path.")
        self.inbox = inbox
        self.token_env = token_env
        self.host = host
        self.port = port
        self.path = path
        self.environment = environment if environment is not None else os.environ
        self._server: ThreadingHTTPServer | None = None
        self._thread: Thread | None = None

    @property
    def callback_url(self) -> str:
        if self._server is None:
            raise A2APushError("A2A push receiver is not running.")
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{self.path}"

    def start(self) -> str:
        token = self.environment.get(self.token_env, "")
        if not token:
            raise A2APushError(f"A2A push token environment variable is missing: {self.token_env}")
        inbox = self.inbox
        expected_path = self.path

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - stdlib callback name
                if self.path != expected_path:
                    self.send_error(404)
                    return
                supplied = self.headers.get("Authorization", "")
                if not hmac.compare_digest(supplied, f"Bearer {token}"):
                    self.send_error(401)
                    return
                try:
                    size = int(self.headers.get("Content-Length", "0"))
                except ValueError:
                    self.send_error(400)
                    return
                if size < 1 or size > MAX_PUSH_BYTES:
                    self.send_error(413)
                    return
                try:
                    payload = json.loads(self.rfile.read(size))
                    if not isinstance(payload, dict):
                        raise A2APushError("payload is not an object")
                    inbox.record(payload)
                except (UnicodeDecodeError, json.JSONDecodeError, A2APushError):
                    self.send_error(400)
                    return
                self.send_response(202)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, _format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = Thread(target=self._server.serve_forever, name="hoh-a2a-push", daemon=True)
        self._thread.start()
        return self.callback_url

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None

    def __enter__(self) -> "A2APushReceiver":
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()


@dataclass
class _ManagedReceiver:
    receiver: A2APushReceiver
    references: int


_RECEIVER_LOCK = Lock()
_MANAGED_RECEIVERS: dict[tuple[str, int, str, str], _ManagedReceiver] = {}


@contextmanager
def managed_loopback_push_receiver(
    *, host: str, port: int, path: str, token_env: str
):
    """Share one loopback receiver between concurrent A2A role invocations."""

    key = (host, port, path, token_env)
    with _RECEIVER_LOCK:
        managed = _MANAGED_RECEIVERS.get(key)
        if managed is None:
            inbox = A2APushInbox()
            receiver = A2APushReceiver(
                inbox,
                token_env=token_env,
                host=host,
                port=port,
                path=path,
            )
            receiver.start()
            managed = _ManagedReceiver(receiver, 0)
            _MANAGED_RECEIVERS[key] = managed
        managed.references += 1
    try:
        yield managed.receiver.inbox
    finally:
        with _RECEIVER_LOCK:
            managed.references -= 1
            if managed.references == 0:
                _MANAGED_RECEIVERS.pop(key, None)
                managed.receiver.stop()
