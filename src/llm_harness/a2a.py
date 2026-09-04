from __future__ import annotations

from dataclasses import dataclass
import json
import os
import re
import time
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import uuid4

from . import __version__
from .oauth import DeviceAuthorization, OAuthClient, OAuthClientConfig, OAuthError
from .a2a_push import (
    A2APushError,
    A2APushInbox,
    managed_loopback_push_receiver,
    shared_push_inbox,
)


A2A_PROTOCOL_VERSION = "1.0"
MAX_AGENT_CARD_BYTES = 2 * 1024 * 1024
MAX_A2A_RESPONSE_BYTES = 10 * 1024 * 1024
TERMINAL_STATES = {"COMPLETED", "FAILED", "CANCELED", "CANCELLED", "REJECTED"}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")


class A2AError(RuntimeError):
    pass


class A2AAuthenticationError(A2AError):
    pass


@dataclass(frozen=True)
class A2AAgentCard:
    name: str
    version: str
    endpoint: str
    protocol_version: str
    streaming: bool
    security_required: bool
    security_schemes: Mapping[str, Mapping[str, Any]]
    skills: tuple[str, ...]


@dataclass(frozen=True)
class A2AResult:
    texts: tuple[str, ...]
    data: tuple[Mapping[str, Any], ...]
    task_id: str | None
    state: str
    request_id: str

    @property
    def text(self) -> str:
        return "\n".join(item for item in self.texts if item).strip()


HttpTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float, int],
    tuple[int, Mapping[str, str], bytes, str],
]
SseTransport = Callable[
    [str, Mapping[str, str], bytes, float, int],
    tuple[int, Mapping[str, str], Sequence[Mapping[str, Any]], str],
]


@dataclass(frozen=True)
class A2AConnection:
    card_url: str
    auth_kind: str = "none"
    credential_env: str | None = None
    api_key_header: str | None = None
    oauth_flow: str = "client_credentials"
    client_id_env: str | None = None
    client_secret_env: str | None = None
    token_url: str | None = None
    device_authorization_url: str | None = None
    oidc_discovery_url: str | None = None
    scopes: tuple[str, ...] = ()
    client_auth_method: str = "basic"
    prefer_streaming: bool = True
    push_callback_url: str | None = None
    push_token_env: str | None = None
    timeout_seconds: float = 300.0
    poll_interval_seconds: float = 1.0

    def __post_init__(self) -> None:
        _validate_remote_url(self.card_url)
        if self.auth_kind not in {"none", "bearer", "api_key", "oauth2", "oidc"}:
            raise A2AError(f"Unsupported A2A authentication kind: {self.auth_kind}")
        if self.auth_kind in {"bearer", "api_key"} and not self.credential_env:
            raise A2AError("Authenticated A2A connection requires credential_env.")
        if self.credential_env and not _ENV_NAME.fullmatch(self.credential_env):
            raise A2AError("A2A credential_env must be an environment-variable name.")
        if self.auth_kind == "api_key" and not self.api_key_header:
            raise A2AError("A2A api_key connection requires api_key_header.")
        if self.api_key_header and not _HEADER_NAME.fullmatch(self.api_key_header):
            raise A2AError("A2A api_key_header must be a valid HTTP header name.")
        if self.api_key_header and self.api_key_header.casefold() in {
            "host", "content-length", "a2a-version", "a2a-extensions"
        }:
            raise A2AError("A2A api_key_header cannot override a protocol-owned HTTP header.")
        if self.auth_kind != "api_key" and self.api_key_header:
            raise A2AError("A2A api_key_header is valid only for api_key authentication.")
        if self.auth_kind in {"oauth2", "oidc"} and not self.credential_env:
            if not self.client_id_env:
                raise A2AError("OAuth/OIDC A2A connection requires client_id_env or credential_env.")
            if self.oauth_flow == "client_credentials" and not self.client_secret_env:
                raise A2AError("OAuth client_credentials requires client_secret_env.")
        if self.oauth_flow not in {"client_credentials", "device_code"}:
            raise A2AError("A2A oauth_flow must be client_credentials or device_code.")
        if self.client_auth_method not in {"basic", "post"}:
            raise A2AError("A2A client_auth_method must be basic or post.")
        if self.push_callback_url:
            _validate_remote_url(self.push_callback_url)
            if not self.push_token_env:
                raise A2AError("A2A push callbacks require push_token_env.")
        if self.timeout_seconds <= 0 or self.poll_interval_seconds <= 0:
            raise A2AError("A2A timeout and poll interval must be greater than zero.")


