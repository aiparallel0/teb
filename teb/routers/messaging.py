"""Router for messaging endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import decomposer
from teb import messaging
from teb import payments
from teb.channels import router as channels_router
from teb.models import (
    Goal, Integration, MessagingConfig,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["messaging"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class MessagingConfigCreate(BaseModel):
    channel: str = Field(..., max_length=50, description="Channel type")
    config: dict = {}
    notify_nudges: bool = True
    notify_tasks: bool = True
    notify_spending: bool = True
    notify_checkins: bool = False

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        valid = {"telegram", "webhook", "slack", "discord", "whatsapp"}
        if v not in valid:
            raise ValueError(f"channel must be one of: {', '.join(sorted(valid))}")
        return v



class MessagingConfigUpdate(BaseModel):
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    notify_nudges: Optional[bool] = None
    notify_tasks: Optional[bool] = None
    notify_spending: Optional[bool] = None
    notify_checkins: Optional[bool] = None



class TelegramUpdate(BaseModel):
    """Minimal Telegram webhook update structure."""
    message: Optional[dict] = None




# ─── External Messaging ─────────────────────────────────────────────────────

@router.post("/api/messaging/config", status_code=201)
async def create_messaging_config(body: MessagingConfigCreate, request: Request):
    """Configure a messaging channel (Telegram or webhook)."""
    import json as _json
    uid = deps.require_user(request)

    valid_channels = {"telegram", "webhook", "slack", "discord", "whatsapp"}
    if body.channel not in valid_channels:
        raise HTTPException(status_code=422, detail=f"channel must be one of {valid_channels}")

    # Validate channel-specific config
    if body.channel == "telegram":
        if "bot_token" not in body.config or "chat_id" not in body.config:
            raise HTTPException(
                status_code=422,
                detail="Telegram config requires 'bot_token' and 'chat_id'",
            )
    elif body.channel == "webhook":
        if "url" not in body.config:
            raise HTTPException(status_code=422, detail="Webhook config requires 'url'")
    elif body.channel == "slack":
        if "bot_token" not in body.config or "channel_id" not in body.config:
            raise HTTPException(
                status_code=422,
                detail="Slack config requires 'bot_token' and 'channel_id'",
            )
    elif body.channel == "discord":
        if "webhook_url" not in body.config:
            raise HTTPException(status_code=422, detail="Discord config requires 'webhook_url'")
    elif body.channel == "whatsapp":
        _wa_required = {"access_token", "phone_number_id", "recipient"}
        if not _wa_required.issubset(body.config):
            raise HTTPException(
                status_code=422,
                detail="WhatsApp config requires 'access_token', 'phone_number_id', and 'recipient'",
            )

    cfg = MessagingConfig(
        channel=body.channel,
        config_json=_json.dumps(body.config),
        notify_nudges=body.notify_nudges,
        notify_tasks=body.notify_tasks,
        notify_spending=body.notify_spending,
        notify_checkins=body.notify_checkins,
        user_id=uid,
    )
    cfg = storage.create_messaging_config(cfg)
    return cfg.to_dict()


@router.get("/api/messaging/configs")
async def list_messaging_configs(request: Request):
    """List messaging configurations for the current user."""
    uid = deps.require_user(request)
    configs = storage.list_messaging_configs(user_id=uid)
    return [c.to_dict() for c in configs]


@router.patch("/api/messaging/config/{config_id}")
async def update_messaging_config_endpoint(config_id: int, body: MessagingConfigUpdate, request: Request):
    """Update a messaging configuration."""
    import json as _json
    uid = deps.require_user(request)

    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Messaging config not found")
    if cfg.user_id is not None and cfg.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")

    if body.config is not None:
        cfg.config_json = _json.dumps(body.config)
    if body.enabled is not None:
        cfg.enabled = body.enabled
    if body.notify_nudges is not None:
        cfg.notify_nudges = body.notify_nudges
    if body.notify_tasks is not None:
        cfg.notify_tasks = body.notify_tasks
    if body.notify_spending is not None:
        cfg.notify_spending = body.notify_spending
    if body.notify_checkins is not None:
        cfg.notify_checkins = body.notify_checkins

    cfg = storage.update_messaging_config(cfg)
    return cfg.to_dict()


@router.delete("/api/messaging/config/{config_id}", status_code=200)
async def delete_messaging_config_endpoint(config_id: int, request: Request):
    """Delete a messaging configuration."""
    uid = deps.require_user(request)
    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Messaging config not found")
    if cfg.user_id is not None and cfg.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")
    storage.delete_messaging_config(config_id)
    return {"deleted": config_id}


@router.post("/api/messaging/test/{config_id}")
async def test_messaging(config_id: int, request: Request):
    """Send a test message to a specific messaging channel."""
    uid = deps.require_user(request)
    cfg = storage.get_messaging_config(config_id)
    if cfg and cfg.user_id is not None and cfg.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = messaging.send_test_message(config_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Test message failed"))
    return result


@router.post("/api/messaging/telegram/webhook")
async def telegram_webhook(body: TelegramUpdate, request: Request):
    """
    Inbound Telegram webhook endpoint.

    Handles /approve, /deny, /goal, /next, /done, /skip commands and maintains
    per-chat conversation state for the full drip question flow.
    Sends a reply back to the user via the Bot API after each command.

    Validates the X-Telegram-Bot-Api-Secret-Token header when
    TEB_TELEGRAM_SECRET_TOKEN is configured.
    """
    # ── Telegram secret token verification ────────────────────────────────
    expected_secret = os.getenv("TEB_TELEGRAM_SECRET_TOKEN", "")
    if expected_secret:
        incoming_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
        if incoming_secret != expected_secret:
            raise HTTPException(status_code=403, detail="Invalid Telegram secret token")
    import re as _re
    import json as _json

    if not body.message:
        return {"ok": True}

    text = body.message.get("text", "").strip()
    chat_id = str(body.message.get("chat", {}).get("id", ""))
    if not text or not chat_id:
        return {"ok": True}

    # Resolve bot_token from the first enabled Telegram messaging config
    bot_token = ""
    tg_configs = [
        c for c in storage.list_messaging_configs(enabled_only=True)
        if c.channel == "telegram"
    ]
    if tg_configs:
        cfg_data = _json.loads(tg_configs[0].config_json) if tg_configs[0].config_json else {}
        bot_token = cfg_data.get("bot_token", "")

    def _reply(msg: str, reply_markup=None) -> None:
        if bot_token:
            messaging.send_telegram_message(bot_token, chat_id, msg, reply_markup=reply_markup)

    # ── /approve {id} ────────────────────────────────────────────────────────
    approve_match = _re.match(r"^/approve +(\d+)$", text)
    if approve_match:
        request_id = int(approve_match.group(1))
        req = storage.get_spending_request(request_id)
        if not req:
            _reply("❌ Spending request not found.")
            return {"ok": True, "error": "Request not found"}
        if req.status != "pending":
            _reply(f"ℹ️ Request #{request_id} is already {req.status}.")
            return {"ok": True, "error": f"Request already {req.status}"}
        req.status = "approved"
        budget = storage.get_spending_budget(req.budget_id)
        if budget:
            budget.spent_today += req.amount
            budget.spent_total += req.amount
            storage.update_spending_budget(budget)
        storage.update_spending_request(req)
        messaging.send_notification("spending_approved", {
            "amount": req.amount,
            "description": req.description,
        })
        _reply(f"✅ Approved ${req.amount:.2f} for: {req.description}")
        return {"ok": True, "action": "approved", "request_id": request_id}

    # ── /deny {id} [reason] ───────────────────────────────────────────────────
    deny_match = _re.match(r"^/deny +(\d+)(?: (.+))?$", text)
    if deny_match:
        request_id = int(deny_match.group(1))
        reason = deny_match.group(2) or ""
        req = storage.get_spending_request(request_id)
        if not req:
            _reply("❌ Spending request not found.")
            return {"ok": True, "error": "Request not found"}
        if req.status != "pending":
            _reply(f"ℹ️ Request #{request_id} is already {req.status}.")
            return {"ok": True, "error": f"Request already {req.status}"}
        req.status = "denied"
        req.denial_reason = reason
        storage.update_spending_request(req)
        messaging.send_notification("spending_denied", {
            "amount": req.amount,
            "description": req.description,
            "reason": reason,
        })
        _reply(f"🚫 Denied ${req.amount:.2f} for: {req.description}")
        return {"ok": True, "action": "denied", "request_id": request_id}

    # ── /goal <text> ──────────────────────────────────────────────────────────
    goal_match = _re.match(r"^/goal (.+)$", text, _re.S)
    if goal_match:
        goal_text = goal_match.group(1).strip()
        goal = Goal(title=goal_text, description="Created via Telegram")
        goal = storage.create_goal(goal)
        try:
            decomposer.decompose(goal)
            goal.status = "decomposed"
            storage.update_goal(goal)
        except Exception as exc:
            logger.error("Telegram goal decomposition failed for goal %s: %s", goal.id, exc)
            goal.status = "drafting"
            storage.update_goal(goal)
            _reply(f"⚠️ Goal created but auto-planning failed. Use /next to try again or answer questions to refine.")
        # Start question flow: get first drip question
        q = decomposer.get_next_drip_question(goal)
        if q:
            storage.upsert_telegram_session(chat_id, goal.id, "awaiting_answer", q.key)
            _reply(
                f"🎯 Goal created: *{goal.title}*\n\n"
                f"Let me ask you a few quick questions to tailor your plan.\n\n"
                f"❓ {q.text}"
                + (f"\n_(Hint: {q.hint})_" if q.hint else "")
            )
        else:
            storage.upsert_telegram_session(chat_id, goal.id, "idle")
            _reply(
                f"🎯 Goal created: *{goal.title}*\n\n"
                f"Type /next to get your first task."
            )
        return {"ok": True, "action": "goal_created", "goal_id": goal.id}

    # ── Helper: resolve goal from session ─────────────────────────────────────
    def _goal_from_session(cid: str) -> Optional[Goal]:
        """Return the goal bound to a Telegram chat session, or None."""
        session = storage.get_telegram_session(cid)
        if session and session.get("goal_id"):
            return storage.get_goal(session["goal_id"])
        return None

    # ── /next ─────────────────────────────────────────────────────────────────
    if text == "/next":
        goal = _goal_from_session(chat_id)
        if not goal:
            _reply("No goal is selected for this chat. Use /goal <description> to create one.")
            return {"ok": True, "message": "No selected goal"}
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        drip = decomposer.drip_next_task(goal, tasks)
        if drip and drip.get("task"):
            td = drip["task"]
            mins = td.get("estimated_minutes", "?")
            skip_hint = f"\n💡 _{drip['skip_suggestion']}_" if drip.get("skip_suggestion") else ""
            _reply(
                f"📋 *Next task:* {td.get('title', '')}\n"
                f"_{td.get('description', '')}_\n"
                f"⏱ ~{mins} min{skip_hint}\n\n"
                f"When done, type /done"
            )
        else:
            _reply(drip.get("message", "All done! 🎉") if drip else "All done! 🎉")
        return {"ok": True, "action": "drip_next", "drip": drip}

    # ── /done ─────────────────────────────────────────────────────────────────
    if text == "/done":
        goal = _goal_from_session(chat_id)
        if not goal:
            _reply("No goal is selected for this chat. Use /goal <description> to create one.")
            return {"ok": True, "message": "No selected goal"}
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        focus = decomposer.get_focus_task(tasks)
        if focus:
            focus.status = "done"
            storage.update_task(focus)
            tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
            drip = decomposer.drip_next_task(goal, tasks, completed_task=focus)
            if drip and drip.get("task"):
                td = drip["task"]
                mins = td.get("estimated_minutes", "?")
                _reply(
                    f"✅ Marked done: *{focus.title}*\n\n"
                    f"📋 *Next:* {td.get('title', '')}\n"
                    f"_{td.get('description', '')}_\n"
                    f"⏱ ~{mins} min\n\nType /done when finished."
                )
            else:
                _reply(f"✅ Marked done: *{focus.title}*\n\n🎉 All tasks complete!")
            return {"ok": True, "action": "task_done", "drip": drip}
        _reply("No current task to mark done.")
        return {"ok": True, "message": "No current task"}

    # ── /skip ─────────────────────────────────────────────────────────────────
    if text == "/skip":
        goal = _goal_from_session(chat_id)
        if not goal:
            _reply("No goal is selected for this chat. Use /goal <description> to create one.")
            return {"ok": True, "message": "No selected goal"}
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        focus = decomposer.get_focus_task(tasks)
        if focus:
            focus.status = "skipped"
            storage.update_task(focus)
            _reply(f"⏭ Skipped: *{focus.title}*\n\nType /next for the next task.")
        else:
            _reply("Nothing to skip.")
        return {"ok": True, "action": "task_skipped"}

    # ── Free text: session-based question flow ────────────────────────────────
    session = storage.get_telegram_session(chat_id)
    if session and session.get("state") == "awaiting_answer" and session.get("pending_question_key"):
        goal_id = session["goal_id"]
        goal = storage.get_goal(goal_id) if goal_id else None
        if goal:
            key = session["pending_question_key"]
            goal.answers[key] = text
            goal.status = "clarifying"
            storage.update_goal(goal)
            # Get next question or move to drip
            next_q = decomposer.get_next_drip_question(goal)
            if next_q:
                storage.upsert_telegram_session(chat_id, goal_id, "awaiting_answer", next_q.key)
                _reply(
                    f"❓ {next_q.text}"
                    + (f"\n_(Hint: {next_q.hint})_" if next_q.hint else "")
                )
            else:
                storage.upsert_telegram_session(chat_id, goal_id, "idle")
                _reply("✅ Got it! Type /next to get your first task.")
            return {"ok": True, "action": "answer_recorded"}

    # Unknown command or message
    _reply(
        "👋 Available commands:\n"
        "/goal <description> — start a new goal\n"
        "/next — get your next task\n"
        "/done — mark current task done\n"
        "/skip — skip current task\n"
        "/approve <id> — approve a spending request\n"
        "/deny <id> [reason] — deny a spending request"
    )
    return {"ok": True}



# ─── Channel Webhook Endpoints ───────────────────────────────────────────────

from teb.channels import route_command as _route_channel_command  # noqa: E402
from teb.channels.slack import SlackChannel as _SlackChannel  # noqa: E402
from teb.channels.discord import DiscordChannel as _DiscordChannel  # noqa: E402
from teb.channels.whatsapp import WhatsAppChannel as _WhatsAppChannel  # noqa: E402


@router.post("/api/channels/slack/webhook")
async def slack_channel_webhook(request: Request):
    """Inbound Slack webhook endpoint.

    Handles Events API callbacks and slash commands, routing recognised
    teb commands through the command router.
    """
    body_bytes = await request.body()

    # Verify Slack signature when a signing secret is configured
    slack = _SlackChannel()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    if slack.signing_secret and not slack.verify_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    payload = await request.json()
    parsed = slack.receive_command(payload)

    # Handle URL verification challenge
    if "challenge" in parsed:
        return {"challenge": parsed["challenge"]}

    text = parsed.get("text", "")
    if not text:
        return {"ok": True}

    result = _route_channel_command(text, user_id=parsed.get("user_id"))

    # Reply via Slack if we have a channel_id
    channel_id = parsed.get("channel_id", "")
    if channel_id and result.message:
        slack.send_message(channel_id, result.message)

    return {
        "ok": True,
        "command": result.command,
        "success": result.success,
        "message": result.message,
    }


@router.post("/api/channels/discord/webhook")
async def discord_channel_webhook(request: Request):
    """Inbound Discord interactions webhook endpoint.

    Handles PING verification and application command interactions,
    routing recognised teb commands through the command router.
    """
    body_bytes = await request.body()

    discord = _DiscordChannel()
    timestamp = request.headers.get("x-signature-timestamp", "")
    signature = request.headers.get("x-signature-ed25519", "")
    if discord.public_key and not discord.verify_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Discord signature")

    payload = await request.json()
    parsed = discord.receive_command(payload)

    # PING response (type 1)
    if parsed.get("type") == 1:
        return {"type": 1}

    text = parsed.get("text", "")
    if not text:
        return {"type": 4, "data": {"content": "No command received."}}

    result = _route_channel_command(text, user_id=parsed.get("user_id"))

    # Discord interaction response (type 4 = CHANNEL_MESSAGE_WITH_SOURCE)
    return {
        "type": 4,
        "data": {"content": result.message or "Done."},
    }


@router.get("/api/channels/whatsapp/webhook")
async def whatsapp_channel_webhook_verify(
    request: Request,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """WhatsApp webhook verification (GET).

    Meta sends a GET request with hub.mode, hub.verify_token, and
    hub.challenge to verify the webhook endpoint.
    """
    wa = _WhatsAppChannel()
    challenge = wa.verify_webhook(hub_mode, hub_token, hub_challenge)
    if challenge is not None:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@router.post("/api/channels/whatsapp/webhook")
async def whatsapp_channel_webhook(request: Request):
    """Inbound WhatsApp Cloud API webhook endpoint.

    Parses incoming messages and routes recognised teb commands through
    the command router.  Replies are sent back via the WhatsApp API.
    """
    payload = await request.json()

    wa = _WhatsAppChannel()
    parsed = wa.receive_command(payload)

    text = parsed.get("text", "")
    sender = parsed.get("user_id", "")
    if not text:
        return {"ok": True}

    result = _route_channel_command(text, user_id=sender)

    # Reply to the sender
    if sender and result.message:
        wa.send_message(sender, result.message)

    return {
        "ok": True,
        "command": result.command,
        "success": result.success,
        "message": result.message,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Payment Integration
# ═══════════════════════════════════════════════════════════════════════════════

from teb import payments as _payments  # noqa: E402


class PaymentAccountCreate(BaseModel):
    provider: str
    account_id: str = ""
    config: dict = {}


class PaymentExecute(BaseModel):
    provider: str
    amount: float
    currency: str = "USD"
    recipient: str = ""
    description: str = ""
    spending_request_id: Optional[int] = None


@router.get("/api/payments/providers")
async def list_payment_providers(request: Request):
    """List available payment providers and their configuration status."""
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    return _payments.list_providers()


@router.post("/api/payments/accounts", status_code=201)
async def create_payment_account(body: PaymentAccountCreate, request: Request):
    """Register a payment account (Mercury, Stripe) for the current user."""
    import json as _json
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    valid_providers = {"mercury", "stripe"}
    if body.provider not in valid_providers:
        raise HTTPException(status_code=422, detail=f"provider must be one of {valid_providers}")
    account = storage.create_payment_account(
        user_id=uid,
        provider=body.provider,
        account_id=body.account_id,
        config_json=_json.dumps(body.config),
    )
    return account


@router.get("/api/payments/accounts")
async def list_payment_accounts(request: Request):
    """List payment accounts for the current user."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    return storage.list_payment_accounts(uid)


@router.get("/api/payments/balance/{provider}")
async def get_payment_balance(provider: str, request: Request):
    """Get account balance for a payment provider."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    result = _payments.get_account_balance(uid, provider)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.post("/api/payments/execute")
async def execute_payment(body: PaymentExecute, request: Request):
    """Execute a real payment through a configured provider.

    The user must have a registered and enabled payment account for the
    specified provider. If a spending_request_id is given, the payment
    is linked to that approval-gated spending request.

    Balance is verified before executing the transfer to prevent
    overdraft. The provider layer retries on transient failures.
    """
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    result = _payments.execute_payment(
        user_id=uid,
        provider_name=body.provider,
        amount=body.amount,
        currency=body.currency,
        recipient=body.recipient,
        description=body.description,
        spending_request_id=body.spending_request_id,
    )
    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result.get("error", "Payment failed"))
    return result


@router.get("/api/payments/transactions/{account_id}")
async def list_payment_transactions(account_id: int, request: Request):
    """List transactions for a payment account."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    account = storage.get_payment_account(account_id)
    if not account or account["user_id"] != uid:
        raise HTTPException(status_code=404, detail="Payment account not found")
    return storage.list_payment_transactions(account_id)


