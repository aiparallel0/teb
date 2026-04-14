"""Router for tasks endpoints — extracted from main.py."""
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
from teb import decomposer
from teb import messaging
from teb import executor, decomposer
from teb import dag
from teb.models import (
    ExecutionLog, Task, TaskArtifact, TaskComment,
)

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 500
_MAX_DESCRIPTION_LEN = 10000
_MAX_TAG_LEN = 1000

router = APIRouter(tags=["tasks"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class TaskPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = Field(None, max_length=_MAX_DESCRIPTION_LEN)
    title: Optional[str] = Field(None, max_length=_MAX_TITLE_LEN)
    order_index: Optional[int] = Field(None, ge=0)
    due_date: Optional[str] = Field(None, max_length=30)
    depends_on: Optional[list] = None
    tags: Optional[str] = Field(None, max_length=_MAX_TAG_LEN)
    priority: Optional[str] = None

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            valid = {"todo", "in_progress", "done", "skipped", "executing", "failed"}
            if v not in valid:
                raise ValueError(f"status must be one of: {', '.join(sorted(valid))}")
        return v



class TaskCreate(BaseModel):
    goal_id: int
    title: str = Field(..., min_length=1, max_length=_MAX_TITLE_LEN, description="Task title")
    description: str = Field("", max_length=_MAX_DESCRIPTION_LEN)
    estimated_minutes: int = Field(30, ge=1, le=10080, description="Estimated minutes (1 min to 7 days)")
    parent_id: Optional[int] = None
    due_date: Optional[str] = Field(None, max_length=30)
    depends_on: Optional[list] = None
    tags: Optional[str] = Field(None, max_length=_MAX_TAG_LEN)



class CredentialCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., min_length=1, max_length=2000)
    auth_header: str = Field("Authorization", max_length=200)
    auth_value: str = Field("", max_length=5000)
    description: str = Field("", max_length=1000)





# ─── Tasks ────────────────────────────────────────────────────────────────────

@router.get("/api/tasks", tags=["tasks"])
async def list_tasks(
    request: Request,
    goal_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
    page: Optional[int] = Query(default=None, ge=1, description="Page number (enables pagination)"),
    per_page: Optional[int] = Query(default=None, ge=1, le=100, description="Items per page"),
):
    """List tasks with optional filtering by goal and status.

    When `page` or `per_page` is provided, returns a paginated response.
    Otherwise returns a plain list for backward compatibility.
    """
    uid = deps.require_user(request)
    if goal_id is not None:
        deps.get_goal_for_user(goal_id, uid)  # ownership check
    all_tasks = [t.to_dict() for t in storage.list_tasks(goal_id=goal_id, status=status)]
    if page is not None or per_page is not None:
        return deps.paginate(all_tasks, page=page or 1, per_page=per_page or _DEFAULT_PAGE_SIZE)
    return all_tasks


@router.post("/api/tasks", status_code=201)
async def create_task_manual(body: TaskCreate, request: Request):
    """Create a custom user task (not from decomposition)."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(body.goal_id, uid)
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    if body.parent_id is not None:
        parent = storage.get_task(body.parent_id)
        if not parent:
            raise HTTPException(status_code=404, detail="Parent task not found")
        if parent.goal_id != body.goal_id:
            raise HTTPException(status_code=422, detail="Parent task belongs to a different goal")
    # Determine order_index: append after existing siblings
    existing = storage.list_tasks(goal_id=body.goal_id)
    siblings = [t for t in existing if t.parent_id == body.parent_id]
    next_order = max((s.order_index for s in siblings), default=-1) + 1
    task = Task(
        goal_id=body.goal_id,
        parent_id=body.parent_id,
        title=title,
        description=body.description.strip(),
        estimated_minutes=max(1, body.estimated_minutes),
        order_index=next_order,
    )
    if body.due_date is not None:
        task.due_date = body.due_date
    if body.depends_on is not None:
        task.depends_on = json.dumps(body.depends_on)
    if body.tags is not None:
        task.tags = body.tags
    task = storage.create_task(task)
    from teb import events as _events
    _events.event_bus.publish(uid, "task_created", {"task_id": task.id, "title": task.title, "goal_id": task.goal_id})
    return task.to_dict()


@router.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, body: TaskPatch, request: Request):
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid, require_role="editor")

    valid_statuses = {"todo", "in_progress", "done", "skipped", "executing", "failed"}
    if body.status is not None:
        if body.status not in valid_statuses:
            raise HTTPException(status_code=422, detail=f"status must be one of {valid_statuses}")
        task.status = body.status

    if body.notes is not None:
        task.description = body.notes

    if body.title is not None:
        stripped = body.title.strip()
        if not stripped:
            raise HTTPException(status_code=422, detail="title must not be empty")
        task.title = stripped

    if body.order_index is not None:
        task.order_index = body.order_index

    if body.due_date is not None:
        task.due_date = body.due_date

    if body.depends_on is not None:
        task.depends_on = json.dumps(body.depends_on)

    if body.tags is not None:
        task.tags = body.tags

    if body.priority is not None:
        valid_priorities = {"high", "normal", "low"}
        if body.priority not in valid_priorities:
            raise HTTPException(status_code=422, detail=f"priority must be one of {valid_priorities}")
        task.priority = body.priority

    # Update the goal's status if all tasks are done
    try:
        task = storage.update_task(task)
    except storage.VersionConflictError:
        raise HTTPException(status_code=409, detail="Task was modified by another request. Please refresh and try again.")

    from teb import events as _events
    _events.event_bus.publish(uid, "task_updated", {"task_id": task.id, "status": task.status, "goal_id": task.goal_id})

    all_tasks = storage.list_tasks(goal_id=task.goal_id)
    top_level = [t for t in all_tasks if t.parent_id is None]
    if top_level and all(t.status in ("done", "skipped") for t in top_level):
        goal = storage.get_goal(task.goal_id)
        if goal and goal.status != "done":
            goal.status = "done"
            try:
                storage.update_goal(goal)
            except storage.VersionConflictError:
                pass
            # Auto-capture success path when goal completes
            sp = decomposer.capture_success_path(goal, all_tasks)
            if sp:
                storage.create_success_path(sp)
            # Notify goal completion
            messaging.send_notification("goal_complete", {"goal_title": goal.title})
    elif any(t.status == "in_progress" for t in all_tasks):
        goal = storage.get_goal(task.goal_id)
        if goal and goal.status == "decomposed":
            goal.status = "in_progress"
            try:
                storage.update_goal(goal)
            except storage.VersionConflictError:
                pass

    # Notify on task completion or failure
    if body.status == "done":
        messaging.send_notification("task_done", {"title": task.title, "task_id": task.id})
    elif body.status == "failed":
        messaging.send_notification("task_failed", {"title": task.title, "task_id": task.id})

    return task.to_dict()


@router.delete("/api/tasks/{task_id}", status_code=200)
async def delete_task(task_id: int, request: Request):
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid, require_role="editor")
    goal_id = task.goal_id
    storage.delete_task(task_id)
    from teb import events as _events
    _events.event_bus.publish(uid, "task_deleted", {"task_id": task_id, "goal_id": goal_id})
    return {"deleted": task_id}


# ─── API Credentials ─────────────────────────────────────────────────────────

# Credential routes moved to teb/routers/settings.py


# ─── Task execution ──────────────────────────────────────────────────────────

@router.post("/api/tasks/{task_id}/execute")
async def execute_task(task_id: int, request: Request):
    """Ask teb to autonomously execute a task via registered APIs."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)

    if task.status in ("done", "skipped"):
        raise HTTPException(status_code=409, detail="Task is already completed")

    credentials = storage.list_credentials(user_id=uid)

    # Generate execution plan
    plan = executor.generate_plan(task, credentials)

    if not plan.can_execute:
        return {
            "task_id": task_id,
            "executed": False,
            "reason": plan.reason,
            "plan": plan.to_dict(),
            "logs": [],
        }

    # P2.1: Budget validation — check if the goal has a budget and if spending is required
    approval = _check_budget_approval(task_id, task.title, task.goal_id)
    if approval:
        # Log pending approval
        pending_log = ExecutionLog(
            task_id=task_id,
            credential_id=None,
            action="Execution paused — pending budget approval",
            request_summary=f"Budget requires approval",
            response_summary=f"Spending request #{approval['spending_request_id']} created",
            status="success",
        )
        storage.create_execution_log(pending_log)
        return {
            "task_id": task_id,
            "executed": False,
            "reason": "Budget requires approval. A spending request has been created.",
            "plan": plan.to_dict(),
            "logs": [pending_log.to_dict()],
            "spending_request_id": approval["spending_request_id"],
        }

    # Mark task as executing
    task.status = "executing"
    storage.update_task(task)

    # Execute the plan
    creds_by_id = {c.id: c for c in credentials if c.id is not None}
    results = executor.execute_plan(plan, creds_by_id)

    # Log each step
    logs: list[dict] = []
    all_success = True
    for result in results:
        cred = creds_by_id.get(result.step.credential_id)
        log = ExecutionLog(
            task_id=task_id,
            credential_id=result.step.credential_id,
            action=result.step.description,
            request_summary=executor.build_request_summary(result.step, cred),
            response_summary=executor.build_response_summary(result),
            status="success" if result.success else "error",
        )
        saved_log = storage.create_execution_log(log)
        logs.append(saved_log.to_dict())
        if not result.success:
            all_success = False

    # Update task status
    task.status = "done" if all_success else "failed"
    storage.update_task(task)

    return {
        "task_id": task_id,
        "executed": True,
        "success": all_success,
        "plan": plan.to_dict(),
        "logs": logs,
    }


@router.get("/api/tasks/{task_id}/executions")
async def get_task_executions(task_id: int, request: Request):
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)
    logs = storage.list_execution_logs(task_id)
    return {"task_id": task_id, "logs": [log.to_dict() for log in logs]}


