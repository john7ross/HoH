from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import random
import socket
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from .config import CloudModelEndpoint
from .domain import ModelInvocationEvidence
from .secret_store import resolve_credential


DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com",
}
DEFAULT_API_KEY_ENVS = {
    "openai": "OPENAI_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
}


class ModelProviderError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        provider: str,
        kind: str,
        retryable: bool = False,
        status_code: int | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.kind = kind
        self.retryable = retryable
        self.status_code = status_code
        self.request_id = request_id


@dataclass(frozen=True)
class ModelRequest:
    instructions: str
    input_text: str
    output_schema_name: str
    output_schema: Mapping[str, Any]


@dataclass(frozen=True)
class ModelResponse:
    output: dict[str, Any]
    evidence: ModelInvocationEvidence


HttpSender = Callable[[str, Mapping[str, str], bytes, float], tuple[int, Mapping[str, str], bytes]]


def create_model_provider(
    endpoint: CloudModelEndpoint,
    *,
    environment: Mapping[str, str] | None = None,
    http_sender: HttpSender | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    random_source: Callable[[], float] = random.random,
):
    if endpoint.provider == "stub":
        return StubModelProvider(endpoint)
    return JsonHttpsModelProvider(
        endpoint,
        environment=environment,
        http_sender=http_sender,
        sleeper=sleeper,
        random_source=random_source,
    )


class StubModelProvider:
    def __init__(self, endpoint: CloudModelEndpoint) -> None:
        self.endpoint = endpoint

    def invoke(self, request: ModelRequest) -> ModelResponse:
        raise ModelProviderError(
            "Model role uses the offline stub; configure provider=openai, deepseek, or openai_compatible.",
            provider="stub",
            kind="not_configured",
        )