def connection_from_role(
    card_url: str,
    environment: tuple[tuple[str, str], ...],
    timeout_seconds: float,
) -> A2AConnection:
    values = dict(environment)
    try:
        poll_interval = float(values.get("HOH_A2A_POLL_INTERVAL", "1"))
    except ValueError as exc:
        raise A2AError("HOH_A2A_POLL_INTERVAL must be numeric.") from exc
    return A2AConnection(
        card_url=card_url,
        auth_kind=values.get("HOH_A2A_AUTH_KIND", "none"),
        credential_env=values.get("HOH_A2A_CREDENTIAL_ENV"),
        api_key_header=values.get("HOH_A2A_API_KEY_HEADER"),
        oauth_flow=values.get("HOH_A2A_OAUTH_FLOW", "client_credentials"),
        client_id_env=values.get("HOH_A2A_CLIENT_ID_ENV"),
        client_secret_env=values.get("HOH_A2A_CLIENT_SECRET_ENV"),
        token_url=values.get("HOH_A2A_TOKEN_URL"),
        device_authorization_url=values.get("HOH_A2A_DEVICE_AUTHORIZATION_URL"),
        oidc_discovery_url=values.get("HOH_A2A_OIDC_DISCOVERY_URL"),
        scopes=tuple(values.get("HOH_A2A_SCOPES", "").split()),
        client_auth_method=values.get("HOH_A2A_CLIENT_AUTH_METHOD", "basic"),
        prefer_streaming=values.get("HOH_A2A_PREFER_STREAMING", "1") not in {"0", "false", "False"},
        push_callback_url=values.get("HOH_A2A_PUSH_CALLBACK_URL"),
        push_token_env=values.get("HOH_A2A_PUSH_TOKEN_ENV"),
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval,
    )


