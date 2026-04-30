"""Small security helpers shared by runtime-facing HTTP surfaces."""

from __future__ import annotations

import ipaddress
import os
from urllib.parse import urlparse


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}


def is_loopback_host(host: str) -> bool:
    """Return true for hosts that bind/connect only to the local machine."""
    value = (host or "").strip().strip("[]").lower()
    if value in _LOOPBACK_HOSTS:
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False


def public_bind_requires_auth(host: str, api_key: str) -> bool:
    """True when a bind host can be reached off-machine and auth is empty."""
    return not is_loopback_host(host) and not bool(api_key)


def validate_http_url(
    url: str,
    *,
    allow_loopback: bool = False,
    allow_private_env: str = "",
) -> str:
    """Validate an outbound HTTP(S) URL before server-side requests.

    The guard blocks obvious SSRF targets by default: local hostnames and
    literal private/link-local/reserved IPs. Operators can opt into private
    address targets for trusted deployments by setting ``allow_private_env``.
    """
    parsed = urlparse((url or "").strip())
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("URL must use http or https")
    if not parsed.hostname:
        raise ValueError("URL must include a hostname")

    host = parsed.hostname.strip("[]").lower()
    if host == "localhost" and not allow_loopback:
        raise ValueError("URL host is local-only")

    private_allowed = bool(allow_private_env and os.environ.get(allow_private_env))
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return parsed.geturl()

    if ip.is_loopback and allow_loopback:
        return parsed.geturl()
    if (
        ip.is_loopback
        or ip.is_link_local
        or ip.is_private
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ) and not private_allowed:
        raise ValueError("URL host resolves to a blocked local or private address")
    return parsed.geturl()
