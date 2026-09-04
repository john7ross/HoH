from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
import json
import os
import time
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from . import __version__
from .oauth_store import OAuthTokenStore, OAuthTokenStoreError, StoredOAuthToken


MAX_OAUTH_RESPONSE_BYTES = 2 * 1024 * 1024
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


class OAuthError(RuntimeError):
    pass


class OAuthInteractionRequired(OAuthError):
    pass


def require_secure_endpoint(url: str, purpose: str) -> str:
    """OAuth endpoints carry the client secret and the token itself, so plaintext is refused.

    A remote Agent Card can name any token endpoint it likes; without this check a
    `http://` endpoint would receive the Basic credentials in the clear.
    """
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise OAuthError(f"OAuth {purpose} must be an absolute HTTP(S) URL.")
    if parsed.username or parsed.password:
        raise OAuthError(f"OAuth {purpose} must not contain credentials.")
    if parsed.scheme == "http" and parsed.hostname.casefold() not in LOOPBACK_HOSTS:
        raise OAuthError(
            f"OAuth {purpose} requires HTTPS; plaintext HTTP is allowed only on loopback."
        )
    return url


@dataclass(frozen=True)
class OAuthClientConfig:
    auth_kind: str
    flow: str
    access_token_env: str | None = None
    client_id_env: str | None = None
    client_secret_env: str | None = None
    token_url: str | None = None
    device_authorization_url: str | None = None
    discovery_url: str | None = None
    scopes: tuple[str, ...] = ()
    client_auth_method: str = "basic"
    timeout_seconds: float = 60.0


@dataclass(frozen=True)
class OAuthToken:
    access_token: str
    token_type: str
    expires_at: float
    scope: str | None = None
    refresh_token: str | None = None

    @property
    def valid(self) -> bool:
        return bool(self.access_token) and time.time() + 30 < self.expires_at


@dataclass(frozen=True)
class DeviceAuthorization:
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    expires_in: int


OAuthTransport = Callable[
    [str, str, Mapping[str, str], bytes | None, float],
    tuple[int, Mapping[str, str], bytes],
]


_TOKEN_CACHE: dict[tuple[str, str, str, tuple[str, ...]], OAuthToken] = {}