class A2AClient:
    def __init__(
        self,
        connection: A2AConnection,
        *,
        transport: HttpTransport | None = None,
        sse_transport: SseTransport | None = None,
        push_inbox: A2APushInbox | None = None,
    ) -> None:
        self.connection = connection
        self._transport = transport or _http_transport
        self._sse_transport = sse_transport or _http_sse_transport
        self._loopback_push_endpoint: tuple[str, int, str, str] | None = None
        if push_inbox is not None:
            self._push_inbox = push_inbox
        elif connection.push_callback_url:
            parsed_callback = urlparse(connection.push_callback_url)
            if (
                parsed_callback.scheme == "http"
                and parsed_callback.hostname in {"localhost", "127.0.0.1"}
                and not parsed_callback.query
                and not parsed_callback.fragment
            ):
                self._push_inbox = None
                self._loopback_push_endpoint = (
                    parsed_callback.hostname,
                    parsed_callback.port or 80,
                    parsed_callback.path or "/",
                    connection.push_token_env or "",
                )
            else:
                self._push_inbox = shared_push_inbox()
        else:
            self._push_inbox = None
        self._next_id = 1
        self._card: A2AAgentCard | None = None
        self._oauth = OAuthClient(
            OAuthClientConfig(
                auth_kind=connection.auth_kind,
                flow=connection.oauth_flow,
                access_token_env=connection.credential_env,
                client_id_env=connection.client_id_env,
                client_secret_env=connection.client_secret_env,
                token_url=connection.token_url,
                device_authorization_url=connection.device_authorization_url,
                discovery_url=connection.oidc_discovery_url,
                scopes=connection.scopes,
                client_auth_method=connection.client_auth_method,
                timeout_seconds=connection.timeout_seconds,
            )
        ) if connection.auth_kind in {"oauth2", "oidc"} else None

    def discover(self) -> A2AAgentCard:
        if self._card is not None:
            return self._card
        card_url = _agent_card_url(self.connection.card_url)
        status, _headers, payload, final_url = self._transport(
            card_url,
            "GET",
            {"Accept": "application/json", "User-Agent": f"HoH/{__version__}"},
            None,
            min(self.connection.timeout_seconds, 20.0),
            MAX_AGENT_CARD_BYTES,
        )
        _validate_remote_url(final_url)
        if status != 200:
            raise A2AError(f"A2A Agent Card returned HTTP {status}.")
        raw = _json_object(payload, "A2A Agent Card")
        interfaces = raw.get("supportedInterfaces")
        if not isinstance(interfaces, list):
            raise A2AError("A2A v1 Agent Card must declare supportedInterfaces.")
        selected = next(
            (
                item
                for item in interfaces
                if isinstance(item, dict)
                and str(item.get("protocolBinding", "")).upper() == "JSONRPC"
                and str(item.get("protocolVersion", "")) == A2A_PROTOCOL_VERSION
            ),
            None,
        )
        if selected is None or not isinstance(selected.get("url"), str):
            raise A2AError("A2A Agent Card offers no JSONRPC v1.0 interface.")
        endpoint = str(selected["url"])
        _validate_remote_url(endpoint)
        capabilities = raw.get("capabilities")
        raw_skills = raw.get("skills")
        raw_security_schemes = raw.get("securitySchemes")
        security_schemes = {
            str(key): dict(value)
            for key, value in raw_security_schemes.items()
            if isinstance(key, str) and isinstance(value, dict)
        } if isinstance(raw_security_schemes, dict) else {}
        skills = tuple(
            str(item["id"])
            for item in raw_skills
            if isinstance(item, dict) and isinstance(item.get("id"), str)
        ) if isinstance(raw_skills, list) else ()
        self._card = A2AAgentCard(
            name=str(raw.get("name") or "remote-agent"),
            version=str(raw.get("version") or "unknown"),
            endpoint=endpoint,
            protocol_version=A2A_PROTOCOL_VERSION,
            streaming=isinstance(capabilities, dict) and bool(capabilities.get("streaming")),
            security_required=bool(raw.get("security") or raw.get("securityRequirements")),
            security_schemes=security_schemes,
            skills=skills,
        )
        self._configure_oauth_from_card(raw, security_schemes)
        return self._card

    def _configure_oauth_from_card(
        self,
        raw_card: Mapping[str, Any],
        schemes: Mapping[str, Mapping[str, Any]],
    ) -> None:
        if self.connection.auth_kind not in {"oauth2", "oidc"}:
            return
        selected = _select_security_scheme(raw_card, schemes, self.connection.auth_kind)
        token_url = self.connection.token_url
        device_url = self.connection.device_authorization_url
        discovery_url = self.connection.oidc_discovery_url
        if selected is not None:
            if self.connection.auth_kind == "oidc":
                discovery_url = discovery_url or _mapping_string(
                    selected, "openIdConnectUrl", "open_id_connect_url"
                )
            flows = selected.get("flows")
            if isinstance(flows, dict):
                flow = flows.get("clientCredentials") or flows.get("client_credentials")
                if self.connection.oauth_flow == "device_code":
                    flow = flows.get("deviceCode") or flows.get("device_code")
                if isinstance(flow, dict):
                    token_url = token_url or _mapping_string(flow, "tokenUrl", "token_url")
                    device_url = device_url or _mapping_string(
                        flow, "deviceAuthorizationUrl", "device_authorization_url"
                    )
        self._oauth = OAuthClient(
            OAuthClientConfig(
                auth_kind=self.connection.auth_kind,
                flow=self.connection.oauth_flow,
                access_token_env=self.connection.credential_env,
                client_id_env=self.connection.client_id_env,
                client_secret_env=self.connection.client_secret_env,
                token_url=token_url,
                device_authorization_url=device_url,
                discovery_url=discovery_url,
                scopes=self.connection.scopes,
                client_auth_method=self.connection.client_auth_method,
                timeout_seconds=self.connection.timeout_seconds,
            )
        )

    def send_message(self, prompt: str) -> A2AResult:
        if not prompt.strip():
            raise A2AError("A2A message must be non-empty.")
        card = self.discover()
        if card.security_required and self.connection.auth_kind == "none":
            raise A2AAuthenticationError(
                "The A2A Agent Card requires authentication; configure bearer, api_key, OAuth2, or OIDC."
            )
        if card.streaming and self.connection.prefer_streaming:
            return self._send_streaming(card, prompt)
        if self._loopback_push_endpoint is not None:
            try:
                host, port, path, token_env = self._loopback_push_endpoint
                with managed_loopback_push_receiver(
                    host=host, port=port, path=path, token_env=token_env
                ) as inbox:
                    self._push_inbox = inbox
                    try:
                        return self._send_message_non_streaming(card, prompt)
                    finally:
                        self._push_inbox = None
            except (A2APushError, OSError) as exc:
                raise A2AError(f"A2A push receiver failed: {exc}") from exc
        return self._send_message_non_streaming(card, prompt)

    def _send_message_non_streaming(self, card: A2AAgentCard, prompt: str) -> A2AResult:
        request_id = str(uuid4())
        params: dict[str, Any] = {
            "message": {
                "messageId": request_id,
                "role": "ROLE_USER",
                "parts": [{"text": prompt}],
            }
        }
        if self.connection.push_callback_url:
            push_token = os.environ.get(self.connection.push_token_env or "", "")
            if not push_token:
                raise A2AAuthenticationError(
                    f"A2A push credential environment variable is missing: {self.connection.push_token_env}"
                )
            params["configuration"] = {
                "taskPushNotificationConfig": {
                    "url": self.connection.push_callback_url,
                    "authentication": {"scheme": "Bearer", "credentials": push_token},
                }
            }
        result = self._request(
            card,
            "SendMessage",
            params,
        )
        payload = _unwrap_message_or_task(result)
        task_id = _task_id(payload)
        state = _task_state(payload)
        if (
            task_id is not None
            and state not in TERMINAL_STATES
            and self.connection.push_callback_url
            and self._push_inbox is not None
        ):
            return self._wait_for_push(card, task_id, request_id)
        deadline = time.monotonic() + self.connection.timeout_seconds
        while task_id is not None and state not in TERMINAL_STATES:
            if time.monotonic() >= deadline:
                try:
                    self._request(card, "CancelTask", {"id": task_id})
                except A2AError:
                    pass
                raise A2AError(f"A2A task {task_id} timed out and cancellation was requested.")
            time.sleep(min(self.connection.poll_interval_seconds, max(0.01, deadline - time.monotonic())))
            payload = _unwrap_message_or_task(self._request(card, "GetTask", {"id": task_id}))
            state = _task_state(payload)
        if state in {"FAILED", "CANCELED", "CANCELLED", "REJECTED"}:
            detail = "\n".join(_extract_texts(payload))[:2000]
            raise A2AError(f"A2A task {task_id or request_id} ended in {state}: {detail}")
        texts = _extract_texts(payload)
        data = _extract_data(payload)
        return A2AResult(texts, data, task_id, state or "COMPLETED", request_id)

    def _wait_for_push(self, card: A2AAgentCard, task_id: str, request_id: str) -> A2AResult:
        deadline = time.monotonic() + self.connection.timeout_seconds
        events: list[Mapping[str, Any]] = []
        try:
            while time.monotonic() < deadline:
                remaining = max(0.01, deadline - time.monotonic())
                received = self._push_inbox.wait(
                    task_id,
                    after=len(events),
                    timeout_seconds=remaining,
                )
                events.extend(received)
                if events:
                    result = _stream_result(events, request_id)
                    if result.state in TERMINAL_STATES:
                        if result.state in {"FAILED", "CANCELED", "CANCELLED", "REJECTED"}:
                            raise A2AError(
                                f"A2A task {task_id} ended in {result.state}: {result.text[:2000]}"
                            )
                        return result
            try:
                self._request(card, "CancelTask", {"id": task_id})
            except A2AError:
                pass
            raise A2AError(
                f"A2A task {task_id} timed out waiting for push and cancellation was requested."
            )
        finally:
            self._push_inbox.discard(task_id)

    def authorize_device(self, on_user_code: Callable[[DeviceAuthorization], None]) -> None:
        self.discover()
        if self._oauth is None or self.connection.oauth_flow != "device_code":
            raise A2AAuthenticationError("This A2A connection is not configured for OAuth device authorization.")
        try:
            self._oauth.authorize_device(on_user_code)
        except OAuthError as exc:
            raise A2AAuthenticationError(str(exc)) from exc

    def authenticate(self) -> None:
        self.discover()
        if self.connection.auth_kind not in {"oauth2", "oidc"}:
            raise A2AAuthenticationError("This A2A connection does not use OAuth2 or OIDC.")
        self._credential()

    def create_push_notification_config(
        self,
        task_id: str,
        *,
        callback_url: str | None = None,
        token_env: str | None = None,
    ) -> Mapping[str, Any]:
        card = self.discover()
        url = callback_url or self.connection.push_callback_url
        environment_name = token_env or self.connection.push_token_env
        if not url or not environment_name:
            raise A2AError("Push notification configuration requires callback_url and token_env.")
        _validate_remote_url(url)
        token = os.environ.get(environment_name, "")
        if not token:
            raise A2AAuthenticationError(f"A2A push credential environment variable is missing: {environment_name}")
        return self._request(
            card,
            "CreateTaskPushNotificationConfig",
            {
                "taskId": task_id,
                "config": {
                    "url": url,
                    "authentication": {"scheme": "Bearer", "credentials": token},
                },
            },
        )

    def list_push_notification_configs(self, task_id: str) -> Mapping[str, Any]:
        return self._request(self.discover(), "ListTaskPushNotificationConfigs", {"taskId": task_id})

    def delete_push_notification_config(self, task_id: str, config_id: str) -> Mapping[str, Any]:
        return self._request(
            self.discover(),
            "DeleteTaskPushNotificationConfig",
            {"taskId": task_id, "id": config_id},
            allow_empty_result=True,
        )

    def _send_streaming(self, card: A2AAgentCard, prompt: str) -> A2AResult:
        request_id = str(uuid4())
        params: dict[str, Any] = {
            "message": {
                "messageId": request_id,
                "role": "ROLE_USER",
                "parts": [{"text": prompt}],
            }
        }
        events = list(self._stream_request(card, "SendStreamingMessage", params))
        result = _stream_result(events, request_id)
        if result.task_id and result.state not in TERMINAL_STATES:
            events.extend(self._stream_request(card, "SubscribeToTask", {"id": result.task_id}))
            result = _stream_result(events, request_id)
        if result.state in {"FAILED", "CANCELED", "CANCELLED", "REJECTED"}:
            raise A2AError(
                f"A2A task {result.task_id or request_id} ended in {result.state}: {result.text[:2000]}"
            )
        if result.task_id and result.state not in TERMINAL_STATES:
            raise A2AError(f"A2A stream ended before task {result.task_id} reached a terminal state.")
        return result

    def _stream_request(
        self,
        card: A2AAgentCard,
        method: str,
        params: Mapping[str, Any],
    ) -> Sequence[Mapping[str, Any]]:
        rpc_id = self._next_id
        self._next_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": dict(params)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            "User-Agent": f"HoH/{__version__}",
            "A2A-Version": card.protocol_version,
            **self._authentication_headers(),
        }
        status, response_headers, events, final_url = self._sse_transport(
            card.endpoint,
            headers,
            body,
            self.connection.timeout_seconds,
            MAX_A2A_RESPONSE_BYTES,
        )
        _validate_remote_url(final_url)
        if status in {401, 403}:
            raise A2AAuthenticationError(f"A2A endpoint rejected authentication with HTTP {status}.")
        if status != 200:
            raise A2AError(f"A2A endpoint returned HTTP {status} for {method}.")
        content_type = next(
            (value for key, value in response_headers.items() if key.casefold() == "content-type"), ""
        )
        if "text/event-stream" not in content_type.casefold():
            raise A2AError(f"A2A {method} did not return text/event-stream.")
        results: list[Mapping[str, Any]] = []
        for envelope in events:
            if envelope.get("id") != rpc_id:
                raise A2AError(f"A2A {method} stream response id does not match the request.")
            error = envelope.get("error")
            if error is not None:
                raise A2AError(f"A2A {method} failed: {_redacted_error(error, self._credential())}")
            result = envelope.get("result")
            if not isinstance(result, dict):
                raise A2AError(f"A2A {method} stream event returned no result object.")
            results.append(result)
        if not results:
            raise A2AError(f"A2A {method} returned an empty event stream.")
        return tuple(results)

    def _request(
        self,
        card: A2AAgentCard,
        method: str,
        params: Mapping[str, Any],
        *,
        allow_empty_result: bool = False,
    ) -> Mapping[str, Any]:
        rpc_id = self._next_id
        self._next_id += 1
        body = json.dumps(
            {"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": dict(params)},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": f"HoH/{__version__}",
            "A2A-Version": card.protocol_version,
            **self._authentication_headers(),
        }
        status, _response_headers, response, final_url = self._transport(
            card.endpoint,
            "POST",
            headers,
            body,
            self.connection.timeout_seconds,
            MAX_A2A_RESPONSE_BYTES,
        )
        _validate_remote_url(final_url)
        if status in {401, 403}:
            raise A2AAuthenticationError(f"A2A endpoint rejected authentication with HTTP {status}.")
        if status != 200:
            raise A2AError(f"A2A endpoint returned HTTP {status} for {method}.")
        envelope = _json_object(response, f"A2A {method} response")
        if envelope.get("id") != rpc_id:
            raise A2AError(f"A2A {method} response id does not match the request.")
        error = envelope.get("error")
        if error is not None:
            raise A2AError(f"A2A {method} failed: {_redacted_error(error, self._credential())}")
        result = envelope.get("result")
        if result is None and allow_empty_result:
            return {}
        if not isinstance(result, dict):
            raise A2AError(f"A2A {method} returned no result object.")
        return result

    def _credential(self) -> str:
        if self.connection.auth_kind == "none":
            return ""
        if self.connection.auth_kind in {"oauth2", "oidc"}:
            if self._oauth is None:
                raise A2AAuthenticationError("OAuth client is unavailable.")
            try:
                return self._oauth.access_token()
            except OAuthError as exc:
                raise A2AAuthenticationError(str(exc)) from exc
        name = self.connection.credential_env or ""
        value = os.environ.get(name, "")
        if not value:
            raise A2AAuthenticationError(f"A2A credential environment variable is missing: {name}")
        return value

    def _authentication_headers(self) -> dict[str, str]:
        if self.connection.auth_kind in {"bearer", "oauth2", "oidc"}:
            return {"Authorization": f"Bearer {self._credential()}"}
        if self.connection.auth_kind == "api_key":
            return {str(self.connection.api_key_header): self._credential()}
        return {}


