from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from teb import decomposer, storage
from teb.models import CheckIn, Goal, NudgeEvent, OutcomeMetric, Task


# ─── Startup / lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    yield


app = FastAPI(title="teb — Task Execution Bridge", lifespan=lifespan)

# Static files
_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ─── Request / Response schemas ───────────────────────────────────────────────

class GoalCreate(BaseModel):
    title: str
    description: str = ""


class TaskPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None   # stored in description for simplicity
    title: Optional[str] = None
    order_index: Optional[int] = None


class TaskCreate(BaseModel):
    goal_id: int
    title: str
    description: str = ""
    estimated_minutes: int = 30
    parent_id: Optional[int] = None


class ClarifyAnswer(BaseModel):
    key: str
    answer: str


class CheckInCreate(BaseModel):
    done_summary: str
    blockers: str = ""


class OutcomeMetricCreate(BaseModel):
    label: str
    target_value: float = 0.0
    unit: str = ""


class OutcomeMetricPatch(BaseModel):
    current_value: Optional[float] = None
    target_value: Optional[float] = None
    label: Optional[str] = None


# ─── Frontend ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html = (_TEMPLATES_DIR / "index.html").read_text()
    return HTMLResponse(content=html)


# ─── Goals ────────────────────────────────────────────────────────────────────

@app.post("/api/goals", status_code=201)
async def create_goal(body: GoalCreate):
    goal = Goal(title=body.title.strip(), description=body.description.strip())
    if not goal.title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    goal = storage.create_goal(goal)
    return goal.to_dict()


@app.get("/api/goals")
async def list_goals():
    return [g.to_dict() for g in storage.list_goals()]


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    data = goal.to_dict()
    data["tasks"] = [t.to_dict() for t in tasks]
    return data


