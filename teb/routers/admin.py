"""Admin API router — extracted from main.py."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from teb import storage
from teb.models import (
    AuditEvent, Goal, GoalTemplate, Integration, Milestone, Task,
)
from teb.routers import deps


# ─── Schemas ──────────────────────────────────────────────────────────────────

class AdminUserUpdate(BaseModel):
    role: Optional[str] = None
    locked_until: Optional[str] = None
    email_verified: Optional[bool] = None


router = APIRouter(tags=["admin"])


# ─── Admin API ────────────────────────────────────────────────────────────────

@router.get("/api/admin/users")
async def admin_list_users(request: Request):
    """Admin: list all users with goal and task counts."""
    deps.require_admin(request)
    users = storage.list_all_users()
    result = []
    for u in users:
        goals = storage.list_goals(user_id=u.id)
        task_count = sum(len(storage.list_tasks(goal_id=g.id)) for g in goals)
        d = u.to_dict()
        d["failed_login_attempts"] = u.failed_login_attempts
        d["locked_until"] = u.locked_until.isoformat() if u.locked_until else None
        d["goals_count"] = len(goals)
        d["tasks_count"] = task_count
        result.append(d)
    return result


@router.get("/api/admin/users/{user_id}")
async def admin_get_user(user_id: int, request: Request):
    """Admin: get a single user's detail plus their goals."""
    deps.require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    goals = storage.list_goals(user_id=user_id)
    d = user.to_dict()
    d["failed_login_attempts"] = user.failed_login_attempts
    d["locked_until"] = user.locked_until.isoformat() if user.locked_until else None
    d["goals"] = [g.to_dict() for g in goals]
    return d


@router.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, body: AdminUserUpdate, request: Request):
    """Admin: update user role, lock status, or email_verified."""
    deps.require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if body.role is not None:
        if body.role not in ("user", "admin"):
            raise HTTPException(status_code=422, detail="role must be 'user' or 'admin'")
        user.role = body.role
    if body.email_verified is not None:
        user.email_verified = body.email_verified
    if body.locked_until is not None:
        if body.locked_until in ("null", ""):
            user.locked_until = None
        else:
            try:
                user.locked_until = datetime.fromisoformat(body.locked_until)
            except ValueError:
                raise HTTPException(status_code=422, detail="locked_until must be a valid ISO datetime or 'null'")
    storage.update_user(user)
    d = user.to_dict()
    d["locked_until"] = user.locked_until.isoformat() if user.locked_until else None
    return d


@router.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    """Admin: delete a user account and all their data."""
    admin_id = deps.require_admin(request)
    if user_id == admin_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    storage.delete_user(user_id)
    return {"deleted": user_id}


@router.get("/api/admin/stats")
async def admin_get_stats(request: Request):
    """Admin: return aggregate platform statistics."""
    deps.require_admin(request)
    return storage.get_system_stats()


@router.get("/api/admin/integrations")
async def admin_list_integrations(request: Request):
    """Admin: list all integrations in the DB with full detail."""
    deps.require_admin(request)
    integrations_list = storage.list_integrations()
    return [i.to_dict() for i in integrations_list]


@router.post("/api/admin/integrations", status_code=201)
async def admin_create_integration(request: Request):
    """Admin: add a new integration entry to the DB catalog."""
    deps.require_admin(request)
    import json as _json
    body = await request.json()
    service_name = body.get("service_name", "").strip()
    if not service_name:
        raise HTTPException(status_code=422, detail="service_name is required")
    existing = storage.get_integration(service_name)
    if existing:
        raise HTTPException(status_code=409, detail="Integration already exists")
    from teb.models import Integration as _Integration
    caps = body.get("capabilities", [])
    endpoints = body.get("common_endpoints", [])
    if isinstance(caps, list):
        caps_json = _json.dumps(caps)
    elif caps:
        caps_json = _json.dumps([c.strip() for c in caps.split(",")])
    else:
        caps_json = "[]"
    if isinstance(endpoints, (list, dict)):
        endpoints_json = _json.dumps(endpoints)
    else:
        endpoints_json = endpoints or "[]"
    integ = _Integration(
        service_name=service_name,
        category=body.get("category", "general"),
        base_url=body.get("base_url", ""),
        auth_type=body.get("auth_type", "api_key"),
        auth_header=body.get("auth_header", "Authorization"),
        docs_url=body.get("docs_url", ""),
        capabilities=caps_json,
        common_endpoints=endpoints_json,
    )
    created = storage.create_integration(integ)
    return created.to_dict()