def probe_a2a_agent(connection: A2AConnection, *, transport: HttpTransport | None = None) -> tuple[bool, str, str | None]:
    try:
        client = A2AClient(connection, transport=transport)
        card = client.discover()
    except A2AError as exc:
        return False, str(exc), None
    if card.security_required and connection.auth_kind == "none":
        return False, "Agent Card requires authentication but no A2A auth is configured.", card.version
    if connection.auth_kind in {"bearer", "api_key"} and not os.environ.get(connection.credential_env or ""):
        return False, f"Missing credential environment variable: {connection.credential_env}", card.version
    if connection.auth_kind in {"oauth2", "oidc"}:
        required = [connection.client_id_env]
        if connection.oauth_flow == "client_credentials":
            required.append(connection.client_secret_env)
        missing = [name for name in required if name and not os.environ.get(name)]
        if connection.credential_env and os.environ.get(connection.credential_env):
            missing = []
        if missing:
            return False, "Missing OAuth environment variable(s): " + ", ".join(missing), card.version
    delivery = "SSE" if card.streaming and connection.prefer_streaming else (
        "push" if connection.push_callback_url else "JSONRPC"
    )
    return True, f"A2A {card.protocol_version} {delivery} · {card.name}", card.version


def _http_transport(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[int, Mapping[str, str], bytes, str]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(max_bytes + 1)
            if len(payload) > max_bytes:
                raise A2AError("A2A response exceeds its safety limit.")
            return int(response.status), dict(response.headers.items()), payload, response.geturl()
    except HTTPError as exc:
        payload = exc.read(min(max_bytes, 64 * 1024))
        return int(exc.code), dict(exc.headers.items()), payload, exc.geturl()
    except (OSError, URLError) as exc:
        raise A2AError(f"A2A network request failed: {exc}") from exc


def _http_sse_transport(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_seconds: float,
    max_bytes: int,
) -> tuple[int, Mapping[str, str], Sequence[Mapping[str, Any]], str]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            response_headers = dict(response.headers.items())
            final_url = response.geturl()
            events: list[Mapping[str, Any]] = []
            data_lines: list[bytes] = []
            total = 0
            while True:
                line = response.readline()
                if not line:
                    if data_lines:
                        events.append(_json_object(b"\n".join(data_lines), "A2A SSE event"))
                    break
                total += len(line)
                if total > max_bytes:
                    raise A2AError("A2A event stream exceeds its safety limit.")
                stripped = line.rstrip(b"\r\n")
                if not stripped:
                    if data_lines:
                        events.append(_json_object(b"\n".join(data_lines), "A2A SSE event"))
                        data_lines = []
                    continue
                if stripped.startswith(b":"):
                    continue
                if stripped.startswith(b"data:"):
                    value = stripped[5:]
                    if value.startswith(b" "):
                        value = value[1:]
                    data_lines.append(value)
            return int(response.status), response_headers, tuple(events), final_url
    except HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), (), exc.geturl()
    except (OSError, URLError) as exc:
        raise A2AError(f"A2A streaming request failed: {exc}") from exc