# ─── Task Comments (Phase 1, Step 3) ─────────────────────────────────────────

@router.post("/api/tasks/{task_id}/comments", status_code=201)
async def create_task_comment(task_id: int, request: Request):
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)
    body = await request.json()
    content = str(body.get("content", "")).strip()
    if not content:
        raise HTTPException(status_code=422, detail="content must not be empty")
    author_type = body.get("author_type", "human")
    if author_type not in ("human", "agent", "system"):
        raise HTTPException(status_code=422, detail="author_type must be human, agent, or system")
    comment = TaskComment(
        task_id=task_id,
        content=content,
        author_type=author_type,
        author_id=body.get("author_id", str(uid)),
    )
    comment = storage.create_task_comment(comment)

    # Extract @mentions and create notifications for mentioned users
    mentions = storage.extract_mentions(content)
    for username in mentions:
        mentioned_user = storage.get_user_by_email(username)
        if mentioned_user and mentioned_user.id and mentioned_user.id != uid:
            storage.create_notification(Notification(
                user_id=mentioned_user.id,
                title=f"You were mentioned in a comment on task #{task_id}",
                body=content[:200],
                notification_type="mention",
                source_type="comment",
                source_id=comment.id,
            ))
            from teb import events as _events
            _events.event_bus.publish(mentioned_user.id, "mention", {
                "task_id": task_id, "comment_id": comment.id, "by_user_id": uid,
            })

    return comment.to_dict()


