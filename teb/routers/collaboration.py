"""Router for collaboration endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import scheduler

from teb.models import (
    ActivityFeedEntry, CommentReaction, DashboardLayout, DirectMessage, EmailNotificationConfig, GoalChatMessage, GoalCollaborator, PushSubscription, SavedView, ScheduledReport, Workspace, WorkspaceMember,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["collaboration"])


# ─── Phase 2: Workspace endpoints ────────────────────────────────────────────

@router.post("/api/workspaces", status_code=201)
async def create_workspace_endpoint(request: Request):
    """Create a new workspace and auto-add the owner as a member."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    name = str(body.get("name", "")).strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    invite_code = secrets.token_urlsafe(8)
    ws = Workspace(
        name=name,
        owner_id=uid,
        description=str(body.get("description", "")),
        invite_code=invite_code,
        plan=body.get("plan", "free"),
    )
    ws = storage.create_workspace(ws)
    member = WorkspaceMember(workspace_id=ws.id, user_id=uid, role="owner")
    storage.add_workspace_member(member)
    storage.create_activity_entry(ActivityFeedEntry(
        user_id=uid, action="created", entity_type="workspace",
        entity_id=ws.id, entity_title=ws.name, workspace_id=ws.id,
    ))
    return ws.to_dict()


@router.get("/api/workspaces")
async def list_workspaces_endpoint(request: Request):
    """List workspaces for the current user."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    return [w.to_dict() for w in storage.list_user_workspaces(uid)]


@router.get("/api/workspaces/{ws_id}")
async def get_workspace_endpoint(ws_id: int, request: Request):
    """Get workspace details."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    ws = storage.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    members = storage.list_workspace_members(ws_id)
    member_ids = [m.user_id for m in members]
    if uid not in member_ids:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return ws.to_dict()


@router.post("/api/workspaces/{ws_id}/members", status_code=201)
async def add_workspace_member_endpoint(ws_id: int, request: Request):
    """Add a member to a workspace."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    ws = storage.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    members = storage.list_workspace_members(ws_id)
    caller_member = next((m for m in members if m.user_id == uid), None)
    if not caller_member or caller_member.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only owners and admins can add members")
    body = await request.json()
    new_user_id = body.get("user_id")
    if not new_user_id:
        raise HTTPException(status_code=422, detail="user_id is required")
    if any(m.user_id == new_user_id for m in members):
        raise HTTPException(status_code=409, detail="User is already a member")
    role = body.get("role", "member")
    if role not in ("admin", "member", "viewer"):
        raise HTTPException(status_code=422, detail="Invalid role")
    member = WorkspaceMember(workspace_id=ws_id, user_id=new_user_id, role=role)
    member = storage.add_workspace_member(member)
    storage.create_notification(Notification(
        user_id=new_user_id, title=f"You were added to workspace '{ws.name}'",
        notification_type="info", source_type="workspace", source_id=ws_id,
    ))
    return member.to_dict()


@router.get("/api/workspaces/{ws_id}/members")
async def list_workspace_members_endpoint(ws_id: int, request: Request):
    """List members of a workspace."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    ws = storage.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    members = storage.list_workspace_members(ws_id)
    if not any(m.user_id == uid for m in members):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return [m.to_dict() for m in members]


@router.delete("/api/workspaces/{ws_id}/members/{member_uid}")
async def remove_workspace_member_endpoint(ws_id: int, member_uid: int, request: Request):
    """Remove a member from a workspace."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    ws = storage.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    members = storage.list_workspace_members(ws_id)
    caller_member = next((m for m in members if m.user_id == uid), None)
    if not caller_member or caller_member.role not in ("owner", "admin"):
        raise HTTPException(status_code=403, detail="Only owners and admins can remove members")
    if member_uid == ws.owner_id:
        raise HTTPException(status_code=400, detail="Cannot remove workspace owner")
    removed = storage.remove_workspace_member(ws_id, member_uid)
    if not removed:
        raise HTTPException(status_code=404, detail="Member not found")
    return {"deleted": True}


@router.post("/api/workspaces/join")
async def join_workspace_endpoint(request: Request):
    """Join a workspace by invite code."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    code = str(body.get("invite_code", "")).strip()
    if not code:
        raise HTTPException(status_code=422, detail="invite_code is required")
    ws = storage.get_workspace_by_invite_code(code)
    if not ws:
        raise HTTPException(status_code=404, detail="Invalid invite code")
    members = storage.list_workspace_members(ws.id)
    if any(m.user_id == uid for m in members):
        raise HTTPException(status_code=409, detail="Already a member")
    member = WorkspaceMember(workspace_id=ws.id, user_id=uid, role="member")
    member = storage.add_workspace_member(member)
    storage.create_activity_entry(ActivityFeedEntry(
        user_id=uid, action="created", entity_type="workspace",
        entity_id=ws.id, entity_title=f"Joined '{ws.name}'", workspace_id=ws.id,
    ))
    return member.to_dict()


