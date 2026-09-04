from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from . import __version__
from .durable_io import atomic_write_json, read_json
from .protocol import canonical_json_bytes


SIGNED_CATALOG_SCHEMA = "hoh.signed-catalog"
TRUST_STORE_SCHEMA = "hoh.trusted-publishers"
MAX_CATALOG_BYTES = 5 * 1024 * 1024
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


class SignedCatalogError(RuntimeError):
    pass


def publisher_trust_path(environment: Mapping[str, str] | None = None) -> Path:
    env = environment if environment is not None else os.environ
    override = env.get("HOH_PUBLISHER_TRUST")
    if override:
        return Path(override).expanduser().resolve()
    if os.name == "nt" and env.get("LOCALAPPDATA"):
        return Path(env["LOCALAPPDATA"]) / "HoH" / "trusted-publishers-v1.json"
    root = Path(env["XDG_CONFIG_HOME"]) if env.get("XDG_CONFIG_HOME") else Path.home() / ".config"
    return root / "hoh" / "trusted-publishers-v1.json"


def load_trust_store(path: Path | None = None) -> dict[str, Any]:
    target = path or publisher_trust_path()
    payload = read_json(target, default={"schema": TRUST_STORE_SCHEMA, "version": "1.0", "publishers": []})
    if not isinstance(payload, dict) or payload.get("schema") != TRUST_STORE_SCHEMA:
        raise SignedCatalogError(f"Unsupported publisher trust store: {target}")
    publishers = payload.get("publishers")
    if not isinstance(publishers, list):
        raise SignedCatalogError(f"Publisher trust store has no publishers array: {target}")
    for publisher in publishers:
        _validate_publisher(publisher)
    return payload


def import_publisher(publisher_path: Path, trust_path: Path | None = None) -> str:
    try:
        publisher = json.loads(publisher_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise SignedCatalogError(f"Cannot read publisher identity {publisher_path}: {exc}") from exc
    _validate_publisher(publisher)
    target = trust_path or publisher_trust_path()
    trust = load_trust_store(target)
    publishers = [item for item in trust["publishers"] if item["key_id"] != publisher["key_id"]]
    publishers.append(publisher)
    atomic_write_json(target, {"schema": TRUST_STORE_SCHEMA, "version": "1.0", "publishers": publishers})
    return str(publisher["key_id"])


def fetch_signed_catalog(
    source: str,
    *,
    trust_path: Path | None = None,
    expected_kind: str | None = None,
    timeout_seconds: float = 20.0,
) -> dict[str, Any]:
    payload = _read_source(source, timeout_seconds=timeout_seconds)
    try:
        envelope = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SignedCatalogError("Signed catalog is not valid UTF-8 JSON.") from exc
    return verify_signed_catalog(envelope, load_trust_store(trust_path), expected_kind=expected_kind)


def verify_signed_catalog(
    envelope: Mapping[str, Any],
    trust_store: Mapping[str, Any],
    *,
    expected_kind: str | None = None,
) -> dict[str, Any]:
    if envelope.get("schema") != SIGNED_CATALOG_SCHEMA or envelope.get("version") != "1.0":
        raise SignedCatalogError("Unsupported signed catalog envelope.")
    if envelope.get("algorithm") != "RS256":
        raise SignedCatalogError("Only RS256 signed catalogs are supported.")
    key_id = envelope.get("key_id")
    payload = envelope.get("payload")
    signature_text = envelope.get("signature")
    if not isinstance(key_id, str) or not isinstance(payload, dict) or not isinstance(signature_text, str):
        raise SignedCatalogError("Signed catalog envelope is incomplete.")
    publisher = next(
        (item for item in trust_store.get("publishers", []) if isinstance(item, dict) and item.get("key_id") == key_id),
        None,
    )
    if publisher is None:
        raise SignedCatalogError(f"Catalog publisher is not trusted: {key_id}")
    _validate_publisher(publisher)
    rsa = publisher["rsa"]
    signature = _base64url_decode(signature_text)
    if not _verify_rs256(canonical_json_bytes(payload), signature, rsa["n"], rsa["e"]):
        raise SignedCatalogError("Catalog signature is invalid.")
    kind = payload.get("kind")
    if expected_kind is not None and kind != expected_kind:
        raise SignedCatalogError(f"Expected {expected_kind} catalog, received {kind!r}.")
    return dict(payload)


def _read_source(source: str, *, timeout_seconds: float) -> bytes:
    local_path = Path(source).expanduser()
    if local_path.exists():
        try:
            data = local_path.resolve().read_bytes()
        except OSError as exc:
            raise SignedCatalogError(f"Cannot read signed catalog: {exc}") from exc
        if len(data) > MAX_CATALOG_BYTES:
            raise SignedCatalogError("Signed catalog exceeds the 5 MiB safety limit.")
        return data
    parsed = urlparse(source)
    if parsed.scheme in {"http", "https"}:
        if parsed.scheme != "https" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise SignedCatalogError("Remote catalogs require HTTPS; HTTP is allowed only for loopback testing.")
        request = Request(source, headers={"Accept": "application/json", "User-Agent": f"HoH/{__version__}"})
        try:
            with urlopen(request, timeout=timeout_seconds) as response:
                data = response.read(MAX_CATALOG_BYTES + 1)
        except OSError as exc:
            raise SignedCatalogError(f"Cannot download signed catalog: {exc}") from exc
    elif parsed.scheme:
        raise SignedCatalogError(f"Unsupported catalog source scheme: {parsed.scheme}")
    else:
        try:
            data = local_path.resolve().read_bytes()
        except OSError as exc:
            raise SignedCatalogError(f"Cannot read signed catalog: {exc}") from exc
    if len(data) > MAX_CATALOG_BYTES:
        raise SignedCatalogError("Signed catalog exceeds the 5 MiB safety limit.")
    return data


def _validate_publisher(value: Any) -> None:
    if not isinstance(value, dict):
        raise SignedCatalogError("Publisher identity must be an object.")
    if not isinstance(value.get("key_id"), str) or not value["key_id"].strip():
        raise SignedCatalogError("Publisher key_id must be non-empty.")
    rsa = value.get("rsa")
    if not isinstance(rsa, dict) or not isinstance(rsa.get("n"), str) or not isinstance(rsa.get("e"), str):
        raise SignedCatalogError("Publisher identity must contain RSA modulus and exponent.")
    modulus = _base64url_decode(rsa["n"])
    exponent = _base64url_decode(rsa["e"])
    if len(modulus) < 256 or not exponent:
        raise SignedCatalogError("Publisher RSA key must be at least 2048 bits.")


def _verify_rs256(message: bytes, signature: bytes, modulus_text: str, exponent_text: str) -> bool:
    modulus_bytes = _base64url_decode(modulus_text)
    exponent_bytes = _base64url_decode(exponent_text)
    modulus = int.from_bytes(modulus_bytes, "big")
    exponent = int.from_bytes(exponent_bytes, "big")
    size = (modulus.bit_length() + 7) // 8
    if len(signature) != size or exponent < 3:
        return False
    encoded = pow(int.from_bytes(signature, "big"), exponent, modulus).to_bytes(size, "big")
    digest_info = _SHA256_DIGEST_INFO + hashlib.sha256(message).digest()
    padding_size = size - len(digest_info) - 3
    if padding_size < 8:
        return False
    expected = b"\x00\x01" + (b"\xff" * padding_size) + b"\x00" + digest_info
    return hmac.compare_digest(encoded, expected)


def _base64url_decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, UnicodeEncodeError) as exc:
        raise SignedCatalogError("Publisher key or signature is not valid base64url.") from exc
