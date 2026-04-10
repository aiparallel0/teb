"""
Webhook delivery engine (Phase 2, Step 7).

Sends HTTP POST notifications to user-configured webhook URLs when
events occur in teb (task completed, goal updated, etc.).

Includes HMAC-SHA256 signature for payload verification.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from typing import Any, Dict, List, Optional

import httpx

from teb import security

logger = logging.getLogger(__name__)

_WEBHOOK_TIMEOUT = 10  # seconds
_MAX_RETRIES = 2


def _sign_payload(payload: str, secret: str) -> str:
    """Generate HMAC-SHA256 signature for a webhook payload."""
    return hmac.new(
        secret.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def deliver_webhook(
    url: str,
    event_type: str,
    data: Dict[str, Any],
    secret: str = "",
) -> bool:
    """Deliver a webhook payload to a URL.

    Returns True if delivery succeeded, False otherwise.
    """
    if not security.is_safe_url(url):
        logger.warning("Webhook URL blocked by SSRF protection: %s", url)
        return False

    payload = json.dumps({
        "event": event_type,
        "data": data,
        "timestamp": int(time.time()),
    })

    headers = {
        "Content-Type": "application/json",
        "X-Teb-Event": event_type,
    }
    if secret:
        headers["X-Teb-Signature"] = _sign_payload(payload, secret)

    for attempt in range(1 + _MAX_RETRIES):
        try:
            with httpx.Client(timeout=_WEBHOOK_TIMEOUT) as client:
                resp = client.post(url, content=payload, headers=headers)
            if 200 <= resp.status_code < 300:
                return True
            if resp.status_code >= 500 and attempt < _MAX_RETRIES:
                time.sleep(1 * (attempt + 1))
                continue
            logger.warning("Webhook delivery to %s returned %d", url, resp.status_code)
            return False
        except (httpx.TimeoutException, httpx.RequestError) as exc:
            if attempt < _MAX_RETRIES:
                time.sleep(1 * (attempt + 1))
                continue
            logger.warning("Webhook delivery to %s failed: %s", url, exc)
            return False

    return False


def deliver_to_user_webhooks(user_id: int, event_type: str, data: Dict[str, Any]) -> int:
    """Deliver an event to all matching webhooks for a user.

    Returns the number of successful deliveries.
    """
    from teb import storage  # noqa: PLC0415

    webhooks = storage.list_webhooks_for_event(user_id, event_type)
    if not webhooks:
        return 0

    success_count = 0
    for wh in webhooks:
        if deliver_webhook(wh.url, event_type, data, secret=wh.secret):
            success_count += 1

    return success_count