class OAuthClient:
    def __init__(
        self,
        config: OAuthClientConfig,
        *,
        environment: Mapping[str, str] | None = None,
        transport: OAuthTransport | None = None,
        token_store: OAuthTokenStore | None = None,
    ) -> None:
        self.config = config
        self.environment = environment if environment is not None else os.environ
        self._transport = transport or _oauth_transport
        self._discovery: dict[str, Any] | None = None
        self._token_store = token_store if token_store is not None else (
            OAuthTokenStore() if self.environment is os.environ else None
        )

    def access_token(self) -> str:
        environment_token = self._environment_value(self.config.access_token_env, required=False)
        if environment_token:
            return environment_token
        key = self._cache_key()
        cached = _TOKEN_CACHE.get(key)
        if cached is not None and cached.valid:
            return cached.access_token
        persisted = self._load_persisted_token()
        if persisted is not None and persisted.valid:
            _TOKEN_CACHE[key] = persisted
            return persisted.access_token
        if persisted is not None and persisted.refresh_token:
            token = self._refresh_access_token(persisted.refresh_token)
            self._cache_token(token, persist=True)
            return token.access_token
        if self.config.flow == "device_code":
            raise OAuthInteractionRequired(
                "OAuth/OIDC device authorization is required. Run the A2A login action before starting work."
            )
        token = self._client_credentials_token()
        _TOKEN_CACHE[key] = token
        return token.access_token

    def authorize_device(self, on_user_code: Callable[[DeviceAuthorization], None]) -> OAuthToken:
        client_id = self._environment_value(self.config.client_id_env)
        discovery = self._load_discovery()
        device_url = self.config.device_authorization_url or _string(discovery.get("device_authorization_endpoint"))
        token_url = self.config.token_url or _string(discovery.get("token_endpoint"))
        if not device_url or not token_url:
            raise OAuthError("OIDC discovery did not provide device and token endpoints.")
        initial = self._post_form(
            device_url,
            {"client_id": client_id, **self._scope_form()},
            include_client_auth=False,
        )
        device_code = _required_string(initial, "device_code")
        authorization = DeviceAuthorization(
            user_code=_required_string(initial, "user_code"),
            verification_uri=_required_string(initial, "verification_uri"),
            verification_uri_complete=_string(initial.get("verification_uri_complete")),
            expires_in=max(1, int(initial.get("expires_in", 600))),
        )
        on_user_code(authorization)
        interval = max(1.0, float(initial.get("interval", 5)))
        deadline = time.monotonic() + min(authorization.expires_in, self.config.timeout_seconds)
        while time.monotonic() < deadline:
            time.sleep(min(interval, max(0.01, deadline - time.monotonic())))
            payload = self._post_form(
                token_url,
                {
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    "device_code": device_code,
                    "client_id": client_id,
                },
                include_client_auth=False,
                allow_oauth_error=True,
            )
            error = _string(payload.get("error"))
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                continue
            if error:
                raise OAuthError(f"OAuth device authorization failed: {error}")
            token = self._token(payload)
            self._cache_token(token, persist=True)
            return token
        raise OAuthError("OAuth device authorization timed out before the user completed sign-in.")

    def _client_credentials_token(self) -> OAuthToken:
        token_url = self.config.token_url or _string(self._load_discovery().get("token_endpoint"))
        if not token_url:
            raise OAuthError("OAuth token endpoint is not configured or discoverable.")
        payload = self._post_form(
            token_url,
            {"grant_type": "client_credentials", **self._scope_form()},
            include_client_auth=True,
        )
        return self._token(payload)

    def _refresh_access_token(self, refresh_token: str) -> OAuthToken:
        token_url = self.config.token_url or _string(self._load_discovery().get("token_endpoint"))
        if not token_url:
            raise OAuthError("OAuth token endpoint is not configured or discoverable.")
        payload = self._post_form(
            token_url,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self._environment_value(self.config.client_id_env),
            },
            include_client_auth=False,
        )
        token = self._token(payload)
        if token.refresh_token is None:
            token = OAuthToken(
                token.access_token,
                token.token_type,
                token.expires_at,
                token.scope,
                refresh_token,
            )
        return token

    def _load_discovery(self) -> dict[str, Any]:
        if self._discovery is not None:
            return self._discovery
        if not self.config.discovery_url:
            self._discovery = {}
            return self._discovery
        status, _headers, body = self._transport(
            require_secure_endpoint(self.config.discovery_url, "discovery endpoint"),
            "GET",
            {"Accept": "application/json", "User-Agent": f"HoH/{__version__}"},
            None,
            min(self.config.timeout_seconds, 20.0),
        )
        if status != 200:
            raise OAuthError(f"OIDC discovery returned HTTP {status}.")
        self._discovery = _json_object(body, "OIDC discovery")
        return self._discovery

    def _post_form(
        self,
        url: str,
        values: Mapping[str, str],
        *,
        include_client_auth: bool,
        allow_oauth_error: bool = False,
    ) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": f"HoH/{__version__}",
        }
        form = dict(values)
        if include_client_auth:
            client_id = self._environment_value(self.config.client_id_env)
            client_secret = self._environment_value(self.config.client_secret_env)
            if self.config.client_auth_method == "basic":
                encoded = b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
                headers["Authorization"] = f"Basic {encoded}"
            else:
                form["client_id"] = client_id
                form["client_secret"] = client_secret
        status, _response_headers, body = self._transport(
            require_secure_endpoint(url, "token endpoint"),
            "POST",
            headers,
            urlencode(form).encode("utf-8"),
            min(self.config.timeout_seconds, 60.0),
        )
        payload = _json_object(body, "OAuth response")
        if status != 200 and not (allow_oauth_error and payload.get("error")):
            raise OAuthError(f"OAuth endpoint returned HTTP {status}: {_safe_error(payload)}")
        return payload

    def _token(self, payload: Mapping[str, Any]) -> OAuthToken:
        access_token = _required_string(payload, "access_token")
        token_type = str(payload.get("token_type") or "Bearer")
        if token_type.casefold() != "bearer":
            raise OAuthError(f"Unsupported OAuth token type: {token_type}")
        try:
            expires_in = max(60.0, float(payload.get("expires_in", 3600)))
        except (TypeError, ValueError) as exc:
            raise OAuthError("OAuth expires_in must be numeric.") from exc
        return OAuthToken(
            access_token=access_token,
            token_type="Bearer",
            expires_at=time.time() + expires_in,
            scope=_string(payload.get("scope")),
            refresh_token=_string(payload.get("refresh_token")),
        )

    def _cache_token(self, token: OAuthToken, *, persist: bool) -> None:
        _TOKEN_CACHE[self._cache_key()] = token
        if persist and self._token_store is not None:
            self._token_store.save(
                self._persistent_key(),
                StoredOAuthToken(
                    token.access_token,
                    token.refresh_token,
                    token.expires_at,
                    token.token_type,
                    token.scope,
                ),
            )

    def _load_persisted_token(self) -> OAuthToken | None:
        if self._token_store is None:
            return None
        try:
            token = self._token_store.load(self._persistent_key())
        except OAuthTokenStoreError as exc:
            raise OAuthError(str(exc)) from exc
        if token is None:
            return None
        return OAuthToken(
            token.access_token,
            token.token_type,
            token.expires_at,
            token.scope,
            token.refresh_token,
        )

    def _scope_form(self) -> dict[str, str]:
        return {"scope": " ".join(self.config.scopes)} if self.config.scopes else {}

    def _environment_value(self, name: str | None, *, required: bool = True) -> str:
        value = self.environment.get(name or "", "")
        if required and not value:
            raise OAuthError(f"Required OAuth environment variable is missing: {name or '<not configured>'}")
        return value

    def _cache_key(self) -> tuple[str, str, str, tuple[str, ...]]:
        return (
            self.config.auth_kind,
            self.config.token_url or self.config.discovery_url or "",
            self.environment.get(self.config.client_id_env or "", ""),
            self.config.scopes,
        )

    def _persistent_key(self) -> str:
        return json.dumps(self._cache_key(), ensure_ascii=False, separators=(",", ":"))


