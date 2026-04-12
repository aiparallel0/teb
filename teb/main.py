from __future__ import annotations

import collections
import json
import logging
import logging.config
import os
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from teb import agents, auth, browser, config, decomposer, deployer, executor, integrations, messaging, provisioning, scheduler, storage, transcribe
from teb.models import (
    ActivityFeedEntry, AgentGoalMemory, ApiCredential, AuditEvent, BrowserAction, CheckIn,
    CommentReaction, CustomField, DashboardLayout, DashboardWidget, DirectMessage, EmailNotificationConfig,
    ExecutionLog, Goal, GoalChatMessage, GoalCollaborator,
    GoalTemplate, MessagingConfig, Milestone, Notification, NotificationPreference,
    NudgeEvent, OutcomeMetric, PersonalApiKey, PluginManifest,
    ProgressSnapshot, PushSubscription, RecurrenceRule, SavedView, ScheduledReport,
    SpendingBudget, SpendingRequest,
    Task, TaskArtifact, TaskBlocker, TaskComment, TimeEntry, WebhookConfig,
    Workspace, WorkspaceMember,
)

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.config.dictConfig({
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "level": config.LOG_LEVEL,
        "handlers": ["console"],
    },
})
logger = logging.getLogger(__name__)


# ─── Rate limiting (in-memory, per IP) ────────────────────────────────────────

_RATE_WINDOW = 60   # seconds
_RATE_LIMIT = 20    # max auth requests per window per IP
_API_RATE_LIMIT = 120  # max general API requests per window per IP
# bucket: IP -> deque of timestamps
_rate_buckets: dict[str, collections.deque] = {}
_api_rate_buckets: dict[str, collections.deque] = {}


def _check_rate_limit(request: Request) -> None:
    """Raise 429 if caller exceeds the per-IP auth rate limit (stricter)."""
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _rate_buckets.setdefault(ip, collections.deque())
    # Purge timestamps outside the window
    while bucket and bucket[0] <= now - _RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    bucket.append(now)


def _check_api_rate_limit(request: Request) -> None:
    """Raise 429 if caller exceeds the per-IP general API rate limit."""
    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    bucket = _api_rate_buckets.setdefault(ip, collections.deque())
    while bucket and bucket[0] <= now - _RATE_WINDOW:
        bucket.popleft()
    if len(bucket) >= _API_RATE_LIMIT:
        raise HTTPException(status_code=429, detail="Too many requests")
    bucket.append(now)


def reset_rate_limits() -> None:
    """Clear all rate-limit buckets. Called on startup and available for tests."""
    _rate_buckets.clear()
    _api_rate_buckets.clear()


# ─── Startup / lifespan ───────────────────────────────────────────────────────

import asyncio

_auto_exec_task: Optional[asyncio.Task] = None


def _check_budget_approval(task_id: int, task_title: str, goal_id: int) -> Optional[dict]:
    """Check if any budget for the goal requires manual approval.

    Returns a dict with spending request info if approval is needed, else None.
    When autopilot is enabled on a budget, approval is bypassed.
    """
    budgets = storage.list_spending_budgets(goal_id)
    for budget in budgets:
        storage.maybe_reset_daily_spending(budget)
        if budget.require_approval and not budget.autopilot_enabled:
            # Create a spending request and signal pause
            sr = SpendingRequest(
                task_id=task_id,
                budget_id=budget.id if budget.id is not None else 0,
                amount=0,
                description=f"Automated execution of: {task_title}",
                service="api_execution",
                status="pending",
            )
            sr = storage.create_spending_request(sr)
            messaging.send_notification("spending_request", {
                "request_id": sr.id,
                "amount": 0,
                "description": sr.description,
                "service": sr.service,
                "task_title": task_title,
            })
            return {
                "spending_request_id": sr.id,
                "budget_category": budget.category,
            }
    return None


