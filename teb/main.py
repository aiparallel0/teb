from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from teb import decomposer, executor, storage
from teb.models import ApiCredential, CheckIn, ExecutionLog, Goal, NudgeEvent, OutcomeMetric, Task


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


class CredentialCreate(BaseModel):
    name: str
    base_url: str
    auth_header: str = "Authorization"
    auth_value: str = ""
    description: str = ""


class CheckInCreate(BaseModel):
    done_summary: str = ""
    blockers: str = ""


class OutcomeCreate(BaseModel):
    label: str
    target_value: float = 0.0
    unit: str = ""


class OutcomeUpdate(BaseModel):
    current_value: Optional[float] = None
    label: Optional[str] = None
    target_value: Optional[float] = None
    unit: Optional[str] = None


class SuggestionAction(BaseModel):
    status: str  # accepted | dismissed


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


# ─── API Credentials ─────────────────────────────────────────────────────────

@app.post("/api/credentials", status_code=201)
async def create_credential(body: CredentialCreate):
    name = body.name.strip()
    base_url = body.base_url.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if not base_url:
        raise HTTPException(status_code=422, detail="base_url must not be empty")
    cred = ApiCredential(
        name=name,
        base_url=base_url,
        auth_header=body.auth_header.strip() or "Authorization",
        auth_value=body.auth_value,
        description=body.description.strip(),
    )
    cred = storage.create_credential(cred)
    return cred.to_dict()


@app.get("/api/credentials")
async def list_credentials():
    return [c.to_dict() for c in storage.list_credentials()]


@app.delete("/api/credentials/{cred_id}", status_code=200)
async def delete_credential(cred_id: int):
    cred = storage.get_credential(cred_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    storage.delete_credential(cred_id)
    return {"deleted": cred_id}


# ─── Task execution ──────────────────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/execute")
async def execute_task(task_id: int):
    """Ask teb to autonomously execute a task via registered APIs."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("done", "skipped"):
        raise HTTPException(status_code=409, detail="Task is already completed")

    credentials = storage.list_credentials()

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


@app.get("/api/tasks/{task_id}/executions")
async def get_task_executions(task_id: int):
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    logs = storage.list_execution_logs(task_id)
    return {"task_id": task_id, "logs": [log.to_dict() for log in logs]}


# ─── Check-ins ───────────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/checkin", status_code=201)
async def create_checkin(goal_id: int, body: CheckInCreate):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

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


@app.get("/api/goals/{goal_id}/checkins")
async def list_checkins(goal_id: int, limit: Optional[int] = Query(default=None)):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    checkins = storage.list_checkins(goal_id, limit=limit)
    return [c.to_dict() for c in checkins]


# ─── Nudges ──────────────────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/nudge")
async def get_nudge(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

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
        return {"nudge": ne.to_dict()}

    return {"nudge": None, "message": "No nudge needed — you're on track!"}


@app.post("/api/nudges/{nudge_id}/acknowledge")
async def acknowledge_nudge(nudge_id: int):
    ne = storage.acknowledge_nudge(nudge_id)
    if not ne:
        raise HTTPException(status_code=404, detail="Nudge not found")
    return ne.to_dict()


# ─── Outcome Metrics ─────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/outcomes", status_code=201)
async def create_outcome(goal_id: int, body: OutcomeCreate):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
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


@app.get("/api/goals/{goal_id}/outcomes")
async def list_outcomes(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    metrics = storage.list_outcome_metrics(goal_id)
    return [m.to_dict() for m in metrics]


@app.patch("/api/outcomes/{metric_id}")
async def update_outcome(metric_id: int, body: OutcomeUpdate):
    om = storage.get_outcome_metric(metric_id)
    if not om:
        raise HTTPException(status_code=404, detail="Outcome metric not found")
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


@app.get("/api/goals/{goal_id}/outcome_suggestions")
async def outcome_suggestions(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    return decomposer.suggest_outcome_metrics(goal.title, goal.description)


# ─── User Profile ────────────────────────────────────────────────────────────

@app.get("/api/profile")
async def get_profile():
    profile = storage.get_or_create_profile()
    return profile.to_dict()


@app.patch("/api/profile")
async def update_profile(body: dict):
    profile = storage.get_or_create_profile()
    for key in ("skills", "available_hours_per_day", "experience_level",
                "interests", "preferred_learning_style"):
        if key in body:
            setattr(profile, key, body[key])
    profile = storage.update_profile(profile)
    return profile.to_dict()


# ─── Proactive Suggestions ──────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/suggestions")
async def get_suggestions(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Generate new suggestions if none exist
    existing = storage.list_suggestions(goal_id, status="pending")
    if not existing:
        tasks = storage.list_tasks(goal_id=goal_id)
        new_suggestions = decomposer.generate_proactive_suggestions(goal, tasks)
        for s in new_suggestions:
            storage.create_suggestion(s)
        existing = storage.list_suggestions(goal_id, status="pending")

    return [s.to_dict() for s in existing]


@app.post("/api/suggestions/{suggestion_id}")
async def act_on_suggestion(suggestion_id: int, body: SuggestionAction):
    valid = {"accepted", "dismissed"}
    if body.status not in valid:
        raise HTTPException(status_code=422, detail=f"status must be one of {valid}")
    ps = storage.update_suggestion_status(suggestion_id, body.status)
    if not ps:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return ps.to_dict()


# ─── Success Paths (Knowledge Base) ─────────────────────────────────────────

@app.get("/api/knowledge/paths")
async def list_success_paths(goal_type: Optional[str] = Query(default=None)):
    paths = storage.list_success_paths(goal_type=goal_type)
    return [p.to_dict() for p in paths]