class JsonHttpsModelProvider:
    def __init__(
        self,
        endpoint: CloudModelEndpoint,
        *,
        environment: Mapping[str, str] | None = None,
        http_sender: HttpSender | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        random_source: Callable[[], float] = random.random,
    ) -> None:
        self.endpoint = endpoint
        self.environment = environment if environment is not None else os.environ
        self.http_sender = http_sender or _send_http
        self.sleeper = sleeper
        self.random_source = random_source

    def invoke(self, request: ModelRequest) -> ModelResponse:
        api_key_env = self.endpoint.api_key_env or DEFAULT_API_KEY_ENVS[self.endpoint.provider]
        api_key = resolve_credential(api_key_env, self.environment)
        if not api_key:
            raise ModelProviderError(
                f"No credential for {api_key_env}. Export it, or save it in project settings.",
                provider=self.endpoint.provider,
                kind="authentication",
            )
        url, payload = self._build_request(request)
        _validate_model_url(url)
        body = _canonical_json(payload)
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "llm-harness/0.2",
        }
        started = time.monotonic()
        attempts = 0
        while True:
            attempts += 1
            try:
                status, response_headers, response_body = self.http_sender(
                    url, headers, body, self.endpoint.timeout_seconds
                )
                if status < 200 or status >= 300:
                    raise _http_status_error(
                        self.endpoint.provider, status, response_headers, response_body
                    )
                return self._parse_response(
                    response_headers,
                    response_body,
                    attempts,
                    int((time.monotonic() - started) * 1000),
                    body,
                    url,
                )
            except ModelProviderError as exc:
                if not exc.retryable or attempts > self.endpoint.max_retries:
                    raise
                self.sleeper(min(8.0, (0.5 * (2 ** (attempts - 1))) + self.random_source() * 0.25))
            except (TimeoutError, socket.timeout, URLError) as exc:
                retryable = attempts <= self.endpoint.max_retries
                if not retryable:
                    raise ModelProviderError(
                        "Model request timed out or could not reach the provider.",
                        provider=self.endpoint.provider,
                        kind="transport",
                        retryable=True,
                    ) from exc
                self.sleeper(min(8.0, (0.5 * (2 ** (attempts - 1))) + self.random_source() * 0.25))

    def _build_request(self, request: ModelRequest) -> tuple[str, dict[str, Any]]:
        base_url = (self.endpoint.base_url or DEFAULT_BASE_URLS[self.endpoint.provider]).rstrip("/")
        if self.endpoint.provider == "openai":
            return (
                base_url + "/responses",
                {
                    "model": self.endpoint.model,
                    "instructions": request.instructions,
                    "input": request.input_text,
                    "max_output_tokens": self.endpoint.max_output_tokens,
                    "text": {
                        "format": {
                            "type": "json_schema",
                            "name": request.output_schema_name,
                            "strict": True,
                            "schema": request.output_schema,
                        }
                    },
                },
            )
        return (
            base_url + "/chat/completions",
            {
                "model": self.endpoint.model,
                "messages": [
                    {"role": "system", "content": request.instructions},
                    {
                        "role": "user",
                        "content": request.input_text
                        + "\nReturn JSON only. The response must be a JSON object matching this contract:\n"
                        + json.dumps(request.output_schema, ensure_ascii=False, sort_keys=True),
                    },
                ],
                "response_format": {"type": "json_object"},
                "max_tokens": self.endpoint.max_output_tokens,
                "stream": False,
            },
        )

    def _parse_response(
        self,
        headers: Mapping[str, str],
        body: bytes,
        attempts: int,
        latency_ms: int,
        request_body: bytes,
        url: str,
    ) -> ModelResponse:
        try:
            payload = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ModelProviderError(
                "Model provider returned invalid JSON.",
                provider=self.endpoint.provider,
                kind="invalid_response",
            ) from exc
        if not isinstance(payload, dict):
            raise ModelProviderError(
                "Model provider returned a JSON value that is not an object.",
                provider=self.endpoint.provider,
                kind="invalid_response",
            )
        try:
            if self.endpoint.provider == "openai":
                text = payload.get("output_text") or _openai_output_text(payload)
                usage = payload.get("usage") or {}
                input_tokens = usage.get("input_tokens")
                output_tokens = usage.get("output_tokens")
            else:
                text = payload["choices"][0]["message"]["content"]
                usage = payload.get("usage") or {}
                input_tokens = usage.get("prompt_tokens")
                output_tokens = usage.get("completion_tokens")
            output = json.loads(text)
            if not isinstance(output, dict):
                raise ValueError("structured output is not an object")
        except (KeyError, IndexError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ModelProviderError(
                "Model provider response did not contain a valid structured JSON object.",
                provider=self.endpoint.provider,
                kind="invalid_response",
            ) from exc
        request_id = _header(headers, "x-request-id") or payload.get("id")
        evidence = ModelInvocationEvidence(
            provider=self.endpoint.provider,
            model=self.endpoint.model,
            endpoint=urlparse(url).path,
            request_id=str(request_id) if request_id else None,
            attempts=attempts,
            latency_ms=latency_ms,
            input_tokens=_optional_int(input_tokens),
            output_tokens=_optional_int(output_tokens),
            total_tokens=_optional_int(usage.get("total_tokens")),
            request_sha256=hashlib.sha256(request_body).hexdigest(),
            response_sha256=hashlib.sha256(body).hexdigest(),
        )
        return ModelResponse(output=output, evidence=evidence)


def model_endpoint_preflight(
    endpoint: CloudModelEndpoint,
    environment: Mapping[str, str] | None = None,
) -> tuple[bool, str]:
    if endpoint.provider == "stub":
        return True, f"provider=stub model={endpoint.model} offline"
    env = environment if environment is not None else os.environ
    key_env = endpoint.api_key_env or DEFAULT_API_KEY_ENVS[endpoint.provider]
    source = "environment" if env.get(key_env) else ("stored" if resolve_credential(key_env, env) else "")
    if not source:
        return False, f"provider={endpoint.provider} no credential for {key_env}"
    url = (endpoint.base_url or DEFAULT_BASE_URLS[endpoint.provider]).rstrip("/")
    try:
        _validate_model_url(url)
    except ValueError as exc:
        return False, f"provider={endpoint.provider} {exc}"
    return (
        True,
        f"provider={endpoint.provider} model={endpoint.model} credential_env={key_env} "
        f"credential_source={source} timeout={endpoint.timeout_seconds:g}s retries={endpoint.max_retries}",
    )


def _send_http(
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with urlopen(request, timeout=timeout) as response:
            return response.status, dict(response.headers.items()), response.read()
    except HTTPError as exc:
        return exc.code, dict(exc.headers.items()), exc.read()


def _http_status_error(
    provider: str,
    status: int,
    headers: Mapping[str, str],
    body: bytes,
) -> ModelProviderError:
    request_id = _header(headers, "x-request-id")
    code = ""
    try:
        error = json.loads(body).get("error") or {}
        code = str(error.get("code") or error.get("type") or "")
    except (UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        pass
    quota = code.casefold() in {"insufficient_quota", "billing_hard_limit_reached"} or status == 402
    retryable = not quota and (status in {408, 429} or status >= 500)
    if status in {401, 403}:
        kind = "authentication"
    elif quota:
        kind = "quota"
    elif status == 408:
        kind = "timeout"
    elif status == 429:
        kind = "rate_limit"
    elif status >= 500:
        kind = "provider_unavailable"
    else:
        kind = "invalid_request"
    return ModelProviderError(
        f"Model provider request failed with HTTP {status} ({kind}).",
        provider=provider,
        kind=kind,
        retryable=retryable,
        status_code=status,
        request_id=request_id,
    )


def _validate_model_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme == "https" and parsed.netloc:
        return
    if parsed.scheme == "http" and parsed.hostname in {"127.0.0.1", "localhost", "::1"}:
        return
    raise ValueError("model base_url must use HTTPS (HTTP is allowed only for loopback tests).")


def _openai_output_text(payload: Mapping[str, Any]) -> str:
    for item in payload.get("output", ()):
        if item.get("type") != "message":
            continue
        for content in item.get("content", ()):
            if content.get("type") == "output_text":
                return str(content["text"])
    raise KeyError("output_text")


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.casefold()
    return next((str(value) for key, value in headers.items() if key.casefold() == target), None)


def _canonical_json(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _optional_int(value: Any) -> int | None:
    return int(value) if isinstance(value, int) else None
