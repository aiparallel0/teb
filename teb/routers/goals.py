"""Router for goals endpoints — extracted from main.py."""
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
from teb import messaging
from teb import decomposer, intelligence
from teb import transcribe
from teb.models import (
    CheckIn, Goal, NudgeEvent, OutcomeMetric, ProactiveSuggestion, SuccessPath, Task,
)

logger = logging.getLogger(__name__)

_MAX_TITLE_LEN = 500
_MAX_DESCRIPTION_LEN = 10000
_MAX_TAG_LEN = 1000

router = APIRouter(tags=["goals"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class GoalCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=_MAX_TITLE_LEN, description="Goal title")
    description: str = Field("", max_length=_MAX_DESCRIPTION_LEN, description="Goal description")
    tags: Optional[str] = Field(None, max_length=_MAX_TAG_LEN, description="Comma-separated tags")



class GoalPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=_MAX_TITLE_LEN)
    description: Optional[str] = Field(None, max_length=_MAX_DESCRIPTION_LEN)
    status: Optional[str] = None
    tags: Optional[str] = Field(None, max_length=_MAX_TAG_LEN)


class ClarifyAnswer(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    answer: str = Field(..., min_length=1, max_length=5000)



class CheckInCreate(BaseModel):
    done_summary: str = Field("", max_length=5000)
    blockers: str = Field("", max_length=5000)



class OutcomeCreate(BaseModel):
    label: str = Field(..., min_length=1, max_length=200)
    target_value: float = Field(0.0, ge=0)
    unit: str = Field("", max_length=50)



class OutcomeUpdate(BaseModel):
    current_value: Optional[float] = None
    label: Optional[str] = Field(None, max_length=200)
    target_value: Optional[float] = Field(None, ge=0)
    unit: Optional[str] = Field(None, max_length=50)



class SuggestionAction(BaseModel):
    status: str  # accepted | dismissed



class DripClarifyAnswer(BaseModel):
    key: str
    answer: str





# ─── Goals ────────────────────────────────────────────────────────────────────

@router.post("/api/goals", status_code=201)
async def create_goal(body: GoalCreate, request: Request):
    deps.check_api_rate_limit(request)
    user_id = deps.get_user_id(request)
    goal = Goal(title=body.title.strip(), description=body.description.strip())
    if not goal.title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    goal.user_id = user_id
    if body.tags:
        goal.tags = body.tags
    goal = storage.create_goal(goal)
    from teb import events as _events
    if user_id:
        _events.event_bus.publish(user_id, "goal_created", {"goal_id": goal.id, "title": goal.title})
    return goal.to_dict()


@router.get("/api/goals", tags=["goals"])
async def list_goals(
    request: Request,
    page: Optional[int] = Query(default=None, ge=1, description="Page number (enables pagination)"),
    per_page: Optional[int] = Query(default=None, ge=1, le=100, description="Items per page"),
):
    """List goals for the authenticated user.

    When `page` or `per_page` is provided, returns a paginated response:
    `{"data": [...], "pagination": {"page": N, "per_page": N, "total": N, "pages": N}}`

    Otherwise returns a plain list for backward compatibility.
    """
    user_id = deps.get_user_id(request)
    all_goals = [g.to_dict() for g in storage.list_goals(user_id=user_id)]
    if page is not None or per_page is not None:
        return deps.paginate(all_goals, page=page or 1, per_page=per_page or deps._DEFAULT_PAGE_SIZE)
    return all_goals


@router.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    data = goal.to_dict()
    data["tasks"] = [t.to_dict() for t in tasks]
    return data


@router.patch("/api/goals/{goal_id}")
async def patch_goal(goal_id: int, body: GoalPatch, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    if body.title is not None:
        stripped = body.title.strip()
        if not stripped:
            raise HTTPException(status_code=422, detail="title must not be empty")
        goal.title = stripped
    if body.description is not None:
        goal.description = body.description
    if body.status is not None:
        valid_statuses = {"drafting", "clarifying", "decomposed", "in_progress", "done"}
        if body.status not in valid_statuses:
            raise HTTPException(status_code=422, detail=f"status must be one of {valid_statuses}")
        goal.status = body.status
    if body.tags is not None:
        goal.tags = body.tags
    try:
        goal = storage.update_goal(goal)
    except storage.VersionConflictError:
        raise HTTPException(status_code=409, detail="Goal was modified by another request.")
    return goal.to_dict()

@router.delete("/api/goals/{goal_id}", status_code=200)
async def delete_goal_endpoint(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    storage.delete_goal(goal_id)
    from teb import events as _events  # noqa: E402
    _events.event_bus.publish(uid, "goal_deleted", {"goal_id": goal_id, "title": goal.title})
    return {"ok": True, "deleted_goal_id": goal_id}


@router.post("/api/goals/{goal_id}/decompose")
async def decompose_goal(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)

    # Clear any previous tasks
    storage.delete_tasks_for_goal(goal_id)

    # Decompose
    tasks = decomposer.decompose(goal)

    # Persist tasks; handle subtask relationships
    saved: list[dict] = []
    for task in tasks:
        subtask_templates = getattr(task, "_subtask_templates", [])
        saved_task = storage.create_task(task)
        saved.append(saved_task.to_dict())

        for s_idx, sub_template in enumerate(subtask_templates):
            if isinstance(sub_template, decomposer._TemplateTask):
                sub = Task(
                    goal_id=goal_id,
                    parent_id=saved_task.id,
                    title=sub_template.title,
                    description=sub_template.description,
                    estimated_minutes=sub_template.estimated_minutes,
                    order_index=s_idx,
                )
            else:
                # AI mode: sub_template is a dict
                sub = Task(
                    goal_id=goal_id,
                    parent_id=saved_task.id,
                    title=str(sub_template.get("title", "Subtask")),
                    description=str(sub_template.get("description", "")),
                    estimated_minutes=int(sub_template.get("estimated_minutes", 15)),
                    order_index=s_idx,
                )
            saved_sub = storage.create_task(sub)
            saved.append(saved_sub.to_dict())

    # Update goal status
    goal.status = "decomposed"
    storage.update_goal(goal)

    return {"goal_id": goal_id, "tasks": saved}


# ─── Task-level decomposition ─────────────────────────────────────────────────

_MAX_DECOMPOSE_DEPTH = 3  # Maximum nesting depth for task decomposition


@router.post("/api/tasks/{task_id}/decompose")
async def decompose_task(task_id: int, request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)

    # Don't re-decompose if already has children
    existing = storage.list_tasks(goal_id=task.goal_id)
    has_children = any(t.parent_id == task_id for t in existing)
    if has_children:
        raise HTTPException(status_code=409, detail="Task already has sub-tasks")

    # Enforce depth limit
    depth = _get_task_depth(task, existing)
    if depth >= _MAX_DECOMPOSE_DEPTH:
        raise HTTPException(
            status_code=422,
            detail=f"Maximum decomposition depth ({_MAX_DECOMPOSE_DEPTH}) reached",
        )

    subtasks = decomposer.decompose_task(task)

    saved: list[dict] = []
    for sub in subtasks:
        saved_sub = storage.create_task(sub)
        saved.append(saved_sub.to_dict())

    return {"task_id": task_id, "subtasks": saved}


def _get_task_depth(task: Task, all_tasks: list[Task]) -> int:
    """Return the depth of a task in the hierarchy (0 = top-level)."""
    by_id = {t.id: t for t in all_tasks if t.id is not None}
    depth = 0
    current = task
    while current.parent_id is not None and current.parent_id in by_id:
        depth += 1
        current = by_id[current.parent_id]
    return depth


# ─── Focus mode ───────────────────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/focus")
async def get_focus(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    focus = decomposer.get_focus_task(tasks)
    if focus is None:
        return {"focus_task": None, "message": "All tasks completed — well done!"}

    result: dict = {"focus_task": focus.to_dict()}

    # 2.1: Surface stall detection in focus endpoint
    stall_info = decomposer._detect_task_stall(focus)
    if stall_info:
        result["stall_detected"] = True
        result["stall_message"] = stall_info["message"]
        result["sub_task_suggestion"] = stall_info.get("sub_task")
    return result


# ─── Progress summary ─────────────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/progress")
async def get_progress(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    summary = decomposer.get_progress_summary(tasks)
    summary["goal_id"] = goal_id
    summary["goal_status"] = goal.status

    # 2.1: Surface stall detection in progress endpoint
    focus = decomposer.get_focus_task(tasks)
    if focus:
        stall_info = decomposer._detect_task_stall(focus)
        if stall_info:
            summary["stall_detected"] = True
            summary["stall_message"] = stall_info["message"]
            summary["sub_task_suggestion"] = stall_info.get("sub_task")

    return summary


# ─── Clarifying questions ─────────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/next_question")
async def next_question(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    q = decomposer.get_next_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@router.post("/api/goals/{goal_id}/clarify")
async def submit_clarify(goal_id: int, body: ClarifyAnswer, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    goal.answers[body.key] = body.answer
    goal.status = "clarifying"
    storage.update_goal(goal)
    next_q = decomposer.get_next_question(goal)
    if next_q is None:
        return {"done": True, "next_question": None}
    return {
        "done": False,
        "next_question": {"key": next_q.key, "text": next_q.text, "hint": next_q.hint},
    }


# ─── Check-ins ───────────────────────────────────────────────────────────────

@router.post("/api/goals/{goal_id}/checkin", status_code=201)
async def create_checkin(goal_id: int, body: CheckInCreate, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)

    if not body.done_summary.strip() and not body.blockers.strip():
        raise HTTPException(status_code=422, detail="At least one of done_summary or blockers must be provided")

    # Analyze the check-in for coaching feedback
    coaching = decomposer.analyze_checkin(body.done_summary, body.blockers)

    ci = CheckIn(
        goal_id=goal_id,
        done_summary=body.done_summary.strip(),
        blockers=body.blockers.strip(),
        mood=coaching["mood_detected"],
        feedback=coaching["feedback"],
    )
    ci = storage.create_checkin(ci)

    return {"checkin": ci.to_dict(), "coaching": coaching}


@router.post("/api/goals/{goal_id}/checkin/voice", status_code=201)
async def create_voice_checkin(
    goal_id: int,
    request: Request,
    audio: UploadFile,
    blockers: str = Form(""),
    mood: Optional[str] = Form(None),
):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)

    audio_bytes = await audio.read()
    filename = audio.filename or "audio.wav"

    try:
        done_summary = transcribe.transcribe_audio(audio_bytes, filename)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    if not done_summary and not blockers.strip():
        raise HTTPException(
            status_code=422,
            detail="Transcription returned empty text and no blockers provided",
        )

    coaching = decomposer.analyze_checkin(done_summary, blockers)
    stripped_mood = mood.strip() if mood else ""
    detected_mood = stripped_mood if stripped_mood else coaching["mood_detected"]

    ci = CheckIn(
        goal_id=goal_id,
        done_summary=done_summary,
        blockers=blockers.strip(),
        mood=detected_mood,
        feedback=coaching["feedback"],
    )
    ci = storage.create_checkin(ci)

    return {
        "checkin": ci.to_dict(),
        "coaching": coaching,
        "transcription": done_summary,
    }


@router.get("/api/goals/{goal_id}/checkins")
async def list_checkins(goal_id: int, request: Request, limit: Optional[int] = Query(default=None)):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    checkins = storage.list_checkins(goal_id, limit=limit)
    return [c.to_dict() for c in checkins]


# ─── Nudges ──────────────────────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/nudge")
async def get_nudge(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)

    tasks = storage.list_tasks(goal_id=goal_id)
    last_ci = storage.get_last_checkin(goal_id)

    last_ci_age: Optional[float] = None
    if last_ci and last_ci.created_at:
        delta = datetime.now(timezone.utc) - last_ci.created_at.replace(tzinfo=timezone.utc)
        last_ci_age = delta.total_seconds() / 3600

    nudge_info = decomposer.detect_stagnation(tasks, last_ci_age, goal.status)

    if nudge_info:
        ne = NudgeEvent(
            goal_id=goal_id,
            nudge_type=nudge_info["nudge_type"],
            message=nudge_info["message"],
        )
        ne = storage.create_nudge(ne)
        # Send nudge notification via external messaging
        messaging.send_notification("nudge", {"message": ne.message, "goal_id": goal_id})
        return {"nudge": ne.to_dict()}

    return {"nudge": None, "message": "No nudge needed — you're on track!"}


@router.post("/api/nudges/{nudge_id}/acknowledge")
async def acknowledge_nudge(nudge_id: int, request: Request):
    uid = deps.require_user(request)
    ne = storage.get_nudge(nudge_id)
    if not ne:
        raise HTTPException(status_code=404, detail="Nudge not found")
    if ne.goal_id is not None:
        deps.get_goal_for_user(ne.goal_id, uid)  # ownership check
    ne = storage.acknowledge_nudge(nudge_id)
    if not ne:
        raise HTTPException(status_code=404, detail="Nudge not found")
    return ne.to_dict()


# ─── Outcome Metrics ─────────────────────────────────────────────────────────

@router.post("/api/goals/{goal_id}/outcomes", status_code=201)
async def create_outcome(goal_id: int, body: OutcomeCreate, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    label = body.label.strip()
    if not label:
        raise HTTPException(status_code=422, detail="label must not be empty")
    om = OutcomeMetric(
        goal_id=goal_id,
        label=label,
        target_value=body.target_value,
        unit=body.unit.strip(),
    )
    om = storage.create_outcome_metric(om)
    return om.to_dict()


@router.get("/api/goals/{goal_id}/outcomes")
async def list_outcomes(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    metrics = storage.list_outcome_metrics(goal_id)
    return [m.to_dict() for m in metrics]


@router.patch("/api/outcomes/{metric_id}")
async def update_outcome(metric_id: int, body: OutcomeUpdate, request: Request):
    uid = deps.require_user(request)
    om = storage.get_outcome_metric(metric_id)
    if not om:
        raise HTTPException(status_code=404, detail="Outcome metric not found")
    if om.goal_id is not None:
        deps.get_goal_for_user(om.goal_id, uid)  # ownership check
    if body.current_value is not None:
        om.current_value = body.current_value
    if body.label is not None:
        om.label = body.label.strip()
    if body.target_value is not None:
        om.target_value = body.target_value
    if body.unit is not None:
        om.unit = body.unit.strip()
    om = storage.update_outcome_metric(om)
    return om.to_dict()


@router.get("/api/goals/{goal_id}/outcome_suggestions")
async def outcome_suggestions(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    return decomposer.suggest_outcome_metrics(goal.title, goal.description)


# ─── User Profile ────────────────────────────────────────────────────────────

# Profile routes moved to teb/routers/settings.py


# ─── Proactive Suggestions ──────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/suggestions")
async def get_suggestions(goal_id: int, request: Request):
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)

    # Generate new suggestions if none exist
    existing = storage.list_suggestions(goal_id, status="pending")
    if not existing:
        tasks = storage.list_tasks(goal_id=goal_id)
        new_suggestions = decomposer.generate_proactive_suggestions(goal, tasks)
        for s in new_suggestions:
            storage.create_suggestion(s)
        existing = storage.list_suggestions(goal_id, status="pending")

    return [s.to_dict() for s in existing]


@router.post("/api/suggestions/{suggestion_id}")
async def act_on_suggestion(suggestion_id: int, body: SuggestionAction, request: Request):
    deps.require_user(request)
    valid = {"accepted", "dismissed"}
    if body.status not in valid:
        raise HTTPException(status_code=422, detail=f"status must be one of {valid}")
    ps = storage.update_suggestion_status(suggestion_id, body.status)
    if not ps:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return ps.to_dict()


# ─── Success Paths (Knowledge Base) — see /api/knowledge/* endpoints below ──


# ─── Adaptive Micro-Tasking (Drip Mode) ─────────────────────────────────────

@router.get("/api/goals/{goal_id}/drip")
async def drip_next(goal_id: int, request: Request):
    """
    Get the next single task in drip mode.

    Drip mode gives one task at a time and adapts based on what the user
    has completed.  It may also include an adaptive follow-up question.
    """
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Find the most recently completed task for context
    completed = [t for t in tasks if t.status in ("done", "skipped")]
    last_completed = max(completed, key=lambda t: t.updated_at or t.created_at, default=None) if completed else None

    result = decomposer.drip_next_task(goal, tasks, completed_task=last_completed)
    if not result:
        return {"task": None, "message": "No more tasks."}

    # If it's a new task, persist it
    if result.get("is_new") and result.get("task"):
        task_data = result["task"]
        new_task = Task(
            goal_id=goal_id,
            title=task_data["title"],
            description=task_data["description"],
            estimated_minutes=task_data["estimated_minutes"],
            order_index=task_data["order_index"],
        )
        saved = storage.create_task(new_task)
        result["task"] = saved.to_dict()

        # Also create subtasks if they exist
        subtask_templates = getattr(new_task, "_subtask_templates", None)
        # Note: subtask_templates won't persist through to_dict/from_dict.
        # The drip_next_task attached them to the Task object before to_dict.

        # Send drip notification
        messaging.send_notification("drip_task", {
            "title": saved.title,
            "description": saved.description,
            "estimated_minutes": saved.estimated_minutes,
        })

        # Update goal status
        if goal.status in ("drafting", "clarifying", "decomposed"):
            goal.status = "in_progress"
            storage.update_goal(goal)

    return result


@router.get("/api/goals/{goal_id}/drip/question")
async def drip_question(goal_id: int, request: Request):
    """Get the next drip-mode clarifying question (first 5 upfront)."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    q = decomposer.get_next_drip_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@router.post("/api/goals/{goal_id}/drip/clarify")
async def drip_clarify(goal_id: int, body: DripClarifyAnswer, request: Request):
    """Submit an answer to a drip-mode clarifying question."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    goal.answers[body.key] = body.answer
    goal.status = "clarifying"
    storage.update_goal(goal)
    next_q = decomposer.get_next_drip_question(goal)
    if next_q is None:
        return {"done": True, "next_question": None}
    return {
        "done": False,
        "next_question": {"key": next_q.key, "text": next_q.text, "hint": next_q.hint},
    }


# ─── Success Path Learning ─────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/insights")
async def get_goal_insights(goal_id: int, request: Request):
    """
    Get insights from success paths of similar completed goals.

    Uses the knowledge base of successful completions to recommend
    which steps to focus on and which are commonly skipped.
    """
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    paths = storage.list_success_paths()
    insights = decomposer.apply_success_paths(goal, paths)

    # P2.2: increment_success_path_reuse when paths influence a new decomposition
    if insights:
        template_name = decomposer._detect_template(goal)
        relevant = [sp for sp in paths if sp.goal_type == template_name]
        for sp in relevant:
            if sp.id is not None:
                storage.increment_success_path_reuse(sp.id)

    return {"goal_id": goal_id, "insights": insights}



