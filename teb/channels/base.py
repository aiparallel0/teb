"""
Channel abstraction layer for teb execution updates.

Defines the Channel protocol (ABC) and supporting dataclasses used by
all concrete channel adapters (Slack, Discord, WhatsApp).
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Retry settings shared across all channel adapters — mirrors the pattern
# in teb.messaging for Telegram / webhook delivery.
MAX_RETRIES = 3
RETRY_BASE_DELAY = 1.0  # seconds; used with exponential backoff


@dataclass
class CommandResult:
    """Structured result returned by the command router after parsing an
    inbound command from any channel."""

    command: str
    success: bool
    message: str = ""
    data: Dict[str, Any] = field(default_factory=dict)


class Channel(ABC):
    """Abstract base class that every channel adapter must implement.

    Channels are NOT for free-form chat — they are command surfaces for
    the teb execution pipeline.
    """

    @abstractmethod
    def send_message(self, user_id: str, message: str) -> bool:
        """Deliver a notification *message* to the given *user_id*.

        Platform semantics of *user_id* vary:
          - Slack:    a channel ID or user DM channel
          - Discord:  not used (messages go to the webhook URL)
          - WhatsApp: recipient phone number

        Returns ``True`` on success.
        """

    @abstractmethod
    def receive_command(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Parse an inbound webhook *payload* from the platform.

        Returns a normalised dict with at least::

            {"text": "<raw command text>", "user_id": "<sender id>"}

        Extra platform-specific keys may be included.
        """


def send_with_retry(
    send_fn: Any,
    *,
    max_retries: int = MAX_RETRIES,
    base_delay: float = RETRY_BASE_DELAY,
    label: str = "channel",
) -> bool:
    """Call *send_fn* (a no-arg callable returning an httpx Response) with
    exponential-backoff retries on transient failures (429 / 5xx).

    Returns ``True`` if a 2xx response is eventually received.
    """
    for attempt in range(max_retries):
        try:
            resp = send_fn()
            if 200 <= resp.status_code < 300:
                return True
            if resp.status_code == 429 or resp.status_code >= 500:
                logger.warning(
                    "%s returned %d (attempt %d/%d)",
                    label, resp.status_code, attempt + 1, max_retries,
                )
                if attempt < max_retries - 1:
                    time.sleep(base_delay * (2 ** attempt))
                    continue
            logger.warning(
                "%s returned %d: %s",
                label, resp.status_code, resp.text[:200],
            )
            return False
        except Exception as exc:
            logger.warning(
                "%s send failed (attempt %d/%d): %s",
                label, attempt + 1, max_retries, exc,
            )
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
                continue
            return False
    return False
