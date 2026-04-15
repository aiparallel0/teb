"""Router for gamification endpoints — extracted from main.py."""
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
from teb import events
from teb import gamification, search, nlp_input
from teb.models import (
    Achievement, ActivityFeedEntry, CustomField, DashboardWidget, Goal, GoalTemplate, ProgressSnapshot, RecurrenceRule, Task, TimeEntry, UserXP,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["gamification"])


# ─── Gamification (WP-04) ────────────────────────────────────────────────────


@router.get("/api/users/me/xp")
async def get_user_xp(request: Request):
    """Get current user's XP, level, and streak."""
    uid = deps.require_user(request)
    uxp = storage.get_or_create_user_xp(uid)
    return uxp.to_dict()


@router.get("/api/users/me/achievements")
async def get_user_achievements(request: Request):
    """Get current user's achievements."""
    uid = deps.require_user(request)
    return [a.to_dict() for a in storage.list_achievements(uid)]


# ─── Semantic Search (WP-05) ─────────────────────────────────────────────────
from teb import search as teb_search  # noqa: E402


@router.get("/api/search")
async def search_all(request: Request, q: str = "", limit: int = 50, semantic: bool = False):
    """Search across all entities. Use ?semantic=true for AI-powered re-ranking."""
    uid = deps.require_user(request)
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    results = teb_search.quick_search(q, user_id=uid, limit=min(limit, 100), semantic=semantic)
    return {"query": q, "count": len(results), "results": results, "semantic": semantic}


@router.post("/api/search/reindex")
async def reindex_search(request: Request):
    """Rebuild the search index."""
    uid = deps.require_user(request)
    teb_search.init_search_index()
    counts = teb_search.reindex_all(user_id=uid)
    return {"status": "reindexed", "counts": counts}


# ─── Natural Language Task Input (WP-06) ─────────────────────────────────────
from teb import nlp_input  # noqa: E402


@router.post("/api/tasks/parse")
async def parse_task_text_endpoint(request: Request):
    """Parse natural language text into structured task fields."""
    uid = deps.require_user(request)
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    return {"parsed": nlp_input.parse_task_text(text), "original": text}


@router.post("/api/goals/{goal_id}/quick-add")
async def quick_add_task(goal_id: int, request: Request):
    """Parse natural language and create a task in one step."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    parsed = nlp_input.parse_task_text(text)
    task = Task(
        goal_id=goal_id, title=parsed.get("title", text),
        description=body.get("description", ""),
        estimated_minutes=parsed.get("estimated_minutes", 30),
        due_date=parsed.get("due_date", ""),
        depends_on=json.dumps(parsed.get("depends_on", [])),
        tags=",".join(parsed.get("tags", [])),
    )
    task = storage.create_task(task)
    return {"task": task.to_dict(), "parsed_from": parsed}


# ─── Goal Cloning / Templates (WP-07) ────────────────────────────────────────


@router.post("/api/goals/{goal_id}/clone")
async def clone_goal(goal_id: int, request: Request):
    """Clone a goal with all its tasks."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    new_title = body.get("title", f"{goal.title} (Copy)")
    new_goal = Goal(user_id=uid, title=new_title, description=goal.description,
                    status="drafting")
    new_goal = storage.create_goal(new_goal)
    tasks = storage.list_tasks(goal_id=goal_id)
    id_map: dict = {}
    for t in sorted(tasks, key=lambda x: x.order_index):
        old_id = t.id
        new_task = Task(goal_id=new_goal.id, title=t.title, description=t.description,
                        estimated_minutes=t.estimated_minutes, order_index=t.order_index,
                        due_date=t.due_date, tags=t.tags)
        if t.parent_id and t.parent_id in id_map:
            new_task.parent_id = id_map[t.parent_id]
        new_task = storage.create_task(new_task)
        id_map[old_id] = new_task.id
    return {"goal": new_goal.to_dict(), "tasks_cloned": len(id_map)}


# ─── Time Tracking (WP-08) ───────────────────────────────────────────────────


@router.post("/api/tasks/{task_id}/time")
async def log_time_entry(task_id: int, request: Request):
    """Log a time entry for a task."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    duration = body.get("duration_minutes", 0)
    if not isinstance(duration, (int, float)) or duration < 0:
        raise HTTPException(status_code=400, detail="duration_minutes must be non-negative")
    entry = TimeEntry(task_id=task_id, user_id=uid,
                      started_at=body.get("started_at", ""),
                      ended_at=body.get("ended_at", ""),
                      duration_minutes=int(duration),
                      note=body.get("note", ""))
    entry = storage.create_time_entry(entry)
    return entry.to_dict()


@router.get("/api/tasks/{task_id}/time")
async def get_time_entries(task_id: int, request: Request):
    """List time entries for a task."""
    uid = deps.require_user(request)
    entries = storage.list_time_entries(task_id)
    total = storage.get_task_total_time(task_id)
    return {"entries": [e.to_dict() for e in entries], "total_minutes": total}


# ─── Goal Activity Feed (WP-09) ──────────────────────────────────────────────


@router.get("/api/goals/{goal_id}/activity")
async def get_goal_activity(goal_id: int, request: Request):
    """Get activity feed for a goal from audit events."""
    uid = deps.require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    events = storage.list_audit_events(goal_id=goal_id, limit=50)
    return [e.to_dict() for e in events]


# ─── Task Recurrence (WP-10) ─────────────────────────────────────────────────


@router.post("/api/tasks/{task_id}/recurrence")
async def set_task_recurrence(task_id: int, request: Request):
    """Set or update recurrence rule for a task."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    freq = body.get("frequency", "weekly")
    if freq not in ("daily", "weekly", "monthly"):
        raise HTTPException(status_code=400, detail="frequency must be daily, weekly, or monthly")
    storage.delete_recurrence_rule(task_id)
    rule = RecurrenceRule(task_id=task_id, frequency=freq,
                          interval=body.get("interval", 1),
                          next_due=body.get("next_due", ""),
                          end_date=body.get("end_date", ""))
    rule = storage.create_recurrence_rule(rule)
    return rule.to_dict()


