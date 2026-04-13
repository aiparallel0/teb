"""
Professional middleware for teb.

Provides:
- Security headers (CSP, X-Frame-Options, HSTS, etc.)
- Request ID tracking (X-Request-Id)
- Request/response logging with timing
- Structured error responses
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

logger = logging.getLogger(__name__)


# ─── Security Headers Middleware ──────────────────────────────────────────────

# Content-Security-Policy: restrict where resources can load from.
# default-src 'self' — only allow resources from same origin by default.
# script-src/style-src include 'unsafe-inline' because teb uses inline scripts
# and styles in its single-page frontend.
# font-src includes Google Fonts CDN.
# img-src allows data: URIs for inline images (avatars, icons).
# connect-src 'self' allows API calls and SSE connections.
_CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: https:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "X-XSS-Protection": "0",  # Modern browsers: CSP is preferred; disable legacy XSS auditor
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    "Content-Security-Policy": _CSP_POLICY,
    "Cross-Origin-Opener-Policy": "same-origin",
}


def add_security_headers(response: Response) -> None:
    """Attach security headers to a response."""
    for header, value in _SECURITY_HEADERS.items():
        response.headers[header] = value


# ─── Request ID Middleware ────────────────────────────────────────────────────

def _generate_request_id() -> str:
    """Generate a unique request ID (UUID4 hex, 32 chars)."""
    return uuid.uuid4().hex


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-Id to every request/response.

    If the client sends an X-Request-Id header, it is accepted (but validated).
    Otherwise a new UUID is generated. The ID is stored on request.state for
    use in logging and error responses.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Accept client-provided request ID if it looks safe (alphanumeric + hyphens, max 64)
        incoming_id = request.headers.get("x-request-id", "")
        if incoming_id and len(incoming_id) <= 64 and incoming_id.replace("-", "").isalnum():
            request_id = incoming_id
        else:
            request_id = _generate_request_id()

        # Store on request.state for downstream use
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


# ─── Request Logging Middleware ───────────────────────────────────────────────

class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every request with method, path, status, and duration.

    Skips noisy paths (health checks, static assets) at INFO level
    but still logs them at DEBUG.
    """

    _QUIET_PREFIXES = ("/static/", "/health", "/favicon.ico")

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        start = time.monotonic()
        path = request.url.path

        response = await call_next(request)

        duration_ms = round((time.monotonic() - start) * 1000, 1)
        request_id = getattr(request.state, "request_id", "-")
        method = request.method
        status = response.status_code
        client_ip = request.client.host if request.client else "-"

        log_msg = (
            f"{method} {path} {status} {duration_ms}ms "
            f"client={client_ip} req_id={request_id}"
        )

        if any(path.startswith(p) for p in self._QUIET_PREFIXES):
            logger.debug(log_msg)
        elif status >= 500:
            logger.error(log_msg)
        elif status >= 400:
            logger.warning(log_msg)
        else:
            logger.info(log_msg)

        return response


# ─── Security Headers Middleware (ASGI) ───────────────────────────────────────

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Add security headers to every HTTP response."""

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        response = await call_next(request)
        add_security_headers(response)

        # Add HSTS only if the request came over HTTPS (or via a proxy
        # that sets X-Forwarded-Proto)
        proto = request.headers.get("x-forwarded-proto", request.url.scheme)
        if proto == "https":
            response.headers["Strict-Transport-Security"] = (
                "max-age=31536000; includeSubDomains"
            )

        return response