async def _autonomous_execution_loop() -> None:
    """Background loop: every N seconds, pick up pending tasks and execute them.

    Only processes tasks from goals with auto_execute=True.
    Respects budget autopilot settings for financial autonomy.
    """
    interval = config.AUTONOMOUS_EXECUTION_INTERVAL
    while True:
        try:
            if not config.AUTONOMOUS_EXECUTION_ENABLED:
                await asyncio.sleep(interval)
                continue

            pending = storage.list_auto_execute_tasks()
            for task in pending:
                try:
                    # Get the goal's user_id for credential scoping
                    goal = storage.get_goal(task.goal_id)
                    goal_user_id = goal.user_id if goal else None
                    credentials = storage.list_credentials(user_id=goal_user_id)
                    plan = executor.generate_plan(task, credentials)

                    if not plan.can_execute:
                        logger.debug("Task %s cannot be auto-executed: %s", task.id, plan.reason)
                        continue

                    # Check budget constraints with autopilot support
                    approval = _check_budget_approval(task.id or 0, task.title, task.goal_id)
                    if approval:
                        continue

                    # Execute the task
                    task.status = "executing"
                    storage.update_task(task)

                    creds_by_id = {c.id: c for c in credentials if c.id is not None}
                    results = executor.execute_plan(plan, creds_by_id)

                    # Log results
                    all_success = True
                    for result in results:
                        cred = creds_by_id.get(result.step.credential_id)
                        log = ExecutionLog(
                            task_id=task.id or 0,
                            credential_id=result.step.credential_id,
                            action=result.step.description,
                            request_summary=executor.build_request_summary(result.step, cred),
                            response_summary=executor.build_response_summary(result),
                            status="success" if result.success else "error",
                        )
                        storage.create_execution_log(log)
                        if not result.success:
                            all_success = False

                    task.status = "done" if all_success else "failed"
                    storage.update_task(task)

                    if all_success:
                        messaging.send_notification("task_done", {
                            "task_id": task.id,
                            "task_title": task.title,
                        })
                    logger.info("Auto-executed task %s: %s", task.id,
                                "success" if all_success else "failed")

                except Exception as e:
                    logger.error("Auto-execution failed for task %s: %s", task.id, e)
                    task.status = "failed"
                    storage.update_task(task)
                    messaging.send_notification("task_done", {
                        "task_id": task.id,
                        "task_title": task.title,
                        "error": str(e),
                    })

            await asyncio.sleep(interval)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Autonomous execution loop error: %s", e)
            await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _auto_exec_task, _APP_START_TIME
    _APP_START_TIME = time.monotonic()
    storage.init_db()
    integrations.seed_integrations()
    storage.reset_all_daily_spending()
    _rate_buckets.clear()
    if config.JWT_SECRET == "change-me-in-production-not-safe":
        logger.warning(
            "TEB_JWT_SECRET is set to the default insecure value. "
            "Set a strong secret via environment variable before deploying."
        )
    if not config.SECRET_KEY:
        logger.warning(
            "TEB_SECRET_KEY is not set — API credentials will be stored UNENCRYPTED. "
            "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    # Start autonomous execution loop
    _auto_exec_task = asyncio.create_task(_autonomous_execution_loop())
    yield
    # Shutdown: cancel loop
    if _auto_exec_task:
        _auto_exec_task.cancel()
        try:
            await _auto_exec_task
        except asyncio.CancelledError:
            pass


tags_metadata = [
    {"name": "auth", "description": "Authentication and user management"},
    {"name": "goals", "description": "Goal CRUD and decomposition"},
    {"name": "tasks", "description": "Task management and execution"},
    {"name": "intelligence", "description": "AI scheduling, prioritization, and risk detection"},
    {"name": "collaboration", "description": "Workspaces, notifications, activity feed"},
    {"name": "views", "description": "Kanban, Gantt, calendar, timeline views"},
    {"name": "integrations", "description": "External service integrations"},
    {"name": "admin", "description": "Admin panel and platform management"},
]

app = FastAPI(
    title="teb API",
    description="Task Execution Bridge — AI-powered goal decomposition and autonomous execution",
    version="2.0.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
    openapi_tags=tags_metadata,
)

# ─── CORS ─────────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Static files
_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ─── Request / Response schemas ───────────────────────────────────────────────

class GoalCreate(BaseModel):
    title: str
    description: str = ""
    tags: Optional[str] = None  # comma-separated tags


class TaskPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None   # stored in description for simplicity
    title: Optional[str] = None
    order_index: Optional[int] = None
    due_date: Optional[str] = None
    depends_on: Optional[list] = None
    tags: Optional[str] = None


class TaskCreate(BaseModel):
    goal_id: int
    title: str
    description: str = ""
    estimated_minutes: int = 30
    parent_id: Optional[int] = None
    due_date: Optional[str] = None
    depends_on: Optional[list] = None
    tags: Optional[str] = None


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
    autopilot_enabled: bool = False
    autopilot_threshold: float = 50.0


class BudgetUpdate(BaseModel):
    daily_limit: Optional[float] = None
    total_limit: Optional[float] = None
    require_approval: Optional[bool] = None
    autopilot_enabled: Optional[bool] = None
    autopilot_threshold: Optional[float] = None


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
    channel: str         # telegram | webhook | slack | discord | whatsapp
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


class AuthRefresh(BaseModel):
    refresh_token: str


class AuthLogout(BaseModel):
    refresh_token: Optional[str] = None


class TelegramUpdate(BaseModel):
    """Minimal Telegram webhook update structure."""
    message: Optional[dict] = None


class AdminUserUpdate(BaseModel):
    role: Optional[str] = None           # "user" | "admin"
    locked_until: Optional[str] = None   # ISO datetime string, or "null"/"" to unlock
    email_verified: Optional[bool] = None


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


def _require_admin(request: Request) -> int:
    """Extract user_id and verify admin role, or raise 401/403."""
    uid = _require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return uid


def _get_goal_for_user(goal_id: int, user_id: int) -> Goal:
    """Fetch a goal and verify the requesting user owns it (or it has no owner)."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if goal.user_id is not None and goal.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return goal


def _get_task_for_user(task_id: int, user_id: int) -> Task:
    """Fetch a task and verify the requesting user owns its goal."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.goal_id is not None:
        goal = storage.get_goal(task.goal_id)
        if goal and goal.user_id is not None and goal.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
    return task


# ─── Health check ─────────────────────────────────────────────────────────────

_APP_START_TIME: float = time.monotonic()  # Updated in lifespan startup


@app.get("/health")
async def health_check():
    """Health check — returns DB status, uptime, version, and component health."""
    import platform

    components: dict = {}

    # Database connectivity
    try:
        with storage._conn() as con:
            con.execute("SELECT 1")
            # Check table count as a deeper validation
            row = con.execute(
                "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type='table'"
            ).fetchone()
            table_count = row["cnt"] if row else 0
        components["database"] = {"status": "ok", "tables": table_count}
        db_ok = True
    except Exception as exc:
        components["database"] = {"status": "error", "detail": str(exc)}
        db_ok = False

    # AI provider availability
    ai_provider = config.get_ai_provider()
    components["ai"] = {
        "status": "ok" if ai_provider else "unconfigured",
        "provider": ai_provider or "none",
    }

    # Payment providers
    from teb import payments as _pay
    providers = _pay.list_providers()
    components["payments"] = {
        "status": "ok" if any(p["configured"] for p in providers) else "unconfigured",
        "providers": providers,
    }

    # Overall status
    status = "healthy" if db_ok else "degraded"
    code = 200 if db_ok else 503
    uptime_seconds = round(time.monotonic() - _APP_START_TIME, 1)

    return JSONResponse(
        status_code=code,
        content={
            "status": status,
            "version": "1.0.0",
            "uptime_seconds": uptime_seconds,
            "python_version": platform.python_version(),
            "components": components,
        },
    )


# ─── Auth ─────────────────────────────────────────────────────────────────────

@app.post("/api/auth/register", status_code=201)
async def register(body: AuthRegister, request: Request):
    """Register a new user and return a JWT token."""
    _check_rate_limit(request)
    try:
        result = auth.register_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@app.post("/api/auth/login")
async def login(body: AuthLogin, request: Request):
    """Log in and return a JWT token."""
    _check_rate_limit(request)
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


@app.post("/api/auth/refresh")
async def auth_refresh(request: Request, body: AuthRefresh):
    """Exchange a refresh token for a new access token + refresh token."""
    _check_rate_limit(request)
    try:
        result = auth.refresh_access_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return result


@app.post("/api/auth/logout")
async def auth_logout(request: Request, body: AuthLogout):
    """Revoke refresh tokens. Revokes all tokens if no specific token provided."""
    uid = _require_user(request)
    auth.logout_user(uid, body.refresh_token)
    return {"message": "Logged out"}


# ─── Frontend ─────────────────────────────────────────────────────────────────

def _render_index() -> str:
    """Render index.html with BASE_PATH substituted."""
    html = (_TEMPLATES_DIR / "index.html").read_text()
    return html.replace("{{BASE_PATH}}", config.BASE_PATH)


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    return HTMLResponse(content=_render_index())


# ─── Goals ────────────────────────────────────────────────────────────────────

@app.post("/api/goals", status_code=201)
async def create_goal(body: GoalCreate, request: Request):
    _check_api_rate_limit(request)
    user_id = _get_user_id(request)
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


@app.get("/api/goals")
async def list_goals(request: Request):
    user_id = _get_user_id(request)
    return [g.to_dict() for g in storage.list_goals(user_id=user_id)]


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    data = goal.to_dict()
    data["tasks"] = [t.to_dict() for t in tasks]
    return data


@app.post("/api/goals/{goal_id}/decompose")
async def decompose_goal(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)

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
async def decompose_task(task_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)

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
async def get_focus(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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

@app.get("/api/goals/{goal_id}/progress")
async def get_progress(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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

@app.get("/api/goals/{goal_id}/next_question")
async def next_question(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    q = decomposer.get_next_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@app.post("/api/goals/{goal_id}/clarify")
async def submit_clarify(goal_id: int, body: ClarifyAnswer, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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
    request: Request,
    goal_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    uid = _require_user(request)
    if goal_id is not None:
        _get_goal_for_user(goal_id, uid)  # ownership check
    return [t.to_dict() for t in storage.list_tasks(goal_id=goal_id, status=status)]


@app.post("/api/tasks", status_code=201)
async def create_task_manual(body: TaskCreate, request: Request):
    """Create a custom user task (not from decomposition)."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    goal = _get_goal_for_user(body.goal_id, uid)
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


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, body: TaskPatch, request: Request):
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)

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


@app.delete("/api/tasks/{task_id}", status_code=200)
async def delete_task(task_id: int, request: Request):
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
    goal_id = task.goal_id
    storage.delete_task(task_id)
    from teb import events as _events
    _events.event_bus.publish(uid, "task_deleted", {"task_id": task_id, "goal_id": goal_id})
    return {"deleted": task_id}


# ─── API Credentials ─────────────────────────────────────────────────────────

@app.post("/api/credentials", status_code=201)
async def create_credential(body: CredentialCreate, request: Request):
    uid = _require_user(request)
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
        user_id=uid,
    )
    cred = storage.create_credential(cred)
    return cred.to_dict()


@app.get("/api/credentials")
async def list_credentials(request: Request):
    uid = _require_user(request)
    return [c.to_dict() for c in storage.list_credentials(user_id=uid)]


@app.delete("/api/credentials/{cred_id}", status_code=200)
async def delete_credential(cred_id: int, request: Request):
    uid = _require_user(request)
    cred = storage.get_credential(cred_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    # Users can only delete their own credentials (or legacy unscoped ones)
    if cred.user_id is not None and cred.user_id != uid:
        raise HTTPException(status_code=403, detail="Not your credential")
    storage.delete_credential(cred_id)
    return {"deleted": cred_id}


# ─── Task execution ──────────────────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/execute")
async def execute_task(task_id: int, request: Request):
    """Ask teb to autonomously execute a task via registered APIs."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)

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


@app.get("/api/tasks/{task_id}/executions")
async def get_task_executions(task_id: int, request: Request):
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
    logs = storage.list_execution_logs(task_id)
    return {"task_id": task_id, "logs": [log.to_dict() for log in logs]}


# ─── Check-ins ───────────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/checkin", status_code=201)
async def create_checkin(goal_id: int, body: CheckInCreate, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)

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


@app.post("/api/goals/{goal_id}/checkin/voice", status_code=201)
async def create_voice_checkin(
    goal_id: int,
    request: Request,
    audio: UploadFile,
    blockers: str = Form(""),
    mood: Optional[str] = Form(None),
):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)

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


@app.get("/api/goals/{goal_id}/checkins")
async def list_checkins(goal_id: int, request: Request, limit: Optional[int] = Query(default=None)):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    checkins = storage.list_checkins(goal_id, limit=limit)
    return [c.to_dict() for c in checkins]


# ─── Nudges ──────────────────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/nudge")
async def get_nudge(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)

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
async def acknowledge_nudge(nudge_id: int, request: Request):
    uid = _require_user(request)
    ne = storage.get_nudge(nudge_id)
    if not ne:
        raise HTTPException(status_code=404, detail="Nudge not found")
    if ne.goal_id is not None:
        _get_goal_for_user(ne.goal_id, uid)  # ownership check
    ne = storage.acknowledge_nudge(nudge_id)
    if not ne:
        raise HTTPException(status_code=404, detail="Nudge not found")
    return ne.to_dict()


# ─── Outcome Metrics ─────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/outcomes", status_code=201)
async def create_outcome(goal_id: int, body: OutcomeCreate, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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
async def list_outcomes(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    metrics = storage.list_outcome_metrics(goal_id)
    return [m.to_dict() for m in metrics]


@app.patch("/api/outcomes/{metric_id}")
async def update_outcome(metric_id: int, body: OutcomeUpdate, request: Request):
    uid = _require_user(request)
    om = storage.get_outcome_metric(metric_id)
    if not om:
        raise HTTPException(status_code=404, detail="Outcome metric not found")
    if om.goal_id is not None:
        _get_goal_for_user(om.goal_id, uid)  # ownership check
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
async def outcome_suggestions(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    return decomposer.suggest_outcome_metrics(goal.title, goal.description)


# ─── User Profile ────────────────────────────────────────────────────────────

@app.get("/api/profile")
async def get_profile(request: Request):
    uid = _require_user(request)
    profile = storage.get_or_create_profile(user_id=uid)
    return profile.to_dict()


@app.patch("/api/profile")
async def update_profile(body: dict, request: Request):
    uid = _require_user(request)
    profile = storage.get_or_create_profile(user_id=uid)
    for key in ("skills", "available_hours_per_day", "experience_level",
                "interests", "preferred_learning_style"):
        if key in body:
            setattr(profile, key, body[key])
    profile = storage.update_profile(profile)
    return profile.to_dict()


# ─── Proactive Suggestions ──────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/suggestions")
async def get_suggestions(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)

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
async def act_on_suggestion(suggestion_id: int, body: SuggestionAction, request: Request):
    _require_user(request)
    valid = {"accepted", "dismissed"}
    if body.status not in valid:
        raise HTTPException(status_code=422, detail=f"status must be one of {valid}")
    ps = storage.update_suggestion_status(suggestion_id, body.status)
    if not ps:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    return ps.to_dict()


# ─── Success Paths (Knowledge Base) — see /api/knowledge/* endpoints below ──


# ─── Multi-Agent Delegation ─────────────────────────────────────────────────

@app.get("/api/agents")
async def list_agent_types():
    """List all available agent types and their capabilities."""
    return [a.to_dict() for a in agents.list_agents()]


@app.post("/api/agents/register", status_code=201)
async def register_agent_endpoint(request: Request):
    """Register a new agent type dynamically (admin only)."""
    _require_admin(request)
    body = await request.json()
    agent_type = body.get("agent_type", "")
    if not agent_type or not body.get("name"):
        raise HTTPException(status_code=422, detail="agent_type and name are required")

    spec = agents.AgentSpec(
        agent_type=agent_type,
        name=body.get("name", ""),
        description=body.get("description", ""),
        expertise=body.get("expertise", []),
        system_prompt=body.get("system_prompt", "You are a helpful agent."),
        can_delegate_to=body.get("can_delegate_to", []),
    )
    agents.register_agent(spec)
    return spec.to_dict()


@app.post("/api/goals/{goal_id}/orchestrate")
async def orchestrate_goal(goal_id: int, request: Request):
    """
    Run multi-agent orchestration on a goal.

    The coordinator agent analyzes the goal, delegates to specialists
    (marketing, web_dev, outreach, research, finance), each specialist
    produces concrete tasks and may sub-delegate to others.

    All handoffs are logged and all tasks are created in the database.
    """
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)

    # Clear any previous tasks for a clean orchestration
    storage.delete_tasks_for_goal(goal_id)

    result = agents.orchestrate_goal(goal)
    return result


@app.get("/api/goals/{goal_id}/handoffs")
async def list_handoffs(goal_id: int, request: Request):
    """View the agent delegation chain for a goal."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    handoffs = storage.list_handoffs(goal_id)
    return [h.to_dict() for h in handoffs]


@app.get("/api/goals/{goal_id}/messages")
async def list_goal_messages(goal_id: int, request: Request, agent: Optional[str] = Query(default=None)):
    """View inter-agent messages for a goal, optionally filtered by agent."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    messages = storage.list_agent_messages(goal_id, agent_type=agent)
    return [m.to_dict() for m in messages]


@app.get("/api/goals/{goal_id}/agent-activity")
async def get_agent_activity(goal_id: int, request: Request):
    """Get combined agent activity for a goal — handoffs, messages, and task map.

    Returns a unified view of all agent orchestration activity, suitable
    for rendering an agent activity timeline in the UI.
    """
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    handoffs = storage.list_handoffs(goal_id)
    messages = storage.list_agent_messages(goal_id)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Build agent summary
    agent_types = set()
    for h in handoffs:
        agent_types.add(h.from_agent)
        agent_types.add(h.to_agent)

    # Map tasks to agents via handoffs
    task_agent_map: dict[int, str] = {}
    for h in handoffs:
        if h.task_id is not None:
            task_agent_map[h.task_id] = h.to_agent

    return {
        "goal_id": goal_id,
        "agents_involved": sorted(agent_types),
        "handoffs": [h.to_dict() for h in handoffs],
        "messages": [m.to_dict() for m in messages],
        "task_agent_map": task_agent_map,
        "total_tasks_created": len(tasks),
        "tasks_by_agent": {
            agent: sum(1 for tid, a in task_agent_map.items() if a == agent)
            for agent in agent_types
        },
    }


# ─── Browser Automation ─────────────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/browser")
async def browser_execute_task(task_id: int, request: Request):
    """
    Generate and execute a browser automation plan for a task.

    Uses AI to create a step-by-step browser plan, then executes via
    Playwright (if available) or returns the plan as a guided walkthrough.
    """
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)

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
async def get_browser_actions(task_id: int, request: Request):
    """View browser automation actions for a task."""
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
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
async def drip_next(goal_id: int, request: Request):
    """
    Get the next single task in drip mode.

    Drip mode gives one task at a time and adapts based on what the user
    has completed.  It may also include an adaptive follow-up question.
    """
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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
async def drip_question(goal_id: int, request: Request):
    """Get the next drip-mode clarifying question (first 5 upfront)."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    q = decomposer.get_next_drip_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@app.post("/api/goals/{goal_id}/drip/clarify")
async def drip_clarify(goal_id: int, body: DripClarifyAnswer, request: Request):
    """Submit an answer to a drip-mode clarifying question."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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
async def get_goal_insights(goal_id: int, request: Request):
    """
    Get insights from success paths of similar completed goals.

    Uses the knowledge base of successful completions to recommend
    which steps to focus on and which are commonly skipped.
    """
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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
async def create_budget(body: BudgetCreate, request: Request):
    """Create a spending budget for a goal."""
    uid = _require_user(request)
    goal = _get_goal_for_user(body.goal_id, uid)

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
        autopilot_enabled=body.autopilot_enabled,
        autopilot_threshold=body.autopilot_threshold,
    )
    budget = storage.create_spending_budget(budget)
    return budget.to_dict()


@app.get("/api/goals/{goal_id}/budgets")
async def list_budgets(goal_id: int, request: Request):
    """List all spending budgets for a goal."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    budgets = storage.list_spending_budgets(goal_id)
    return [b.to_dict() for b in budgets]


@app.patch("/api/budgets/{budget_id}")
async def update_budget(budget_id: int, body: BudgetUpdate, request: Request):
    """Update a spending budget's limits or approval requirement."""
    uid = _require_user(request)
    budget = storage.get_spending_budget(budget_id)
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    if budget.goal_id is not None:
        _get_goal_for_user(budget.goal_id, uid)  # ownership check

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
    if body.autopilot_enabled is not None:
        budget.autopilot_enabled = body.autopilot_enabled
    if body.autopilot_threshold is not None:
        if body.autopilot_threshold < 0:
            raise HTTPException(status_code=422, detail="autopilot_threshold must be non-negative")
        budget.autopilot_threshold = body.autopilot_threshold

    budget = storage.update_spending_budget(budget)
    return budget.to_dict()


@app.post("/api/spending/request", status_code=201)
async def create_spending_request(body: SpendingRequestCreate, request: Request):
    """
    Request to spend money on a task.

    Validates against the goal's budget limits. If the budget requires
    approval, the request is created as 'pending'. If no approval is
    needed and the amount is within limits, it's auto-approved.
    """
    uid = _require_user(request)
    task = storage.get_task(body.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.goal_id is not None:
        _get_goal_for_user(task.goal_id, uid)  # ownership check

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
async def action_spending_request(request_id: int, body: SpendingAction, request: Request):
    """Approve or deny a pending spending request."""
    uid = _require_user(request)
    req = storage.get_spending_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Spending request not found")

    # Verify ownership: the requesting user must own the task's goal
    task = storage.get_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    _get_task_for_user(task.id, uid)

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
async def list_goal_spending(goal_id: int, request: Request, status: Optional[str] = Query(default=None)):
    """List all spending requests for a goal's tasks."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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


# ─── ROI Dashboard ──────────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/roi")
async def get_goal_roi(goal_id: int, request: Request):
    """Get ROI dashboard for a goal: money spent by AI vs money earned.

    Returns spending breakdown by category, spending timeline, earnings
    from outcome metrics, budget utilization, and overall ROI percentage.
    """
    uid = _require_user(request)
    _check_api_rate_limit(request)
    _get_goal_for_user(goal_id, uid)
    return storage.get_goal_roi(goal_id)


@app.get("/api/users/me/roi")
async def get_user_roi(request: Request):
    """Get aggregate ROI across all of the current user's goals."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    return storage.get_user_roi_summary(uid)


# ─── Platform Insights (Aggregate Learning) ─────────────────────────────────

@app.get("/api/platform/insights")
async def get_platform_insights(request: Request):
    """Get anonymized platform-wide patterns aggregated across all users.

    Returns goal type completion rates, commonly-skipped tasks, popular
    services, proven success paths, and common behavior patterns.
    Used for platform-wide learning and improving AI decomposition.
    """
    _require_user(request)
    _check_api_rate_limit(request)
    return storage.get_platform_patterns()


# ─── External Messaging ─────────────────────────────────────────────────────

@app.post("/api/messaging/config", status_code=201)
async def create_messaging_config(body: MessagingConfigCreate, request: Request):
    """Configure a messaging channel (Telegram or webhook)."""
    import json as _json
    uid = _require_user(request)

    valid_channels = {"telegram", "webhook", "slack", "discord", "whatsapp"}
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
    elif body.channel == "slack":
        if "bot_token" not in body.config or "channel_id" not in body.config:
            raise HTTPException(
                status_code=422,
                detail="Slack config requires 'bot_token' and 'channel_id'",
            )
    elif body.channel == "discord":
        if "webhook_url" not in body.config:
            raise HTTPException(status_code=422, detail="Discord config requires 'webhook_url'")
    elif body.channel == "whatsapp":
        _wa_required = {"access_token", "phone_number_id", "recipient"}
        if not _wa_required.issubset(body.config):
            raise HTTPException(
                status_code=422,
                detail="WhatsApp config requires 'access_token', 'phone_number_id', and 'recipient'",
            )

    cfg = MessagingConfig(
        channel=body.channel,
        config_json=_json.dumps(body.config),
        notify_nudges=body.notify_nudges,
        notify_tasks=body.notify_tasks,
        notify_spending=body.notify_spending,
        notify_checkins=body.notify_checkins,
        user_id=uid,
    )
    cfg = storage.create_messaging_config(cfg)
    return cfg.to_dict()


@app.get("/api/messaging/configs")
async def list_messaging_configs(request: Request):
    """List messaging configurations for the current user."""
    uid = _require_user(request)
    configs = storage.list_messaging_configs(user_id=uid)
    return [c.to_dict() for c in configs]


@app.patch("/api/messaging/config/{config_id}")
async def update_messaging_config_endpoint(config_id: int, body: MessagingConfigUpdate, request: Request):
    """Update a messaging configuration."""
    import json as _json
    uid = _require_user(request)

    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Messaging config not found")
    if cfg.user_id is not None and cfg.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")

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
async def delete_messaging_config_endpoint(config_id: int, request: Request):
    """Delete a messaging configuration."""
    uid = _require_user(request)
    cfg = storage.get_messaging_config(config_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="Messaging config not found")
    if cfg.user_id is not None and cfg.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")
    storage.delete_messaging_config(config_id)
    return {"deleted": config_id}


@app.post("/api/messaging/test/{config_id}")
async def test_messaging(config_id: int, request: Request):
    """Send a test message to a specific messaging channel."""
    uid = _require_user(request)
    cfg = storage.get_messaging_config(config_id)
    if cfg and cfg.user_id is not None and cfg.user_id != uid:
        raise HTTPException(status_code=403, detail="Not authorized")
    result = messaging.send_test_message(config_id)
    if not result.get("success"):
        raise HTTPException(status_code=400, detail=result.get("error", "Test message failed"))
    return result


@app.post("/api/messaging/telegram/webhook")
async def telegram_webhook(body: TelegramUpdate, request: Request):
    """
    Inbound Telegram webhook endpoint.

    Handles /approve, /deny, /goal, /next, /done, /skip commands and maintains
    per-chat conversation state for the full drip question flow.
    Sends a reply back to the user via the Bot API after each command.

    Validates the X-Telegram-Bot-Api-Secret-Token header when
    TEB_TELEGRAM_SECRET_TOKEN is configured.
    """
    # ── Telegram secret token verification ────────────────────────────────
    expected_secret = os.getenv("TEB_TELEGRAM_SECRET_TOKEN", "")
    if expected_secret:
        incoming_secret = request.headers.get("x-telegram-bot-api-secret-token", "")
        if incoming_secret != expected_secret:
            raise HTTPException(status_code=403, detail="Invalid Telegram secret token")
    import re as _re
    import json as _json

    if not body.message:
        return {"ok": True}

    text = body.message.get("text", "").strip()
    chat_id = str(body.message.get("chat", {}).get("id", ""))
    if not text or not chat_id:
        return {"ok": True}

    # Resolve bot_token from the first enabled Telegram messaging config
    bot_token = ""
    tg_configs = [
        c for c in storage.list_messaging_configs(enabled_only=True)
        if c.channel == "telegram"
    ]
    if tg_configs:
        cfg_data = _json.loads(tg_configs[0].config_json) if tg_configs[0].config_json else {}
        bot_token = cfg_data.get("bot_token", "")

    def _reply(msg: str, reply_markup=None) -> None:
        if bot_token:
            messaging.send_telegram_message(bot_token, chat_id, msg, reply_markup=reply_markup)

    # ── /approve {id} ────────────────────────────────────────────────────────
    approve_match = _re.match(r"^/approve +(\d+)$", text)
    if approve_match:
        request_id = int(approve_match.group(1))
        req = storage.get_spending_request(request_id)
        if not req:
            _reply("❌ Spending request not found.")
            return {"ok": True, "error": "Request not found"}
        if req.status != "pending":
            _reply(f"ℹ️ Request #{request_id} is already {req.status}.")
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
        _reply(f"✅ Approved ${req.amount:.2f} for: {req.description}")
        return {"ok": True, "action": "approved", "request_id": request_id}

    # ── /deny {id} [reason] ───────────────────────────────────────────────────
    deny_match = _re.match(r"^/deny +(\d+)(?: (.+))?$", text)
    if deny_match:
        request_id = int(deny_match.group(1))
        reason = deny_match.group(2) or ""
        req = storage.get_spending_request(request_id)
        if not req:
            _reply("❌ Spending request not found.")
            return {"ok": True, "error": "Request not found"}
        if req.status != "pending":
            _reply(f"ℹ️ Request #{request_id} is already {req.status}.")
            return {"ok": True, "error": f"Request already {req.status}"}
        req.status = "denied"
        req.denial_reason = reason
        storage.update_spending_request(req)
        messaging.send_notification("spending_denied", {
            "amount": req.amount,
            "description": req.description,
            "reason": reason,
        })
        _reply(f"🚫 Denied ${req.amount:.2f} for: {req.description}")
        return {"ok": True, "action": "denied", "request_id": request_id}

    # ── /goal <text> ──────────────────────────────────────────────────────────
    goal_match = _re.match(r"^/goal (.+)$", text, _re.S)
    if goal_match:
        goal_text = goal_match.group(1).strip()
        goal = Goal(title=goal_text, description="Created via Telegram")
        goal = storage.create_goal(goal)
        try:
            decomposer.decompose(goal)
            goal.status = "decomposed"
            storage.update_goal(goal)
        except Exception as exc:
            logger.error("Telegram goal decomposition failed for goal %s: %s", goal.id, exc)
            goal.status = "drafting"
            storage.update_goal(goal)
            _reply(f"⚠️ Goal created but auto-planning failed. Use /next to try again or answer questions to refine.")
        # Start question flow: get first drip question
        q = decomposer.get_next_drip_question(goal)
        if q:
            storage.upsert_telegram_session(chat_id, goal.id, "awaiting_answer", q.key)
            _reply(
                f"🎯 Goal created: *{goal.title}*\n\n"
                f"Let me ask you a few quick questions to tailor your plan.\n\n"
                f"❓ {q.text}"
                + (f"\n_(Hint: {q.hint})_" if q.hint else "")
            )
        else:
            storage.upsert_telegram_session(chat_id, goal.id, "idle")
            _reply(
                f"🎯 Goal created: *{goal.title}*\n\n"
                f"Type /next to get your first task."
            )
        return {"ok": True, "action": "goal_created", "goal_id": goal.id}

    # ── Helper: resolve goal from session ─────────────────────────────────────
    def _goal_from_session(cid: str) -> Optional[Goal]:
        """Return the goal bound to a Telegram chat session, or None."""
        session = storage.get_telegram_session(cid)
        if session and session.get("goal_id"):
            return storage.get_goal(session["goal_id"])
        return None

    # ── /next ─────────────────────────────────────────────────────────────────
    if text == "/next":
        goal = _goal_from_session(chat_id)
        if not goal:
            _reply("No goal is selected for this chat. Use /goal <description> to create one.")
            return {"ok": True, "message": "No selected goal"}
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        drip = decomposer.drip_next_task(goal, tasks)
        if drip and drip.get("task"):
            td = drip["task"]
            mins = td.get("estimated_minutes", "?")
            skip_hint = f"\n💡 _{drip['skip_suggestion']}_" if drip.get("skip_suggestion") else ""
            _reply(
                f"📋 *Next task:* {td.get('title', '')}\n"
                f"_{td.get('description', '')}_\n"
                f"⏱ ~{mins} min{skip_hint}\n\n"
                f"When done, type /done"
            )
        else:
            _reply(drip.get("message", "All done! 🎉") if drip else "All done! 🎉")
        return {"ok": True, "action": "drip_next", "drip": drip}

    # ── /done ─────────────────────────────────────────────────────────────────
    if text == "/done":
        goal = _goal_from_session(chat_id)
        if not goal:
            _reply("No goal is selected for this chat. Use /goal <description> to create one.")
            return {"ok": True, "message": "No selected goal"}
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        focus = decomposer.get_focus_task(tasks)
        if focus:
            focus.status = "done"
            storage.update_task(focus)
            tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
            drip = decomposer.drip_next_task(goal, tasks, completed_task=focus)
            if drip and drip.get("task"):
                td = drip["task"]
                mins = td.get("estimated_minutes", "?")
                _reply(
                    f"✅ Marked done: *{focus.title}*\n\n"
                    f"📋 *Next:* {td.get('title', '')}\n"
                    f"_{td.get('description', '')}_\n"
                    f"⏱ ~{mins} min\n\nType /done when finished."
                )
            else:
                _reply(f"✅ Marked done: *{focus.title}*\n\n🎉 All tasks complete!")
            return {"ok": True, "action": "task_done", "drip": drip}
        _reply("No current task to mark done.")
        return {"ok": True, "message": "No current task"}

    # ── /skip ─────────────────────────────────────────────────────────────────
    if text == "/skip":
        goal = _goal_from_session(chat_id)
        if not goal:
            _reply("No goal is selected for this chat. Use /goal <description> to create one.")
            return {"ok": True, "message": "No selected goal"}
        tasks = storage.list_tasks(goal.id)  # type: ignore[arg-type]
        focus = decomposer.get_focus_task(tasks)
        if focus:
            focus.status = "skipped"
            storage.update_task(focus)
            _reply(f"⏭ Skipped: *{focus.title}*\n\nType /next for the next task.")
        else:
            _reply("Nothing to skip.")
        return {"ok": True, "action": "task_skipped"}

    # ── Free text: session-based question flow ────────────────────────────────
    session = storage.get_telegram_session(chat_id)
    if session and session.get("state") == "awaiting_answer" and session.get("pending_question_key"):
        goal_id = session["goal_id"]
        goal = storage.get_goal(goal_id) if goal_id else None
        if goal:
            key = session["pending_question_key"]
            goal.answers[key] = text
            goal.status = "clarifying"
            storage.update_goal(goal)
            # Get next question or move to drip
            next_q = decomposer.get_next_drip_question(goal)
            if next_q:
                storage.upsert_telegram_session(chat_id, goal_id, "awaiting_answer", next_q.key)
                _reply(
                    f"❓ {next_q.text}"
                    + (f"\n_(Hint: {next_q.hint})_" if next_q.hint else "")
                )
            else:
                storage.upsert_telegram_session(chat_id, goal_id, "idle")
                _reply("✅ Got it! Type /next to get your first task.")
            return {"ok": True, "action": "answer_recorded"}

    # Unknown command or message
    _reply(
        "👋 Available commands:\n"
        "/goal <description> — start a new goal\n"
        "/next — get your next task\n"
        "/done — mark current task done\n"
        "/skip — skip current task\n"
        "/approve <id> — approve a spending request\n"
        "/deny <id> [reason] — deny a spending request"
    )
    return {"ok": True}


# ─── Channel Webhook Endpoints ───────────────────────────────────────────────

from teb.channels import route_command as _route_channel_command  # noqa: E402
from teb.channels.slack import SlackChannel as _SlackChannel  # noqa: E402
from teb.channels.discord import DiscordChannel as _DiscordChannel  # noqa: E402
from teb.channels.whatsapp import WhatsAppChannel as _WhatsAppChannel  # noqa: E402


@app.post("/api/channels/slack/webhook")
async def slack_channel_webhook(request: Request):
    """Inbound Slack webhook endpoint.

    Handles Events API callbacks and slash commands, routing recognised
    teb commands through the command router.
    """
    body_bytes = await request.body()

    # Verify Slack signature when a signing secret is configured
    slack = _SlackChannel()
    timestamp = request.headers.get("x-slack-request-timestamp", "")
    signature = request.headers.get("x-slack-signature", "")
    if slack.signing_secret and not slack.verify_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=403, detail="Invalid Slack signature")

    payload = await request.json()
    parsed = slack.receive_command(payload)

    # Handle URL verification challenge
    if "challenge" in parsed:
        return {"challenge": parsed["challenge"]}

    text = parsed.get("text", "")
    if not text:
        return {"ok": True}

    result = _route_channel_command(text, user_id=parsed.get("user_id"))

    # Reply via Slack if we have a channel_id
    channel_id = parsed.get("channel_id", "")
    if channel_id and result.message:
        slack.send_message(channel_id, result.message)

    return {
        "ok": True,
        "command": result.command,
        "success": result.success,
        "message": result.message,
    }


@app.post("/api/channels/discord/webhook")
async def discord_channel_webhook(request: Request):
    """Inbound Discord interactions webhook endpoint.

    Handles PING verification and application command interactions,
    routing recognised teb commands through the command router.
    """
    body_bytes = await request.body()

    discord = _DiscordChannel()
    timestamp = request.headers.get("x-signature-timestamp", "")
    signature = request.headers.get("x-signature-ed25519", "")
    if discord.public_key and not discord.verify_signature(body_bytes, timestamp, signature):
        raise HTTPException(status_code=401, detail="Invalid Discord signature")

    payload = await request.json()
    parsed = discord.receive_command(payload)

    # PING response (type 1)
    if parsed.get("type") == 1:
        return {"type": 1}

    text = parsed.get("text", "")
    if not text:
        return {"type": 4, "data": {"content": "No command received."}}

    result = _route_channel_command(text, user_id=parsed.get("user_id"))

    # Discord interaction response (type 4 = CHANNEL_MESSAGE_WITH_SOURCE)
    return {
        "type": 4,
        "data": {"content": result.message or "Done."},
    }


@app.get("/api/channels/whatsapp/webhook")
async def whatsapp_channel_webhook_verify(
    request: Request,
    hub_mode: str = Query("", alias="hub.mode"),
    hub_token: str = Query("", alias="hub.verify_token"),
    hub_challenge: str = Query("", alias="hub.challenge"),
):
    """WhatsApp webhook verification (GET).

    Meta sends a GET request with hub.mode, hub.verify_token, and
    hub.challenge to verify the webhook endpoint.
    """
    wa = _WhatsAppChannel()
    challenge = wa.verify_webhook(hub_mode, hub_token, hub_challenge)
    if challenge is not None:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(content=challenge)
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/channels/whatsapp/webhook")
async def whatsapp_channel_webhook(request: Request):
    """Inbound WhatsApp Cloud API webhook endpoint.

    Parses incoming messages and routes recognised teb commands through
    the command router.  Replies are sent back via the WhatsApp API.
    """
    payload = await request.json()

    wa = _WhatsAppChannel()
    parsed = wa.receive_command(payload)

    text = parsed.get("text", "")
    sender = parsed.get("user_id", "")
    if not text:
        return {"ok": True}

    result = _route_channel_command(text, user_id=sender)

    # Reply to the sender
    if sender and result.message:
        wa.send_message(sender, result.message)

    return {
        "ok": True,
        "command": result.command,
        "success": result.success,
        "message": result.message,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Payment Integration
# ═══════════════════════════════════════════════════════════════════════════════

from teb import payments as _payments  # noqa: E402


class PaymentAccountCreate(BaseModel):
    provider: str
    account_id: str = ""
    config: dict = {}


class PaymentExecute(BaseModel):
    provider: str
    amount: float
    currency: str = "USD"
    recipient: str = ""
    description: str = ""
    spending_request_id: Optional[int] = None


@app.get("/api/payments/providers")
async def list_payment_providers(request: Request):
    """List available payment providers and their configuration status."""
    _check_api_rate_limit(request)
    _require_user(request)
    return _payments.list_providers()


@app.post("/api/payments/accounts", status_code=201)
async def create_payment_account(body: PaymentAccountCreate, request: Request):
    """Register a payment account (Mercury, Stripe) for the current user."""
    import json as _json
    _check_api_rate_limit(request)
    uid = _require_user(request)
    valid_providers = {"mercury", "stripe"}
    if body.provider not in valid_providers:
        raise HTTPException(status_code=422, detail=f"provider must be one of {valid_providers}")
    account = storage.create_payment_account(
        user_id=uid,
        provider=body.provider,
        account_id=body.account_id,
        config_json=_json.dumps(body.config),
    )
    return account


@app.get("/api/payments/accounts")
async def list_payment_accounts(request: Request):
    """List payment accounts for the current user."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    return storage.list_payment_accounts(uid)


@app.get("/api/payments/balance/{provider}")
async def get_payment_balance(provider: str, request: Request):
    """Get account balance for a payment provider."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    result = _payments.get_account_balance(uid, provider)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@app.post("/api/payments/execute")
async def execute_payment(body: PaymentExecute, request: Request):
    """Execute a real payment through a configured provider.

    The user must have a registered and enabled payment account for the
    specified provider. If a spending_request_id is given, the payment
    is linked to that approval-gated spending request.

    Balance is verified before executing the transfer to prevent
    overdraft. The provider layer retries on transient failures.
    """
    _check_api_rate_limit(request)
    uid = _require_user(request)
    result = _payments.execute_payment(
        user_id=uid,
        provider_name=body.provider,
        amount=body.amount,
        currency=body.currency,
        recipient=body.recipient,
        description=body.description,
        spending_request_id=body.spending_request_id,
    )
    if result.get("status") == "failed":
        raise HTTPException(status_code=400, detail=result.get("error", "Payment failed"))
    return result


@app.get("/api/payments/transactions/{account_id}")
async def list_payment_transactions(account_id: int, request: Request):
    """List transactions for a payment account."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    account = storage.get_payment_account(account_id)
    if not account or account["user_id"] != uid:
        raise HTTPException(status_code=404, detail="Payment account not found")
    return storage.list_payment_transactions(account_id)


# ─── Payment Webhooks ─────────────────────────────────────────────────────────

@app.post("/api/webhooks/payments/{provider}")
async def payment_webhook(provider: str, request: Request):
    """Receive payment status webhooks from Mercury or Stripe.

    Verifies the webhook signature if a secret is configured, then
    reconciles the event with local transaction records.
    """
    valid_providers = {"mercury", "stripe"}
    if provider not in valid_providers:
        raise HTTPException(status_code=404, detail="Unknown provider")

    body = await request.body()
    signature = request.headers.get("x-signature", "") or request.headers.get("stripe-signature", "")

    result = _payments.process_webhook(provider, body, signature)
    if result.get("error"):
        raise HTTPException(status_code=400, detail=result["error"])
    return result


# ─── Transaction Recovery ─────────────────────────────────────────────────────

@app.post("/api/payments/recover")
async def recover_transactions(request: Request):
    """Attempt to recover failed payment transactions.

    Re-checks provider status for each failed transaction with remaining
    retries and reconciles local records accordingly. Admin only.
    """
    _check_api_rate_limit(request)
    _require_admin(request)
    result = _payments.recover_failed_transactions()
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomous Execution Control
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/api/goals/{goal_id}/auto-execute")
async def enable_auto_execute(goal_id: int, request: Request):
    """Enable autonomous execution for a goal.

    When enabled, teb's background loop automatically picks up and executes
    pending tasks for this goal without manual triggering.
    """
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    goal.auto_execute = True
    storage.update_goal(goal)
    return {"goal_id": goal_id, "auto_execute": True, "message": "Autonomous execution enabled."}


@app.delete("/api/goals/{goal_id}/auto-execute")
async def disable_auto_execute(goal_id: int, request: Request):
    """Disable autonomous execution for a goal."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    goal.auto_execute = False
    storage.update_goal(goal)
    return {"goal_id": goal_id, "auto_execute": False, "message": "Autonomous execution disabled."}


@app.get("/api/auto-execute/status")
async def auto_execute_status(request: Request):
    """Get the status of the autonomous execution loop and pending tasks."""
    _require_user(request)
    pending = storage.list_auto_execute_tasks()
    return {
        "loop_enabled": config.AUTONOMOUS_EXECUTION_ENABLED,
        "loop_running": _auto_exec_task is not None and not _auto_exec_task.done(),
        "interval_seconds": config.AUTONOMOUS_EXECUTION_INTERVAL,
        "pending_tasks": len(pending),
        "pending_task_ids": [t.id for t in pending],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Deployment / Infrastructure Lifecycle
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/api/tasks/{task_id}/deploy")
async def deploy_task(task_id: int, request: Request):
    """Deploy an application as part of task execution.

    Analyzes the task description to determine the hosting service
    (Vercel, Railway, Render) and deploys via their API.
    Requires a matching API credential registered via POST /api/credentials.
    """
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
    credentials = storage.list_credentials(user_id=uid)
    plan = deployer.generate_deployment_plan(task, credentials)

    if not plan.can_deploy:
        return {
            "task_id": task_id,
            "deployed": False,
            "plan": plan.to_dict(),
        }

    result = deployer.deploy(plan, credentials, task)

    if result.get("success"):
        task.status = "done"
        storage.update_task(task)
    return {
        "task_id": task_id,
        "deployed": result.get("success", False),
        "result": {k: v for k, v in result.items() if k != "success"},
        "plan": plan.to_dict(),
    }


@app.get("/api/goals/{goal_id}/deployments")
async def list_goal_deployments(goal_id: int, request: Request):
    """List all deployments for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    return storage.list_deployments(goal_id)


@app.get("/api/deployments/{deploy_id}/health")
async def check_deployment_health(deploy_id: int, request: Request):
    """Run a health check on a deployment."""
    _require_user(request)
    return deployer.monitor_deployment(deploy_id)


@app.get("/api/goals/{goal_id}/deployments/health")
async def check_all_deployments_health(goal_id: int, request: Request):
    """Run health checks on all active deployments for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    return deployer.monitor_all_deployments(goal_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Auto-Provisioning (sign up for services)
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/api/tasks/{task_id}/provision")
async def provision_task_service(task_id: int, request: Request):
    """Auto-provision a service (sign up + extract credentials).

    Detects the service from the task description and attempts to
    sign up via browser automation. Without Playwright, returns
    step-by-step manual instructions.
    """
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
    result = provisioning.provision_service(task)
    return {"task_id": task_id, **result}


@app.get("/api/provision/services")
async def list_provisionable_services(request: Request):
    """List all services that can be auto-provisioned."""
    _require_user(request)
    return provisioning.list_provisionable_services()


@app.get("/api/tasks/{task_id}/provisioning-logs")
async def get_provisioning_logs(task_id: int, request: Request):
    """Get provisioning attempt logs for a task."""
    uid = _require_user(request)
    _get_task_for_user(task_id, uid)
    return storage.list_provisioning_logs(task_id)


# ═══════════════════════════════════════════════════════════════════════════════
# Tool / Service Discovery
# ═══════════════════════════════════════════════════════════════════════════════

from teb import discovery as _discovery  # noqa: E402


@app.get("/api/discover/services")
async def discover_services(request: Request, q: Optional[str] = Query(default=None)):
    """Discover tools and services relevant to a goal or query.

    Pass ?q=<goal text> to discover services for a specific goal,
    or omit to discover based on the user's existing goals.
    """
    uid = _get_user_id(request)
    if q:
        return _discovery.discover_for_goal(q)
    if uid:
        return _discovery.discover_for_user(uid)
    return []


@app.get("/api/discover/services/ai")
async def ai_discover_services(request: Request, q: str = Query(...)):
    """Use AI to discover new tools/services for a goal (requires AI key)."""
    _require_user(request)
    return _discovery.ai_discover_services(q)


@app.get("/api/discover/catalog")
async def list_discovery_catalog():
    """List the full built-in discovery catalog (no auth required)."""
    return [
        {
            "service_name": s["service_name"],
            "category": s.get("category", ""),
            "description": s.get("description", ""),
            "url": s.get("url", ""),
            "skill_level": s.get("skill_level", ""),
        }
        for s in _discovery._DISCOVERABLE_SERVICES
    ]


@app.post("/api/discover/record", status_code=201)
async def record_discovered_service(request: Request):
    """Record a newly discovered service for future recommendations."""
    _require_user(request)
    body = await request.json()
    return _discovery.record_discovery(
        service_name=body.get("service_name", ""),
        category=body.get("category", ""),
        description=body.get("description", ""),
        url=body.get("url", ""),
        capabilities=body.get("capabilities", []),
        discovered_by=body.get("discovered_by", "user"),
        relevance_score=body.get("relevance_score", 0.5),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# User Behavior Inference & Abandonment Detection
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/users/me/behaviors")
async def list_user_behaviors(request: Request, behavior_type: Optional[str] = Query(default=None)):
    """List behavior patterns detected for the current user."""
    uid = _require_user(request)
    return storage.list_user_behaviors(uid, behavior_type)


@app.get("/api/users/me/abandonment")
async def detect_abandonment(request: Request):
    """Detect stalled goals and suggest rerouting for the current user.

    Checks for:
    - Goals with no activity in 3+ days
    - Tasks stuck in 'in_progress' for 2+ days
    - High skip rates indicating discomfort
    """
    uid = _require_user(request)
    goals = storage.list_goals(user_id=uid)
    stalled: list[dict] = []
    now = datetime.now(timezone.utc)

    for goal in goals:
        if goal.status in ("done", "drafting"):
            continue

        tasks = storage.list_tasks(goal.id)
        done_count = sum(1 for t in tasks if t.status == "done")
        skip_count = sum(1 for t in tasks if t.status == "skipped")
        in_progress = [t for t in tasks if t.status == "in_progress"]
        total = len(tasks)

        days_since_update = 0
        if goal.updated_at:
            days_since_update = (now - goal.updated_at).days

        issues: list[str] = []
        suggestions: list[str] = []

        # Stale goal (no update in 3+ days)
        if days_since_update >= 3:
            issues.append(f"No activity for {days_since_update} days")
            suggestions.append("Consider breaking the next task into smaller steps")
            # Record behavior
            storage.record_user_behavior(uid, "stalled", f"goal_{goal.id}", f"{days_since_update}_days")

        # High skip rate
        if total > 2 and skip_count / total > 0.4:
            issues.append(f"High skip rate ({skip_count}/{total} tasks skipped)")
            suggestions.append("These tasks may not match your skills — try discovering alternative approaches")
            storage.record_user_behavior(uid, "high_skip_rate", f"goal_{goal.id}",
                                        f"{skip_count}/{total}")

        # Stuck tasks
        for t in in_progress:
            if t.created_at:
                task_days = (now - t.created_at).days
                if task_days >= 2:
                    issues.append(f"Task '{t.title}' stuck for {task_days} days")
                    suggestions.append(f"Try a 15-min quick win: break '{t.title}' into a tiny first step")
                    storage.record_user_behavior(uid, "avoids", t.title.lower().split()[0] if t.title else "unknown")

        if issues:
            stalled.append({
                "goal_id": goal.id,
                "goal_title": goal.title,
                "progress": f"{done_count}/{total} done",
                "days_since_update": days_since_update,
                "issues": issues,
                "suggestions": suggestions,
            })

    return {"stalled_goals": stalled, "total_stalled": len(stalled)}


# ═══════════════════════════════════════════════════════════════════════════════
# Persistent Agent Memory
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/agents/memory/{agent_type}")
async def get_agent_memory(agent_type: str, request: Request,
                           goal_type: Optional[str] = Query(default=None)):
    """Get persistent memories for an agent."""
    _require_user(request)
    return storage.list_agent_memories(agent_type, goal_type or "")


@app.post("/api/agents/memory", status_code=201)
async def store_agent_memory(request: Request):
    """Store a memory for an agent (what worked, what didn't)."""
    _require_user(request)
    body = await request.json()
    return storage.create_agent_memory(
        agent_type=body.get("agent_type", "coordinator"),
        goal_type=body.get("goal_type", ""),
        memory_key=body.get("memory_key", ""),
        memory_value=body.get("memory_value", ""),
        confidence=body.get("confidence", 1.0),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Success Path Knowledge Graph (MVP)
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/knowledge/paths")
async def list_knowledge_paths(request: Request,
                               goal_type: Optional[str] = Query(default=None)):
    """List success paths as a knowledge graph — encoding experience of multiple users.

    Returns proven paths ranked by reuse count, along with common deviations.
    """
    _require_user(request)
    try:
        paths = storage.list_success_paths(goal_type=goal_type) if goal_type else storage.list_success_paths()
    except Exception:
        paths = []

    result = []
    for sp in paths:
        steps = json.loads(sp.steps_json) if sp.steps_json else {}
        deviations = {}
        if isinstance(steps, dict):
            deviations = steps.get("deviations", {})
            steps_list = steps.get("steps", [])
        elif isinstance(steps, list):
            steps_list = steps
        else:
            steps_list = []

        result.append({
            "id": sp.id,
            "goal_type": sp.goal_type,
            "outcome_summary": sp.outcome_summary,
            "times_reused": sp.times_reused,
            "steps_count": len(steps_list),
            "steps": steps_list[:10],  # First 10 steps as preview
            "deviations": deviations,
            "source_goal_id": sp.source_goal_id,
        })

    # Sort by reuse count
    result.sort(key=lambda x: x["times_reused"], reverse=True)
    return {"paths": result, "total": len(result)}


@app.get("/api/knowledge/recommend/{goal_type}")
async def recommend_path(goal_type: str, request: Request):
    """Recommend the best proven path for a goal type.

    Returns the most-reused success path for this goal type, effectively
    encoding the experience of many users into a recommended plan.
    """
    _require_user(request)
    try:
        paths = storage.list_success_paths(goal_type=goal_type)
    except Exception:
        paths = []

    if not paths:
        return {"recommendation": None, "message": f"No proven paths yet for '{goal_type}'"}

    # Pick the most reused
    best = max(paths, key=lambda p: p.times_reused)
    steps = json.loads(best.steps_json) if best.steps_json else {}
    steps_list = steps.get("steps", steps) if isinstance(steps, dict) else steps

    return {
        "recommendation": {
            "path_id": best.id,
            "goal_type": best.goal_type,
            "outcome_summary": best.outcome_summary,
            "times_reused": best.times_reused,
            "steps": steps_list,
        },
        "total_paths": len(paths),
    }


# ─── Admin API ────────────────────────────────────────────────────────────────

@app.get("/api/admin/users")
async def admin_list_users(request: Request):
    """Admin: list all users with goal and task counts."""
    _require_admin(request)
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


@app.get("/api/admin/users/{user_id}")
async def admin_get_user(user_id: int, request: Request):
    """Admin: get a single user's detail plus their goals."""
    _require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    goals = storage.list_goals(user_id=user_id)
    d = user.to_dict()
    d["failed_login_attempts"] = user.failed_login_attempts
    d["locked_until"] = user.locked_until.isoformat() if user.locked_until else None
    d["goals"] = [g.to_dict() for g in goals]
    return d


@app.patch("/api/admin/users/{user_id}")
async def admin_update_user(user_id: int, body: AdminUserUpdate, request: Request):
    """Admin: update user role, lock status, or email_verified."""
    _require_admin(request)
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


@app.delete("/api/admin/users/{user_id}")
async def admin_delete_user(user_id: int, request: Request):
    """Admin: delete a user account and all their data."""
    admin_id = _require_admin(request)
    if user_id == admin_id:
        raise HTTPException(status_code=400, detail="Cannot delete your own account")
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    storage.delete_user(user_id)
    return {"deleted": user_id}


@app.get("/api/admin/stats")
async def admin_get_stats(request: Request):
    """Admin: return aggregate platform statistics."""
    _require_admin(request)
    return storage.get_system_stats()


@app.get("/api/admin/integrations")
async def admin_list_integrations(request: Request):
    """Admin: list all integrations in the DB with full detail."""
    _require_admin(request)
    integrations_list = storage.list_integrations()
    return [i.to_dict() for i in integrations_list]


@app.post("/api/admin/integrations", status_code=201)
async def admin_create_integration(request: Request):
    """Admin: add a new integration entry to the DB catalog."""
    _require_admin(request)
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


@app.delete("/api/admin/integrations/{name}")
async def admin_delete_integration(name: str, request: Request):
    """Admin: remove an integration from the DB by service_name."""
    _require_admin(request)
    existing = storage.get_integration(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Integration not found")
    storage.delete_integration(existing.id)
    return {"deleted": name}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Persistent Agent Goal Memory
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/goals/{goal_id}/agent-memory")
async def list_goal_agent_memories(goal_id: int, request: Request):
    """List all agent working memories for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    mems = storage.list_agent_goal_memories(goal_id)
    return {"memories": [m.to_dict() for m in mems]}


@app.get("/api/goals/{goal_id}/agent-memory/{agent_type}")
async def get_goal_agent_memory(goal_id: int, agent_type: str, request: Request):
    """Get a specific agent's working memory for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    mem = storage.get_or_create_agent_goal_memory(agent_type, goal_id)
    return mem.to_dict()


@app.post("/api/goals/{goal_id}/agent-memory/prune")
async def prune_goal_agent_memory(goal_id: int, request: Request):
    """Prune overly long agent memories for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    storage.prune_agent_goal_memory(goal_id)
    return {"pruned": True, "goal_id": goal_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: Goal Hierarchy (Sub-goals & Milestones)
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/api/goals/{goal_id}/sub-goals", status_code=201)
async def create_sub_goal(goal_id: int, request: Request):
    """Create a sub-goal under a parent goal."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    parent = _get_goal_for_user(goal_id, uid)
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


@app.get("/api/goals/{goal_id}/sub-goals")
async def list_sub_goals(goal_id: int, request: Request):
    """List sub-goals of a parent goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    subs = storage.list_sub_goals(goal_id)
    return {"sub_goals": [s.to_dict() for s in subs]}


@app.get("/api/goals/{goal_id}/hierarchy")
async def get_goal_hierarchy(goal_id: int, request: Request):
    """Get the full goal hierarchy: parent, sub-goals, milestones, and task counts."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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


@app.post("/api/goals/{goal_id}/milestones", status_code=201)
async def create_milestone(goal_id: int, request: Request):
    """Create a milestone for a goal."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    _get_goal_for_user(goal_id, uid)
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


@app.get("/api/goals/{goal_id}/milestones")
async def list_milestones(goal_id: int, request: Request):
    """List milestones for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    milestones = storage.list_milestones(goal_id)
    return {"milestones": [m.to_dict() for m in milestones]}


@app.patch("/api/milestones/{milestone_id}")
async def update_milestone(milestone_id: int, request: Request):
    """Update a milestone (progress, status, etc.)."""
    uid = _require_user(request)
    ms = storage.get_milestone(milestone_id)
    if not ms:
        raise HTTPException(status_code=404, detail="Milestone not found")
    _get_goal_for_user(ms.goal_id, uid)

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


@app.get("/api/events/stream")
async def sse_stream(request: Request,
                     last_event_id: Optional[str] = Query(default=None, alias="Last-Event-ID")):
    """Server-Sent Events stream for real-time updates.

    Clients should use EventSource to connect. Supports Last-Event-ID for reconnection.
    """
    uid = _require_user(request)

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


@app.get("/api/events/status")
async def sse_status(request: Request):
    """Get SSE event bus status."""
    _require_user(request)
    from teb import events as _events  # noqa: E402
    return {
        "subscribers": _events.event_bus.subscriber_count,
        "backlog_size": len(_events.event_bus._backlog),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Step 5: Goal Template Marketplace
# ═══════════════════════════════════════════════════════════════════════════════


@app.post("/api/templates/export/{goal_id}", status_code=201)
async def export_goal_template(goal_id: int, request: Request):
    """Export a completed goal as a reusable template (sanitized of personal data)."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    goal = _get_goal_for_user(goal_id, uid)

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


@app.post("/api/templates/import/{template_id}", status_code=201)
async def import_goal_template(template_id: int, request: Request):
    """Import a template to create a new goal pre-populated with tasks and milestones."""
    uid = _require_user(request)
    _check_api_rate_limit(request)

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


@app.get("/api/templates")
async def list_templates(request: Request,
                         goal_type: Optional[str] = Query(default=None),
                         category: Optional[str] = Query(default=None),
                         skill_level: Optional[str] = Query(default=None)):
    """Browse the goal template marketplace."""
    _require_user(request)
    templates = storage.list_goal_templates(goal_type=goal_type, category=category,
                                            skill_level=skill_level)
    return {"templates": [t.to_dict() for t in templates], "total": len(templates)}


@app.get("/api/templates/{template_id}")
async def get_template(template_id: int, request: Request):
    """Get details of a specific template."""
    _require_user(request)
    tpl = storage.get_goal_template(template_id)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    return tpl.to_dict()


@app.post("/api/templates/{template_id}/rate")
async def rate_template(template_id: int, request: Request):
    """Rate a template (1-5 stars)."""
    _require_user(request)
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


@app.get("/api/goals/{goal_id}/audit")
async def get_goal_audit_trail(goal_id: int, request: Request,
                               event_type: Optional[str] = Query(default=None),
                               limit: int = Query(default=100)):
    """Get the full audit trail for a goal — immutable lifecycle events."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    events = storage.list_audit_events(goal_id=goal_id, event_type=event_type, limit=limit)
    return {"events": [e.to_dict() for e in events], "total": len(events)}


@app.get("/api/audit/events")
async def list_all_audit_events(request: Request,
                                event_type: Optional[str] = Query(default=None),
                                limit: int = Query(default=100)):
    """List audit events across all goals (admin view)."""
    _require_admin(request)
    events = storage.list_audit_events(event_type=event_type, limit=limit)
    return {"events": [e.to_dict() for e in events], "total": len(events)}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 7: MCP Server Exposure
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/mcp/info")
async def mcp_server_info(request: Request):
    """MCP server metadata — tool definitions for AI coding assistants."""
    _require_user(request)
    from teb import mcp_server  # noqa: E402
    return mcp_server.get_server_info()


@app.post("/api/mcp/tools/call")
async def mcp_tool_call(request: Request):
    """Execute an MCP tool call."""
    uid = _require_user(request)
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


@app.get("/api/mcp/tools")
async def mcp_list_tools(request: Request):
    """List available MCP tools."""
    _require_user(request)
    from teb import mcp_server  # noqa: E402
    return {"tools": mcp_server.MCP_TOOLS}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 8: Execution Sandbox Isolation
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/goals/{goal_id}/sandbox")
async def get_execution_sandbox(goal_id: int, request: Request):
    """Get or create the isolated execution context for a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    ctx = storage.get_or_create_execution_context(goal_id)
    return ctx.to_dict()


@app.patch("/api/goals/{goal_id}/sandbox")
async def update_execution_sandbox(goal_id: int, request: Request):
    """Update sandbox configuration (credential scope, etc.)."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    ctx = storage.get_or_create_execution_context(goal_id)
    body = await request.json()
    if "credential_scope" in body:
        scope = body["credential_scope"]
        if isinstance(scope, list):
            ctx.credential_scope = json.dumps(scope)
        elif isinstance(scope, str):
            ctx.credential_scope = scope
        else:
            raise HTTPException(status_code=400, detail="credential_scope must be a list or JSON string")
    ctx = storage.update_execution_context(ctx)
    return ctx.to_dict()


@app.post("/api/goals/{goal_id}/sandbox/cleanup")
async def cleanup_execution_sandbox(goal_id: int, request: Request):
    """Clean up the execution sandbox for a completed goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    storage.cleanup_execution_context(goal_id)
    return {"cleaned_up": True, "goal_id": goal_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Execution Plugin System
# ═══════════════════════════════════════════════════════════════════════════════


@app.get("/api/plugins")
async def list_plugins(request: Request,
                       enabled_only: bool = Query(default=False)):
    """List registered execution plugins."""
    _require_user(request)
    plugins = storage.list_plugins(enabled_only=enabled_only)
    return {"plugins": [p.to_dict() for p in plugins]}


@app.post("/api/plugins", status_code=201)
async def register_plugin(request: Request):
    """Register a new execution plugin."""
    _require_admin(request)
    body = await request.json()
    plugin = PluginManifest(
        name=body.get("name", ""),
        version=body.get("version", "0.1.0"),
        description=body.get("description", ""),
        task_types=json.dumps(body.get("task_types", [])),
        required_credentials=json.dumps(body.get("required_credentials", [])),
        module_path=body.get("module_path", ""),
    )
    if not plugin.name:
        raise HTTPException(status_code=400, detail="Plugin name is required")
    existing = storage.get_plugin(plugin.name)
    if existing:
        raise HTTPException(status_code=409, detail="Plugin already exists")
    plugin = storage.create_plugin(plugin)
    return plugin.to_dict()


@app.delete("/api/plugins/{name}")
async def delete_plugin(name: str, request: Request):
    """Delete a plugin by name."""
    _require_admin(request)
    existing = storage.get_plugin(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Plugin not found")
    storage.delete_plugin(name)
    from teb import plugins as _plugins  # noqa: E402
    _plugins.unregister_executor(name)
    return {"deleted": name}


@app.post("/api/plugins/{name}/execute")
async def execute_plugin(name: str, request: Request):
    """Execute a plugin with given task context and credentials."""
    uid = _require_user(request)
    body = await request.json()
    from teb import plugins as _plugins  # noqa: E402
    result = _plugins.execute_plugin(
        name,
        task_context=body.get("task_context", {}),
        credentials=body.get("credentials", {}),
    )
    return result.to_dict()


@app.get("/api/plugins/match")
async def match_plugins_for_task(request: Request,
                                  task_type: str = Query()):
    """Find plugins that can handle a given task type."""
    _require_user(request)
    from teb import plugins as _plugins  # noqa: E402
    matches = _plugins.find_plugins_for_task(task_type)
    return {"plugins": [p.to_dict() for p in matches]}


# ─── Task Comments (Phase 1, Step 3) ─────────────────────────────────────────

@app.post("/api/tasks/{task_id}/comments", status_code=201)
async def create_task_comment(task_id: int, request: Request):
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
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


@app.get("/api/tasks/{task_id}/comments")
async def list_task_comments(task_id: int, request: Request):
    uid = _require_user(request)
    _get_task_for_user(task_id, uid)
    return [c.to_dict() for c in storage.list_task_comments(task_id)]


@app.delete("/api/comments/{comment_id}", status_code=200)
async def delete_task_comment_endpoint(comment_id: int, request: Request):
    _require_user(request)
    storage.delete_task_comment(comment_id)
    return {"deleted": True}


# ─── Task Artifacts (Phase 1, Step 4) ────────────────────────────────────────

@app.post("/api/tasks/{task_id}/artifacts", status_code=201)
async def create_task_artifact(task_id: int, request: Request):
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
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


@app.get("/api/tasks/{task_id}/artifacts")
async def list_task_artifacts(task_id: int, request: Request):
    uid = _require_user(request)
    _get_task_for_user(task_id, uid)
    return [a.to_dict() for a in storage.list_task_artifacts(task_id)]


@app.delete("/api/artifacts/{artifact_id}", status_code=200)
async def delete_task_artifact_endpoint(artifact_id: int, request: Request):
    _require_user(request)
    storage.delete_task_artifact(artifact_id)
    return {"deleted": True}


# ─── Task Search (Phase 1, Step 2 enhancement) ──────────────────────────────

@app.get("/api/tasks/search")
async def search_tasks(request: Request,
                       goal_id: Optional[int] = Query(default=None),
                       q: Optional[str] = Query(default=None),
                       tags: Optional[str] = Query(default=None),
                       status: Optional[str] = Query(default=None)):
    uid = _require_user(request)
    if goal_id:
        _get_goal_for_user(goal_id, uid)
    results = storage.search_tasks(goal_id=goal_id, query=q or "", tags=tags, status=status)
    return [t.to_dict() for t in results]


# ─── Dependency Graph (Phase 2, Step 5) ──────────────────────────────────────

@app.get("/api/goals/{goal_id}/dependency-graph")
async def get_dependency_graph(goal_id: int, request: Request):
    """Get the dependency DAG for a goal's tasks."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    nodes = []
    edges = []
    for t in tasks:
        nodes.append({"id": t.id, "title": t.title, "status": t.status})
        deps = json.loads(t.depends_on) if t.depends_on else []
        for dep_id in deps:
            edges.append({"from": dep_id, "to": t.id})

    cycle_error = storage.validate_no_cycles(goal_id)
    return {
        "nodes": nodes,
        "edges": edges,
        "has_cycles": cycle_error is not None,
        "cycle_error": cycle_error,
    }


@app.get("/api/goals/{goal_id}/ready-tasks")
async def get_ready_tasks(goal_id: int, request: Request):
    """Get tasks ready to execute (all dependencies satisfied)."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    ready = storage.get_ready_tasks(goal_id)
    return [t.to_dict() for t in ready]


# ─── Execution Replay (Phase 2, Step 6) ──────────────────────────────────────

@app.post("/api/tasks/{task_id}/replay")
async def replay_task_execution(task_id: int, request: Request):
    """Replay the last execution of a failed task."""
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid)
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


# ─── Webhooks (Phase 2, Step 7) ──────────────────────────────────────────────

@app.post("/api/webhooks", status_code=201)
async def create_webhook(request: Request):
    uid = _require_user(request)
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=422, detail="url is required")
    from teb import security as _sec  # noqa: E402
    if not _sec.is_safe_url(url):
        raise HTTPException(status_code=422, detail="URL targets a private or disallowed address")
    events = body.get("events", [])
    if not isinstance(events, list):
        raise HTTPException(status_code=422, detail="events must be a list")
    wh = WebhookConfig(
        user_id=uid,
        url=url,
        events=json.dumps(events),
        secret=str(body.get("secret", "")),
        enabled=bool(body.get("enabled", True)),
    )
    wh = storage.create_webhook_config(wh)
    return wh.to_dict()


@app.get("/api/webhooks")
async def list_webhooks(request: Request):
    uid = _require_user(request)
    return [wh.to_dict() for wh in storage.list_webhook_configs(uid)]


@app.patch("/api/webhooks/{webhook_id}")
async def update_webhook(webhook_id: int, request: Request):
    uid = _require_user(request)
    wh = storage.get_webhook_config(webhook_id)
    if not wh or wh.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook not found")
    body = await request.json()
    if "url" in body:
        url = str(body["url"]).strip()
        from teb import security as _sec  # noqa: E402
        if not _sec.is_safe_url(url):
            raise HTTPException(status_code=422, detail="URL targets a private or disallowed address")
        wh.url = url
    if "events" in body:
        wh.events = json.dumps(body["events"])
    if "enabled" in body:
        wh.enabled = bool(body["enabled"])
    if "secret" in body:
        wh.secret = str(body["secret"])
    wh = storage.update_webhook_config(wh)
    return wh.to_dict()


@app.delete("/api/webhooks/{webhook_id}", status_code=200)
async def delete_webhook(webhook_id: int, request: Request):
    uid = _require_user(request)
    wh = storage.get_webhook_config(webhook_id)
    if not wh or wh.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook not found")
    storage.delete_webhook_config(webhook_id)
    return {"deleted": True}


# ─── Import Adapters (Phase 3, Step 9) ───────────────────────────────────────

@app.post("/api/import/trello", status_code=201)
async def import_trello_board(request: Request):
    """Import a Trello board JSON export into teb goals and tasks."""
    uid = _require_user(request)
    body = await request.json()
    board = body.get("board", {})
    if not board or not isinstance(board, dict):
        raise HTTPException(status_code=422, detail="board (Trello JSON export) is required")

    board_name = board.get("name", "Imported Trello Board")
    goal = Goal(title=board_name, description=board.get("desc", ""))
    goal.user_id = uid
    goal.tags = "imported,trello"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created = []
    lists_data = board.get("lists", [])
    cards_data = board.get("cards", [])

    # Map list IDs to names for tagging
    list_names = {lst["id"]: lst.get("name", "") for lst in lists_data if not lst.get("closed")}

    for idx, card in enumerate(cards_data):
        if card.get("closed"):
            continue
        list_name = list_names.get(card.get("idList", ""), "")
        status = "todo"
        if list_name.lower() in ("done", "complete", "completed", "finished"):
            status = "done"
        elif list_name.lower() in ("in progress", "doing", "wip"):
            status = "in_progress"

        task = Task(
            goal_id=goal.id,
            title=card.get("name", f"Card {idx+1}"),
            description=card.get("desc", ""),
            status=status,
            order_index=idx,
            tags=f"trello,{list_name}" if list_name else "trello",
        )
        if card.get("due"):
            task.due_date = card["due"][:10]  # extract date part
        task = storage.create_task(task)
        tasks_created.append(task)

    return {
        "goal": goal.to_dict(),
        "tasks_imported": len(tasks_created),
    }


@app.post("/api/import/asana", status_code=201)
async def import_asana_project(request: Request):
    """Import an Asana project (simplified JSON) into teb goals and tasks."""
    uid = _require_user(request)
    body = await request.json()
    project = body.get("project", {})
    if not project or not isinstance(project, dict):
        raise HTTPException(status_code=422, detail="project (Asana JSON) is required")

    project_name = project.get("name", "Imported Asana Project")
    goal = Goal(title=project_name, description=project.get("notes", ""))
    goal.user_id = uid
    goal.tags = "imported,asana"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created = []
    asana_tasks = project.get("tasks", [])

    for idx, at in enumerate(asana_tasks):
        completed = at.get("completed", False)
        task = Task(
            goal_id=goal.id,
            title=at.get("name", f"Task {idx+1}"),
            description=at.get("notes", ""),
            status="done" if completed else "todo",
            order_index=idx,
            tags="asana",
        )
        if at.get("due_on"):
            task.due_date = at["due_on"]
        task = storage.create_task(task)
        tasks_created.append(task)

        # Import subtasks
        for si, sub in enumerate(at.get("subtasks", [])):
            sub_task = Task(
                goal_id=goal.id,
                parent_id=task.id,
                title=sub.get("name", f"Subtask {si+1}"),
                description=sub.get("notes", ""),
                status="done" if sub.get("completed") else "todo",
                order_index=si,
                tags="asana",
            )
            sub_task = storage.create_task(sub_task)
            tasks_created.append(sub_task)

    return {
        "goal": goal.to_dict(),
        "tasks_imported": len(tasks_created),
    }


# ─── Adaptive Pacing (Phase 3, Step 10) ──────────────────────────────────────

@app.get("/api/goals/{goal_id}/pacing")
async def get_goal_pacing(goal_id: int, request: Request):
    """Analyze user's pacing and suggest adjustments."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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


# ─── Outcome Attribution (Phase 3, Step 11) ──────────────────────────────────

@app.get("/api/goals/{goal_id}/impact")
async def get_goal_impact(goal_id: int, request: Request):
    """Trace which tasks/agents contributed most to goal outcomes."""
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)

    tasks = storage.list_tasks(goal_id=goal_id)
    metrics = storage.list_outcome_metrics(goal_id)
    audit_events = storage.list_audit_events(goal_id=goal_id)
    exec_logs: list = []
    for t in tasks:
        logs = storage.list_execution_logs(t.id)
        for log in logs:
            exec_logs.append({"task_id": t.id, "task_title": t.title, "log": log.to_dict()})

    # Build impact attribution
    agent_contributions: dict = {}
    for evt in audit_events:
        if evt.actor_type == "agent":
            agent_contributions[evt.actor_id] = agent_contributions.get(evt.actor_id, 0) + 1

    task_impact = []
    for t in tasks:
        if t.status == "done":
            logs_count = sum(1 for el in exec_logs if el["task_id"] == t.id)
            task_impact.append({
                "task_id": t.id,
                "title": t.title,
                "executions": logs_count,
            })

    return {
        "goal_id": goal_id,
        "metrics": [m.to_dict() for m in metrics],
        "agent_contributions": agent_contributions,
        "task_impact": sorted(task_impact, key=lambda x: x["executions"], reverse=True),
        "total_audit_events": len(audit_events),
    }


# ─── Sync Adapters (Phase 4, Step 13) ────────────────────────────────────────

@app.post("/api/sync/trello/export")
async def export_to_trello_format(request: Request):
    """Export a goal as Trello-compatible JSON for import."""
    uid = _require_user(request)
    body = await request.json()
    goal_id = body.get("goal_id")
    if not goal_id:
        raise HTTPException(status_code=422, detail="goal_id is required")
    goal = _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Build Trello-compatible board structure
    lists = [
        {"id": "todo", "name": "To Do", "closed": False},
        {"id": "in_progress", "name": "In Progress", "closed": False},
        {"id": "done", "name": "Done", "closed": False},
    ]
    status_to_list = {
        "todo": "todo", "in_progress": "in_progress", "executing": "in_progress",
        "done": "done", "skipped": "done", "failed": "todo",
    }
    cards = []
    for t in tasks:
        card = {
            "name": t.title,
            "desc": t.description,
            "idList": status_to_list.get(t.status, "todo"),
            "closed": False,
        }
        if t.due_date:
            card["due"] = t.due_date
        cards.append(card)

    return {
        "name": goal.title,
        "desc": goal.description,
        "lists": lists,
        "cards": cards,
    }


@app.post("/api/sync/asana/export")
async def export_to_asana_format(request: Request):
    """Export a goal as Asana-compatible JSON for import."""
    uid = _require_user(request)
    body = await request.json()
    goal_id = body.get("goal_id")
    if not goal_id:
        raise HTTPException(status_code=422, detail="goal_id is required")
    goal = _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Build Asana-compatible structure
    asana_tasks = []
    task_map: dict = {}
    for t in tasks:
        task_map[t.id] = t

    top_level = [t for t in tasks if t.parent_id is None]
    for t in top_level:
        subtasks = [c for c in tasks if c.parent_id == t.id]
        at = {
            "name": t.title,
            "notes": t.description,
            "completed": t.status in ("done", "skipped"),
            "subtasks": [
                {
                    "name": s.title,
                    "notes": s.description,
                    "completed": s.status in ("done", "skipped"),
                }
                for s in subtasks
            ],
        }
        if t.due_date:
            at["due_on"] = t.due_date
        asana_tasks.append(at)

    return {
        "name": goal.title,
        "notes": goal.description,
        "tasks": asana_tasks,
    }


# ─── Execution State Machine (WP-01) ─────────────────────────────────────────
from teb import state_machine  # noqa: E402


@app.post("/api/goals/{goal_id}/resume")
async def resume_goal_execution(goal_id: int, request: Request):
    """Resume goal execution from the last checkpoint."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    state = state_machine.resume_execution(goal_id)
    if not state:
        raise HTTPException(status_code=404, detail="No active checkpoint to resume from")
    return {"resumed": True, "state": state.to_dict()}


@app.get("/api/goals/{goal_id}/checkpoints")
async def list_goal_checkpoints(goal_id: int, request: Request):
    """List execution checkpoints for a goal."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    return state_machine.get_execution_summary(goal_id)


# ─── Agent Flows & Schedules (WP-02) ─────────────────────────────────────────
from teb.models import AgentFlow, AgentSchedule  # noqa: E402


@app.post("/api/goals/{goal_id}/flows")
async def create_agent_flow_endpoint(goal_id: int, request: Request):
    """Create an event-driven agent flow for a goal."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    body = await request.json()
    steps = body.get("steps", [])
    if not steps:
        raise HTTPException(status_code=400, detail="Steps are required")
    flow = AgentFlow(goal_id=goal_id, steps_json=json.dumps(steps), status="pending")
    flow = storage.create_agent_flow(flow)
    return flow.to_dict()


@app.get("/api/goals/{goal_id}/flows")
async def list_agent_flows_endpoint(goal_id: int, request: Request):
    """List agent flows for a goal."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    flows = storage.list_agent_flows(goal_id)
    return [f.to_dict() for f in flows]


@app.post("/api/agents/{agent_type}/schedule")
async def configure_agent_schedule(agent_type: str, request: Request):
    """Configure heartbeat schedule for an agent on a goal."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    goal_id = body.get("goal_id")
    if not goal_id:
        raise HTTPException(status_code=400, detail="goal_id is required")
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    schedule = AgentSchedule(agent_type=agent_type, goal_id=goal_id,
                             interval_hours=body.get("interval_hours", 8))
    schedule = storage.create_agent_schedule(schedule)
    return schedule.to_dict()


# ─── Gamification (WP-04) ────────────────────────────────────────────────────
from teb import gamification  # noqa: E402


@app.get("/api/users/me/xp")
async def get_user_xp(request: Request):
    """Get current user's XP, level, and streak."""
    uid = _require_user(request)
    uxp = storage.get_or_create_user_xp(uid)
    return uxp.to_dict()


@app.get("/api/users/me/achievements")
async def get_user_achievements(request: Request):
    """Get current user's achievements."""
    uid = _require_user(request)
    return [a.to_dict() for a in storage.list_achievements(uid)]


# ─── Semantic Search (WP-05) ─────────────────────────────────────────────────
from teb import search as teb_search  # noqa: E402


@app.get("/api/search")
async def search_all(request: Request, q: str = "", limit: int = 50):
    """Search across all entities."""
    uid = _require_user(request)
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    results = teb_search.quick_search(q, user_id=uid, limit=min(limit, 100))
    return {"query": q, "count": len(results), "results": results}


@app.post("/api/search/reindex")
async def reindex_search(request: Request):
    """Rebuild the search index."""
    uid = _require_user(request)
    teb_search.init_search_index()
    counts = teb_search.reindex_all(user_id=uid)
    return {"status": "reindexed", "counts": counts}


# ─── Natural Language Task Input (WP-06) ─────────────────────────────────────
from teb import nlp_input  # noqa: E402


@app.post("/api/tasks/parse")
async def parse_task_text_endpoint(request: Request):
    """Parse natural language text into structured task fields."""
    uid = _require_user(request)
    body = await request.json()
    text = body.get("text", "").strip()
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    return {"parsed": nlp_input.parse_task_text(text), "original": text}


@app.post("/api/goals/{goal_id}/quick-add")
async def quick_add_task(goal_id: int, request: Request):
    """Parse natural language and create a task in one step."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.post("/api/goals/{goal_id}/clone")
async def clone_goal(goal_id: int, request: Request):
    """Clone a goal with all its tasks."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.post("/api/tasks/{task_id}/time")
async def log_time_entry(task_id: int, request: Request):
    """Log a time entry for a task."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.get("/api/tasks/{task_id}/time")
async def get_time_entries(task_id: int, request: Request):
    """List time entries for a task."""
    uid = _require_user(request)
    entries = storage.list_time_entries(task_id)
    total = storage.get_task_total_time(task_id)
    return {"entries": [e.to_dict() for e in entries], "total_minutes": total}


# ─── Goal Activity Feed (WP-09) ──────────────────────────────────────────────


@app.get("/api/goals/{goal_id}/activity")
async def get_goal_activity(goal_id: int, request: Request):
    """Get activity feed for a goal from audit events."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    events = storage.list_audit_events(goal_id=goal_id, limit=50)
    return [e.to_dict() for e in events]


# ─── Task Recurrence (WP-10) ─────────────────────────────────────────────────


@app.post("/api/tasks/{task_id}/recurrence")
async def set_task_recurrence(task_id: int, request: Request):
    """Set or update recurrence rule for a task."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.get("/api/tasks/{task_id}/recurrence")
async def get_task_recurrence(task_id: int, request: Request):
    """Get recurrence rule for a task."""
    uid = _require_user(request)
    rule = storage.get_recurrence_rule(task_id)
    if not rule:
        return {"recurrence": None}
    return rule.to_dict()


@app.delete("/api/tasks/{task_id}/recurrence")
async def delete_task_recurrence(task_id: int, request: Request):
    """Remove recurrence rule from a task."""
    uid = _require_user(request)
    storage.delete_recurrence_rule(task_id)
    return {"deleted": True}


# ─── Goal Collaboration (WP-11) ──────────────────────────────────────────────


@app.post("/api/goals/{goal_id}/collaborators")
async def add_goal_collaborator(goal_id: int, request: Request):
    """Add a collaborator to a goal."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    body = await request.json()
    collab_user_id = body.get("user_id")
    if not collab_user_id:
        raise HTTPException(status_code=400, detail="user_id is required")
    if collab_user_id == uid:
        raise HTTPException(status_code=400, detail="Cannot add yourself as collaborator")
    collab = GoalCollaborator(goal_id=goal_id, user_id=collab_user_id,
                              role=body.get("role", "viewer"))
    collab = storage.add_collaborator(collab)
    return collab.to_dict()


@app.get("/api/goals/{goal_id}/collaborators")
async def list_goal_collaborators(goal_id: int, request: Request):
    """List collaborators for a goal."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    return [c.to_dict() for c in storage.list_collaborators(goal_id)]


@app.delete("/api/goals/{goal_id}/collaborators/{collab_user_id}")
async def remove_goal_collaborator(goal_id: int, collab_user_id: int, request: Request):
    """Remove a collaborator from a goal."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    storage.remove_collaborator(goal_id, collab_user_id)
    return {"deleted": True}


# ─── Custom Fields (WP-12) ───────────────────────────────────────────────────


@app.post("/api/tasks/{task_id}/fields")
async def add_custom_field(task_id: int, request: Request):
    """Add a custom field to a task."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    name = body.get("field_name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="field_name is required")
    cf = CustomField(task_id=task_id, field_name=name,
                     field_value=body.get("field_value", ""),
                     field_type=body.get("field_type", "text"))
    cf = storage.create_custom_field(cf)
    return cf.to_dict()


@app.get("/api/tasks/{task_id}/fields")
async def list_custom_fields_endpoint(task_id: int, request: Request):
    """List custom fields for a task."""
    uid = _require_user(request)
    return [f.to_dict() for f in storage.list_custom_fields(task_id)]


@app.delete("/api/fields/{field_id}")
async def delete_custom_field_endpoint(field_id: int, request: Request):
    """Delete a custom field."""
    uid = _require_user(request)
    storage.delete_custom_field(field_id)
    return {"deleted": True}


# ─── Bulk Task Operations (WP-13) ────────────────────────────────────────────


@app.post("/api/goals/{goal_id}/tasks/bulk")
async def bulk_task_operations(goal_id: int, request: Request):
    """Batch update/delete tasks."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    body = await request.json()
    task_ids = body.get("task_ids", [])
    operation = body.get("operation", "")
    if not task_ids:
        raise HTTPException(status_code=400, detail="task_ids is required")
    if operation not in ("update_status", "delete", "move"):
        raise HTTPException(status_code=400, detail="operation must be update_status, delete, or move")
    affected = 0
    for tid in task_ids:
        task = storage.get_task(tid)
        if not task or task.goal_id != goal_id:
            continue
        if operation == "update_status":
            new_status = body.get("status", "todo")
            task.status = new_status
            storage.update_task(task)
            affected += 1
        elif operation == "delete":
            storage.delete_task(tid)
            affected += 1
        elif operation == "move":
            target_goal_id = body.get("target_goal_id")
            if target_goal_id:
                target_goal = storage.get_goal(target_goal_id)
                if target_goal and target_goal.user_id == uid:
                    task.goal_id = target_goal_id
                    storage.update_task(task)
                    affected += 1
    return {"operation": operation, "affected": affected}


# ─── Goal Progress Snapshots (WP-14) ─────────────────────────────────────────


@app.post("/api/goals/{goal_id}/snapshots")
async def capture_goal_snapshot(goal_id: int, request: Request):
    """Capture a progress snapshot for a goal."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    snap = storage.capture_progress_snapshot(goal_id)
    return snap.to_dict()


@app.get("/api/goals/{goal_id}/snapshots")
async def list_goal_snapshots(goal_id: int, request: Request):
    """List progress snapshots for a goal."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    return [s.to_dict() for s in storage.list_progress_snapshots(goal_id)]


# ─── Task Priority Levels (WP-15) ────────────────────────────────────────────


@app.put("/api/tasks/{task_id}/priority")
async def set_task_priority(task_id: int, request: Request):
    """Set priority on a task (stored as a tag)."""
    uid = _require_user(request)
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    priority = body.get("priority", "medium")
    valid = {"critical", "high", "medium", "low"}
    if priority not in valid:
        raise HTTPException(status_code=400, detail=f"priority must be one of {valid}")
    # Store priority as a special tag — remove existing priority tags first
    existing_tags = [t.strip() for t in task.tags.split(",") if t.strip()] if task.tags else []
    filtered = [t for t in existing_tags if t not in valid]
    filtered.append(priority)
    task.tags = ",".join(filtered)
    task = storage.update_task(task)
    return task.to_dict()


@app.get("/api/goals/{goal_id}/tasks/by-priority")
async def list_tasks_by_priority(goal_id: int, request: Request):
    """List tasks grouped by priority level."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    priority_order = {"critical": 0, "high": 1, "medium": 2, "low": 3}
    result: dict = {"critical": [], "high": [], "medium": [], "low": [], "unset": []}
    for t in tasks:
        tags = [tg.strip() for tg in t.tags.split(",") if tg.strip()] if t.tags else []
        found = False
        for p in priority_order:
            if p in tags:
                result[p].append(t.to_dict())
                found = True
                break
        if not found:
            result["unset"].append(t.to_dict())
    return result


# ─── Notification Preferences (WP-16) ────────────────────────────────────────


@app.get("/api/users/me/notifications/preferences")
async def get_notification_preferences(request: Request):
    """Get notification preferences for current user."""
    uid = _require_user(request)
    return [p.to_dict() for p in storage.list_notification_preferences(uid)]


@app.put("/api/users/me/notifications/preferences")
async def set_notification_preference_endpoint(request: Request):
    """Set a notification preference."""
    uid = _require_user(request)
    body = await request.json()
    pref = NotificationPreference(
        user_id=uid,
        channel=body.get("channel", "in_app"),
        event_type=body.get("event_type", "all"),
        enabled=body.get("enabled", True),
    )
    pref = storage.set_notification_preference(pref)
    return pref.to_dict()


# ─── API Key Management (WP-17) ──────────────────────────────────────────────

import hashlib  # noqa: E402
import secrets  # noqa: E402


@app.post("/api/users/me/api-keys")
async def create_api_key(request: Request):
    """Create a personal API key."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="name is required")
    raw_key = f"teb_{secrets.token_urlsafe(32)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    key = PersonalApiKey(user_id=uid, name=name, key_hash=key_hash,
                         key_prefix=raw_key[:12])
    key = storage.create_personal_api_key(key)
    result = key.to_dict()
    result["key"] = raw_key  # Only returned on creation
    return result


@app.get("/api/users/me/api-keys")
async def list_api_keys(request: Request):
    """List personal API keys."""
    uid = _require_user(request)
    return [k.to_dict() for k in storage.list_personal_api_keys(uid)]


@app.delete("/api/users/me/api-keys/{key_id}")
async def delete_api_key(key_id: int, request: Request):
    """Revoke a personal API key."""
    uid = _require_user(request)
    storage.delete_personal_api_key(key_id, uid)
    return {"deleted": True}


# ─── Goal Export (WP-18) ─────────────────────────────────────────────────────


@app.get("/api/goals/{goal_id}/export")
async def export_goal(goal_id: int, request: Request, format: str = "markdown"):
    """Export a goal to markdown or JSON format."""
    uid = _require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    if format == "json":
        return {
            "goal": goal.to_dict(),
            "tasks": [t.to_dict() for t in tasks],
        }
    # Default: markdown
    lines = [f"# {goal.title}", "", goal.description or "_No description_", ""]
    lines.append("## Tasks\n")
    status_emoji = {"done": "\u2705", "in_progress": "\U0001F551", "todo": "\u2B1C",
                    "failed": "\u274C", "skipped": "\u23ED\uFE0F", "executing": "\u26A1"}
    for t in sorted(tasks, key=lambda x: x.order_index):
        emoji = status_emoji.get(t.status, "\u2B1C")
        lines.append(f"- {emoji} **{t.title}** ({t.status}, {t.estimated_minutes}m)")
        if t.description:
            lines.append(f"  > {t.description}")
    lines.append(f"\n---\n_Exported from teb_")
    md = "\n".join(lines)
    return JSONResponse(content={"format": "markdown", "content": md})


# ─── Task Blockers (WP-19) ───────────────────────────────────────────────────


@app.post("/api/tasks/{task_id}/blockers")
async def add_task_blocker(task_id: int, request: Request):
    """Add a blocker to a task."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    body = await request.json()
    desc = body.get("description", "").strip()
    if not desc:
        raise HTTPException(status_code=400, detail="description is required")
    blocker = TaskBlocker(task_id=task_id, description=desc,
                          blocker_type=body.get("blocker_type", "internal"))
    blocker = storage.create_task_blocker(blocker)
    return blocker.to_dict()


@app.get("/api/tasks/{task_id}/blockers")
async def list_task_blockers_endpoint(task_id: int, request: Request, status: Optional[str] = None):
    """List blockers for a task."""
    uid = _require_user(request)
    return [b.to_dict() for b in storage.list_task_blockers(task_id, status=status)]


@app.post("/api/blockers/{blocker_id}/resolve")
async def resolve_blocker(blocker_id: int, request: Request):
    """Resolve a task blocker."""
    uid = _require_user(request)
    blocker = storage.resolve_task_blocker(blocker_id)
    if not blocker:
        raise HTTPException(status_code=404, detail="Blocker not found")
    return blocker.to_dict()


# ─── Dashboard Widgets (WP-20) ───────────────────────────────────────────────


@app.get("/api/users/me/dashboard")
async def get_dashboard(request: Request):
    """Get user's dashboard widget configuration."""
    uid = _require_user(request)
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


@app.post("/api/users/me/dashboard/widgets")
async def add_dashboard_widget(request: Request):
    """Add a widget to user's dashboard."""
    uid = _require_user(request)
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


@app.put("/api/users/me/dashboard/widgets/{widget_id}")
async def update_widget(widget_id: int, request: Request):
    """Update a dashboard widget."""
    uid = _require_user(request)
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


@app.delete("/api/users/me/dashboard/widgets/{widget_id}")
async def delete_widget(widget_id: int, request: Request):
    """Delete a dashboard widget."""
    uid = _require_user(request)
    storage.delete_dashboard_widget(widget_id, uid)
    return {"deleted": True}


# ─── Phase 2: Workspace endpoints ────────────────────────────────────────────

@app.post("/api/workspaces", status_code=201)
async def create_workspace_endpoint(request: Request):
    """Create a new workspace and auto-add the owner as a member."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.get("/api/workspaces")
async def list_workspaces_endpoint(request: Request):
    """List workspaces for the current user."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    return [w.to_dict() for w in storage.list_user_workspaces(uid)]


@app.get("/api/workspaces/{ws_id}")
async def get_workspace_endpoint(ws_id: int, request: Request):
    """Get workspace details."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    ws = storage.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    members = storage.list_workspace_members(ws_id)
    member_ids = [m.user_id for m in members]
    if uid not in member_ids:
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return ws.to_dict()


@app.post("/api/workspaces/{ws_id}/members", status_code=201)
async def add_workspace_member_endpoint(ws_id: int, request: Request):
    """Add a member to a workspace."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.get("/api/workspaces/{ws_id}/members")
async def list_workspace_members_endpoint(ws_id: int, request: Request):
    """List members of a workspace."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    ws = storage.get_workspace(ws_id)
    if not ws:
        raise HTTPException(status_code=404, detail="Workspace not found")
    members = storage.list_workspace_members(ws_id)
    if not any(m.user_id == uid for m in members):
        raise HTTPException(status_code=403, detail="Not a member of this workspace")
    return [m.to_dict() for m in members]


@app.delete("/api/workspaces/{ws_id}/members/{member_uid}")
async def remove_workspace_member_endpoint(ws_id: int, member_uid: int, request: Request):
    """Remove a member from a workspace."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.post("/api/workspaces/join")
async def join_workspace_endpoint(request: Request):
    """Join a workspace by invite code."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


# ─── Phase 2: Notification endpoints ─────────────────────────────────────────

@app.get("/api/notifications")
async def list_notifications_endpoint(request: Request,
                                      unread_only: bool = Query(False),
                                      limit: int = Query(50)):
    """List notifications for the current user."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    return [n.to_dict() for n in storage.list_user_notifications(uid, unread_only=unread_only, limit=limit)]


@app.post("/api/notifications/{notif_id}/read")
async def mark_notification_read_endpoint(notif_id: int, request: Request):
    """Mark a single notification as read."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    ok = storage.mark_notification_read(notif_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"read": True}


@app.post("/api/notifications/read-all")
async def mark_all_notifications_read_endpoint(request: Request):
    """Mark all notifications as read."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    count = storage.mark_all_notifications_read(uid)
    return {"marked": count}


@app.get("/api/notifications/count")
async def count_notifications_endpoint(request: Request):
    """Get unread notification count."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    return {"unread": storage.count_unread_notifications(uid)}


# ─── Phase 2: Activity Feed endpoint ─────────────────────────────────────────

@app.get("/api/activity")
async def list_activity_endpoint(request: Request,
                                 goal_id: Optional[int] = Query(None),
                                 workspace_id: Optional[int] = Query(None),
                                 limit: int = Query(50)):
    """List activity feed entries."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    return [e.to_dict() for e in storage.list_activity_feed(
        user_id=uid, goal_id=goal_id, workspace_id=workspace_id, limit=limit,
    )]


# ─── Phase 2: Comment Reactions endpoints ─────────────────────────────────────

@app.post("/api/comments/{comment_id}/reactions", status_code=201)
async def add_comment_reaction_endpoint(comment_id: int, request: Request):
    """Add an emoji reaction to a comment."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.delete("/api/comments/{comment_id}/reactions/{emoji}")
async def remove_comment_reaction_endpoint(comment_id: int, emoji: str, request: Request):
    """Remove an emoji reaction from a comment."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    removed = storage.remove_comment_reaction(comment_id, uid, emoji)
    if not removed:
        raise HTTPException(status_code=404, detail="Reaction not found")
    return {"deleted": True}


@app.get("/api/comments/{comment_id}/reactions")
async def list_comment_reactions_endpoint(comment_id: int, request: Request):
    """List reactions on a comment."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    return [r.to_dict() for r in storage.list_comment_reactions(comment_id)]


# ── Phase 4: Intelligence ─────────────────────────────────────────────


@app.get("/api/goals/{goal_id}/schedule", tags=["intelligence"])
async def get_ai_schedule(goal_id: int, request: Request):
    """Auto-schedule tasks into time blocks respecting dependencies and capacity."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.auto_schedule_tasks(tasks)


@app.get("/api/goals/{goal_id}/smart-priority", tags=["intelligence"])
async def get_smart_priority(goal_id: int, request: Request):
    """ML-based priority ranking of tasks."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.smart_prioritize(tasks)


@app.get("/api/goals/{goal_id}/completion-estimate", tags=["intelligence"])
async def get_completion_estimate(goal_id: int, request: Request):
    """Predict goal completion date based on remaining work and velocity."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.estimate_completion(tasks)


@app.get("/api/goals/{goal_id}/risks", tags=["intelligence"])
async def get_risks(goal_id: int, request: Request):
    """Detect at-risk tasks: overdue, blocked, stagnant, overloaded."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.detect_risks(tasks)


@app.get("/api/goals/{goal_id}/focus-blocks", tags=["intelligence"])
async def get_focus_blocks(goal_id: int, request: Request, available_hours: int = 4):
    """Suggest optimal focus work blocks grouped by tags and task size."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.suggest_focus_blocks(tasks, available_hours=available_hours)


@app.get("/api/goals/{goal_id}/duplicates", tags=["intelligence"])
async def get_duplicates(goal_id: int, request: Request):
    """Detect potential duplicate tasks using word overlap similarity."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    return scheduler.detect_duplicates(tasks)


@app.post("/api/goals/{goal_id}/auto-prioritize", tags=["intelligence"])
async def auto_prioritize(goal_id: int, request: Request):
    """Apply smart prioritization to all tasks and update their order_index."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    ranked = scheduler.smart_prioritize(tasks)
    task_map = {t.id: t for t in tasks}
    for idx, entry in enumerate(ranked):
        task = task_map.get(entry["task_id"])
        if task:
            task.order_index = idx
            storage.update_task(task)
    return {"updated": len(ranked), "ranking": ranked}


# ─── ASGI app for deployment ──────────────────────────────────────────────────
# When BASE_PATH is set (e.g., "/teb"), wrap the app so it handles requests
# routed through a reverse proxy at that sub-path.
# Tests and local dev import `app` directly (BASE_PATH defaults to "").
# Production deploys via `uvicorn teb.main:asgi_app`.


class _PrefixMiddleware:
    """Strip BASE_PATH prefix so the inner app sees /api/..., /static/..., etc.

    Requests that don't start with the prefix (e.g. GET /health for infra
    probes sent directly to the port) are forwarded unchanged.

    Note: We intentionally do NOT modify ``root_path``.  Starlette's
    ``get_route_path()`` (used by ``Mount`` and ``StaticFiles``) subtracts
    ``root_path`` from ``path`` to decide which file to serve.  If we
    accumulated the prefix into ``root_path``, the subtraction would fail
    for mounted sub-apps (e.g. ``/teb/static/style.css`` → ``root_path``
    becomes ``/teb/static`` which is not a prefix of the stripped ``path``
    ``/static/style.css``), causing every static-file request to 404.
    """

    def __init__(self, inner, prefix: str) -> None:
        self._inner = inner
        self._prefix = prefix.rstrip("/")
        self._prefix_slash = self._prefix + "/"

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path == self._prefix or path.startswith(self._prefix_slash):
                # Strip the prefix so the inner app sees its natural paths
                new_path = path[len(self._prefix):] or "/"
                scope = dict(scope, path=new_path)
        await self._inner(scope, receive, send)


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6 — Enterprise: 2FA & Session Management
# ═══════════════════════════════════════════════════════════════════════════════

@app.post("/api/auth/2fa/setup")
async def setup_2fa(request: Request):
    """Generate TOTP secret and backup codes for 2FA setup."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    from teb import totp as _totp  # noqa: E402
    existing = storage.get_two_factor_config(uid)
    if existing and existing.is_enabled:
        raise HTTPException(400, "2FA is already enabled")
    secret = _totp.generate_secret()
    backup_codes = _totp.generate_backup_codes()
    import json as _json, hashlib as _hl
    hashed = _json.dumps([_hl.sha256(c.encode()).hexdigest() for c in backup_codes])
    from teb.models import TwoFactorConfig  # noqa: E402
    cfg = TwoFactorConfig(user_id=uid, totp_secret=secret, is_enabled=False, backup_codes_hash=hashed)
    storage.save_two_factor_config(cfg)
    user = storage.get_user(uid)
    email = user.email if user else "user@teb"
    uri = _totp.get_totp_uri(secret, email)
    return {"secret": secret, "uri": uri, "backup_codes": backup_codes}


@app.post("/api/auth/2fa/verify")
async def verify_2fa(request: Request):
    """Verify TOTP code and enable 2FA."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    body = await request.json()
    code = body.get("code", "")
    from teb import totp as _totp  # noqa: E402
    cfg = storage.get_two_factor_config(uid)
    if not cfg or not cfg.totp_secret:
        raise HTTPException(400, "Run 2FA setup first")
    if not _totp.verify_totp(cfg.totp_secret, code):
        raise HTTPException(400, "Invalid TOTP code")
    cfg.is_enabled = True
    storage.save_two_factor_config(cfg)
    return {"enabled": True}


@app.post("/api/auth/2fa/disable")
async def disable_2fa(request: Request):
    """Disable 2FA (requires current TOTP code)."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    body = await request.json()
    code = body.get("code", "")
    from teb import totp as _totp  # noqa: E402
    cfg = storage.get_two_factor_config(uid)
    if not cfg or not cfg.is_enabled:
        raise HTTPException(400, "2FA is not enabled")
    if not _totp.verify_totp(cfg.totp_secret, code):
        raise HTTPException(400, "Invalid TOTP code")
    storage.disable_two_factor(uid)
    return {"enabled": False}


@app.get("/api/auth/2fa/status")
async def get_2fa_status(request: Request):
    """Check 2FA status."""
    uid = _require_user(request)
    cfg = storage.get_two_factor_config(uid)
    return {"enabled": bool(cfg and cfg.is_enabled)}


@app.get("/api/auth/sessions")
async def list_sessions(request: Request):
    """List active sessions."""
    uid = _require_user(request)
    sessions = storage.list_user_sessions(uid)
    return {"sessions": [s.to_dict() for s in sessions]}


@app.delete("/api/auth/sessions/{session_id}")
async def revoke_session_endpoint(session_id: int, request: Request):
    """Revoke a specific session."""
    uid = _require_user(request)
    ok = storage.revoke_session(session_id, uid)
    if not ok:
        raise HTTPException(404, "Session not found")
    return {"revoked": True}


@app.delete("/api/auth/sessions")
async def revoke_all_sessions_endpoint(request: Request):
    """Revoke all other sessions."""
    uid = _require_user(request)
    count = storage.revoke_all_sessions(uid)
    return {"revoked_count": count}


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Remaining Collaboration Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Goal Sharing ────────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/share", status_code=201)
async def share_goal_endpoint(goal_id: int, request: Request):
    """Share a goal with another user."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    body = await request.json()
    target_user_id = body.get("user_id")
    if not target_user_id:
        raise HTTPException(status_code=422, detail="user_id is required")
    role = body.get("role", "viewer")
    if role not in ("viewer", "editor", "admin"):
        raise HTTPException(status_code=422, detail="role must be viewer, editor, or admin")
    collab = storage.share_goal(goal_id, target_user_id, role)
    # Notify the target user
    storage.create_notification(Notification(
        user_id=target_user_id,
        title=f"A goal has been shared with you",
        body=f"You now have {role} access to goal #{goal_id}",
        notification_type="info",
        source_type="goal",
        source_id=goal_id,
    ))
    return collab.to_dict()


@app.get("/api/goals/{goal_id}/collaborators")
async def list_goal_collaborators_endpoint(goal_id: int, request: Request):
    """List collaborators on a goal."""
    uid = _require_user(request)
    return [c.to_dict() for c in storage.list_goal_collaborators(goal_id)]


@app.delete("/api/goals/{goal_id}/share/{target_user_id}")
async def unshare_goal_endpoint(goal_id: int, target_user_id: int, request: Request):
    """Remove a collaborator from a goal."""
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    removed = storage.unshare_goal(goal_id, target_user_id)
    if not removed:
        raise HTTPException(status_code=404, detail="Collaborator not found")
    return {"removed": True}


# ─── Task Assignment ─────────────────────────────────────────────────────────

@app.put("/api/tasks/{task_id}/assign")
async def assign_task_endpoint(task_id: int, request: Request):
    """Assign a task to a user."""
    uid = _require_user(request)
    _get_task_for_user(task_id, uid)
    body = await request.json()
    assignee_id = body.get("user_id")
    task = storage.assign_task(task_id, assignee_id)
    # Notify the assignee
    if assignee_id and assignee_id != uid:
        storage.create_notification(Notification(
            user_id=assignee_id,
            title=f"You have been assigned to task: {task.title}",
            body=f"Task #{task_id} has been assigned to you",
            notification_type="assignment",
            source_type="task",
            source_id=task_id,
        ))
        from teb import events as _events
        _events.event_bus.publish(assignee_id, "task_assigned", {
            "task_id": task_id, "title": task.title, "assigned_by": uid,
        })
    return task.to_dict()


@app.get("/api/users/me/assigned-tasks")
async def list_my_assigned_tasks(request: Request):
    """List tasks assigned to the current user."""
    uid = _require_user(request)
    tasks = storage.list_tasks_assigned_to(uid)
    return [t.to_dict() for t in tasks]


# ─── Presence Indicators ─────────────────────────────────────────────────────

import time as _time_module

_presence_store: dict[str, dict[int, float]] = {}  # "type:id" -> {user_id: timestamp}
_PRESENCE_TTL = 60  # seconds


@app.post("/api/presence/heartbeat")
async def presence_heartbeat(request: Request):
    """Update presence for the current user on a resource."""
    uid = _require_user(request)
    body = await request.json()
    resource_type = body.get("resource_type", "")
    resource_id = body.get("resource_id", "")
    if not resource_type or resource_id == "":
        raise HTTPException(status_code=422, detail="resource_type and resource_id are required")
    key = f"{resource_type}:{resource_id}"
    now = _time_module.time()
    if key not in _presence_store:
        _presence_store[key] = {}
    _presence_store[key][uid] = now
    # Clean stale entries
    _presence_store[key] = {u: ts for u, ts in _presence_store[key].items() if now - ts < _PRESENCE_TTL}
    return {"status": "ok"}


@app.get("/api/presence/{resource_type}/{resource_id}")
async def get_presence(resource_type: str, resource_id: str, request: Request):
    """Get active users on a resource."""
    _require_user(request)
    key = f"{resource_type}:{resource_id}"
    now = _time_module.time()
    users = _presence_store.get(key, {})
    active = [uid for uid, ts in users.items() if now - ts < _PRESENCE_TTL]
    # Also clean up while we're here
    if key in _presence_store:
        _presence_store[key] = {u: ts for u, ts in _presence_store[key].items() if now - ts < _PRESENCE_TTL}
    return {"resource_type": resource_type, "resource_id": resource_id, "active_users": active}


# ─── Direct Messaging ────────────────────────────────────────────────────────

@app.post("/api/messages", status_code=201)
async def send_message_endpoint(request: Request):
    """Send a direct message to another user."""
    uid = _require_user(request)
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


@app.get("/api/messages/conversations")
async def list_conversations_endpoint(request: Request):
    """List conversation partners for the current user."""
    uid = _require_user(request)
    return storage.list_conversations(uid)


@app.get("/api/messages/{other_user_id}")
async def list_messages_endpoint(other_user_id: int, request: Request):
    """List messages between current user and another user."""
    uid = _require_user(request)
    messages = storage.list_messages(uid, other_user_id)
    return [m.to_dict() for m in messages]


@app.put("/api/messages/{message_id}/read")
async def mark_message_read_endpoint(message_id: int, request: Request):
    """Mark a message as read."""
    uid = _require_user(request)
    ok = storage.mark_message_read(message_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Message not found or not your message")
    return {"read": True}


# ─── Goal-Scoped Chat ────────────────────────────────────────────────────────

@app.post("/api/goals/{goal_id}/chat", status_code=201)
async def create_goal_chat_endpoint(goal_id: int, request: Request):
    """Send a chat message in a goal's chat room."""
    uid = _require_user(request)
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


@app.get("/api/goals/{goal_id}/chat")
async def list_goal_chat_endpoint(goal_id: int, request: Request):
    """List chat messages for a goal."""
    _require_user(request)
    messages = storage.list_goal_chat_messages(goal_id)
    return [m.to_dict() for m in messages]


# ─── Email Notification Preferences ──────────────────────────────────────────

@app.get("/api/users/me/email-preferences")
async def get_email_preferences(request: Request):
    """Get email notification preferences."""
    uid = _require_user(request)
    cfg = storage.get_email_notification_config(uid)
    if not cfg:
        cfg = EmailNotificationConfig(user_id=uid)
    return cfg.to_dict()


@app.put("/api/users/me/email-preferences")
async def update_email_preferences(request: Request):
    """Update email notification preferences."""
    uid = _require_user(request)
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

@app.post("/api/push/subscribe", status_code=201)
async def push_subscribe(request: Request):
    """Register a push notification subscription."""
    uid = _require_user(request)
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


@app.delete("/api/push/unsubscribe")
async def push_unsubscribe(request: Request):
    """Remove a push notification subscription."""
    uid = _require_user(request)
    body = await request.json()
    endpoint = str(body.get("endpoint", "")).strip()
    if not endpoint:
        raise HTTPException(status_code=422, detail="endpoint is required")
    removed = storage.delete_push_subscription(endpoint, uid)
    if not removed:
        raise HTTPException(status_code=404, detail="Subscription not found")
    return {"removed": True}


# ─── Phase 3: Saved Views ────────────────────────────────────────────────────

@app.post("/api/views", status_code=201)
async def create_saved_view(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
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


@app.get("/api/views")
async def list_views_endpoint(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    views = storage.list_saved_views(uid)
    return [v.to_dict() for v in views]


@app.get("/api/views/{view_id}")
async def get_view_endpoint(view_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    view = storage.get_saved_view(view_id)
    if not view:
        raise HTTPException(status_code=404, detail="View not found")
    return view.to_dict()


@app.delete("/api/views/{view_id}")
async def delete_view_endpoint(view_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    storage.delete_saved_view(view_id, uid)
    return {"deleted": True}


# ─── Phase 3: Dashboard Layouts ──────────────────────────────────────────────

@app.post("/api/dashboards", status_code=201)
async def create_dashboard_endpoint(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    body = await request.json()
    layout = DashboardLayout(
        user_id=uid,
        name=body.get("name", "My Dashboard"),
        widgets_json=json.dumps(body.get("widgets", [])),
    )
    layout = storage.save_dashboard(layout)
    return layout.to_dict()


@app.get("/api/dashboards")
async def list_dashboards_endpoint(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    dashboards = storage.list_dashboards(uid)
    return [d.to_dict() for d in dashboards]


@app.get("/api/dashboards/{dashboard_id}")
async def get_dashboard_endpoint(dashboard_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    dashboard = storage.get_dashboard(dashboard_id)
    if not dashboard:
        raise HTTPException(status_code=404, detail="Dashboard not found")
    return dashboard.to_dict()


@app.put("/api/dashboards/{dashboard_id}")
async def update_dashboard_endpoint(dashboard_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
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


@app.delete("/api/dashboards/{dashboard_id}")
async def delete_dashboard_endpoint(dashboard_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    storage.delete_dashboard(dashboard_id, uid)
    return {"deleted": True}


# ─── Phase 3: Goal Progress Timeline ────────────────────────────────────────

@app.get("/api/goals/{goal_id}/timeline")
async def get_goal_timeline_endpoint(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    snapshots = storage.get_goal_progress_timeline(goal_id)
    return [s.to_dict() for s in snapshots]


# ─── Phase 3: Export Reports ─────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/export")
async def export_goal_endpoint(goal_id: int, request: Request, format: str = Query("json")):
    _check_api_rate_limit(request)
    _require_user(request)
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

@app.post("/api/reports/scheduled", status_code=201)
async def create_scheduled_report_endpoint(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    body = await request.json()
    report = ScheduledReport(
        user_id=uid,
        report_type=body.get("report_type", "progress"),
        frequency=body.get("frequency", "weekly"),
        recipients_json=json.dumps(body.get("recipients", [])),
    )
    report = storage.create_scheduled_report(report)
    return report.to_dict()


@app.get("/api/reports/scheduled")
async def list_scheduled_reports_endpoint(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    reports = storage.list_scheduled_reports(uid)
    return [r.to_dict() for r in reports]


@app.delete("/api/reports/scheduled/{report_id}")
async def delete_scheduled_report_endpoint(report_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    storage.delete_scheduled_report(report_id, uid)
    return {"deleted": True}


# ─── Phase 3: Burndown / Burnup Chart Data ──────────────────────────────────

@app.get("/api/goals/{goal_id}/burndown")
async def get_burndown_endpoint(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    data = storage.get_burndown_data(goal_id)
    return data


# ─── Phase 3: Time Tracking Reports ─────────────────────────────────────────

@app.get("/api/goals/{goal_id}/time-report")
async def get_time_report_endpoint(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    report = storage.get_time_tracking_report(goal_id)
    return report


if config.BASE_PATH:
    asgi_app = _PrefixMiddleware(app, config.BASE_PATH)
else:
    asgi_app = app


def cli() -> None:
    """Entry point for ``pip install teb`` → ``teb`` command."""
    import uvicorn

    try:
        port = int(os.getenv("PORT", "8000"))
    except ValueError:
        print("Error: PORT must be a number", file=sys.stderr)
        sys.exit(1)

    uvicorn.run(
        "teb.main:asgi_app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )
