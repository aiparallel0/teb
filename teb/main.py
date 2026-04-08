from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from teb import agents, auth, browser, decomposer, executor, integrations, messaging, storage
from teb.models import (
    ApiCredential, BrowserAction, CheckIn, ExecutionLog,
    Goal, MessagingConfig, NudgeEvent, OutcomeMetric,
    SpendingBudget, SpendingRequest, Task,
)


# ─── Startup / lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    integrations.seed_integrations()
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


class BudgetCreate(BaseModel):
    goal_id: int
    daily_limit: float = 50.0
    total_limit: float = 500.0
    category: str = "general"
    require_approval: bool = True


class BudgetUpdate(BaseModel):
    daily_limit: Optional[float] = None
    total_limit: Optional[float] = None
    require_approval: Optional[bool] = None


class SpendingRequestCreate(BaseModel):
    task_id: int
    amount: float
    description: str = ""
    service: str = ""
    currency: str = "USD"


class SpendingAction(BaseModel):
    action: str          # approve | deny
    reason: str = ""


class MessagingConfigCreate(BaseModel):
    channel: str         # telegram | webhook
    config: dict = {}    # channel-specific config
    notify_nudges: bool = True
    notify_tasks: bool = True
    notify_spending: bool = True
    notify_checkins: bool = False


class MessagingConfigUpdate(BaseModel):
    config: Optional[dict] = None
    enabled: Optional[bool] = None
    notify_nudges: Optional[bool] = None
    notify_tasks: Optional[bool] = None
    notify_spending: Optional[bool] = None
    notify_checkins: Optional[bool] = None


class DripClarifyAnswer(BaseModel):
    key: str
    answer: str


class AuthRegister(BaseModel):
    email: str
    password: str


class AuthLogin(BaseModel):
    email: str
    password: str


class TelegramUpdate(BaseModel):
    """Minimal Telegram webhook update structure."""
    message: Optional[dict] = None


# ─── Auth helper ──────────────────────────────────────────────────────────────

def _get_user_id(request: Request) -> Optional[int]:
    """Extract user_id from the request's Authorization header (Bearer token).

    Returns None if no valid token is present (allows unauthenticated access
    to legacy endpoints).
    """
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[7:]
        return auth.decode_token(token)
    return None


def _require_user(request: Request) -> int:
    """Extract user_id or raise 401."""
    uid = _get_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register", status_code=201)
async def register(body: AuthRegister):
    """Register a new user and return a JWT token."""
    try:
        result = auth.register_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@app.post("/api/auth/login")
async def login(body: AuthLogin):
    """Log in and return a JWT token."""
    try:
        result = auth.login_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return result


@app.get("/api/auth/me")
async def auth_me(request: Request):
    """Get the current authenticated user."""
    uid = _require_user(request)
    user = storage.get_user(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.to_dict()


# ─── Frontend ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html = (_TEMPLATES_DIR / "index.html").read_text()
    return HTMLResponse(content=html)


# ─── Goals ────────────────────────────────────────────────────────────────────

@app.post("/api/goals", status_code=201)
async def create_goal(body: GoalCreate, request: Request):
    user_id = _get_user_id(request)
    goal = Goal(title=body.title.strip(), description=body.description.strip())
    if not goal.title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    goal.user_id = user_id
    goal = storage.create_goal(goal)
    return goal.to_dict()


@app.get("/api/goals")
async def list_goals(request: Request):
    user_id = _get_user_id(request)
    return [g.to_dict() for g in storage.list_goals(user_id=user_id)]


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
            storage.update_goal(goal)

    # Notify on task completion or failure
    if body.status == "done":
        messaging.send_notification("task_done", {"title": task.title, "task_id": task.id})
    elif body.status == "failed":
        messaging.send_notification("task_failed", {"title": task.title, "task_id": task.id})

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

    # P2.1: Budget validation — check if the goal has a budget and if spending is required
    budgets = storage.list_spending_budgets(task.goal_id)
    if budgets:
        # Check if any budget requires approval
        for budget in budgets:
            storage.maybe_reset_daily_spending(budget)
            if budget.require_approval:
                # Create a spending request and pause execution
                from teb.models import SpendingRequest as SpReq
                sr = SpReq(
                    task_id=task_id,
                    budget_id=budget.id,  # type: ignore[arg-type]
                    amount=0,  # estimated; real amount unknown until execution
                    description=f"Automated execution of: {task.title}",
                    service="api_execution",
                    status="pending",
                )
                sr = storage.create_spending_request(sr)
                messaging.send_notification("spending_request", {
                    "request_id": sr.id,
                    "amount": 0,
                    "description": sr.description,
                    "service": sr.service,
                    "task_title": task.title,
                })
                # Log pending approval
                pending_log = ExecutionLog(
                    task_id=task_id,
                    credential_id=None,
                    action="Execution paused — pending budget approval",
                    request_summary=f"Budget {budget.category} requires approval",
                    response_summary=f"Spending request #{sr.id} created",
                    status="success",
                )
                storage.create_execution_log(pending_log)
                return {
                    "task_id": task_id,
                    "executed": False,
                    "reason": "Budget requires approval. A spending request has been created.",
                    "plan": plan.to_dict(),
                    "logs": [pending_log.to_dict()],
                    "spending_request_id": sr.id,
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
        # Send nudge notification via external messaging
        messaging.send_notification("nudge", {"message": ne.message, "goal_id": goal_id})
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


# ─── Multi-Agent Delegation ─────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agent_types():
    """List all available agent types and their capabilities."""
    return [a.to_dict() for a in agents.list_agents()]


@app.post("/api/goals/{goal_id}/orchestrate")
async def orchestrate_goal(goal_id: int):
    """
    Run multi-agent orchestration on a goal.

    The coordinator agent analyzes the goal, delegates to specialists
    (marketing, web_dev, outreach, research, finance), each specialist
    produces concrete tasks and may sub-delegate to others.

    All handoffs are logged and all tasks are created in the database.
    """
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Clear any previous tasks for a clean orchestration
    storage.delete_tasks_for_goal(goal_id)

    result = agents.orchestrate_goal(goal)
    return result


@app.get("/api/goals/{goal_id}/handoffs")
async def list_handoffs(goal_id: int):
    """View the agent delegation chain for a goal."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    handoffs = storage.list_handoffs(goal_id)
    return [h.to_dict() for h in handoffs]


@app.get("/api/goals/{goal_id}/messages")
async def list_goal_messages(goal_id: int, agent: Optional[str] = Query(default=None)):
    """View inter-agent messages for a goal, optionally filtered by agent."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    messages = storage.list_agent_messages(goal_id, agent_type=agent)
    return [m.to_dict() for m in messages]


# ─── Browser Automation ─────────────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/browser")
async def browser_execute_task(task_id: int):
    """
    Generate and execute a browser automation plan for a task.

    Uses AI to create a step-by-step browser plan, then executes via
    Playwright (if available) or returns the plan as a guided walkthrough.
    """
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status in ("done", "skipped"):
        raise HTTPException(status_code=409, detail="Task is already completed")

    # Get relevant integrations for plan generation
    task_text = f"{task.title} {task.description}"
    matching = integrations.find_matching_integrations(task_text)
    from teb.models import Integration as IntModel
    integration_objs = [
        IntModel(service_name=m["service_name"], category=m["category"],
                 base_url=m["base_url"])
        for m in matching
    ]

    plan = browser.generate_browser_plan(task, integration_objs)

    if not plan.can_automate:
        return {
            "task_id": task_id,
            "executed": False,
            "reason": plan.reason,
            "plan": plan.to_dict(),
            "actions": [],
            "playwright_available": browser.is_playwright_available(),
        }

    # Mark task as executing
    task.status = "executing"
    storage.update_task(task)

    # Execute the browser plan
    results = browser.execute_browser_plan(plan)

    # Log each step as a browser action
    actions: list[dict] = []
    all_success = True
    for result in results:
        action = BrowserAction(
            task_id=task_id,
            action_type=result.step.action_type,
            target=result.step.target,
            value=result.extracted_text or result.step.value,
            status="success" if result.success else "error",
            error=result.error,
            screenshot_path=result.screenshot_path,
        )
        saved = storage.create_browser_action(action)
        actions.append(saved.to_dict())
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
        "actions": actions,
        "playwright_available": browser.is_playwright_available(),
    }


@app.get("/api/tasks/{task_id}/browser_actions")
async def get_browser_actions(task_id: int):
    """View browser automation actions for a task."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    actions = storage.list_browser_actions(task_id)
    return {"task_id": task_id, "actions": [a.to_dict() for a in actions]}


# ─── Integration Registry ───────────────────────────────────────────────────

@app.get("/api/integrations")
async def list_available_integrations(category: Optional[str] = Query(default=None)):
    """List all known service integrations, optionally filtered by category."""
    db_integrations = storage.list_integrations(category=category)
    return [i.to_dict() for i in db_integrations]


@app.get("/api/integrations/catalog")
async def get_integration_catalog():
    """Get the built-in integration catalog (no DB required)."""
    return integrations.get_catalog()


@app.get("/api/integrations/match")
async def match_integrations(q: str = Query(description="Task description to match against")):
    """Find integrations relevant to a task description."""
    return integrations.find_matching_integrations(q)


@app.get("/api/integrations/{service_name}/endpoints")
async def get_service_endpoints(service_name: str):
    """Get common API endpoints for a known service."""
    endpoints = integrations.get_endpoints_for_service(service_name)
    if not endpoints:
        raise HTTPException(status_code=404, detail=f"No endpoints known for '{service_name}'")
    return {"service_name": service_name, "endpoints": endpoints}


# ─── Adaptive Micro-Tasking (Drip Mode) ─────────────────────────────────────

@app.get("/api/goals/{goal_id}/drip")
async def drip_next(goal_id: int):
    """
    Get the next single task in drip mode.

    Drip mode gives one task at a time and adapts based on what the user
    has completed.  It may also include an adaptive follow-up question.
    """
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
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


@app.get("/api/goals/{goal_id}/drip/question")
async def drip_question(goal_id: int):
    """Get the next drip-mode clarifying question (first 5 upfront)."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    q = decomposer.get_next_drip_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@app.post("/api/goals/{goal_id}/drip/clarify")
async def drip_clarify(goal_id: int, body: DripClarifyAnswer):
    """Submit an answer to a drip-mode clarifying question."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
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

@app.get("/api/goals/{goal_id}/insights")
async def get_goal_insights(goal_id: int):
    """
    Get insights from success paths of similar completed goals.

    Uses the knowledge base of successful completions to recommend
    which steps to focus on and which are commonly skipped.
    """
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
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


# ─── Financial Execution Pipeline ───────────────────────────────────────────

@app.post("/api/budgets", status_code=201)
async def create_budget(body: BudgetCreate):
    """Create a spending budget for a goal."""
    goal = storage.get_goal(body.goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    if body.daily_limit < 0 or body.total_limit < 0:
        raise HTTPException(status_code=422, detail="Limits must be non-negative")

    valid_categories = {"general", "hosting", "domain", "marketing", "tools", "services"}
    if body.category not in valid_categories:
        raise HTTPException(status_code=422, detail=f"category must be one of {valid_categories}")

    budget = SpendingBudget(
        goal_id=body.goal_id,
        daily_limit=body.daily_limit,
        total_limit=body.total_limit,
        category=body.category,
        require_approval=body.require_approval,
    )
    budget = storage.create_spending_budget(budget)
    return budget.to_dict()


@app.get("/api/goals/{goal_id}/budgets")
async def list_budgets(goal_id: int):
    """List all spending budgets for a goal."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    budgets = storage.list_spending_budgets(goal_id)
    return [b.to_dict() for b in budgets]


@app.patch("/api/budgets/{budget_id}")
async def update_budget(budget_id: int, body: BudgetUpdate):
    """Update a spending budget's limits or approval requirement."""
    budget = storage.get_spending_budget(budget_id)
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")

    if body.daily_limit is not None:
        if body.daily_limit < 0:
            raise HTTPException(status_code=422, detail="daily_limit must be non-negative")
        budget.daily_limit = body.daily_limit
    if body.total_limit is not None:
        if body.total_limit < 0:
            raise HTTPException(status_code=422, detail="total_limit must be non-negative")
        budget.total_limit = body.total_limit
    if body.require_approval is not None:
        budget.require_approval = body.require_approval

    budget = storage.update_spending_budget(budget)
    return budget.to_dict()


@app.post("/api/spending/request", status_code=201)
async def create_spending_request(body: SpendingRequestCreate):
    """
    Request to spend money on a task.

    Validates against the goal's budget limits. If the budget requires
    approval, the request is created as 'pending'. If no approval is
    needed and the amount is within limits, it's auto-approved.
    """
    task = storage.get_task(body.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if body.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be positive")

    # Find applicable budget
    category = _guess_spending_category(body.service)
    budget = storage.find_spending_budget(task.goal_id, category)
    if not budget:
        raise HTTPException(
            status_code=404,
            detail=f"No budget found for goal {task.goal_id}. Create one first via POST /api/budgets",
        )

    # Check-on-request daily reset
    budget = storage.maybe_reset_daily_spending(budget)

    # Validate against limits
    validation = decomposer.validate_spending(
        body.amount, budget.daily_limit, budget.total_limit,
        budget.spent_today, budget.spent_total,
    )

    if not validation["allowed"]:
        raise HTTPException(status_code=422, detail=validation["reason"])

    # Create the spending request
    initial_status = "pending" if budget.require_approval else "approved"
    req = SpendingRequest(
        task_id=body.task_id,
        budget_id=budget.id,  # type: ignore[arg-type]
        amount=body.amount,
        currency=body.currency,
        description=body.description,
        service=body.service,
        status=initial_status,
    )
    req = storage.create_spending_request(req)

    # If auto-approved, update budget spending
    if initial_status == "approved":
        budget.spent_today += body.amount
        budget.spent_total += body.amount
        storage.update_spending_budget(budget)

    # Notify about spending request (if pending approval)
    if initial_status == "pending":
        messaging.send_notification("spending_request", {
            "request_id": req.id,
            "amount": req.amount,
            "description": req.description,
            "service": req.service,
            "task_title": task.title,
        })

    return {
        "request": req.to_dict(),
        "budget_remaining": {
            "daily": max(0, budget.daily_limit - budget.spent_today),
            "total": max(0, budget.total_limit - budget.spent_total),
        },
        "auto_approved": initial_status == "approved",
    }


@app.post("/api/spending/{request_id}/action")
async def action_spending_request(request_id: int, body: SpendingAction):
    """Approve or deny a pending spending request."""
    req = storage.get_spending_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Spending request not found")

    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is already {req.status}")

    valid_actions = {"approve", "deny"}
    if body.action not in valid_actions:
        raise HTTPException(status_code=422, detail=f"action must be one of {valid_actions}")

    if body.action == "approve":
        req.status = "approved"
        # Update budget
        budget = storage.get_spending_budget(req.budget_id)
        if budget:
            budget.spent_today += req.amount
            budget.spent_total += req.amount
            storage.update_spending_budget(budget)
        messaging.send_notification("spending_approved", {
            "amount": req.amount,
            "description": req.description,
        })
    else:
        req.status = "denied"
        req.denial_reason = body.reason
        messaging.send_notification("spending_denied", {
            "amount": req.amount,
            "description": req.description,
            "reason": body.reason,
        })

    storage.update_spending_request(req)
    return req.to_dict()


@app.get("/api/goals/{goal_id}/spending")
async def list_goal_spending(goal_id: int, status: Optional[str] = Query(default=None)):
    """List all spending requests for a goal's tasks."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    all_requests: list[dict] = []
    for task in tasks:
        if task.id is not None:
            reqs = storage.list_spending_requests(task_id=task.id, status=status)
            all_requests.extend(r.to_dict() for r in reqs)
    return all_requests


def _guess_spending_category(service: str) -> str:
    """Guess the spending category from a service name."""
    service_lower = service.lower()
    if any(w in service_lower for w in ("namecheap", "godaddy", "domain", "cloudflare")):
        return "domain"
    if any(w in service_lower for w in ("vercel", "aws", "heroku", "digitalocean", "hosting")):
        return "hosting"
    if any(w in service_lower for w in ("google ads", "facebook ads", "twitter ads", "marketing", "ads")):
        return "marketing"
    if any(w in service_lower for w in ("github", "openai", "tool", "software", "saas")):
        return "tools"
    if any(w in service_lower for w in ("stripe", "paypal", "sendgrid", "twilio")):
        return "services"
    return "general"


# ─── External Messaging ─────────────────────────────────────────────────────

@app.post("/api/messaging/config", status_code=201)
async def create_messaging_config(body: MessagingConfigCreate):
    """Configure a messaging channel (Telegram or webhook)."""
    import json as _json

    valid_channels = {"telegram", "webhook"}
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

    cfg = MessagingConfig(
        channel=body.channel,
        config_json=_json.dumps(body.config),
        notify_nudges=body.notify_nudges,
        notify_tasks=body.notify_tasks,
        notify_spending=body.notify_spending,
        notify_checkins=body.notify_checkins,
    )
    cfg = storage.create_messaging_config(cfg)
    return cfg.to_dict()


@app.get("/api/messaging/configs")
async def list_messaging_configs():
    """List all messaging configurations."""
    configs = storage.list_messaging_configs()
    return [c.to_dict() for c in configs]


@app.patch("/api/messaging/config/{config_id}")
async def update_messaging_config_endpoint(config_id: int, body: MessagingConfigUpdate):
    """Update a messaging configuration."""
    import json as _json

    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Messaging config not found")

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


@app.delete("/api/messaging/config/{config_id}", status_code=200)
async def delete_messaging_config_endpoint(config_id: int):
    """Delete a messaging configuration."""
    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Messaging config not found")
    storage.delete_messaging_config(config_id)
    return {"deleted": config_id}


@app.post("/api/messaging/test/{config_id}")
async def test_messaging(config_id: int):
    """Send a test message to a specific messaging channel."""
    result = messaging.send_test_message(config_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Test message failed"))
    return result


@app.post("/api/messaging/telegram/webhook")
async def telegram_webhook(body: TelegramUpdate):
    """
    Inbound Telegram webhook endpoint.

    Parses /approve {id} and /deny {id} commands from Telegram messages
    and routes them to the spending action endpoint.
    """
    import re as _re

    if not body.message:
        return {"ok": True}

    text = body.message.get("text", "").strip()
    if not text:
        return {"ok": True}

    # Parse /approve {id} or /deny {id}
    approve_match = _re.match(r"^/approve +(\d+)$", text)
    deny_match = _re.match(r"^/deny +(\d+)(?: (.+))?$", text)

    if approve_match:
        request_id = int(approve_match.group(1))
        req = storage.get_spending_request(request_id)
        if not req:
            return {"ok": True, "error": "Request not found"}
        if req.status != "pending":
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
        return {"ok": True, "action": "approved", "request_id": request_id}

    if deny_match:
        request_id = int(deny_match.group(1))
        reason = deny_match.group(2) or ""
        req = storage.get_spending_request(request_id)
        if not req:
            return {"ok": True, "error": "Request not found"}
        if req.status != "pending":
            return {"ok": True, "error": f"Request already {req.status}"}
        req.status = "denied"
        req.denial_reason = reason
        storage.update_spending_request(req)
        messaging.send_notification("spending_denied", {
            "amount": req.amount,
            "description": req.description,
            "reason": reason,
        })
        return {"ok": True, "action": "denied", "request_id": request_id}

    # P3.4: Telegram bot drip flow commands
    # /goal <text> — create a new goal
    goal_match = _re.match(r"^/goal (.+)$", text, _re.S)
    if goal_match:
        goal_text = goal_match.group(1).strip()
        goal = Goal(title=goal_text, description="Created via Telegram")
        goal = storage.create_goal(goal)
        # Start decomposition
        try:
            decomposer.decompose(goal)
            goal.status = "decomposed"
            storage.update_goal(goal)
        except Exception:
            pass
        return {"ok": True, "action": "goal_created", "goal_id": goal.id}

    # /next — get next drip task for most recent goal
    if text == "/next":
        goals = storage.list_goals()
        if not goals:
            return {"ok": True, "message": "No goals yet. Use /goal <description> to create one."}
        goal = goals[0]
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        drip = decomposer.drip_next_task(goal, tasks)
        if drip and drip.get("task"):
            task_data = drip["task"]
            messaging.send_notification("drip_task", {
                "title": task_data.get("title", ""),
                "description": task_data.get("description", ""),
                "estimated_minutes": task_data.get("estimated_minutes", 0),
            })
        return {"ok": True, "action": "drip_next", "drip": drip}

    # /done — mark current task as done and get next
    if text == "/done":
        goals = storage.list_goals()
        if not goals:
            return {"ok": True, "message": "No goals yet."}
        goal = goals[0]
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        focus = decomposer.get_focus_task(tasks)
        if focus:
            focus.status = "done"
            storage.update_task(focus)
            tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
            drip = decomposer.drip_next_task(goal, tasks, completed_task=focus)
            if drip and drip.get("task"):
                messaging.send_notification("drip_task", {
                    "title": drip["task"].get("title", ""),
                    "description": drip["task"].get("description", ""),
                    "estimated_minutes": drip["task"].get("estimated_minutes", 0),
                })
            return {"ok": True, "action": "task_done", "drip": drip}
        return {"ok": True, "message": "No current task to mark done."}

    # /skip — skip current task
    if text == "/skip":
        goals = storage.list_goals()
        if not goals:
            return {"ok": True, "message": "No goals yet."}
        goal = goals[0]
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        focus = decomposer.get_focus_task(tasks)
        if focus:
            focus.status = "skipped"
            storage.update_task(focus)
        return {"ok": True, "action": "task_skipped"}

    return {"ok": True}