@app.post("/api/goals/{goal_id}/decompose")
async def decompose_goal(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

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


@app.post("/api/tasks/{task_id}/decompose")
async def decompose_task(task_id: int):
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

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

@app.get("/api/goals/{goal_id}/focus")
async def get_focus(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    focus = decomposer.get_focus_task(tasks)
    if focus is None:
        return {"focus_task": None, "message": "All tasks completed — well done!"}
    return {"focus_task": focus.to_dict()}


# ─── Progress summary ─────────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/progress")
async def get_progress(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    summary = decomposer.get_progress_summary(tasks)
    summary["goal_id"] = goal_id
    summary["goal_status"] = goal.status
    return summary


# ─── Clarifying questions ─────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/next_question")
async def next_question(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    q = decomposer.get_next_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@app.post("/api/goals/{goal_id}/clarify")
async def submit_clarify(goal_id: int, body: ClarifyAnswer):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
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


# ─── Tasks ────────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def list_tasks(
    goal_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    return [t.to_dict() for t in storage.list_tasks(goal_id=goal_id, status=status)]


@app.post("/api/tasks", status_code=201)
async def create_task_manual(body: TaskCreate):
    """Create a custom user task (not from decomposition)."""
    goal = storage.get_goal(body.goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
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
    task = storage.create_task(task)
    return task.to_dict()


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, body: TaskPatch):
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    valid_statuses = {"todo", "in_progress", "done", "skipped"}
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

    # Update the goal's status if all tasks are done
    task = storage.update_task(task)

    all_tasks = storage.list_tasks(goal_id=task.goal_id)
    top_level = [t for t in all_tasks if t.parent_id is None]
    if top_level and all(t.status in ("done", "skipped") for t in top_level):
        goal = storage.get_goal(task.goal_id)
        if goal and goal.status != "done":
            goal.status = "done"
            storage.update_goal(goal)
    elif any(t.status == "in_progress" for t in all_tasks):
        goal = storage.get_goal(task.goal_id)
        if goal and goal.status == "decomposed":
            goal.status = "in_progress"
            storage.update_goal(goal)

    return task.to_dict()


@app.delete("/api/tasks/{task_id}", status_code=200)
async def delete_task(task_id: int):
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    storage.delete_task(task_id)
    return {"deleted": task_id}


# ─── Daily Check-ins ──────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/checkin", status_code=201)
async def create_checkin(goal_id: int, body: CheckInCreate):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if not body.done_summary.strip() and not body.blockers.strip():
        raise HTTPException(status_code=422, detail="Provide at least a summary or blockers")

    # Analyze the check-in and detect mood
    analysis = decomposer.analyze_checkin(body.done_summary, body.blockers)

    ci = CheckIn(
        goal_id=goal_id,
        done_summary=body.done_summary.strip(),
        blockers=body.blockers.strip(),
        mood=analysis["mood_detected"],
    )
    ci = storage.create_checkin(ci)

    return {
        "checkin": ci.to_dict(),
        "coaching": analysis["feedback"],
    }


@app.get("/api/goals/{goal_id}/checkins")
async def list_checkins(goal_id: int, limit: int = Query(default=30)):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return [ci.to_dict() for ci in storage.list_checkins(goal_id, limit=limit)]


# ─── Active Nudges (stagnation detection) ─────────────────────────────────────

@app.get("/api/goals/{goal_id}/nudge")
async def get_nudge(goal_id: int):
    """Check for stagnation and return an active nudge if needed."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    tasks = storage.list_tasks(goal_id=goal_id)
    last_ci = storage.get_last_checkin(goal_id)

    last_checkin_age_hours: Optional[float] = None
    if last_ci and last_ci.created_at:
        from datetime import timezone as _tz
        delta = datetime.now(_tz.utc) - last_ci.created_at
        last_checkin_age_hours = delta.total_seconds() / 3600

    nudge_data = decomposer.detect_stagnation(tasks, last_checkin_age_hours, goal.status)
    if nudge_data is None:
        # Also check for unacknowledged nudges
        pending = storage.list_nudges(goal_id, unacknowledged_only=True)
        if pending:
            return {"nudge": pending[0].to_dict()}
        return {"nudge": None, "message": "You're on track!"}

    # Persist the nudge
    ne = NudgeEvent(
        goal_id=goal_id,
        nudge_type=nudge_data["nudge_type"],
        message=nudge_data["message"],
    )
    ne = storage.create_nudge(ne)
    return {"nudge": ne.to_dict()}


@app.post("/api/nudges/{nudge_id}/acknowledge")
async def acknowledge_nudge(nudge_id: int):
    ne = storage.acknowledge_nudge(nudge_id)
    if not ne:
        raise HTTPException(status_code=404, detail="Nudge not found")
    return ne.to_dict()


# ─── Outcome Metrics ──────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/outcomes", status_code=201)
async def create_outcome(goal_id: int, body: OutcomeMetricCreate):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if not body.label.strip():
        raise HTTPException(status_code=422, detail="label must not be empty")
    om = OutcomeMetric(
        goal_id=goal_id,
        label=body.label.strip(),
        target_value=body.target_value,
        unit=body.unit.strip(),
    )
    om = storage.create_outcome_metric(om)
    return om.to_dict()


@app.get("/api/goals/{goal_id}/outcomes")
async def list_outcomes(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    metrics = storage.list_outcome_metrics(goal_id)
    return [m.to_dict() for m in metrics]


@app.patch("/api/outcomes/{metric_id}")
async def update_outcome(metric_id: int, body: OutcomeMetricPatch):
    om = storage.get_outcome_metric(metric_id)
    if not om:
        raise HTTPException(status_code=404, detail="Outcome metric not found")
    if body.current_value is not None:
        om.current_value = body.current_value
    if body.target_value is not None:
        om.target_value = body.target_value
    if body.label is not None:
        stripped = body.label.strip()
        if not stripped:
            raise HTTPException(status_code=422, detail="label must not be empty")
        om.label = stripped
    om = storage.update_outcome_metric(om)
    return om.to_dict()


@app.get("/api/goals/{goal_id}/outcome_suggestions")
async def suggest_outcomes(goal_id: int):
    """Suggest measurable outcomes for a goal based on its vertical."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return decomposer.suggest_outcome_metrics(goal.title, goal.description)