def clear_oauth_token_cache() -> None:
    _TOKEN_CACHE.clear()


def _oauth_transport(
    url: str,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None,
    timeout_seconds: float,
) -> tuple[int, Mapping[str, str], bytes]:
    request = Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            payload = response.read(MAX_OAUTH_RESPONSE_BYTES + 1)
            if len(payload) > MAX_OAUTH_RESPONSE_BYTES:
                raise OAuthError("OAuth response exceeds its safety limit.")
            return int(response.status), dict(response.headers.items()), payload
    except HTTPError as exc:
        return int(exc.code), dict(exc.headers.items()), exc.read(MAX_OAUTH_RESPONSE_BYTES)
    except (OSError, URLError) as exc:
        raise OAuthError(f"OAuth network request failed: {exc}") from exc


def _json_object(payload: bytes, label: str) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OAuthError(f"{label} is not valid UTF-8 JSON.") from exc
    if not isinstance(value, dict):
        raise OAuthError(f"{label} must be a JSON object.")
    return value


def _required_string(value: Mapping[str, Any], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str) or not item:
        raise OAuthError(f"OAuth response is missing {key}.")
    return item


def _string(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _safe_error(value: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: item for key, item in value.items() if key not in {"access_token", "refresh_token", "id_token"}},
        ensure_ascii=False,
    )[:1000]
