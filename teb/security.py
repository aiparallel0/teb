"""
Security utilities for teb.

Provides SSRF-safe URL validation used by the executor, messaging webhook
sender, browser automation engine, and health-check monitor to prevent
server-side request forgery to private network resources.
"""

from __future__ import annotations

import ipaddress
import logging
import os
import urllib.parse
from typing import Optional

logger = logging.getLogger(__name__)

# ─── Dangerous URI schemes ────────────────────────────────────────────────────
# Only http and https are permitted for outbound requests.

_ALLOWED_SCHEMES = frozenset(["http", "https"])

# ─── Blocked hostnames (case-insensitive exact match) ─────────────────────────

_BLOCKED_HOSTS = frozenset([
    "localhost",
    "metadata.google.internal",         # GCP metadata
    "metadata.internal",
    "169.254.169.254",                   # AWS/GCP/Azure metadata (IPv4 link-local)
    "fd00:ec2::254",                     # AWS metadata (IPv6)
    "[fd00:ec2::254]",
    "::1",
    "[::1]",
])

# ─── Private IP ranges blocked for SSRF protection ───────────────────────────

_BLOCKED_NETWORKS: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = [
    ipaddress.IPv4Network("0.0.0.0/8"),       # This network
    ipaddress.IPv4Network("10.0.0.0/8"),      # Private class A
    ipaddress.IPv4Network("127.0.0.0/8"),     # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),  # Link-local (metadata services)
    ipaddress.IPv4Network("172.16.0.0/12"),   # Private class B
    ipaddress.IPv4Network("192.168.0.0/16"),  # Private class C
    ipaddress.IPv4Network("198.18.0.0/15"),   # Benchmarking
    ipaddress.IPv4Network("224.0.0.0/4"),     # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),     # Reserved
    ipaddress.IPv6Network("::1/128"),         # IPv6 loopback
    ipaddress.IPv6Network("fc00::/7"),        # IPv6 unique-local
    ipaddress.IPv6Network("fe80::/10"),       # IPv6 link-local
    ipaddress.IPv6Network("ff00::/8"),        # IPv6 multicast
]


def _is_ip_blocked(host: str) -> bool:
    """Return True if the host is a private/reserved IP address.

    Only raw IP literals are checked here.  Hostname-to-IP resolution is
    intentionally skipped to avoid false-positives in environments with
    restricted DNS and to keep the check fast.  Hostnames are only blocked
    by exact match against _BLOCKED_HOSTS.
    """
    # Strip IPv6 brackets
    bare = host.strip("[]")
    try:
        addr = ipaddress.ip_address(bare)
    except ValueError:
        # Not a raw IP literal — hostname validation is done via _BLOCKED_HOSTS
        return False

    return any(addr in net for net in _BLOCKED_NETWORKS)


def is_safe_url(url: str) -> bool:
    """Return True if *url* is safe to use for an outbound HTTP request.

    Blocks:
    - Non-http/https schemes (javascript:, file:, data:, ftp:, …)
    - Localhost and well-known metadata hostnames (exact match via _BLOCKED_HOSTS)
    - Raw IP literals in private, loopback, link-local, and multicast ranges
    - URLs that do not have a recognisable host

    Note: hostname-to-IP resolution is *not* performed.  Hostnames are only
    blocked by exact match against _BLOCKED_HOSTS; raw IP literals are checked
    against _BLOCKED_NETWORKS.  This keeps the check fast and avoids
    false-positives in environments with restricted DNS.
    """
    if not url or not isinstance(url, str):
        return False

    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False

    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        logger.warning("Blocked outbound request: disallowed scheme %r in %r", scheme, url)
        return False

    host = (parsed.hostname or "").lower()
    if not host:
        return False

    if host in _BLOCKED_HOSTS:
        logger.warning("Blocked outbound request: blocked host %r in %r", host, url)
        return False

    if _is_ip_blocked(host):
        logger.warning("Blocked outbound request: private/reserved IP for host %r in %r", host, url)
        return False

    return True


# ─── Screenshot path safety ───────────────────────────────────────────────────

# Directory where all browser screenshots must be written.
_SCREENSHOT_DIR = os.path.join(os.path.dirname(__file__), "..", "screenshots")


def safe_screenshot_path(requested: Optional[str] = None) -> str:
    """Return a safe absolute path for a browser screenshot.

    Any caller-supplied path is ignored; a new unique filename inside the
    controlled screenshots directory is always returned.  This prevents
    path-traversal attacks via AI-generated browser plans.
    """
    import tempfile
    import secrets as _secrets

    # Prefer a dedicated directory; fall back to a tempdir sub-folder.
    base = os.path.abspath(_SCREENSHOT_DIR)
    try:
        os.makedirs(base, mode=0o700, exist_ok=True)
    except OSError:
        base = os.path.join(tempfile.gettempdir(), "teb_screenshots")
        os.makedirs(base, mode=0o700, exist_ok=True)

    filename = f"teb_{_secrets.token_hex(8)}.png"
    return os.path.join(base, filename)
