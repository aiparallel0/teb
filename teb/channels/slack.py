"""
Slack channel adapter for teb execution updates.

Uses the Slack Web API (chat.postMessage) for outbound messages and
parses Slack Events API / slash-command payloads for inbound commands.

Required env vars (in teb.config):
    TEB_SLACK_BOT_TOKEN   — Bot User OAuth Token (xoxb-…)
    TEB_SLACK_SIGNING_SECRET — Signing secret for webhook verification
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any, Dict

import httpx

from teb import config
from teb.channels.base import Channel, send_with_retry

logger = logging.getLogger(__name__)

_SLACK_POST_MESSAGE_URL = "https://slack.com/api/chat.postMessage"


class SlackChannel(Channel):
    """Concrete Slack adapter."""

    def __init__(
        self,
        bot_token: str | None = None,
        signing_secret: str | None = None,
    ) -> None:
        self.bot_token = bot_token or config.SLACK_BOT_TOKEN or ""
        self.signing_secret = signing_secret or config.SLACK_SIGNING_SECRET or ""

    # ── outbound ──────────────────────────────────────────────────────────

    def send_message(self, user_id: str, message: str) -> bool:
        """Post a message to a Slack channel or DM.

        *user_id* is the Slack channel ID (C…) or DM channel (D…).
        """
        if not self.bot_token:
            logger.warning("Slack bot token not configured")
            return False

        headers = {
            "Authorization": f"Bearer {self.bot_token}",
            "Content-Type": "application/json; charset=utf-8",
        }
        payload = {
            "channel": user_id,
            "text": message,
        }

        def _do_send() -> httpx.Response:
            with httpx.Client(timeout=10) as client:
                return client.post(
                    _SLACK_POST_MESSAGE_URL,
                    json=payload,
                    headers=headers,
                )

        return send_with_retry(_do_send, label="Slack")

    # ── inbound ───────────────────────────────────────────────────────────

    def receive_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a Slack Events API or slash-command payload.

        Supports:
          - Events API ``event_callback`` with ``message`` events
          - Slash command payloads (``/teb approve 42``)
          - URL verification challenge (returns ``{"challenge": …}``)
        """
        # URL verification handshake
        if payload.get("type") == "url_verification":
            return {"challenge": payload.get("challenge", "")}

        # Events API message
        event = payload.get("event", {})
        if event.get("type") == "message" and "subtype" not in event:
            return {
                "text": event.get("text", ""),
                "user_id": event.get("user", ""),
                "channel_id": event.get("channel", ""),
            }

        # Slash command (flat dict from form-encoded body)
        if "command" in payload:
            command_name = payload.get("command", "")
            command_text = payload.get("text", "")
            # Normalise: "/teb approve 42" → "/approve 42"
            text = f"{command_name} {command_text}".strip()
            if text.startswith("/teb "):
                text = "/" + text[5:]
            return {
                "text": text,
                "user_id": payload.get("user_id", ""),
                "channel_id": payload.get("channel_id", ""),
                "response_url": payload.get("response_url", ""),
            }

        return {"text": "", "user_id": ""}

    # ── webhook verification ──────────────────────────────────────────────

    def verify_signature(
        self,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> bool:
        """Verify a Slack request signature (v0=…).

        Returns ``True`` if the signature is valid, ``False`` otherwise.
        """
        if not self.signing_secret:
            # No secret configured — skip verification (development mode)
            return True

        # Reject requests older than 5 minutes to prevent replay attacks
        try:
            if abs(time.time() - float(timestamp)) > 300:
                logger.warning("Slack signature timestamp too old")
                return False
        except (ValueError, TypeError):
            return False

        sig_basestring = f"v0:{timestamp}:{body.decode('utf-8', errors='replace')}"
        expected = (
            "v0="
            + hmac.new(
                self.signing_secret.encode(),
                sig_basestring.encode(),
                hashlib.sha256,
            ).hexdigest()
        )
        return hmac.compare_digest(expected, signature)