@router.delete("/api/admin/integrations/{name}")
async def admin_delete_integration(name: str, request: Request):
    """Admin: remove an integration from the DB by service_name."""
    deps.require_admin(request)
    existing = storage.get_integration(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    storage.delete_integration(existing.id)
    return {"deleted": name}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Persistent Agent Goal Memory
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/goals/{goal_id}/agent-memory")
async def list_goal_agent_memories(goal_id: int, request: Request):
    """List all agent working memories for a goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    mems = storage.list_agent_goal_memories(goal_id)
    return {"memories": [m.to_dict() for m in mems]}


@router.get("/api/goals/{goal_id}/agent-memory/{agent_type}")
async def get_goal_agent_memory(goal_id: int, agent_type: str, request: Request):
    """Get a specific agent's working memory for a goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    mem = storage.get_or_create_agent_goal_memory(agent_type, goal_id)
    return mem.to_dict()


@router.post("/api/goals/{goal_id}/agent-memory/prune")
async def prune_goal_agent_memory(goal_id: int, request: Request):
    """Prune overly long agent memories for a goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    storage.prune_agent_goal_memory(goal_id)
    return {"pruned": True, "goal_id": goal_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: Goal Hierarchy (Sub-goals & Milestones)
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/api/goals/{goal_id}/sub-goals", status_code=201)
async def create_sub_goal(goal_id: int, request: Request):
    """Create a sub-goal under a parent goal."""
    uid = deps.require_user(request)
    
    parent = deps.get_goal_for_user(goal_id, uid)
    body = await request.json()
    sub = Goal(
        title=body.get("title", ""),
        description=body.get("description", ""),
        user_id=uid,
        parent_goal_id=parent.id,
    )
    sub = storage.create_goal(sub)

    from teb import events as _events  # noqa: E402
    _events.emit_goal_updated(uid, goal_id, f"sub_goal_created:{sub.id}")

    storage.create_audit_event(AuditEvent(
        goal_id=goal_id, event_type="sub_goal_created",
        actor_type="human", actor_id=str(uid),
        context_json=json.dumps({"sub_goal_id": sub.id, "title": sub.title}),
    ))
    return sub.to_dict()


@router.get("/api/goals/{goal_id}/sub-goals")
async def list_sub_goals(goal_id: int, request: Request):
    """List sub-goals of a parent goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    subs = storage.list_sub_goals(goal_id)
    return {"sub_goals": [s.to_dict() for s in subs]}


@router.get("/api/goals/{goal_id}/hierarchy")
async def get_goal_hierarchy(goal_id: int, request: Request):
    """Get the full goal hierarchy: parent, sub-goals, milestones, and task counts."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    sub_goals = storage.list_sub_goals(goal_id)
    milestones = storage.list_milestones(goal_id)
    tasks = storage.list_tasks(goal_id=goal_id)
    done_tasks = sum(1 for t in tasks if t.status == "done")

    sub_goal_data = []
    for sg in sub_goals:
        sg_tasks = storage.list_tasks(goal_id=sg.id)
        sg_done = sum(1 for t in sg_tasks if t.status == "done")
        sg_milestones = storage.list_milestones(sg.id) if sg.id else []
        sub_goal_data.append({
            **sg.to_dict(),
            "task_count": len(sg_tasks),
            "tasks_done": sg_done,
            "milestones": [m.to_dict() for m in sg_milestones],
        })

    return {
        "goal": goal.to_dict(),
        "sub_goals": sub_goal_data,
        "milestones": [m.to_dict() for m in milestones],
        "tasks": {"total": len(tasks), "done": done_tasks},
    }


@router.post("/api/goals/{goal_id}/milestones", status_code=201)
async def create_milestone(goal_id: int, request: Request):
    """Create a milestone for a goal."""
    uid = deps.require_user(request)
    
    deps.get_goal_for_user(goal_id, uid)
    body = await request.json()
    ms = Milestone(
        goal_id=goal_id,
        title=body.get("title", ""),
        target_metric=body.get("target_metric", ""),
        target_value=body.get("target_value", 0),
        deadline=body.get("deadline", ""),
    )
    ms = storage.create_milestone(ms)

    storage.create_audit_event(AuditEvent(
        goal_id=goal_id, event_type="milestone_created",
        actor_type="human", actor_id=str(uid),
        context_json=json.dumps({"milestone_id": ms.id, "title": ms.title}),
    ))
    return ms.to_dict()


@router.get("/api/goals/{goal_id}/milestones")
async def list_milestones(goal_id: int, request: Request):
    """List milestones for a goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    milestones = storage.list_milestones(goal_id)
    return {"milestones": [m.to_dict() for m in milestones]}


@router.patch("/api/milestones/{milestone_id}")
async def update_milestone(milestone_id: int, request: Request):
    """Update a milestone (progress, status, etc.)."""
    uid = deps.require_user(request)
    ms = storage.get_milestone(milestone_id)
    if not ms:
        raise HTTPException(status_code=404, detail="Milestone not found")
    deps.get_goal_for_user(ms.goal_id, uid)

    body = await request.json()
    if "title" in body:
        ms.title = body["title"]
    if "current_value" in body:
        ms.current_value = body["current_value"]
    if "target_value" in body:
        ms.target_value = body["target_value"]
    if "status" in body:
        ms.status = body["status"]
    if "deadline" in body:
        ms.deadline = body["deadline"]

    # Auto-detect achievement
    if ms.target_value > 0 and ms.current_value >= ms.target_value and ms.status == "pending":
        ms.status = "achieved"
        from teb import events as _events  # noqa: E402
        _events.emit_goal_milestone(uid, ms.goal_id, ms.title, "achieved")
        storage.create_audit_event(AuditEvent(
            goal_id=ms.goal_id, event_type="milestone_achieved",
            actor_type="system", actor_id="milestone_tracker",
            context_json=json.dumps({"milestone_id": ms.id, "title": ms.title}),
        ))

    ms = storage.update_milestone(ms)
    return ms.to_dict()


# ═══════════════════════════════════════════════════════════════════════════════
# Step 4: Real-time Event Streaming (SSE)
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/events/stream")
async def sse_stream(request: Request,
                     last_event_id: Optional[str] = Query(default=None, alias="Last-Event-ID")):
    """Server-Sent Events stream for real-time updates.

    Clients should use EventSource to connect. Supports Last-Event-ID for reconnection.
    """
    uid = deps.require_user(request)

    # Also check the standard SSE header
    if not last_event_id:
        last_event_id = request.headers.get("Last-Event-ID")

    from teb import events as _events  # noqa: E402
    return StreamingResponse(
        _events.stream_events(uid, last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/goals/{goal_id}/stream")
async def goal_sse_stream(
    goal_id: int,
    request: Request,
    last_event_id: Optional[str] = Query(default=None, alias="Last-Event-ID"),
):
    """Server-Sent Events stream filtered to a specific goal.

    Emits task_started, task_progress, task_completed, and orchestration_complete
    events for real-time orchestration visibility.
    """
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)

    if not last_event_id:
        last_event_id = request.headers.get("Last-Event-ID")

    from teb import events as _events  # noqa: E402

    async def _filtered_stream():
        """Filter the user's event stream to only events for this goal."""
        async for chunk in _events.stream_events(uid, last_event_id):
            # Pass through heartbeats
            if chunk.startswith(": heartbeat"):
                yield chunk
                continue
            # Filter to goal-related events
            if f'"goal_id": {goal_id}' in chunk or f'"goal_id":{goal_id}' in chunk:
                yield chunk

    return StreamingResponse(
        _filtered_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/events/status")
async def sse_status(request: Request):
    """Get SSE event bus status."""
    deps.require_user(request)
    from teb import events as _events  # noqa: E402
    return {
        "subscribers": _events.event_bus.subscriber_count,
        "backlog_size": len(_events.event_bus._backlog),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Step 5: Goal Template Marketplace
# ═══════════════════════════════════════════════════════════════════════════════


@router.post("/api/templates/export/{goal_id}", status_code=201)
async def export_goal_template(goal_id: int, request: Request):
    """Export a completed goal as a reusable template (sanitized of personal data)."""
    uid = deps.require_user(request)
    
    goal = deps.get_goal_for_user(goal_id, uid)

    tasks = storage.list_tasks(goal_id=goal_id)
    milestones = storage.list_milestones(goal_id)
    metrics = storage.list_outcome_metrics(goal_id)

    # Sanitize tasks — keep structure, remove personal details
    task_templates = [
        {"title": t.title, "description": t.description,
         "estimated_minutes": t.estimated_minutes, "order_index": t.order_index}
        for t in tasks
    ]
    milestone_templates = [
        {"title": m.title, "target_metric": m.target_metric,
         "target_value": m.target_value}
        for m in milestones
    ]
    services = []  # Could be enriched from execution logs in future

    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}

    tpl = GoalTemplate(
        title=body.get("title", goal.title),
        description=body.get("description", goal.description),
        goal_type=storage._detect_goal_type(goal.title, goal.description),
        category=body.get("category", "general"),
        skill_level=body.get("skill_level", "any"),
        tasks_json=json.dumps(task_templates),
        milestones_json=json.dumps(milestone_templates),
        services_json=json.dumps(services),
        outcome_type=metrics[0].unit if metrics else "",
        estimated_days=body.get("estimated_days", 0),
        source_goal_id=goal_id,
        author_id=uid,
    )
    tpl = storage.create_goal_template(tpl)

    storage.create_audit_event(AuditEvent(
        goal_id=goal_id, event_type="template_exported",
        actor_type="human", actor_id=str(uid),
        context_json=json.dumps({"template_id": tpl.id}),
    ))
    return tpl.to_dict()


@router.post("/api/templates/import/{template_id}", status_code=201)
async def import_goal_template(template_id: int, request: Request):
    """Import a template to create a new goal pre-populated with tasks and milestones."""
    uid = deps.require_user(request)
    

    tpl = storage.get_goal_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    # Create the goal
    goal = Goal(
        title=tpl.title,
        description=tpl.description,
        user_id=uid,
        status="decomposed",
    )
    goal = storage.create_goal(goal)

    # Create tasks from template
    try:
        task_templates = json.loads(tpl.tasks_json)
    except (json.JSONDecodeError, TypeError):
        task_templates = []
    for i, tt in enumerate(task_templates):
        storage.create_task(Task(
            goal_id=goal.id or 0,
            title=tt.get("title", ""),
            description=tt.get("description", ""),
            estimated_minutes=tt.get("estimated_minutes", 30),
            order_index=tt.get("order_index", i),
        ))

    # Create milestones from template
    try:
        ms_templates = json.loads(tpl.milestones_json)
    except (json.JSONDecodeError, TypeError):
        ms_templates = []
    for mt in ms_templates:
        storage.create_milestone(Milestone(
            goal_id=goal.id or 0,
            title=mt.get("title", ""),
            target_metric=mt.get("target_metric", ""),
            target_value=mt.get("target_value", 0),
        ))

    storage.increment_template_usage(template_id)

    storage.create_audit_event(AuditEvent(
        goal_id=goal.id, event_type="template_imported",
        actor_type="human", actor_id=str(uid),
        context_json=json.dumps({"template_id": template_id}),
    ))
    return {"goal": goal.to_dict(), "template_id": template_id}


@router.get("/api/templates")
async def list_templates(request: Request,
                         goal_type: Optional[str] = Query(default=None),
                         category: Optional[str] = Query(default=None),
                         skill_level: Optional[str] = Query(default=None)):
    """Browse the goal template marketplace."""
    deps.require_user(request)
    templates = storage.list_goal_templates(goal_type=goal_type, category=category,
                                            skill_level=skill_level)
    return {"templates": [t.to_dict() for t in templates], "total": len(templates)}


@router.get("/api/templates/{template_id}")
async def get_template(template_id: int, request: Request):
    """Get details of a specific template."""
    deps.require_user(request)
    tpl = storage.get_goal_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl.to_dict()


@router.post("/api/templates/{template_id}/rate")
async def rate_template(template_id: int, request: Request):
    """Rate a template (1-5 stars)."""
    deps.require_user(request)
    body = await request.json()
    rating = body.get("rating", 0)
    if not (1 <= rating <= 5):
        raise HTTPException(status_code=400, detail="Rating must be between 1 and 5")
    updated = storage.rate_goal_template(template_id, rating)
    if not updated:
        raise HTTPException(status_code=404, detail="Template not found")
    return updated.to_dict()


# ═══════════════════════════════════════════════════════════════════════════════
# Step 6: Structured Execution Audit Trail
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/goals/{goal_id}/audit")
async def get_goal_audit_trail(goal_id: int, request: Request,
                               event_type: Optional[str] = Query(default=None),
                               limit: int = Query(default=100)):
    """Get the full audit trail for a goal — immutable lifecycle events."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    events = storage.list_audit_events(goal_id=goal_id, event_type=event_type, limit=limit)
    return {"events": [e.to_dict() for e in events], "total": len(events)}


@router.get("/api/audit/events")
async def list_all_audit_events(request: Request,
                                event_type: Optional[str] = Query(default=None),
                                limit: int = Query(default=100)):
    """List audit events across all goals (admin view)."""
    deps.require_admin(request)
    events = storage.list_audit_events(event_type=event_type, limit=limit)
    return {"events": [e.to_dict() for e in events], "total": len(events)}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 7: MCP Server Exposure
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/mcp/info")
async def mcp_server_info(request: Request):
    """MCP server metadata — tool definitions for AI coding assistants."""
    deps.require_user(request)
    from teb import mcp_server  # noqa: E402
    return mcp_server.get_server_info()


@router.post("/api/mcp/tools/call")
async def mcp_tool_call(request: Request):
    """Execute an MCP tool call."""
    uid = deps.require_user(request)
    body = await request.json()
    tool_name = body.get("name", "")
    arguments = body.get("arguments", {})
    from teb import mcp_server  # noqa: E402
    try:
        result = mcp_server.handle_tool_call(tool_name, arguments, user_id=uid)
    except Exception:
        logger.exception("MCP tool call failed: %s", tool_name)
        result = {"error": "Internal error processing tool call"}
    return result


@router.get("/api/mcp/tools")
async def mcp_list_tools(request: Request):
    """List available MCP tools."""
    deps.require_user(request)
    from teb import mcp_server  # noqa: E402
    return {"tools": mcp_server.MCP_TOOLS}

