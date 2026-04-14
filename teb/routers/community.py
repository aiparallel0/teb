"""Router for community endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import scheduler
from teb import plugins as plugins_mod
from teb import decomposer
from teb import workload, success_graph, reporting
from teb.models import (
    ContentBlock, GoalTemplate,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["community"])


# ─── Phase 7: Documentation & Community endpoints ────────────────────────────


@router.get("/api/docs/changelog", tags=["documentation"])
async def get_changelog():
    """Return the project changelog."""
    changelog_path = Path(__file__).parent.parent / "CHANGELOG.md"
    if changelog_path.exists():
        return {"content": changelog_path.read_text(encoding="utf-8")}
    return {"content": "No changelog available."}


@router.get("/api/community/links", tags=["community"])
async def community_links():
    """List community channels."""
    return {"links": [
        {"name": "GitHub Discussions", "url": "https://github.com/aiparallel0/teb/discussions", "type": "forum"},
        {"name": "Discord", "url": "https://discord.gg/teb", "type": "chat"},
        {"name": "Twitter/X", "url": "https://x.com/teb_app", "type": "social"},
    ]}


class _TemplateGalleryBody(BaseModel):
    name: str
    description: str = ""
    category: str = ""
    template: dict = {}


@router.get("/api/templates/gallery", tags=["community"])
async def list_template_gallery_endpoint(category: str = ""):
    entries = storage.list_template_gallery(category)
    return {"templates": [e.to_dict() for e in entries]}


@router.get("/api/templates/gallery/{entry_id}", tags=["community"])
async def get_template_gallery_entry_endpoint(entry_id: int):
    entry = storage.get_template_gallery_entry(entry_id)
    if not entry:
        raise HTTPException(404, "Template not found")
    return entry.to_dict()


@router.post("/api/templates/gallery", tags=["community"])
async def create_template_gallery_entry_endpoint(body: _TemplateGalleryBody, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    user = storage.get_user(uid)
    from teb.models import TemplateGalleryEntry
    entry = TemplateGalleryEntry(
        name=body.name, description=body.description,
        author=user.email if user else "", category=body.category,
        template_json=json.dumps(body.template),
    )
    eid = storage.create_template_gallery_entry(entry)
    return {"id": eid}


@router.get("/api/community/plugins", tags=["community"])
async def community_plugins():
    """List community-built plugins."""
    return {"plugins": [], "message": "Community plugin directory — submit yours via PR!"}


class _BlogPostBody(BaseModel):
    title: str
    slug: str
    content: str = ""
    published: bool = False


@router.get("/api/blog", tags=["community"])
async def list_blog_posts_endpoint():
    posts = storage.list_blog_posts(published_only=True)
    return {"posts": [p.to_dict() for p in posts]}


@router.get("/api/blog/{slug}", tags=["community"])
async def get_blog_post_endpoint(slug: str):
    post = storage.get_blog_post_by_slug(slug)
    if not post:
        raise HTTPException(404, "Post not found")
    return post.to_dict()


@router.post("/api/blog", tags=["community"])
async def create_blog_post_endpoint(body: _BlogPostBody, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(403, "Admin only")
    from teb.models import BlogPost
    post = BlogPost(title=body.title, slug=body.slug, content=body.content,
                    author=user.email, published=body.published)
    pid = storage.create_blog_post(post)
    return {"id": pid}


class _RoadmapBody(BaseModel):
    title: str
    description: str = ""
    status: str = "planned"
    category: str = ""
    target_date: str = ""


@router.get("/api/roadmap", tags=["community"])
async def list_roadmap_endpoint(status: str = ""):
    items = storage.list_roadmap_items(status)
    return {"items": [i.to_dict() for i in items]}


@router.post("/api/roadmap", tags=["community"])
async def create_roadmap_endpoint(body: _RoadmapBody, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(403, "Admin only")
    from teb.models import RoadmapItem
    item = RoadmapItem(title=body.title, description=body.description,
                       status=body.status, category=body.category, target_date=body.target_date)
    iid = storage.create_roadmap_item(item)
    return {"id": iid}


@router.put("/api/roadmap/{item_id}", tags=["community"])
async def update_roadmap_endpoint(item_id: int, body: _RoadmapBody, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(403, "Admin only")
    storage.update_roadmap_item(item_id, title=body.title, description=body.description,
                                status=body.status, category=body.category, target_date=body.target_date)
    return {"updated": True}


@router.post("/api/roadmap/{item_id}/vote", tags=["community"])
async def vote_roadmap_endpoint(item_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    ok = storage.cast_feature_vote(uid, item_id)
    return {"voted": ok}


@router.delete("/api/roadmap/{item_id}/vote", tags=["community"])
async def unvote_roadmap_endpoint(item_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    ok = storage.remove_feature_vote(uid, item_id)
    return {"removed": ok}


# ═══════════════════════════════════════════════════════════════════════════════
# Bridging Plan: Risk, Scheduling, Reporting, Workload, Gamification Social
# ═══════════════════════════════════════════════════════════════════════════════


# ─── Phase 1: Risk Assessment & Triage ───────────────────────────────────────

@router.get("/api/tasks/{task_id}/risk", tags=["risk"])
async def get_task_risk(task_id: int, request: Request):
    """Get risk assessment for a specific task."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_task_for_user(task_id, uid)
    result = decomposer.estimate_risk(task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.post("/api/goals/{goal_id}/triage", tags=["risk"])
async def triage_goal_tasks(goal_id: int, request: Request):
    """Auto-prioritize all tasks in a goal using AI (with template fallback)."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    results = decomposer.triage_tasks(goal_id)
    return {"goal_id": goal_id, "triage": results, "count": len(results)}


# ─── Phase 2: Persistent Auto-Scheduling ────────────────────────────────────

@router.post("/api/goals/{goal_id}/auto-schedule", tags=["scheduling"])
async def auto_schedule_goal(goal_id: int, request: Request):
    """Auto-schedule tasks into time blocks and persist the schedule."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    if not tasks:
        return {"goal_id": goal_id, "schedules": [], "count": 0}

    # Clear existing schedule for this goal
    storage.delete_task_schedules(goal_id)

    # Generate schedule using existing scheduler
    schedule_data = scheduler.auto_schedule_tasks(tasks)

    # Persist each schedule entry
    from teb.models import TaskSchedule
    persisted = []
    for entry in schedule_data:
        sched = TaskSchedule(
            task_id=entry["task_id"],
            goal_id=goal_id,
            user_id=uid,
            scheduled_start=entry["scheduled_start"],
            scheduled_end=entry["scheduled_end"],
            calendar_slot=entry.get("day_slot", 1),
        )
        saved = storage.create_task_schedule(sched)
        persisted.append(saved.to_dict())

    return {"goal_id": goal_id, "schedules": persisted, "count": len(persisted)}


@router.get("/api/users/me/schedule", tags=["scheduling"])
async def get_user_schedule(request: Request):
    """Get all scheduled tasks for the current user."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    schedules = storage.list_task_schedules(user_id=uid)
    return {"schedules": [s.to_dict() for s in schedules], "count": len(schedules)}


# ─── Phase 3: Automated Progress Reporting ───────────────────────────────────
from teb import reporting  # noqa: E402


@router.post("/api/goals/{goal_id}/report", tags=["reporting"])
async def generate_report(goal_id: int, request: Request):
    """Generate a progress report for a goal."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    try:
        report = reporting.generate_progress_report(goal_id, uid)
        # Emit SSE event
        from teb import events
        events.emit_report_generated(uid, goal_id, report.id or 0, report.summary)
        return report.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.get("/api/goals/{goal_id}/reports", tags=["reporting"])
async def list_reports(goal_id: int, request: Request):
    """List all progress reports for a goal."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    reports = storage.list_progress_reports(goal_id)
    return {"reports": [r.to_dict() for r in reports], "count": len(reports)}


# ─── Phase 4: Workload Balancing ─────────────────────────────────────────────
from teb import workload  # noqa: E402


@router.get("/api/users/me/workload", tags=["workload"])
async def get_workload(request: Request):
    """Get workload analysis for the current user."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    return workload.get_user_capacity(uid)


@router.post("/api/goals/{goal_id}/rebalance", tags=["workload"])
async def rebalance_goal(goal_id: int, request: Request):
    """Analyze and suggest workload rebalancing for a goal."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    return workload.balance_workload(goal_id, uid)


# ─── Phase 6: Social Gamification ────────────────────────────────────────────


# ─── Success Graph ────────────────────────────────────────────────────────────

@router.get("/api/success-graph/stats", tags=["success-graph"])
async def success_graph_stats(request: Request, goal_type: Optional[str] = Query(default=None)):
    """Get statistics about the success path graph."""
    deps.require_user(request)
    from teb.success_graph import get_graph_stats
    return get_graph_stats(goal_type)


@router.get("/api/success-graph/path", tags=["success-graph"])
async def success_graph_best_path(request: Request, goal_type: str = Query(...)):
    """Get the highest-weight execution path for a goal type."""
    deps.require_user(request)
    from teb.success_graph import get_best_path
    path = get_best_path(goal_type)
    return {"goal_type": goal_type, "path": path, "steps": len(path)}


@router.get("/api/success-graph/paths", tags=["success-graph"])
async def success_graph_top_paths(
    request: Request,
    goal_type: str = Query(...),
    top_k: int = Query(default=3, ge=1, le=10),
):
    """Get the top-K proven execution paths for a goal type."""
    deps.require_user(request)
    from teb.success_graph import get_top_paths
    paths = get_top_paths(goal_type, top_k=top_k)
    return {"goal_type": goal_type, "paths": paths, "count": len(paths)}



