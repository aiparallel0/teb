"""
External messaging integration.

Sends notifications to users via Telegram bots or generic webhooks
when important events occur (nudges, task completions, spending
approval requests, check-in reminders).

Supports two channels:
  - telegram: Uses Telegram Bot API to send messages to a chat
  - webhook: Sends JSON POST to a user-configured URL

Each channel is configured via MessagingConfig in the database.
Multiple channels can be active simultaneously.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from teb import storage
from teb.models import MessagingConfig

logger = logging.getLogger(__name__)

# ─── Message formatting ─────────────────────────────────────────────────────

_EVENT_EMOJI = {
    "nudge": "⏰",
    "task_done": "✅",
    "task_failed": "❌",
    "spending_request": "💰",
    "spending_approved": "✅💰",
    "spending_denied": "🚫💰",
    "checkin_reminder": "📝",
    "goal_complete": "🎉",
    "drip_task": "📋",
}


def _format_message(event_type: str, data: Dict[str, Any]) -> str:
    """Format an event into a human-readable notification message."""
    emoji = _EVENT_EMOJI.get(event_type, "📢")

    if event_type == "nudge":
        return f"{emoji} **Nudge**: {data.get('message', 'Time to check in!')}"

    if event_type == "task_done":
        return f"{emoji} **Task completed**: {data.get('title', 'Unknown task')}"

    if event_type == "task_failed":
        return f"{emoji} **Task failed**: {data.get('title', 'Unknown task')}"

    if event_type == "spending_request":
        return (
            f"{emoji} **Spending approval needed**\n"
            f"Amount: ${data.get('amount', 0):.2f}\n"
            f"For: {data.get('description', 'Unknown')}\n"
            f"Service: {data.get('service', 'Unknown')}\n"
            f"Reply with /approve {data.get('request_id', '?')} or /deny {data.get('request_id', '?')}"
        )

    if event_type == "spending_approved":
        return f"{emoji} **Spending approved**: ${data.get('amount', 0):.2f} for {data.get('description', '')}"

    if event_type == "spending_denied":
        return f"{emoji} **Spending denied**: ${data.get('amount', 0):.2f} — {data.get('reason', 'No reason given')}"

    if event_type == "checkin_reminder":
        return f"{emoji} **Check-in reminder**: How's your progress on \"{data.get('goal_title', 'your goal')}\"?"

    if event_type == "goal_complete":
        return f"{emoji} **Goal completed!** \"{data.get('goal_title', 'Your goal')}\" is done! Great work!"

    if event_type == "drip_task":
        return (
            f"{emoji} **Next task ready**\n"
            f"Task: {data.get('title', 'Unknown')}\n"
            f"Estimated: {data.get('estimated_minutes', '?')} minutes\n"
            f"{data.get('description', '')}"
        )

    return f"{emoji} {event_type}: {json.dumps(data)}"


# ─── Channel senders ─────────────────────────────────────────────────────────

def _send_telegram(config: Dict[str, Any], message: str) -> bool:
    """Send a message via Telegram Bot API."""
    bot_token = config.get("bot_token", "")
    chat_id = config.get("chat_id", "")

    if not bot_token or not chat_id:
        logger.warning("Telegram config missing bot_token or chat_id")
        return False

    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown",
    }

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            if resp.status_code == 200:
                return True
            logger.warning("Telegram API returned %d: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.warning("Telegram send failed: %s", exc)
        return False


def _send_webhook(config: Dict[str, Any], event_type: str, message: str, data: Dict[str, Any]) -> bool:
    """Send a notification via generic webhook (JSON POST)."""
    webhook_url = config.get("url", "")

    if not webhook_url:
        logger.warning("Webhook config missing url")
        return False

    payload = {
        "event_type": event_type,
        "message": message,
        "data": data,
    }

    headers = {"Content-Type": "application/json"}
    # Allow custom headers from config
    extra_headers = config.get("headers", {})
    if isinstance(extra_headers, dict):
        headers.update(extra_headers)

    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(webhook_url, json=payload, headers=headers)
            if 200 <= resp.status_code < 300:
                return True
            logger.warning("Webhook returned %d: %s", resp.status_code, resp.text[:200])
            return False
    except Exception as exc:
        logger.warning("Webhook send failed: %s", exc)
        return False


# ─── Public API ──────────────────────────────────────────────────────────────

def send_notification(
    event_type: str,
    data: Dict[str, Any],
    configs: Optional[List[MessagingConfig]] = None,
    user_id: Optional[int] = None,
) -> Dict[str, Any]:
    """
    Send a notification to all enabled messaging channels.

    Args:
        event_type: One of nudge, task_done, task_failed, spending_request,
                    spending_approved, spending_denied, checkin_reminder,
                    goal_complete, drip_task
        data: Event-specific data dict
        configs: Optional pre-loaded configs; if None, loads from DB
        user_id: Optional user ID to scope notifications to that user's configs

    Returns:
        Dict with "sent" count and "failed" count
    """
    if configs is None:
        configs = storage.list_messaging_configs(enabled_only=True, user_id=user_id)

    if not configs:
        return {"sent": 0, "failed": 0, "channels": []}

    message = _format_message(event_type, data)

    # Filter configs by event type preferences
    _event_to_flag = {
        "nudge": "notify_nudges",
        "task_done": "notify_tasks",
        "task_failed": "notify_tasks",
        "spending_request": "notify_spending",
        "spending_approved": "notify_spending",
        "spending_denied": "notify_spending",
        "checkin_reminder": "notify_checkins",
        "goal_complete": "notify_tasks",
        "drip_task": "notify_tasks",
    }

    flag_name = _event_to_flag.get(event_type, "notify_tasks")

    sent = 0
    failed = 0
    channels: List[str] = []

    for cfg in configs:
        if not getattr(cfg, flag_name, True):
            continue

        config_data = json.loads(cfg.config_json) if cfg.config_json else {}

        success = False
        if cfg.channel == "telegram":
            success = _send_telegram(config_data, message)
        elif cfg.channel == "webhook":
            success = _send_webhook(config_data, event_type, message, data)
        else:
            logger.warning("Unknown messaging channel: %s", cfg.channel)
            continue

        if success:
            sent += 1
            channels.append(cfg.channel)
        else:
            failed += 1

    return {"sent": sent, "failed": failed, "channels": channels}


def send_telegram_message(
    bot_token: str,
    chat_id: str,
    text: str,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    """
    Send a direct Telegram message using a bot token and chat_id.

    Used to reply to inbound webhook commands.  Returns True on success.
    """
    if not bot_token or not chat_id:
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    payload: Dict[str, Any] = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        with httpx.Client(timeout=10) as client:
            resp = client.post(url, json=payload)
            return resp.status_code == 200
    except Exception as exc:  # pragma: no cover
        logger.warning("Telegram direct send failed: %s", exc)
        return False


def send_test_message(config_id: int) -> Dict[str, Any]:
    """Send a test message to a specific messaging config."""
    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        return {"success": False, "error": "Config not found"}

    result = send_notification(
        "nudge",
        {"message": "🧪 Test notification from teb — if you see this, messaging is working!"},
        configs=[cfg],
    )

    return {
        "success": result["sent"] > 0,
        "sent": result["sent"],
        "failed": result["failed"],
    }