@router.get("/api/tasks/{task_id}/recurrence")
async def get_task_recurrence(task_id: int, request: Request):
    """Get recurrence rule for a task."""
    uid = deps.require_user(request)
    rule = storage.get_recurrence_rule(task_id)
    if not rule:
        return {"recurrence": None}
    return rule.to_dict()


@router.delete("/api/tasks/{task_id}/recurrence")
async def delete_task_recurrence(task_id: int, request: Request):
    """Remove recurrence rule from a task."""
    uid = deps.require_user(request)
    storage.delete_recurrence_rule(task_id)
    return {"deleted": True}


# ─── Custom Fields (WP-12) ───────────────────────────────────────────────────


@router.post("/api/tasks/{task_id}/fields")
async def add_custom_field(task_id: int, request: Request):
    """Add a custom field to a task.

    Supports basic types (text, number, date, url) and relational types:
    - relation: links to another task. Pass ``field_value`` as the target task ID.
    - rollup: aggregates across related tasks. Pass ``config`` with aggregation settings.
    - formula: computed field. Pass ``config`` with formula_type.
    """
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    name = body.get("field_name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="field_name is required")
    field_type = body.get("field_type", "text")
    valid_types = {"text", "number", "date", "url", "relation", "rollup", "formula"}
    if field_type not in valid_types:
        raise HTTPException(status_code=400, detail=f"field_type must be one of {valid_types}")
    config = body.get("config", {})
    cf = CustomField(task_id=task_id, field_name=name,
                     field_value=body.get("field_value", ""),
                     field_type=field_type,
                     config_json=json.dumps(config) if config else "{}")
    cf = storage.create_custom_field(cf)
    return cf.to_dict()


@router.get("/api/tasks/{task_id}/fields")
async def list_custom_fields_endpoint(task_id: int, request: Request):
    """List custom fields for a task, with resolved values for computed types."""
    uid = deps.require_user(request)
    fields = storage.list_custom_fields(task_id)
    result = []
    for f in fields:
        d = f.to_dict()
        if f.field_type in ("relation", "rollup", "formula"):
            d["resolved_value"] = storage.resolve_custom_field_value(f)
        result.append(d)
    return result


@router.get("/api/fields/{field_id}/resolve")
async def resolve_custom_field(field_id: int, request: Request):
    """Resolve (compute) the value of a relation/rollup/formula field."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    cf = storage.get_custom_field(field_id)
    if not cf:
        raise HTTPException(status_code=404, detail="Custom field not found")
    resolved = storage.resolve_custom_field_value(cf)
    d = cf.to_dict()
    d["resolved_value"] = resolved
    return d


@router.delete("/api/fields/{field_id}")
async def delete_custom_field_endpoint(field_id: int, request: Request):
    """Delete a custom field."""
    uid = deps.require_user(request)
    storage.delete_custom_field(field_id)
    return {"deleted": True}


# ─── Dashboard Widgets (WP-20) ───────────────────────────────────────────────


@router.get("/api/users/me/dashboard")
async def get_dashboard(request: Request):
    """Get user's dashboard widget configuration."""
    uid = deps.require_user(request)
    widgets = storage.list_dashboard_widgets(uid)
    if not widgets:
        # Return default widgets
        defaults = [
            {"widget_type": "progress_chart", "position": 0},
            {"widget_type": "recent_tasks", "position": 1},
            {"widget_type": "streak", "position": 2},
            {"widget_type": "xp_bar", "position": 3},
        ]
        return {"widgets": defaults, "is_default": True}
    return {"widgets": [w.to_dict() for w in widgets], "is_default": False}


@router.post("/api/users/me/dashboard/widgets")
async def add_dashboard_widget(request: Request):
    """Add a widget to user's dashboard."""
    uid = deps.require_user(request)
    body = await request.json()
    wtype = body.get("widget_type", "").strip()
    valid_types = {"progress_chart", "recent_tasks", "streak", "xp_bar",
                   "activity_feed", "calendar", "blockers", "priority_board"}
    if wtype not in valid_types:
        raise HTTPException(status_code=400, detail=f"widget_type must be one of {valid_types}")
    widget = DashboardWidget(user_id=uid, widget_type=wtype,
                             position=body.get("position", 0),
                             config_json=json.dumps(body.get("config", {})))
    widget = storage.create_dashboard_widget(widget)
    return widget.to_dict()


@router.put("/api/users/me/dashboard/widgets/{widget_id}")
async def update_widget(widget_id: int, request: Request):
    """Update a dashboard widget."""
    uid = deps.require_user(request)
    body = await request.json()
    kwargs: dict = {}
    if "position" in body:
        kwargs["position"] = body["position"]
    if "config" in body:
        kwargs["config_json"] = json.dumps(body["config"])
    if "enabled" in body:
        kwargs["enabled"] = body["enabled"]
    widget = storage.update_dashboard_widget(widget_id, uid, **kwargs)
    if not widget:
        raise HTTPException(status_code=404, detail="Widget not found")
    return widget.to_dict()


@router.delete("/api/users/me/dashboard/widgets/{widget_id}")
async def delete_widget(widget_id: int, request: Request):
    """Delete a dashboard widget."""
    uid = deps.require_user(request)
    storage.delete_dashboard_widget(widget_id, uid)
    return {"deleted": True}



