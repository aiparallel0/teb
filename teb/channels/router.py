"""
Command router for inbound channel messages.

Parses structured commands from any channel and maps them to the
appropriate teb storage / API actions.

Supported commands:
    /approve <id>                — approve a pending spending request
    /deny <id> [reason]          — deny a pending spending request
    /done <task_id>              — mark a task as done
    /checkin <goal_id> <summary> — create a check-in for a goal
    /status                      — list active goals and their progress
    /status <goal_id>            — show status of a specific goal
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, Optional

from teb import messaging, storage
from teb.channels.base import CommandResult
from teb.models import CheckIn

logger = logging.getLogger(__name__)

# Pre-compiled patterns for command parsing
_RE_APPROVE = re.compile(r"^/approve\s+(\d+)$")
_RE_DENY = re.compile(r"^/deny\s+(\d+)(?:\s(.+))?$")
_RE_DONE = re.compile(r"^/done\s+(\d+)$")
_RE_CHECKIN = re.compile(r"^/checkin\s+(\d+)\s(.+)$", re.DOTALL)
_RE_STATUS_GOAL = re.compile(r"^/status\s+(\d+)$")
_RE_STATUS = re.compile(r"^/status$")


def route_command(text: str, user_id: Optional[str] = None) -> CommandResult:
    """Parse *text* as a structured command and execute it.

    Returns a ``CommandResult`` with the outcome.  Unknown or malformed
    commands return ``success=False`` with an informative message.
    """
    text = text.strip()

    # ── /approve <id> ─────────────────────────────────────────────────────
    m = _RE_APPROVE.match(text)
    if m:
        return _handle_approve(int(m.group(1)))

    # ── /deny <id> [reason] ──────────────────────────────────────────────
    m = _RE_DENY.match(text)
    if m:
        return _handle_deny(int(m.group(1)), m.group(2) or "")

    # ── /done <task_id> ──────────────────────────────────────────────────
    m = _RE_DONE.match(text)
    if m:
        return _handle_done(int(m.group(1)))

    # ── /checkin <goal_id> <summary> ─────────────────────────────────────
    m = _RE_CHECKIN.match(text)
    if m:
        return _handle_checkin(int(m.group(1)), m.group(2).strip())

    # ── /status <goal_id> ────────────────────────────────────────────────
    m = _RE_STATUS_GOAL.match(text)
    if m:
        return _handle_status_goal(int(m.group(1)))

    # ── /status ──────────────────────────────────────────────────────────
    if _RE_STATUS.match(text):
        return _handle_status()

    # ── Unknown command ──────────────────────────────────────────────────
    return CommandResult(
        command="unknown",
        success=False,
        message=(
            "Unknown command. Available commands:\n"
            "/approve <id> — approve a spending request\n"
            "/deny <id> [reason] — deny a spending request\n"
            "/done <task_id> — mark a task as done\n"
            "/checkin <goal_id> <summary> — create a check-in\n"
            "/status — list active goals\n"
            "/status <goal_id> — status of a specific goal"
        ),
    )


# ─── Command handlers ─────────────────────────────────────────────────────────


def _handle_approve(request_id: int) -> CommandResult:
    req = storage.get_spending_request(request_id)
    if not req:
        return CommandResult(
            command="approve", success=False,
            message=f"❌ Spending request #{request_id} not found.",
        )
    if req.status != "pending":
        return CommandResult(
            command="approve", success=False,
            message=f"ℹ️ Request #{request_id} is already {req.status}.",
        )

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

    return CommandResult(
        command="approve", success=True,
        message=f"✅ Approved ${req.amount:.2f} for: {req.description}",
        data={"request_id": request_id},
    )


def _handle_deny(request_id: int, reason: str) -> CommandResult:
    req = storage.get_spending_request(request_id)
    if not req:
        return CommandResult(
            command="deny", success=False,
            message=f"❌ Spending request #{request_id} not found.",
        )
    if req.status != "pending":
        return CommandResult(
            command="deny", success=False,
            message=f"ℹ️ Request #{request_id} is already {req.status}.",
        )

    req.status = "denied"
    req.denial_reason = reason
    storage.update_spending_request(req)

    messaging.send_notification("spending_denied", {
        "amount": req.amount,
        "description": req.description,
        "reason": reason,
    })

    return CommandResult(
        command="deny", success=True,
        message=f"🚫 Denied ${req.amount:.2f} for: {req.description}",
        data={"request_id": request_id},
    )


def _handle_done(task_id: int) -> CommandResult:
    task = storage.get_task(task_id)
    if not task:
        return CommandResult(
            command="done", success=False,
            message=f"❌ Task #{task_id} not found.",
        )
    if task.status == "done":
        return CommandResult(
            command="done", success=False,
            message=f"ℹ️ Task #{task_id} is already done.",
        )

    task.status = "done"
    storage.update_task(task)

    messaging.send_notification("task_done", {
        "task_id": task_id,
        "title": task.title,
    })

    return CommandResult(
        command="done", success=True,
        message=f"✅ Task marked done: {task.title}",
        data={"task_id": task_id},
    )


def _handle_checkin(goal_id: int, summary: str) -> CommandResult:
    goal = storage.get_goal(goal_id)
    if not goal:
        return CommandResult(
            command="checkin", success=False,
            message=f"❌ Goal #{goal_id} not found.",
        )

    checkin = CheckIn(goal_id=goal_id, done_summary=summary)
    checkin = storage.create_checkin(checkin)

    return CommandResult(
        command="checkin", success=True,
        message=f"📝 Check-in recorded for goal: {goal.title}",
        data={"checkin_id": checkin.id, "goal_id": goal_id},
    )


def _handle_status_goal(goal_id: int) -> CommandResult:
    goal = storage.get_goal(goal_id)
    if not goal:
        return CommandResult(
            command="status", success=False,
            message=f"❌ Goal #{goal_id} not found.",
        )

    tasks = storage.list_tasks(goal_id)
    total = len(tasks)
    done = sum(1 for t in tasks if t.status == "done")
    in_progress = sum(1 for t in tasks if t.status in ("in_progress", "executing"))
    pct = round((done / total) * 100) if total else 0

    lines = [
        f"📊 **{goal.title}** ({goal.status})",
        f"Progress: {done}/{total} tasks done ({pct}%)",
        f"In progress: {in_progress}",
    ]

    return CommandResult(
        command="status", success=True,
        message="\n".join(lines),
        data={
            "goal_id": goal_id,
            "total_tasks": total,
            "done": done,
            "in_progress": in_progress,
            "pct": pct,
        },
    )


def _handle_status() -> CommandResult:
    """Return a summary of all active goals (non-done)."""
    # list_goals without user_id returns all; we keep it simple here.
    # The caller should scope by user_id if needed.
    all_goals = storage.list_goals()
    active = [g for g in all_goals if g.status not in ("done",)]

    if not active:
        return CommandResult(
            command="status", success=True,
            message="No active goals.",
            data={"goals": []},
        )

    lines = ["📊 **Active Goals**"]
    goal_summaries: list[Dict[str, Any]] = []
    for g in active[:10]:  # cap at 10 to avoid huge messages
        tasks = storage.list_tasks(g.id)  # type: ignore[arg-type]
        total = len(tasks)
        done = sum(1 for t in tasks if t.status == "done")
        pct = round((done / total) * 100) if total else 0
        lines.append(f"• #{g.id} {g.title} — {done}/{total} ({pct}%)")
        goal_summaries.append({
            "goal_id": g.id,
            "title": g.title,
            "status": g.status,
            "done": done,
            "total": total,
            "pct": pct,
        })

    return CommandResult(
        command="status", success=True,
        message="\n".join(lines),
        data={"goals": goal_summaries},
    )
