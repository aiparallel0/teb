"""
WhatsApp Cloud API channel adapter for teb execution updates.

Uses the Meta WhatsApp Business Cloud API for outbound messages and
parses inbound webhook payloads from the Webhooks product.

Required env vars (in teb.config):
    TEB_WHATSAPP_TOKEN        — Permanent access token
    TEB_WHATSAPP_PHONE_ID     — Phone number ID from the Meta dashboard
    TEB_WHATSAPP_VERIFY_TOKEN — Webhook verification token (chosen by you)
"""

from __future__ import annotations

import logging
from typing import Any, Dict

import httpx

from teb import config
from teb.channels.base import Channel, send_with_retry

logger = logging.getLogger(__name__)

_WA_API_BASE = "https://graph.facebook.com/v21.0"


class WhatsAppChannel(Channel):
    """Concrete WhatsApp Cloud API adapter."""

    def __init__(
        self,
        access_token: str | None = None,
        phone_number_id: str | None = None,
        verify_token: str | None = None,
    ) -> None:
        self.access_token = access_token or config.WHATSAPP_TOKEN or ""
        self.phone_number_id = phone_number_id or config.WHATSAPP_PHONE_ID or ""
        self.verify_token = verify_token or config.WHATSAPP_VERIFY_TOKEN or ""

    # ── outbound ──────────────────────────────────────────────────────────

    def send_message(self, user_id: str, message: str) -> bool:
        """Send a text message to a WhatsApp phone number.

        *user_id* is the recipient phone number in international format
        (e.g. ``"15551234567"``).
        """
        if not self.access_token or not self.phone_number_id:
            logger.warning("WhatsApp token or phone ID not configured")
            return False

        url = f"{_WA_API_BASE}/{self.phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        payload = {
            "messaging_product": "whatsapp",
            "to": user_id,
            "type": "text",
            "text": {"body": message},
        }

        def _do_send() -> httpx.Response:
            with httpx.Client(timeout=10) as client:
                return client.post(url, json=payload, headers=headers)

        return send_with_retry(_do_send, label="WhatsApp")

    # ── inbound ───────────────────────────────────────────────────────────

    def receive_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parse a WhatsApp Cloud API webhook payload.

        The webhook sends a nested structure; we extract the first text
        message from ``entry[].changes[].value.messages[]``.
        """
        entries = payload.get("entry", [])
        for entry in entries:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    if msg.get("type") == "text":
                        return {
                            "text": msg.get("text", {}).get("body", ""),
                            "user_id": msg.get("from", ""),
                            "message_id": msg.get("id", ""),
                        }

        return {"text": "", "user_id": ""}

    # ── webhook verification ──────────────────────────────────────────────

    def verify_webhook(
        self,
        mode: str,
        token: str,
        challenge: str,
    ) -> str | None:
        """Handle the WhatsApp webhook verification (GET) request.

        Returns the ``hub.challenge`` value on success, or ``None`` if
        the token does not match.
        """
        if mode == "subscribe" and token == self.verify_token:
            return challenge
        logger.warning("WhatsApp webhook verification failed")
        return None