# Notification routes moved to teb/routers/notifications.py


# ─── Phase 2: Comment Reactions endpoints ─────────────────────────────────────

@router.post("/api/comments/{comment_id}/reactions", status_code=201)
async def add_comment_reaction_endpoint(comment_id: int, request: Request):
    """Add an emoji reaction to a comment."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    emoji = str(body.get("emoji", "👍")).strip()
    if not emoji:
        raise HTTPException(status_code=422, detail="emoji is required")
    existing = storage.list_comment_reactions(comment_id)
    if any(r.user_id == uid and r.emoji == emoji for r in existing):
        raise HTTPException(status_code=409, detail="Reaction already exists")
    reaction = CommentReaction(comment_id=comment_id, user_id=uid, emoji=emoji)
    reaction = storage.add_comment_reaction(reaction)
    return reaction.to_dict()


@router.delete("/api/comments/{comment_id}/reactions/{emoji}")
async def remove_comment_reaction_endpoint(comment_id: int, emoji: str, request: Request):
    """Remove an emoji reaction from a comment."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    removed = storage.remove_comment_reaction(comment_id, uid, emoji)
    if not removed:
        raise HTTPException(status_code=404, detail="Reaction not found")
    return {"deleted": True}


@router.get("/api/comments/{comment_id}/reactions")
async def list_comment_reactions_endpoint(comment_id: int, request: Request):
    """List reactions on a comment."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    return [r.to_dict() for r in storage.list_comment_reactions(comment_id)]


# ── Phase 4: Intelligence ─────────────────────────────────────────────


@router.get("/api/goals/{goal_id}/schedule", tags=["intelligence"])
async def get_ai_schedule(goal_id: int, request: Request):
    """Auto-schedule tasks into time blocks respecting dependencies and capacity."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.auto_schedule_tasks(tasks)


@router.get("/api/goals/{goal_id}/smart-priority", tags=["intelligence"])
async def get_smart_priority(goal_id: int, request: Request):
    """ML-based priority ranking of tasks."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.smart_prioritize(tasks)


@router.get("/api/goals/{goal_id}/completion-estimate", tags=["intelligence"])
async def get_completion_estimate(goal_id: int, request: Request):
    """Predict goal completion date based on remaining work and velocity."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.estimate_completion(tasks)


@router.get("/api/goals/{goal_id}/risks", tags=["intelligence"])
async def get_risks(goal_id: int, request: Request):
    """Detect at-risk tasks: overdue, blocked, stagnant, overloaded."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.detect_risks(tasks)


@router.get("/api/goals/{goal_id}/focus-blocks", tags=["intelligence"])
async def get_focus_blocks(goal_id: int, request: Request, available_hours: int = 4):
    """Suggest optimal focus work blocks grouped by tags and task size."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.suggest_focus_blocks(tasks, available_hours=available_hours)


@router.get("/api/goals/{goal_id}/duplicates", tags=["intelligence"])
async def get_duplicates(goal_id: int, request: Request):
    """Detect potential duplicate tasks using word overlap similarity."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.detect_duplicates(tasks)


@router.post("/api/goals/{goal_id}/auto-prioritize", tags=["intelligence"])
async def auto_prioritize(goal_id: int, request: Request):
    """Apply smart prioritization to all tasks and update their order_index."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    ranked = scheduler.smart_prioritize(tasks)
    task_map = {t.id: t for t in tasks}
    for idx, entry in enumerate(ranked):
        task = task_map.get(entry["task_id"])
        if task:
            task.order_index = idx
            storage.update_task(task)
    return {"updated": len(ranked), "ranking": ranked}