@router.get("/api/tasks/{task_id}/comments")
async def list_task_comments(task_id: int, request: Request):
    uid = deps.require_user(request)
    deps.get_task_for_user(task_id, uid)
    return [c.to_dict() for c in storage.list_task_comments(task_id)]


@router.delete("/api/comments/{comment_id}", status_code=200)
async def delete_task_comment_endpoint(comment_id: int, request: Request):
    deps.require_user(request)
    storage.delete_task_comment(comment_id)
    return {"deleted": True}


# ─── Task Artifacts (Phase 1, Step 4) ────────────────────────────────────────

@router.post("/api/tasks/{task_id}/artifacts", status_code=201)
async def create_task_artifact(task_id: int, request: Request):
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)
    body = await request.json()
    artifact_type = str(body.get("artifact_type", "")).strip()
    if artifact_type not in ("file", "url", "screenshot", "code", "api_response"):
        raise HTTPException(status_code=422, detail="artifact_type must be file, url, screenshot, code, or api_response")
    artifact = TaskArtifact(
        task_id=task_id,
        artifact_type=artifact_type,
        title=str(body.get("title", "")),
        content_url=str(body.get("content_url", "")),
        metadata_json=json.dumps(body.get("metadata", {})),
    )
    artifact = storage.create_task_artifact(artifact)
    return artifact.to_dict()


