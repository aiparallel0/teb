"""
Discord channel adapter for teb execution updates.

Uses Discord webhook URLs for outbound messages and parses Discord
Interactions API payloads for inbound slash commands.

Required env vars (in teb.config):
    TEB_DISCORD_WEBHOOK_URL  — Discord webhook URL for outbound messages
    TEB_DISCORD_PUBLIC_KEY   — Application public key for interaction verification
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from teb import config, security
from teb.channels.base import Channel, send_with_retry

logger = logging.getLogger(__name__)


class DiscordChannel(Channel):
    """Concrete Discord adapter using webhook URLs."""

    def __init__(
        self,
        webhook_url: str | None = None,
        public_key: str | None = None,
    ) -> None:
        self.webhook_url = webhook_url or config.DISCORD_WEBHOOK_URL or ""
        self.public_key = public_key or config.DISCORD_PUBLIC_KEY or ""

    # ── outbound ──────────────────────────────────────────────────────────

    def send_message(self, user_id: str, message: str) -> bool:
        """Send a message via the configured Discord webhook.

        *user_id* is ignored for webhook-based delivery (messages go to
        the webhook's bound channel).  It is accepted for interface
        compatibility.
        """
        if not self.webhook_url:
            logger.warning("Discord webhook URL not configured")
            return False

        if not security.is_safe_url(self.webhook_url):
            logger.warning(
                "Blocked Discord delivery: URL %r targets a private or disallowed address",
                self.webhook_url,
            )
            return False

        payload = {"content": message}

        def _do_send() -> httpx.Response:
            with httpx.Client(timeout=10) as client:
                return client.post(self.webhook_url, json=payload)

        return send_with_retry(_do_send, label="Discord")

    # ── inbound ───────────────────────────────────────────────────────────

    def receive_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a Discord Interactions API payload.

        Supports:
          - PING (type 1) — returns ``{"type": 1}`` for ACK
          - APPLICATION_COMMAND (type 2) — slash command invocations
          - MESSAGE_COMPONENT (type 3) — button interactions
        """
        interaction_type = payload.get("type", 0)

        # PING — Discord verification handshake
        if interaction_type == 1:
            return {"type": 1}

        # APPLICATION_COMMAND — slash command
        if interaction_type == 2:
            data = payload.get("data", {})
            command_name = data.get("name", "")
            options = data.get("options", [])
            # Build text like "/approve 42"
            args = " ".join(str(opt.get("value", "")) for opt in options)
            text = f"/{command_name} {args}".strip() if command_name else ""

            member = payload.get("member", {})
            user = member.get("user", {}) or payload.get("user", {})

            return {
                "text": text,
                "user_id": user.get("id", ""),
                "channel_id": payload.get("channel_id", ""),
                "interaction_id": payload.get("id", ""),
                "interaction_token": payload.get("token", ""),
            }

        # MESSAGE_COMPONENT — button / select interactions
        if interaction_type == 3:
            data = payload.get("data", {})
            custom_id = data.get("custom_id", "")
            # Convention: custom_id is the command text, e.g. "/approve 42"
            member = payload.get("member", {})
            user = member.get("user", {}) or payload.get("user", {})

            return {
                "text": custom_id,
                "user_id": user.get("id", ""),
                "channel_id": payload.get("channel_id", ""),
                "interaction_id": payload.get("id", ""),
                "interaction_token": payload.get("token", ""),
            }

        return {"text": "", "user_id": ""}

    # ── interaction verification ──────────────────────────────────────────

    def verify_signature(
        self,
        body: bytes,
        timestamp: str,
        signature: str,
    ) -> bool:
        """Verify a Discord interaction signature using Ed25519.

        Returns ``True`` if the signature is valid or if no public key is
        configured (development mode).
        """
        if not self.public_key:
            return True

        try:
            # Ed25519 verify: message = timestamp + body
            message = timestamp.encode() + body

            sig_bytes = bytes.fromhex(signature)
            key_bytes = bytes.fromhex(self.public_key)

            # Use the standard library nacl-less approach:
            # For production use, the `PyNaCl` library is recommended.
            # Here we do a best-effort check; if the cryptography lib is
            # available we use it.
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )

            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
            public_key.verify(sig_bytes, message)
            return True
        except Exception as exc:
            logger.warning("Discord signature verification failed: %s", exc)
            return False