# ─── Direct Messaging ────────────────────────────────────────────────────────

@router.post("/api/messages", status_code=201)
async def send_message_endpoint(request: Request):
    """Send a direct message to another user."""
    uid = deps.require_user(request)
    body = await request.json()
    recipient_id = body.get("recipient_id")
    content = str(body.get("content", "")).strip()
    if not recipient_id:
        raise HTTPException(status_code=422, detail="recipient_id is required")
    if not content:
        raise HTTPException(status_code=422, detail="content must not be empty")
    msg = DirectMessage(sender_id=uid, recipient_id=recipient_id, content=content)
    msg = storage.send_message(msg)
    # Notify recipient via SSE
    from teb import events as _events
    _events.event_bus.publish(recipient_id, "new_message", {
        "message_id": msg.id, "sender_id": uid, "content": content[:100],
    })
    return msg.to_dict()


@router.get("/api/messages/conversations")
async def list_conversations_endpoint(request: Request):
    """List conversation partners for the current user."""
    uid = deps.require_user(request)
    return storage.list_conversations(uid)


@router.get("/api/messages/{other_user_id}")
async def list_messages_endpoint(other_user_id: int, request: Request):
    """List messages between current user and another user."""
    uid = deps.require_user(request)
    messages = storage.list_messages(uid, other_user_id)
    return [m.to_dict() for m in messages]


@router.put("/api/messages/{message_id}/read")
async def mark_message_read_endpoint(message_id: int, request: Request):
    """Mark a message as read."""
    uid = deps.require_user(request)
    ok = storage.mark_message_read(message_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found or not your message")
    return {"read": True}


# ─── Goal-Scoped Chat ────────────────────────────────────────────────────────

@router.post("/api/goals/{goal_id}/chat", status_code=201)
async def create_goal_chat_endpoint(goal_id: int, request: Request):
    """Send a chat message in a goal's chat room."""
    uid = deps.require_user(request)
    body = await request.json()
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=422, detail="content must not be empty")
    msg = GoalChatMessage(goal_id=goal_id, user_id=uid, content=content)
    msg = storage.create_goal_chat_message(msg)
    # Broadcast to collaborators via SSE
    from teb import events as _events
    _events.event_bus.publish_broadcast("goal_chat", {
        "goal_id": goal_id, "message_id": msg.id, "user_id": uid, "content": content[:100],
    })
    return msg.to_dict()


@router.get("/api/goals/{goal_id}/chat")
async def list_goal_chat_endpoint(goal_id: int, request: Request):
    """List chat messages for a goal."""
    deps.require_user(request)
    messages = storage.list_goal_chat_messages(goal_id)
    return [m.to_dict() for m in messages]


# ─── Email Notification Preferences ──────────────────────────────────────────

@router.get("/api/users/me/email-preferences")
async def get_email_preferences(request: Request):
    """Get email notification preferences."""
    uid = deps.require_user(request)
    cfg = storage.get_email_notification_config(uid)
    if not cfg:
        cfg = EmailNotificationConfig(user_id=uid)
    return cfg.to_dict()


@router.put("/api/users/me/email-preferences")
async def update_email_preferences(request: Request):
    """Update email notification preferences."""
    uid = deps.require_user(request)
    body = await request.json()
    freq = body.get("digest_frequency", "none")
    if freq not in ("none", "daily", "weekly"):
        raise HTTPException(status_code=422, detail="digest_frequency must be none, daily, or weekly")
    cfg = EmailNotificationConfig(
        user_id=uid,
        digest_frequency=freq,
        notify_on_mention=bool(body.get("notify_on_mention", True)),
        notify_on_assignment=bool(body.get("notify_on_assignment", True)),
        notify_on_comment=bool(body.get("notify_on_comment", True)),
    )
    cfg = storage.upsert_email_notification_config(cfg)
    return cfg.to_dict()


# ─── Push Notification Subscriptions ─────────────────────────────────────────