@router.get("/api/tasks/{task_id}/artifacts")
async def list_task_artifacts(task_id: int, request: Request):
    uid = deps.require_user(request)
    deps.get_task_for_user(task_id, uid)
    return [a.to_dict() for a in storage.list_task_artifacts(task_id)]


@router.delete("/api/artifacts/{artifact_id}", status_code=200)
async def delete_task_artifact_endpoint(artifact_id: int, request: Request):
    deps.require_user(request)
    storage.delete_task_artifact(artifact_id)
    return {"deleted": True}


# ─── Task Search (Phase 1, Step 2 enhancement) ──────────────────────────────

@router.get("/api/tasks/search")
async def search_tasks(request: Request,
                       goal_id: Optional[int] = Query(default=None),
                       q: Optional[str] = Query(default=None),
                       tags: Optional[str] = Query(default=None),
                       status: Optional[str] = Query(default=None)):
    uid = deps.require_user(request)
    if goal_id:
        deps.get_goal_for_user(goal_id, uid)
    results = storage.search_tasks(goal_id=goal_id, query=q or "", tags=tags, status=status)
    return [t.to_dict() for t in results]


# ─── Dependency Graph (Phase 2, Step 5) ──────────────────────────────────────

@router.get("/api/goals/{goal_id}/dependency-graph")
async def get_dependency_graph(goal_id: int, request: Request):
    """Get the dependency DAG for a goal's tasks."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    nodes = []
    edges = []
    for t in tasks:
        nodes.append({"id": t.id, "title": t.title, "status": t.status})
        dep_ids = json.loads(t.depends_on) if t.depends_on else []
        for dep_id in dep_ids:
            edges.append({"from": dep_id, "to": t.id})

    cycle_error = storage.validate_no_cycles(goal_id)
    return {
        "nodes": nodes,
        "edges": edges,
        "has_cycles": cycle_error is not None,
        "cycle_error": cycle_error,
    }


@router.get("/api/goals/{goal_id}/ready-tasks")
async def get_ready_tasks(goal_id: int, request: Request):
    """Get tasks ready to execute (all dependencies satisfied)."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    ready = storage.get_ready_tasks(goal_id)
    return [t.to_dict() for t in ready]


# ─── Execution Replay (Phase 2, Step 6) ──────────────────────────────────────

@router.post("/api/tasks/{task_id}/replay")
async def replay_task_execution(task_id: int, request: Request):
    """Replay the last execution of a failed task."""
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)
    if task.status not in ("failed", "error"):
        raise HTTPException(status_code=422, detail="Only failed tasks can be replayed")
    task.status = "todo"
    task = storage.update_task(task)
    # Add a system comment about replay
    comment = TaskComment(
        task_id=task_id,
        content="Task reset for replay after failure.",
        author_type="system",
        author_id="system",
    )
    storage.create_task_comment(comment)
    return task.to_dict()


# ─── Adaptive Pacing (Phase 3, Step 10) ──────────────────────────────────────

@router.get("/api/goals/{goal_id}/pacing")
async def get_goal_pacing(goal_id: int, request: Request):
    """Analyze user's pacing and suggest adjustments."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    checkins = storage.list_checkins(goal_id)

    total = len(tasks)
    done = sum(1 for t in tasks if t.status == "done")
    failed = sum(1 for t in tasks if t.status == "failed")

    # Analyze mood from recent check-ins
    recent_moods = [c.mood for c in checkins[-5:]] if checkins else []
    struggling = recent_moods.count("frustrated") + recent_moods.count("stuck")
    thriving = recent_moods.count("positive")

    recommendation = "on_track"
    suggestion = ""
    if total > 0:
        fail_rate = failed / total
        if struggling >= 2 or fail_rate > 0.3:
            recommendation = "break_down"
            suggestion = "Consider breaking remaining tasks into smaller, more manageable steps."
        elif thriving >= 3 and done > total * 0.5:
            recommendation = "consolidate"
            suggestion = "Great progress! Consider consolidating similar remaining tasks."

    return {
        "goal_id": goal_id,
        "total_tasks": total,
        "done": done,
        "failed": failed,
        "completion_pct": round(done / total * 100) if total > 0 else 0,
        "recent_moods": recent_moods,
        "recommendation": recommendation,
        "suggestion": suggestion,
    }