def _agent_card_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.path.casefold().endswith(".json"):
        return value
    authority = parsed.netloc
    return f"{parsed.scheme}://{authority}/.well-known/agent-card.json"


def _select_security_scheme(
    card: Mapping[str, Any],
    schemes: Mapping[str, Mapping[str, Any]],
    auth_kind: str,
) -> Mapping[str, Any] | None:
    requirements = card.get("security") or card.get("securityRequirements")
    allowed_names: set[str] = set()
    if isinstance(requirements, list):
        for requirement in requirements:
            if isinstance(requirement, dict):
                allowed_names.update(str(key) for key in requirement)
    for name, scheme in schemes.items():
        if allowed_names and name not in allowed_names:
            continue
        scheme_type = str(scheme.get("type", "")).casefold().replace("_", "")
        if auth_kind == "oidc" and scheme_type == "openidconnect":
            return scheme
        if auth_kind == "oauth2" and scheme_type == "oauth2":
            return scheme
    return None


def _mapping_string(value: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        item = value.get(key)
        if isinstance(item, str) and item:
            return item
    return None


def _validate_remote_url(value: str) -> None:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise A2AError("A2A URL must be absolute HTTP(S).")
    if parsed.username or parsed.password:
        raise A2AError("A2A URL must not contain credentials.")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in {"localhost", "127.0.0.1", "::1"}:
        raise A2AError("Remote A2A communication requires HTTPS; HTTP is allowed only on loopback.")


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise A2AError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise A2AError(f"{label} must be a JSON object.")
    return value


def _unwrap_message_or_task(value: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("task", "message"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _stream_result(events: Sequence[Mapping[str, Any]], request_id: str) -> A2AResult:
    texts: list[str] = []
    data: list[Mapping[str, Any]] = []
    task_id: str | None = None
    state = ""
    for event in events:
        payload = _unwrap_stream_event(event)
        task_id = _task_id(payload) or _task_id(event) or task_id
        candidate_state = _task_state(payload) or _task_state(event)
        if candidate_state:
            state = candidate_state
        texts.extend(_extract_texts(payload))
        data.extend(_extract_data(payload))
    deduped_texts = tuple(dict.fromkeys(item for item in texts if item))
    seen_data: set[str] = set()
    deduped_data: list[Mapping[str, Any]] = []
    for item in data:
        identity = json.dumps(item, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        if identity not in seen_data:
            seen_data.add(identity)
            deduped_data.append(item)
    return A2AResult(
        deduped_texts,
        tuple(deduped_data),
        task_id,
        state or ("COMPLETED" if (texts or data) and task_id is None else ""),
        request_id,
    )


def _unwrap_stream_event(value: Mapping[str, Any]) -> Mapping[str, Any]:
    for key in ("task", "message", "statusUpdate", "artifactUpdate"):
        nested = value.get(key)
        if isinstance(nested, dict):
            return nested
    return value


def _task_id(value: Mapping[str, Any]) -> str | None:
    item = value.get("id") or value.get("taskId")
    return str(item) if isinstance(item, str) and item else None


def _task_state(value: Mapping[str, Any]) -> str:
    status = value.get("status")
    state = status.get("state") if isinstance(status, dict) else value.get("state")
    if not isinstance(state, str):
        return "COMPLETED" if _looks_like_message(value) else ""
    normalized = state.upper().replace("TASK_STATE_", "")
    return normalized


def _looks_like_message(value: Mapping[str, Any]) -> bool:
    return isinstance(value.get("parts"), list) or str(value.get("role", "")).upper() in {"AGENT", "ROLE_AGENT"}


def _extract_texts(value: Mapping[str, Any]) -> tuple[str, ...]:
    texts: list[str] = []
    for part in _all_parts(value):
        text = part.get("text")
        if isinstance(text, str):
            texts.append(text)
    return tuple(texts)


def _extract_data(value: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    data: list[Mapping[str, Any]] = []
    for part in _all_parts(value):
        item = part.get("data")
        if isinstance(item, dict):
            data.append(item)
    return tuple(data)


def _all_parts(value: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    parts: list[Mapping[str, Any]] = []

    def collect(container: object) -> None:
        if not isinstance(container, dict):
            return
        raw_parts = container.get("parts")
        if isinstance(raw_parts, list):
            parts.extend(item for item in raw_parts if isinstance(item, dict))

    collect(value)
    artifacts = value.get("artifacts")
    if isinstance(artifacts, list):
        for artifact in artifacts:
            collect(artifact)
    collect(value.get("artifact"))
    history = value.get("history")
    if isinstance(history, list):
        for message in history:
            collect(message)
    status = value.get("status")
    if isinstance(status, dict):
        collect(status.get("message"))
    return tuple(parts)


def _redacted_error(value: object, credential: str) -> str:
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    if credential:
        text = text.replace(credential, "<redacted>")
    return text[:2000]