@router.post("/api/push/subscribe", status_code=201)
async def push_subscribe(request: Request):
    """Register a push notification subscription."""
    uid = deps.require_user(request)
    body = await request.json()
    endpoint = str(body.get("endpoint", "")).strip()
    if not endpoint:
        raise HTTPException(status_code=422, detail="endpoint is required")
    sub = PushSubscription(
        user_id=uid,
        endpoint=endpoint,
        p256dh=str(body.get("p256dh", "")),
        auth=str(body.get("auth", "")),
    )
    sub = storage.save_push_subscription(sub)
    return sub.to_dict()


@router.delete("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    """Remove a push notification subscription."""
    uid = deps.require_user(request)
    body = await request.json()
    endpoint = str(body.get("endpoint", "")).strip()
    if not endpoint:
        raise HTTPException(status_code=422, detail="endpoint is required")
    removed = storage.delete_push_subscription(endpoint, uid)
    if not removed:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"removed": True}


# ─── Phase 3: Saved Views ────────────────────────────────────────────────────

@router.post("/api/views", status_code=201)
async def create_saved_view(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    view = SavedView(
        user_id=uid,
        name=body.get("name", "Untitled View"),
        view_type=body.get("view_type", "list"),
        filters_json=json.dumps(body.get("filters", {})),
        sort_json=json.dumps(body.get("sort", {})),
        group_by=body.get("group_by", ""),
    )
    view = storage.save_view(view)
    return view.to_dict()


@router.get("/api/views")
async def list_views_endpoint(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    views = storage.list_saved_views(uid)
    return [v.to_dict() for v in views]


@router.get("/api/views/{view_id}")
async def get_view_endpoint(view_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    view = storage.get_saved_view(view_id)
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    return view.to_dict()


@router.delete("/api/views/{view_id}")
async def delete_view_endpoint(view_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    storage.delete_saved_view(view_id, uid)
    return {"deleted": True}


@router.get("/api/users/me/tasks", tags=["cross-goal"])
async def list_user_tasks_endpoint(
    request: Request,
    status: Optional[str] = Query(default=None),
    priority: Optional[str] = Query(default=None),
    assigned_to: Optional[int] = Query(default=None),
    tags: Optional[str] = Query(default=None),
    due_before: Optional[str] = Query(default=None),
    due_after: Optional[str] = Query(default=None),
    sort_field: str = Query(default="order_index"),
    sort_dir: str = Query(default="asc"),
    limit: int = Query(default=200, ge=1, le=1000),
):
    """List all tasks across all goals for the current user.

    Supports filtering by status, priority, assigned_to, tags, and due date range.
    Supports sorting by any allowed field.
    Enables cross-goal portfolio views.
    """
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    tasks = storage.list_user_tasks(
        user_id=uid,
        status=status,
        priority=priority,
        assigned_to=assigned_to,
        tags=tags,
        due_before=due_before,
        due_after=due_after,
        sort_field=sort_field,
        sort_dir=sort_dir,
        limit=limit,
    )
    return [t.to_dict() for t in tasks]


@router.get("/api/views/{view_id}/tasks", tags=["cross-goal"])
async def get_view_tasks(view_id: int, request: Request):
    """Apply a saved view's filters and sort to the user's tasks, returning filtered results.

    Works cross-goal — returns tasks from ALL the user's goals matching the view's config.
    """
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    view = storage.get_saved_view(view_id)
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    if view.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")

    filters = json.loads(view.filters_json) if view.filters_json else {}
    sort_config = json.loads(view.sort_json) if view.sort_json else {}

    tasks = storage.list_user_tasks(
        user_id=uid,
        status=filters.get("status"),
        priority=filters.get("priority"),
        assigned_to=filters.get("assigned_to"),
        tags=filters.get("tags"),
        due_before=filters.get("due_before"),
        due_after=filters.get("due_after"),
        sort_field=sort_config.get("field", "order_index"),
        sort_dir=sort_config.get("direction", "asc"),
    )

    result = [t.to_dict() for t in tasks]

    # Apply group_by on the server side for convenience
    if view.group_by:
        grouped: dict = {}
        for t in result:
            key = t.get(view.group_by, "Other")
            if isinstance(key, list):
                key = ", ".join(str(k) for k in key) if key else "None"
            elif key is None:
                key = "None"
            else:
                key = str(key)
            grouped.setdefault(key, []).append(t)
        return {"view": view.to_dict(), "grouped": grouped, "total": len(result)}

    return {"view": view.to_dict(), "tasks": result, "total": len(result)}


# ─── Phase 3: Dashboard Layouts ──────────────────────────────────────────────

@router.post("/api/dashboards", status_code=201)
async def create_dashboard_endpoint(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    layout = DashboardLayout(
        user_id=uid,
        name=body.get("name", "My Dashboard"),
        widgets_json=json.dumps(body.get("widgets", [])),
    )
    layout = storage.save_dashboard(layout)
    return layout.to_dict()


@router.get("/api/dashboards")
async def list_dashboards_endpoint(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    dashboards = storage.list_dashboards(uid)
    return [d.to_dict() for d in dashboards]


@router.get("/api/dashboards/{dashboard_id}")
async def get_dashboard_endpoint(dashboard_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    dashboard = storage.get_dashboard(dashboard_id)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return dashboard.to_dict()


@router.put("/api/dashboards/{dashboard_id}")
async def update_dashboard_endpoint(dashboard_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    kwargs = {}
    if "name" in body:
        kwargs["name"] = body["name"]
    if "widgets" in body:
        kwargs["widgets_json"] = json.dumps(body["widgets"])
    dashboard = storage.update_dashboard(dashboard_id, uid, **kwargs)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return dashboard.to_dict()


@router.delete("/api/dashboards/{dashboard_id}")
async def delete_dashboard_endpoint(dashboard_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    storage.delete_dashboard(dashboard_id, uid)
    return {"deleted": True}


# ─── Phase 3: Goal Progress Timeline ────────────────────────────────────────

@router.get("/api/goals/{goal_id}/timeline")
async def get_goal_timeline_endpoint(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    snapshots = storage.get_goal_progress_timeline(goal_id)
    return [s.to_dict() for s in snapshots]


# ─── Phase 3: Export Reports ─────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/export")
async def export_goal_endpoint(goal_id: int, request: Request, format: str = Query("json")):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)

    if format == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["id", "title", "description", "status", "estimated_minutes",
                         "due_date", "assigned_to", "order_index", "tags", "created_at"])
        for t in tasks:
            td = t.to_dict()
            writer.writerow([
                td["id"], td["title"], td["description"], td["status"],
                td["estimated_minutes"], td.get("due_date", ""),
                td.get("assigned_to", ""), td["order_index"],
                ",".join(td.get("tags", [])),
                td.get("created_at", ""),
            ])
        csv_content = output.getvalue()
        return StreamingResponse(
            iter([csv_content]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="goal_{goal_id}_tasks.csv"'},
        )
    else:
        return {
            "goal": goal.to_dict(),
            "tasks": [t.to_dict() for t in tasks],
        }


# ─── Phase 3: Scheduled Reports ─────────────────────────────────────────────

@router.post("/api/reports/scheduled", status_code=201)
async def create_scheduled_report_endpoint(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    report = ScheduledReport(
        user_id=uid,
        report_type=body.get("report_type", "progress"),
        frequency=body.get("frequency", "weekly"),
        recipients_json=json.dumps(body.get("recipients", [])),
    )
    report = storage.create_scheduled_report(report)
    return report.to_dict()


@router.get("/api/reports/scheduled")
async def list_scheduled_reports_endpoint(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    reports = storage.list_scheduled_reports(uid)
    return [r.to_dict() for r in reports]


@router.delete("/api/reports/scheduled/{report_id}")
async def delete_scheduled_report_endpoint(report_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    storage.delete_scheduled_report(report_id, uid)
    return {"deleted": True}


# ─── Phase 3: Burndown / Burnup Chart Data ──────────────────────────────────

@router.get("/api/goals/{goal_id}/burndown")
async def get_burndown_endpoint(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    data = storage.get_burndown_data(goal_id)
    return data


# ─── Phase 3: Time Tracking Reports ─────────────────────────────────────────

@router.get("/api/goals/{goal_id}/time-report")
async def get_time_report_endpoint(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    report = storage.get_time_tracking_report(goal_id)
    return report



