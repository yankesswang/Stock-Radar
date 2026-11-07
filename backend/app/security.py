"""Small, deployment-friendly guard for API operations with side effects."""

from __future__ import annotations

import hmac
import ipaddress
from collections.abc import Mapping


SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def _is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _presented_token(headers: Mapping[str, str]) -> str:
    api_key = headers.get("x-api-key", "").strip()
    if api_key:
        return api_key
    authorization = headers.get("authorization", "").strip()
    scheme, separator, value = authorization.partition(" ")
    if separator and scheme.lower() == "bearer":
        return value.strip()
    return ""


def mutation_is_authorized(
    *,
    method: str,
    path: str,
    client_host: str | None,
    headers: Mapping[str, str],
    configured_token: str,
) -> bool:
    """Allow reads; require loopback or the configured token for API writes."""
    if method.upper() in SAFE_METHODS or not path.startswith("/api/"):
        return True
    token = configured_token.strip()
    if not token:
        return _is_loopback(client_host)
    presented = _presented_token(headers)
    return bool(presented) and hmac.compare_digest(presented, token)
