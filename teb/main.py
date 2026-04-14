from __future__ import annotations

import collections
import json
import logging
import logging.config
import os
import re
import sys
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Form, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from teb import agents, auth, browser, config, decomposer, deployer, executor, intelligence, integrations, messaging, provisioning, scheduler, storage, transcribe
from teb.models import (
    ActivityFeedEntry, AgentGoalMemory, ApiCredential, AuditEvent, BrandingConfig, BrowserAction, CheckIn,
    CommentReaction, CustomField, CustomFieldDefinition, DashboardLayout, DashboardWidget, DirectMessage, EmailNotificationConfig,
    ExecutionLog, Goal, GoalChatMessage, GoalCollaborator,
    GoalTemplate, IPAllowlist, IntegrationListing, IntegrationTemplate, MessagingConfig, Milestone, Notification, NotificationPreference,
    NudgeEvent, OAuthConnection, Organization, OutcomeMetric, PersonalApiKey, PluginListing, PluginManifest, PluginView,
    ProgressSnapshot, PushSubscription, RecurrenceRule, SSOConfig, SavedView, ScheduledReport,
    SpendingBudget, SpendingRequest,
    Task, TaskArtifact, TaskBlocker, TaskComment, Theme, TimeEntry, WebhookConfig, WebhookRule,
    Workspace, WorkspaceMember,
)

# ─── Domain routers (extracted from monolith) ─────────────────────────────────
from teb.routers.health import router as health_router, set_start_time as _set_health_start_time, increment_request_metrics
from teb.routers.auth import router as auth_router, set_rate_limiter as _set_auth_rate_limiter
from teb.routers.settings import router as settings_router
from teb.routers.notifications import router as notifications_router, set_rate_limiter as _set_notif_rate_limiter
from teb.routers.admin import router as admin_router
from teb.routers.financial import router as financial_router
from teb.routers.messaging import router as messaging_router
from teb.routers.mcp import router as mcp_router
from teb.routers.execution import router as execution_router
from teb.routers.deps import set_api_rate_limiter as _set_api_rate_limiter

# ─── Logging ──────────────────────────────────────────────────────────────────

class _JsonFormatter(logging.Formatter):
    """Structured JSON log formatter for production-grade observability."""

    def format(self, record: logging.LogRecord) -> str:
        log_entry: Dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            log_entry["exception"] = self.formatException(record.exc_info)
        # Include request_id if available via extra
        if hasattr(record, "request_id"):
            log_entry["request_id"] = record.request_id
        return json.dumps(log_entry, default=str)


_LOG_FORMAT = os.getenv("TEB_LOG_FORMAT", "text")  # "json" or "text"

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

# Apply JSON formatter when configured
if _LOG_FORMAT == "json":
    _json_fmt = _JsonFormatter()
    for handler in logging.root.handlers:
        handler.setFormatter(_json_fmt)

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
    _set_health_start_time(_APP_START_TIME)

    # ── Structured logging ────────────────────────────────────────────────
    try:
        from teb.logging_config import configure_logging
        configure_logging()
    except Exception:
        pass  # Fall back to default logging

    # ── Sentry error tracking ────────────────────────────────────────────
    if config.SENTRY_DSN:
        try:
            import sentry_sdk  # noqa: PLC0415
            sentry_sdk.init(
                dsn=config.SENTRY_DSN,
                traces_sample_rate=0.1,
                environment=config.TEB_ENV,
                release="teb@2.0.0",
            )
            logger.info("Sentry initialized (env=%s)", config.TEB_ENV)
        except ImportError:
            logger.warning("TEB_SENTRY_DSN is set but sentry-sdk is not installed. pip install sentry-sdk")
        except Exception as exc:
            logger.warning("Failed to initialize Sentry: %s", exc)

    # ── Configuration validation at startup ───────────────────────────────
    _validate_startup_config()

    storage.init_db()
    integrations.seed_integrations()
    storage.reset_all_daily_spending()
    _rate_buckets.clear()

    # Log configuration summary (mask secrets)
    _log_startup_config()

    # Start autonomous execution loop
    _auto_exec_task = asyncio.create_task(_autonomous_execution_loop())
    logger.info("teb started — version 2.0.0, PID %d", os.getpid())
    yield
    # ── Graceful shutdown ─────────────────────────────────────────────────
    logger.info("Shutting down teb…")
    if _auto_exec_task:
        _auto_exec_task.cancel()
        try:
            await _auto_exec_task
        except asyncio.CancelledError:
            pass
    # Drain SSE event bus
    try:
        from teb import events as _events
        _events.event_bus.shutdown()
    except Exception:
        pass
    logger.info("Shutdown complete.")


def _validate_startup_config() -> None:
    """Check critical configuration at startup and log warnings."""
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
    # Warn about wildcard CORS in non-development environments
    if config.CORS_ORIGINS == ["*"] and os.getenv("TEB_ENV", "development") != "development":
        logger.warning(
            "TEB_CORS_ORIGINS='*' in non-development environment. "
            "Set specific origins for production security."
        )


def _log_startup_config() -> None:
    """Log a configuration summary at startup, masking sensitive values."""
    def _mask(val: Optional[str]) -> str:
        if not val:
            return "(not set)"
        if len(val) <= 8:
            return "****"
        return val[:4] + "****" + val[-4:]

    logger.info(
        "Configuration: ai_provider=%s, db=%s, cors_origins=%s, "
        "autonomous_exec=%s, log_level=%s, base_path=%s",
        config.get_ai_provider() or "none",
        config.DATABASE_URL.split("///")[-1] if "///" in config.DATABASE_URL else config.DATABASE_URL,
        config.CORS_ORIGINS[:3],
        config.AUTONOMOUS_EXECUTION_ENABLED,
        config.LOG_LEVEL,
        config.BASE_PATH or "/",
    )


tags_metadata = [
    # ── Core ──────────────────────────────────────────────────────────────
    {
        "name": "health",
        "description": (
            "Service health probes and observability. Includes liveness and readiness "
            "checks for container orchestrators, plus a Prometheus-compatible metrics "
            "endpoint for monitoring dashboards."
        ),
    },
    {
        "name": "auth",
        "description": (
            "Authentication and user-identity management. Register and log in with "
            "email/password (bcrypt-hashed), obtain and refresh JWT access tokens, "
            "and retrieve the current user profile. Rate-limited to 20 requests/min "
            "with timing-safe responses for unknown emails."
        ),
    },
    {
        "name": "settings",
        "description": (
            "User settings, profile management, and API credential storage. "
            "Manage encrypted credentials (Fernet-protected when TEB_SECRET_KEY is "
            "set) and update user profile fields."
        ),
    },
    {
        "name": "notifications",
        "description": (
            "In-app notification delivery and management. List, read, and bulk-mark "
            "notifications. Supports unread-count polling for real-time UI badges."
        ),
    },
    # ── Goal → Task lifecycle ─────────────────────────────────────────────
    {
        "name": "goals",
        "description": (
            "Goal CRUD and AI-powered decomposition. Create high-level goals, "
            "trigger decomposition into executable tasks (AI or template fallback), "
            "manage goal metadata, collaborators, milestones, budgets, and ROI "
            "tracking. The starting point of teb's core loop: "
            "Goal → Clarify → Decompose → Execute → Measure → Learn."
        ),
    },
    {
        "name": "tasks",
        "description": (
            "Task management, status transitions, and autonomous execution. "
            "Create, update, reorder, and complete tasks within a goal. "
            "Supports dependency tracking, time estimates, assignees, "
            "and autonomous execution via the built-in executor."
        ),
    },
    {
        "name": "cross-goal",
        "description": (
            "Cross-goal task views and saved filters. Query tasks across all goals "
            "for the current user, and retrieve tasks matching a saved view's filter "
            "criteria — enabling portfolio-level dashboards and unified task lists."
        ),
    },
    {
        "name": "dag",
        "description": (
            "Directed Acyclic Graph operations for task dependency management. "
            "Visualize the full dependency graph for a goal, validate that no cycles "
            "exist, and trigger DAG-aware parallel execution plans that respect "
            "task ordering constraints."
        ),
    },
    # ── Intelligence ──────────────────────────────────────────────────────
    {
        "name": "intelligence",
        "description": (
            "AI-powered intelligence layer. Includes smart scheduling, auto-"
            "prioritization, completion time estimates, risk detection, focus-block "
            "recommendations, duplicate detection, stagnation checks, AI writing "
            "assistance, meeting-to-tasks conversion, tag suggestion, workflow "
            "optimization, skill-gap analysis, and status report generation. "
            "Every endpoint falls back to heuristic/template results when no AI "
            "provider is configured."
        ),
    },
    {
        "name": "risk",
        "description": (
            "Task-level risk assessment and goal-level triage. Evaluate individual "
            "task risk factors (blockers, overdue status, complexity) and run "
            "automated triage across all tasks in a goal to surface the highest-"
            "priority issues."
        ),
    },
    {
        "name": "scheduling",
        "description": (
            "AI-assisted schedule generation and calendar integration. Auto-schedule "
            "tasks into optimal time slots respecting dependencies and capacity, "
            "and retrieve the current user's consolidated schedule across all goals."
        ),
    },
    {
        "name": "reporting",
        "description": (
            "Progress reporting and scheduled report generation. Create on-demand "
            "goal status reports and retrieve the history of generated reports "
            "for audit and stakeholder communication."
        ),
    },
    {
        "name": "workload",
        "description": (
            "Workload analysis and rebalancing. View the current user's workload "
            "distribution across goals and trigger automated task rebalancing to "
            "prevent overcommitment and optimize throughput."
        ),
    },
    # ── Learning & Memory ─────────────────────────────────────────────────
    {
        "name": "success-graph",
        "description": (
            "Success pattern tracking and path analysis. Aggregate statistics on "
            "completed goals, discover the most effective execution paths, and "
            "query historical success patterns to inform future goal planning."
        ),
    },
    {
        "name": "execution-memory",
        "description": (
            "Execution memory and institutional knowledge. Retrieve per-goal "
            "execution history, view aggregate memory statistics, and get "
            "AI-generated advice based on past execution patterns — closing the "
            "Learn phase of teb's core loop."
        ),
    },
    {
        "name": "gamification",
        "description": (
            "Gamification, streaks, leaderboards, and challenges. Track daily "
            "completion streaks, view XP-based leaderboards, create and participate "
            "in challenges, and record progress — inspired by Habitica-style "
            "motivation systems adapted for real-world goal execution."
        ),
    },
    # ── Content & Customization ───────────────────────────────────────────
    {
        "name": "content-blocks",
        "description": (
            "Rich content blocks for goals and tasks. Attach, reorder, update, "
            "and delete structured content blocks (text, checklists, embeds) on "
            "any entity — providing Notion-style flexible documentation within "
            "the execution context."
        ),
    },
    {
        "name": "themes",
        "description": (
            "UI theme management. Browse available themes, create custom themes, "
            "activate a theme for the current session, and retrieve the currently "
            "active theme configuration."
        ),
    },
    # ── Integrations & Extensibility ──────────────────────────────────────
    {
        "name": "integrations",
        "description": (
            "External service integrations and OAuth connections. Browse the "
            "integration directory, initiate and complete OAuth flows, apply "
            "integration templates, and manage Zapier triggers, actions, and "
            "subscriptions. Includes per-integration rate-limit status."
        ),
    },
    {
        "name": "webhooks",
        "description": (
            "Outbound webhook rule management. Create, list, update, delete, and "
            "test webhook delivery rules. Payloads are signed with HMAC-SHA256 "
            "for verification by the receiving service."
        ),
    },
    {
        "name": "plugins",
        "description": (
            "Plugin marketplace and extensibility SDK. Browse and install plugins "
            "from the marketplace, register custom fields and custom views, and "
            "access SDK documentation for building third-party extensions."
        ),
    },
    {
        "name": "mcp-client",
        "description": (
            "Model Context Protocol (MCP) client operations. Register external MCP "
            "servers, discover their available tools, invoke tools by name, and "
            "search across all registered servers for matching capabilities."
        ),
    },
    # ── Data Portability ──────────────────────────────────────────────────
    {
        "name": "import",
        "description": (
            "Bulk data import from external platforms. Import goals and tasks from "
            "Monday.com, Jira, ClickUp, or generic CSV files. Each importer maps "
            "the source schema to teb's goal/task model and returns a summary "
            "of created entities."
        ),
    },
    {
        "name": "export",
        "description": (
            "Data export and schema introspection. Export a complete goal with all "
            "tasks, dependencies, and metadata as a portable JSON bundle, or "
            "retrieve the export schema definition for integration tooling."
        ),
    },
    # ── Enterprise ────────────────────────────────────────────────────────
    {
        "name": "enterprise",
        "description": (
            "Enterprise administration and platform operations. Includes SSO/SAML "
            "configuration, IP allowlisting, audit logging, organization management, "
            "member provisioning, platform analytics, custom branding, compliance "
            "reporting, database and cache diagnostics, Prometheus metrics, CDN "
            "configuration, horizontal scaling status, and multi-region deployment."
        ),
    },
    {
        "name": "scim",
        "description": (
            "SCIM 2.0 user provisioning API. Standards-compliant endpoints for "
            "automated user lifecycle management — list, create, read, update, and "
            "deactivate users from enterprise identity providers (Okta, Azure AD, "
            "OneLogin, etc.)."
        ),
    },
    # ── Community & Documentation ─────────────────────────────────────────
    {
        "name": "documentation",
        "description": (
            "API documentation and changelog. Retrieve the structured changelog "
            "of API and platform updates."
        ),
    },
    {
        "name": "community",
        "description": (
            "Community hub — template gallery, plugin directory, blog, and public "
            "roadmap. Browse and share goal templates, discover community plugins, "
            "read and publish blog posts, view the product roadmap, and vote on "
            "upcoming features."
        ),
    },
]

app = FastAPI(
    title="teb API",
    description="""
# teb — Task Execution Bridge

> Humans are will without infinite execution; AI is infinite execution without will —
> teb sits at that seam, taking your raw intentions and dissolving everything beneath
> them into solved problems.

## Core Loop

Every endpoint in this API serves one phase of teb's execution cycle:

```
Goal → Clarify → Decompose → Execute → Measure → Learn
```

## Endpoint Groups

### Core
| Group | Purpose |
|---|---|
| **health** | Liveness, readiness probes, and Prometheus metrics |
| **auth** | JWT registration, login, token refresh, and user identity |
| **settings** | User profile and encrypted credential management |
| **notifications** | In-app notification delivery and read tracking |

### Goal → Task Lifecycle
| Group | Purpose |
|---|---|
| **goals** | Goal CRUD, AI/template decomposition, milestones, budgets, ROI |
| **tasks** | Task CRUD, status transitions, dependencies, autonomous execution |
| **cross-goal** | Portfolio-level task queries and saved-view filters |
| **dag** | Dependency graph visualization, cycle validation, parallel execution |

### Intelligence
| Group | Purpose |
|---|---|
| **intelligence** | AI scheduling, prioritization, estimates, focus blocks, writing assist |
| **risk** | Task risk assessment and goal-level triage |
| **scheduling** | Auto-scheduling into calendar slots with dependency awareness |
| **reporting** | On-demand and scheduled progress reports |
| **workload** | Workload analysis and automated rebalancing |

### Learning & Memory
| Group | Purpose |
|---|---|
| **success-graph** | Historical success patterns and optimal execution paths |
| **execution-memory** | Per-goal execution history and AI-generated advice |
| **gamification** | Streaks, XP leaderboards, and challenges |

### Content & Customization
| Group | Purpose |
|---|---|
| **content-blocks** | Rich content blocks (text, checklists, embeds) on any entity |
| **themes** | UI theme browsing, creation, and activation |

### Integrations & Extensibility
| Group | Purpose |
|---|---|
| **integrations** | OAuth connections, Zapier, integration directory and templates |
| **webhooks** | Outbound webhook rules with HMAC-SHA256 signing |
| **plugins** | Marketplace, custom fields, custom views, and SDK docs |
| **mcp-client** | MCP server registration, tool discovery, and invocation |

### Data Portability
| Group | Purpose |
|---|---|
| **import** | Bulk import from Monday.com, Jira, ClickUp, and CSV |
| **export** | Full goal export and schema introspection |

### Enterprise
| Group | Purpose |
|---|---|
| **enterprise** | SSO, IP allowlists, audit logs, orgs, branding, compliance, scaling |
| **scim** | SCIM 2.0 automated user provisioning |

### Community
| Group | Purpose |
|---|---|
| **documentation** | API changelog |
| **community** | Template gallery, plugin directory, blog, and public roadmap |

## Authentication

Most endpoints require a valid JWT bearer token. Obtain one via `POST /api/auth/login`
and pass it in the `Authorization: Bearer <token>` header. Tokens can be refreshed
via `POST /api/auth/refresh`. Auth endpoints are rate-limited to **20 requests/min**;
all other API endpoints are rate-limited to **120 requests/min**.

## AI Fallback Guarantee

Every AI-powered endpoint has a built-in template or heuristic fallback.
**No AI API key is required** — when keys are not configured, teb returns
deterministic template results so the core loop always works.
""",
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

# ─── Professional Middleware Stack ────────────────────────────────────────────
# Middleware executes in reverse order of registration (last added = outermost).
# Order: RequestId → SecurityHeaders → RequestLogging → (CORS is already added)
from teb.middleware import RequestIdMiddleware, RequestLoggingMiddleware, SecurityHeadersMiddleware  # noqa: E402

app.add_middleware(RequestLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)

# Static files
_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"


# ─── Include domain routers ───────────────────────────────────────────────────
app.include_router(health_router)
app.include_router(auth_router)
app.include_router(settings_router)
app.include_router(notifications_router)
app.include_router(admin_router)
app.include_router(financial_router)
app.include_router(messaging_router)
app.include_router(mcp_router)
app.include_router(execution_router)

# Wire up shared state for routers
_set_auth_rate_limiter(_check_rate_limit)
_set_notif_rate_limiter(_check_api_rate_limit)
_set_api_rate_limiter(_check_api_rate_limit)

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ─── Request / Response schemas ───────────────────────────────────────────────
# All request models include field-level validation: length limits, format
# checks, and constrained values.  Pydantic v2 enforces these automatically
# and returns structured 422 errors with field-level detail.

_MAX_TITLE_LEN = 500
_MAX_DESCRIPTION_LEN = 10000
_MAX_TAG_LEN = 1000


class GoalCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=_MAX_TITLE_LEN, description="Goal title")
    description: str = Field("", max_length=_MAX_DESCRIPTION_LEN, description="Goal description")
    tags: Optional[str] = Field(None, max_length=_MAX_TAG_LEN, description="Comma-separated tags")


class GoalPatch(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=_MAX_TITLE_LEN)
    description: Optional[str] = Field(None, max_length=_MAX_DESCRIPTION_LEN)
    status: Optional[str] = None
    tags: Optional[str] = Field(None, max_length=_MAX_TAG_LEN)

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


class ClarifyAnswer(BaseModel):
    key: str = Field(..., min_length=1, max_length=200)
    answer: str = Field(..., min_length=1, max_length=5000)


class CredentialCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    base_url: str = Field(..., min_length=1, max_length=2000)
    auth_header: str = Field("Authorization", max_length=200)
    auth_value: str = Field("", max_length=5000)
    description: str = Field("", max_length=1000)


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


class BudgetCreate(BaseModel):
    goal_id: int
    daily_limit: float = Field(50.0, ge=0, le=1000000)
    total_limit: float = Field(500.0, ge=0, le=10000000)
    category: str = Field("general", max_length=100)
    require_approval: bool = True
    autopilot_enabled: bool = False
    autopilot_threshold: float = Field(50.0, ge=0, le=1000000)


class BudgetUpdate(BaseModel):
    daily_limit: Optional[float] = Field(None, ge=0, le=1000000)
    total_limit: Optional[float] = Field(None, ge=0, le=10000000)
    require_approval: Optional[bool] = None
    autopilot_enabled: Optional[bool] = None
    autopilot_threshold: Optional[float] = Field(None, ge=0, le=1000000)


class SpendingRequestCreate(BaseModel):
    task_id: int
    amount: float = Field(..., ge=0, le=1000000)
    description: str = Field("", max_length=1000)
    service: str = Field("", max_length=200)
    currency: str = Field("USD", max_length=10)


class SpendingAction(BaseModel):
    action: str = Field(..., description="approve or deny")
    reason: str = Field("", max_length=1000)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("approve", "deny"):
            raise ValueError("action must be 'approve' or 'deny'")
        return v


class MessagingConfigCreate(BaseModel):
    channel: str = Field(..., max_length=50, description="Channel type")
    config: dict = {}
    notify_nudges: bool = True
    notify_tasks: bool = True
    notify_spending: bool = True
    notify_checkins: bool = False

    @field_validator("channel")
    @classmethod
    def validate_channel(cls, v: str) -> str:
        valid = {"telegram", "webhook", "slack", "discord", "whatsapp"}
        if v not in valid:
            raise ValueError(f"channel must be one of: {', '.join(sorted(valid))}")
        return v


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


class TelegramUpdate(BaseModel):
    """Minimal Telegram webhook update structure."""
    message: Optional[dict] = None


# ─── Pagination helper ────────────────────────────────────────────────────────

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 100


def _paginate(
    items: list,
    page: int = 1,
    per_page: int = _DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Apply pagination to a list and return a standardized paginated response.

    Returns:
        {"data": [...], "pagination": {"page": N, "per_page": N, "total": N, "pages": N}}
    """
    page = max(1, page)
    per_page = max(1, min(per_page, _MAX_PAGE_SIZE))
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": items[start:end],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
        },
    }


# ─── Structured error response helper ────────────────────────────────────────

def _error_response(
    status_code: int,
    code: str,
    message: str,
    details: Optional[list] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """Return a standardized error response."""
    body: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if details:
        body["error"]["details"] = details
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=body)


# ─── Auth helper ──────────────────────────────────────────────────────────────

def _get_user_id(request: Request) -> Optional[int]:
    """Extract user_id from the request's Authorization header (Bearer token)
    or from a ``token`` query parameter (needed for EventSource/SSE which
    cannot set custom headers).

    Returns None if no valid token is present (allows unauthenticated access
    to legacy endpoints).
    """
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[7:]
        return auth.decode_token(token)
    # Fallback: token query parameter (used by EventSource for SSE)
    token_param = request.query_params.get("token")
    if token_param:
        return auth.decode_token(token_param)
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


def _get_collaborator_role(goal_id: int, user_id: int) -> Optional[str]:
    """Return the collaborator role for a user on a goal, or None if not a collaborator."""
    collabs = storage.list_collaborators(goal_id)
    for c in collabs:
        if c.user_id == user_id:
            return c.role
    return None


def _get_goal_for_user(goal_id: int, user_id: int, require_role: Optional[str] = None) -> Goal:
    """Fetch a goal and verify the requesting user owns it or is a collaborator.

    If ``require_role`` is set, the user must be the owner **or** have that
    collaborator role (or higher).  Role hierarchy: admin > editor > viewer.
    """
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Owner always has full access
    if goal.user_id is None or goal.user_id == user_id:
        return goal

    # Check collaborator access
    collab_role = _get_collaborator_role(goal_id, user_id)
    if collab_role is None:
        raise HTTPException(status_code=403, detail="Not authorized")

    # If a specific role is required, check hierarchy
    if require_role:
        _ROLE_LEVEL = {"viewer": 0, "editor": 1, "admin": 2}
        needed = _ROLE_LEVEL.get(require_role, 0)
        actual = _ROLE_LEVEL.get(collab_role, 0)
        if actual < needed:
            raise HTTPException(status_code=403, detail=f"Requires {require_role} access")

    return goal


def _get_task_for_user(task_id: int, user_id: int, require_role: Optional[str] = None) -> Task:
    """Fetch a task and verify the requesting user owns its goal or is a collaborator."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.goal_id is not None:
        _get_goal_for_user(task.goal_id, user_id, require_role=require_role)
    return task


# ─── Health check & Observability ─────────────────────────────────────────────

_APP_START_TIME: float = time.monotonic()  # Updated in lifespan startup

# Simple in-memory metrics counters (no external dependency required)
_metrics: Dict[str, Any] = {
    "requests_total": 0,
    "requests_by_status": {},  # status_code -> count
    "errors_total": 0,
}


def _increment_request_metrics(status_code: int) -> None:
    """Track request metrics (called from middleware or manually)."""
    _metrics["requests_total"] += 1
    key = str(status_code)
    _metrics["requests_by_status"][key] = _metrics["requests_by_status"].get(key, 0) + 1
    if status_code >= 500:
        _metrics["errors_total"] += 1
    # Also update the health router's metrics copy
    increment_request_metrics(status_code)


# Health routes moved to teb/routers/health.py

# Auth routes moved to teb/routers/auth.py

# ─── Frontend ─────────────────────────────────────────────────────────────────

def _render_index() -> str:
    """Render index.html with BASE_PATH and CDN_PREFIX substituted."""
    html = (_TEMPLATES_DIR / "index.html").read_text()
    html = html.replace("{{BASE_PATH}}", config.BASE_PATH)
    cdn_prefix = (config.TEB_CDN_URL.rstrip("/") + "/") if config.TEB_CDN_URL else ""
    html = html.replace("{{CDN_PREFIX}}", cdn_prefix)
    return html


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


@app.get("/api/goals", tags=["goals"])
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
    user_id = _get_user_id(request)
    all_goals = [g.to_dict() for g in storage.list_goals(user_id=user_id)]
    if page is not None or per_page is not None:
        return _paginate(all_goals, page=page or 1, per_page=per_page or _DEFAULT_PAGE_SIZE)
    return all_goals


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    data = goal.to_dict()
    data["tasks"] = [t.to_dict() for t in tasks]
    return data


@app.patch("/api/goals/{goal_id}")
async def patch_goal(goal_id: int, body: GoalPatch, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
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

@app.delete("/api/goals/{goal_id}", status_code=200)
async def delete_goal_endpoint(goal_id: int, request: Request):
    uid = _require_user(request)
    goal = _get_goal_for_user(goal_id, uid)
    storage.delete_goal(goal_id)
    from teb import events as _events  # noqa: E402
    _events.event_bus.publish(uid, "goal_deleted", {"goal_id": goal_id, "title": goal.title})
    return {"ok": True, "deleted_goal_id": goal_id}


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

@app.get("/api/tasks", tags=["tasks"])
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
    uid = _require_user(request)
    if goal_id is not None:
        _get_goal_for_user(goal_id, uid)  # ownership check
    all_tasks = [t.to_dict() for t in storage.list_tasks(goal_id=goal_id, status=status)]
    if page is not None or per_page is not None:
        return _paginate(all_tasks, page=page or 1, per_page=per_page or _DEFAULT_PAGE_SIZE)
    return all_tasks


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
    task = _get_task_for_user(task_id, uid, require_role="editor")

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


@app.delete("/api/tasks/{task_id}", status_code=200)
async def delete_task(task_id: int, request: Request):
    uid = _require_user(request)
    task = _get_task_for_user(task_id, uid, require_role="editor")
    goal_id = task.goal_id
    storage.delete_task(task_id)
    from teb import events as _events
    _events.event_bus.publish(uid, "task_deleted", {"task_id": task_id, "goal_id": goal_id})
    return {"deleted": task_id}


# ─── API Credentials ─────────────────────────────────────────────────────────

# Credential routes moved to teb/routers/settings.py


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

# Profile routes moved to teb/routers/settings.py


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

    # Validate DAG before orchestration
    existing_tasks = storage.list_tasks(goal_id=goal_id)
    if existing_tasks:
        from teb import dag as _dag_mod  # noqa: E402
        dag_validation = _dag_mod.validate_dag(existing_tasks)
        if not dag_validation.is_valid:
            raise HTTPException(status_code=400, detail=f"DAG validation failed: {'; '.join(dag_validation.errors)}")

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
        "activity": [a.to_dict() for a in storage.get_agent_activity(goal_id)],
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


# ─── financial endpoints (extracted to teb/routers/financial.py) ──

# ─── financial endpoints (extracted to teb/routers/financial.py) ──

# ─── financial endpoints (extracted to teb/routers/financial.py) ──

# ─── messaging endpoints (extracted to teb/routers/messaging.py) ──

# ─── messaging endpoints (extracted to teb/routers/messaging.py) ──

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


# ─── Admin API (extracted to teb/routers/admin.py) ──────────────────────────

# ─── mcp endpoints (extracted to teb/routers/mcp.py) ──

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
async def search_all(request: Request, q: str = "", limit: int = 50, semantic: bool = False):
    """Search across all entities. Use ?semantic=true for AI-powered re-ranking."""
    uid = _require_user(request)
    if not q:
        raise HTTPException(status_code=400, detail="Query parameter 'q' is required")
    results = teb_search.quick_search(q, user_id=uid, limit=min(limit, 100), semantic=semantic)
    return {"query": q, "count": len(results), "results": results, "semantic": semantic}


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
    """Add a custom field to a task.

    Supports basic types (text, number, date, url) and relational types:
    - relation: links to another task. Pass ``field_value`` as the target task ID.
    - rollup: aggregates across related tasks. Pass ``config`` with aggregation settings.
    - formula: computed field. Pass ``config`` with formula_type.
    """
    uid = _require_user(request)
    _check_api_rate_limit(request)
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


@app.get("/api/tasks/{task_id}/fields")
async def list_custom_fields_endpoint(task_id: int, request: Request):
    """List custom fields for a task, with resolved values for computed types."""
    uid = _require_user(request)
    fields = storage.list_custom_fields(task_id)
    result = []
    for f in fields:
        d = f.to_dict()
        if f.field_type in ("relation", "rollup", "formula"):
            d["resolved_value"] = storage.resolve_custom_field_value(f)
        result.append(d)
    return result


@app.get("/api/fields/{field_id}/resolve")
async def resolve_custom_field(field_id: int, request: Request):
    """Resolve (compute) the value of a relation/rollup/formula field."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    cf = storage.get_custom_field(field_id)
    if not cf:
        raise HTTPException(status_code=404, detail="Custom field not found")
    resolved = storage.resolve_custom_field_value(cf)
    d = cf.to_dict()
    d["resolved_value"] = resolved
    return d


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


# Notification routes moved to teb/routers/notifications.py


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


@app.get("/api/users/me/tasks", tags=["cross-goal"])
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
    _check_api_rate_limit(request)
    uid = _require_user(request)
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


@app.get("/api/views/{view_id}/tasks", tags=["cross-goal"])
async def get_view_tasks(view_id: int, request: Request):
    """Apply a saved view's filters and sort to the user's tasks, returning filtered results.

    Works cross-goal — returns tasks from ALL the user's goals matching the view's config.
    """
    _check_api_rate_limit(request)
    uid = _require_user(request)
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


# ─── Phase 4: Intelligence endpoints ──────────────────────────────────────────


class _WriteAssistBody(BaseModel):
    context: str = ""
    prompt: str = ""


class _TemplateGenBody(BaseModel):
    description: str


class _MeetingNotesBody(BaseModel):
    notes: str


class _SuggestTagsBody(BaseModel):
    text: str


@app.post("/api/goals/{goal_id}/reschedule", tags=["intelligence"])
async def reschedule_goal(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.auto_reschedule(goal_id)
    return result


@app.get("/api/users/me/focus-recommendations", tags=["intelligence"])
async def focus_recommendations(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    result = intelligence.get_focus_recommendations(uid)
    return result


@app.post("/api/ai/write", tags=["intelligence"])
async def ai_write(body: _WriteAssistBody, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.assist_writing(body.context, body.prompt)
    return result


@app.post("/api/ai/generate-template", tags=["intelligence"])
async def ai_generate_template(body: _TemplateGenBody, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.generate_template_from_description(body.description)
    return result


@app.post("/api/ai/meeting-to-tasks", tags=["intelligence"])
async def ai_meeting_to_tasks(body: _MeetingNotesBody, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.extract_tasks_from_notes(body.notes)
    return result


@app.get("/api/goals/{goal_id}/status-report", tags=["intelligence"])
async def goal_status_report(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.generate_status_report(goal_id)
    return result


@app.post("/api/ai/suggest-tags", tags=["intelligence"])
async def ai_suggest_tags(body: _SuggestTagsBody, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.suggest_tags(body.text)
    return result


@app.get("/api/users/me/workflow-suggestions", tags=["intelligence"])
async def workflow_suggestions(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    result = intelligence.get_workflow_suggestions(uid)
    return result


@app.get("/api/users/me/insights", tags=["intelligence"])
async def cross_goal_insights(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    result = intelligence.get_cross_goal_insights(uid)
    return result


@app.get("/api/users/me/skill-gaps", tags=["intelligence"])
async def skill_gaps(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    result = intelligence.analyze_skill_gaps(uid)
    return result


@app.get("/api/goals/{goal_id}/stagnation-check", tags=["intelligence"])
async def stagnation_check(goal_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    result = intelligence.detect_stagnation(goal_id)
    return result


# ─── Phase 5.1: Integration Marketplace ──────────────────────────────────────

@app.get("/api/integrations/directory", tags=["integrations"])
async def list_integration_directory(request: Request, category: Optional[str] = Query(default=None)):
    """List available integrations from the directory."""
    _check_api_rate_limit(request)
    listings = storage.list_integration_listings(category=category)
    return [il.to_dict() for il in listings]


@app.get("/api/integrations/directory/{listing_id}", tags=["integrations"])
async def get_integration_directory_item(listing_id: int, request: Request):
    """Get details for a specific integration listing."""
    _check_api_rate_limit(request)
    il = storage.get_integration_listing(listing_id)
    if not il:
        raise HTTPException(status_code=404, detail="Integration listing not found")
    return il.to_dict()


@app.post("/api/integrations/oauth/initiate", tags=["integrations"])
async def oauth_initiate(request: Request):
    """Initiate an OAuth flow for a provider. Returns the authorization URL."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    provider = body.get("provider", "")
    redirect_uri = body.get("redirect_uri", "")
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    auth_url = f"https://{provider}.example.com/oauth/authorize?client_id=teb&redirect_uri={redirect_uri}&state={uid}"
    return {"auth_url": auth_url, "provider": provider}


@app.post("/api/integrations/oauth/callback", tags=["integrations"])
async def oauth_callback(request: Request):
    """Handle OAuth callback and store encrypted tokens."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    provider = body.get("provider", "")
    access_token = body.get("access_token", "")
    refresh_token = body.get("refresh_token", "")
    if not provider or not access_token:
        raise HTTPException(status_code=400, detail="provider and access_token are required")
    oc = OAuthConnection(
        user_id=uid,
        provider=provider,
        access_token_encrypted=access_token,
        refresh_token_encrypted=refresh_token,
    )
    oc = storage.upsert_oauth_connection(oc)
    return oc.to_dict()


@app.get("/api/integrations/templates", tags=["integrations"])
async def list_integration_templates(request: Request):
    """List all integration templates."""
    _check_api_rate_limit(request)
    templates = storage.list_integration_templates()
    return [t.to_dict() for t in templates]


@app.post("/api/integrations/templates/{template_id}/apply", tags=["integrations"])
async def apply_integration_template(template_id: int, request: Request):
    """Apply an integration template to set up a new integration mapping."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    template = storage.get_integration_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"applied": True, "template": template.to_dict(), "user_id": uid}


# ─── Webhook Rules (Builder) ────────────────────────────────────────────────

@app.post("/api/webhooks/rules", status_code=201, tags=["webhooks"])
async def create_webhook_rule(request: Request):
    """Create a new webhook routing rule."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    wr = WebhookRule(
        user_id=uid,
        name=body.get("name", ""),
        event_type=body.get("event_type", ""),
        filter_json=json.dumps(body.get("filter", {})),
        target_url=body.get("target_url", ""),
        headers_json=json.dumps(body.get("headers", {})),
        active=body.get("active", True),
    )
    if not wr.target_url:
        raise HTTPException(status_code=400, detail="target_url is required")
    wr = storage.create_webhook_rule(wr)
    return wr.to_dict()


@app.get("/api/webhooks/rules", tags=["webhooks"])
async def list_webhook_rules(request: Request):
    """List all webhook rules for the current user."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    rules = storage.list_webhook_rules(uid)
    return [r.to_dict() for r in rules]


@app.put("/api/webhooks/rules/{rule_id}", tags=["webhooks"])
async def update_webhook_rule(rule_id: int, request: Request):
    """Update an existing webhook rule."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    existing = storage.get_webhook_rule(rule_id)
    if not existing or existing.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook rule not found")
    body = await request.json()
    existing.name = body.get("name", existing.name)
    existing.event_type = body.get("event_type", existing.event_type)
    if "filter" in body:
        existing.filter_json = json.dumps(body["filter"])
    existing.target_url = body.get("target_url", existing.target_url)
    if "headers" in body:
        existing.headers_json = json.dumps(body["headers"])
    if "active" in body:
        existing.active = body["active"]
    existing = storage.update_webhook_rule(existing)
    return existing.to_dict()


@app.delete("/api/webhooks/rules/{rule_id}", tags=["webhooks"])
async def delete_webhook_rule(rule_id: int, request: Request):
    """Delete a webhook rule."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    existing = storage.get_webhook_rule(rule_id)
    if not existing or existing.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook rule not found")
    storage.delete_webhook_rule(rule_id, uid)
    return {"deleted": rule_id}


@app.post("/api/webhooks/rules/{rule_id}/test", tags=["webhooks"])
async def test_webhook_rule(rule_id: int, request: Request):
    """Send a test payload to a webhook rule's target URL."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    rule = storage.get_webhook_rule(rule_id)
    if not rule or rule.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook rule not found")
    test_payload = {
        "event": rule.event_type,
        "test": True,
        "message": "This is a test webhook delivery from teb.",
    }
    return {"sent": True, "target_url": rule.target_url, "payload": test_payload}


# ─── Zapier/Make Native App ─────────────────────────────────────────────────

@app.get("/api/integrations/zapier/triggers", tags=["integrations"])
async def zapier_list_triggers(request: Request):
    """List available triggers for Zapier/Make integration."""
    _check_api_rate_limit(request)
    return {"triggers": [
        {"key": "goal_created", "label": "Goal Created", "description": "Triggers when a new goal is created."},
        {"key": "task_completed", "label": "Task Completed", "description": "Triggers when a task is marked done."},
        {"key": "goal_completed", "label": "Goal Completed", "description": "Triggers when a goal is completed."},
        {"key": "task_created", "label": "Task Created", "description": "Triggers when a new task is created."},
        {"key": "checkin_submitted", "label": "Check-in Submitted", "description": "Triggers when a check-in is submitted."},
    ]}


@app.get("/api/integrations/zapier/actions", tags=["integrations"])
async def zapier_list_actions(request: Request):
    """List available actions for Zapier/Make integration."""
    _check_api_rate_limit(request)
    return {"actions": [
        {"key": "create_goal", "label": "Create Goal", "description": "Create a new goal in teb."},
        {"key": "create_task", "label": "Create Task", "description": "Create a task under a goal."},
        {"key": "update_task_status", "label": "Update Task Status", "description": "Update the status of a task."},
        {"key": "add_comment", "label": "Add Comment", "description": "Add a comment to a task."},
    ]}


@app.post("/api/integrations/zapier/subscribe", tags=["integrations"])
async def zapier_subscribe(request: Request):
    """Subscribe to a trigger event (Zapier subscription endpoint)."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    event_type = body.get("event_type", "")
    target_url = body.get("target_url", "")
    if not event_type or not target_url:
        raise HTTPException(status_code=400, detail="event_type and target_url are required")
    sub_id = storage.create_zapier_subscription(uid, event_type, target_url)
    return {"id": sub_id, "event_type": event_type, "target_url": target_url}


@app.delete("/api/integrations/zapier/unsubscribe/{sub_id}", tags=["integrations"])
async def zapier_unsubscribe(sub_id: int, request: Request):
    """Unsubscribe from a trigger event."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    storage.delete_zapier_subscription(sub_id, uid)
    return {"deleted": sub_id}


# ─── API Rate Limit Dashboard ───────────────────────────────────────────────

@app.get("/api/integrations/rate-limits", tags=["integrations"])
async def get_rate_limit_usage(request: Request):
    """Get API rate limit usage for the current user."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    usage = storage.get_api_rate_limit_usage(uid)
    return usage


# ─── Phase 5.2: Plugin & Extension System ───────────────────────────────────

@app.get("/api/plugins/marketplace", tags=["plugins"])
async def list_plugin_marketplace(request: Request):
    """List plugins available in the marketplace."""
    _check_api_rate_limit(request)
    listings = storage.list_plugin_listings()
    return [pl.to_dict() for pl in listings]


@app.get("/api/plugins/marketplace/{listing_id}", tags=["plugins"])
async def get_plugin_marketplace_item(listing_id: int, request: Request):
    """Get details for a specific plugin listing."""
    _check_api_rate_limit(request)
    pl = storage.get_plugin_listing(listing_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Plugin listing not found")
    return pl.to_dict()


@app.post("/api/plugins/marketplace/{listing_id}/install", status_code=201, tags=["plugins"])
async def install_plugin_from_marketplace(listing_id: int, request: Request):
    """Install a plugin from the marketplace."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    pl = storage.get_plugin_listing(listing_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Plugin listing not found")
    storage.increment_plugin_downloads(listing_id)
    existing = storage.get_plugin(pl.name)
    if existing:
        return {"installed": True, "plugin": existing.to_dict(), "already_installed": True}
    plugin = PluginManifest(
        name=pl.name,
        version=pl.version,
        description=pl.description,
        task_types="[]",
        required_credentials="[]",
        module_path="",
    )
    plugin = storage.create_plugin(plugin)
    return {"installed": True, "plugin": plugin.to_dict(), "already_installed": False}


@app.post("/api/plugins/fields", status_code=201, tags=["plugins"])
async def create_custom_field_definition(request: Request):
    """Create a custom field definition provided by a plugin."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    cfd = CustomFieldDefinition(
        plugin_id=body.get("plugin_id", 0),
        field_type=body.get("field_type", "text"),
        label=body.get("label", ""),
        options_json=json.dumps(body.get("options", [])),
    )
    if not cfd.label:
        raise HTTPException(status_code=400, detail="label is required")
    cfd = storage.create_custom_field_definition(cfd)
    return cfd.to_dict()


@app.get("/api/plugins/fields", tags=["plugins"])
async def list_custom_field_definitions(request: Request, plugin_id: Optional[int] = Query(default=None)):
    """List custom field definitions, optionally filtered by plugin."""
    _check_api_rate_limit(request)
    fields = storage.list_custom_field_definitions(plugin_id=plugin_id)
    return [f.to_dict() for f in fields]


@app.post("/api/plugins/views", status_code=201, tags=["plugins"])
async def create_plugin_view(request: Request):
    """Create a custom view provided by a plugin."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    pv = PluginView(
        plugin_id=body.get("plugin_id", 0),
        name=body.get("name", ""),
        view_type=body.get("view_type", "board"),
        config_json=json.dumps(body.get("config", {})),
    )
    if not pv.name:
        raise HTTPException(status_code=400, detail="name is required")
    pv = storage.create_plugin_view(pv)
    return pv.to_dict()


@app.get("/api/plugins/views", tags=["plugins"])
async def list_plugin_views(request: Request, plugin_id: Optional[int] = Query(default=None)):
    """List custom views, optionally filtered by plugin."""
    _check_api_rate_limit(request)
    views = storage.list_plugin_views(plugin_id=plugin_id)
    return [v.to_dict() for v in views]


@app.get("/api/themes", tags=["themes"])
async def list_themes(request: Request):
    """List all available themes."""
    _check_api_rate_limit(request)
    themes = storage.list_themes()
    return [t.to_dict() for t in themes]


@app.post("/api/themes", status_code=201, tags=["themes"])
async def create_theme(request: Request):
    """Create a new theme."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    theme = Theme(
        name=body.get("name", ""),
        author=body.get("author", ""),
        css_variables_json=json.dumps(body.get("css_variables", {})),
    )
    if not theme.name:
        raise HTTPException(status_code=400, detail="name is required")
    theme = storage.create_theme(theme)
    return theme.to_dict()


@app.put("/api/themes/{theme_id}/activate", tags=["themes"])
async def activate_theme(theme_id: int, request: Request):
    """Activate a theme (deactivates all others)."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    theme = storage.get_theme(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    storage.activate_theme(theme_id)
    return {"activated": theme_id, "name": theme.name}


@app.get("/api/themes/active", tags=["themes"])
async def get_active_theme(request: Request):
    """Get the currently active theme."""
    _check_api_rate_limit(request)
    theme = storage.get_active_theme()
    if not theme:
        return {"active_theme": None}
    return theme.to_dict()


@app.get("/api/plugins/sdk/docs", tags=["plugins"])
async def get_plugin_sdk_docs(request: Request):
    """Return plugin SDK documentation as JSON."""
    _check_api_rate_limit(request)
    return {
        "sdk_version": "1.0.0",
        "overview": "The teb Plugin SDK allows developers to extend teb with custom functionality.",
        "plugin_manifest": {
            "description": "Every plugin must include a manifest.json in its directory.",
            "fields": {
                "name": "Unique plugin name (string, required)",
                "version": "Semantic version (string, required)",
                "description": "Human-readable description (string)",
                "task_types": "List of task types this plugin handles (array of strings)",
                "required_credentials": "Credential names needed (array of strings)",
                "module_path": "Python module path to the plugin entry point",
            },
        },
        "hooks": {
            "on_task_execute": "Called when a task matching plugin task_types is executed. Receives task_context dict.",
            "on_goal_created": "Called when a new goal is created.",
            "on_task_completed": "Called when a task status changes to done.",
        },
        "custom_fields": {
            "description": "Plugins can define custom field types via POST /api/plugins/fields.",
            "supported_types": ["text", "number", "date", "select", "multi_select", "url", "email", "checkbox"],
        },
        "custom_views": {
            "description": "Plugins can register custom views via POST /api/plugins/views.",
            "supported_view_types": ["board", "list", "calendar", "timeline", "chart"],
        },
        "api_endpoints": {
            "register_plugin": "POST /api/plugins",
            "list_plugins": "GET /api/plugins",
            "execute_plugin": "POST /api/plugins/{name}/execute",
            "plugin_marketplace": "GET /api/plugins/marketplace",
        },
    }


# ─── Phase 5.3: Import/Export Ecosystem ──────────────────────────────────────

@app.post("/api/import/monday", status_code=201, tags=["import"])
async def import_monday(request: Request):
    """Import a Monday.com board JSON into teb goals and tasks."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    board = body.get("board", {})
    if not board or not isinstance(board, dict):
        raise HTTPException(status_code=422, detail="board (Monday.com JSON) is required")
    from teb import importers
    goal, tasks = importers.import_from_monday(uid, board)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@app.post("/api/import/jira", status_code=201, tags=["import"])
async def import_jira(request: Request):
    """Import Jira project/sprint data into teb goals and tasks."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    project = body.get("project", {})
    if not project or not isinstance(project, dict):
        raise HTTPException(status_code=422, detail="project (Jira JSON) is required")
    from teb import importers
    goal, tasks = importers.import_from_jira(uid, project)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@app.post("/api/import/clickup", status_code=201, tags=["import"])
async def import_clickup(request: Request):
    """Import ClickUp list data into teb goals and tasks."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    list_data = body.get("list", {})
    if not list_data or not isinstance(list_data, dict):
        raise HTTPException(status_code=422, detail="list (ClickUp JSON) is required")
    from teb import importers
    goal, tasks = importers.import_from_clickup(uid, list_data)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@app.post("/api/import/csv", status_code=201, tags=["import"])
async def import_csv(request: Request):
    """Import tasks from CSV text."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    csv_text = body.get("csv", "")
    if not csv_text:
        raise HTTPException(status_code=422, detail="csv (CSV text content) is required")
    from teb import importers
    goal, tasks = importers.import_from_csv(uid, csv_text)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@app.post("/api/import/langchain", status_code=201, tags=["import"])
async def import_langchain_workflow(request: Request):
    """Import a LangChain agent/chain workflow export."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    data = body.get("data", body)
    from teb import importers
    goal, tasks = importers.import_from_langchain(uid, data)
    return {"goal": goal.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@app.post("/api/import/crewai", status_code=201, tags=["import"])
async def import_crewai_crew(request: Request):
    """Import a CrewAI crew export."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    body = await request.json()
    data = body.get("data", body)
    from teb import importers
    goal, tasks = importers.import_from_crewai(uid, data)
    return {"goal": goal.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@app.get("/api/goals/{goal_id}/export/full", tags=["export"])
async def export_full_project(goal_id: int, request: Request):
    """Export a full goal with all tasks, comments, and artifacts."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    result = storage.export_project(goal_id)
    if not result:
        raise HTTPException(status_code=404, detail="Goal not found")
    return result


@app.get("/api/export/schema", tags=["export"])
async def export_schema_docs(request: Request):
    """Return the data schema documentation for exports."""
    _check_api_rate_limit(request)
    return {
        "version": "1.0.0",
        "description": "teb data export schema documentation",
        "entities": {
            "goal": {
                "fields": {
                    "id": "integer – unique goal identifier",
                    "user_id": "integer – owner user ID",
                    "title": "string – goal title",
                    "description": "string – goal description",
                    "status": "string – drafting | decomposed | in_progress | completed | archived",
                    "answers": "object – structured answers from goal questionnaire",
                    "tags": "string – comma-separated tags",
                    "created_at": "ISO 8601 datetime",
                    "updated_at": "ISO 8601 datetime",
                },
            },
            "task": {
                "fields": {
                    "id": "integer – unique task identifier",
                    "goal_id": "integer – parent goal ID",
                    "parent_id": "integer | null – parent task ID for subtasks",
                    "title": "string – task title",
                    "description": "string – task description",
                    "status": "string – todo | in_progress | done | failed | blocked",
                    "estimated_minutes": "integer – estimated duration",
                    "order_index": "integer – sort order",
                    "due_date": "string | null – YYYY-MM-DD",
                    "tags": "string – comma-separated tags",
                    "created_at": "ISO 8601 datetime",
                    "updated_at": "ISO 8601 datetime",
                },
            },
            "task_comment": {
                "fields": {
                    "id": "integer",
                    "task_id": "integer – parent task ID",
                    "content": "string – comment text",
                    "author": "string – comment author",
                    "created_at": "ISO 8601 datetime",
                },
            },
            "task_artifact": {
                "fields": {
                    "id": "integer",
                    "task_id": "integer – parent task ID",
                    "artifact_type": "string – file type or artifact category",
                    "content": "string – artifact content or URL",
                    "created_at": "ISO 8601 datetime",
                },
            },
        },
        "export_endpoint": "GET /api/goals/{id}/export/full",
        "import_endpoints": {
            "trello": "POST /api/import/trello",
            "asana": "POST /api/import/asana",
            "monday": "POST /api/import/monday",
            "jira": "POST /api/import/jira",
            "clickup": "POST /api/import/clickup",
            "csv": "POST /api/import/csv",
            "langchain": "POST /api/import/langchain",
            "crewai": "POST /api/import/crewai",
        },
    }


# ─── Phase 6.1: SSO/SAML Integration ────────────────────────────────────────

@app.post("/api/admin/sso/configure", tags=["enterprise"])
async def configure_sso(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    existing = storage.get_sso_config(org_id)
    cfg = SSOConfig(
        org_id=org_id,
        provider=body.get("provider", ""),
        entity_id=body.get("entity_id", ""),
        sso_url=body.get("sso_url", ""),
        certificate=body.get("certificate", ""),
    )
    if existing:
        cfg.id = existing.id
        cfg = storage.update_sso_config(cfg)
    else:
        cfg = storage.create_sso_config(cfg)
    return cfg.to_dict()


@app.get("/api/admin/sso/config", tags=["enterprise"])
async def get_sso_config(request: Request, org_id: int = Query(default=1)):
    _check_api_rate_limit(request)
    _require_admin(request)
    cfg = storage.get_sso_config(org_id)
    if not cfg:
        return {"configured": False, "org_id": org_id}
    data = cfg.to_dict()
    data["configured"] = True
    return data


@app.post("/api/auth/sso/initiate", tags=["enterprise"])
async def sso_initiate(request: Request):
    _check_rate_limit(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    cfg = storage.get_sso_config(org_id)
    if not cfg or not cfg.sso_url:
        raise HTTPException(status_code=404, detail="SSO not configured for this organization")
    import secrets as _secrets
    relay_state = _secrets.token_urlsafe(32)
    redirect_url = f"{cfg.sso_url}?SAMLRequest=authn_request&RelayState={relay_state}"
    return {"redirect_url": redirect_url, "relay_state": relay_state, "provider": cfg.provider}


@app.post("/api/auth/sso/callback", tags=["enterprise"])
async def sso_callback(request: Request):
    _check_rate_limit(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    saml_response = body.get("SAMLResponse", "")
    cfg = storage.get_sso_config(org_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="SSO not configured")
    email = body.get("email", "")
    if not email:
        raise HTTPException(status_code=422, detail="Email not provided in SSO response")
    user = storage.get_user_by_email(email)
    if not user:
        from teb.models import User as _User
        user = storage.create_user(_User(email=email, password_hash="sso_managed"))
    token = auth.create_token(user.id)
    return {"user": user.to_dict(), "token": token, "sso_provider": cfg.provider}


# ─── Phase 6.1: IP Allowlisting ─────────────────────────────────────────────

@app.get("/api/admin/ip-allowlist", tags=["enterprise"])
async def list_ip_allowlist(request: Request, org_id: int = Query(default=1)):
    _check_api_rate_limit(request)
    _require_admin(request)
    entries = storage.list_ip_allowlist(org_id)
    return [e.to_dict() for e in entries]


@app.post("/api/admin/ip-allowlist", status_code=201, tags=["enterprise"])
async def create_ip_allowlist(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    body = await request.json()
    entry = IPAllowlist(
        org_id=body.get("org_id", 1),
        cidr_range=body.get("cidr_range", ""),
        description=body.get("description", ""),
    )
    if not entry.cidr_range:
        raise HTTPException(status_code=422, detail="cidr_range is required")
    entry = storage.create_ip_allowlist_entry(entry)
    return entry.to_dict()


@app.delete("/api/admin/ip-allowlist/{entry_id}", tags=["enterprise"])
async def delete_ip_allowlist(entry_id: int, request: Request, org_id: int = Query(default=1)):
    _check_api_rate_limit(request)
    _require_admin(request)
    deleted = storage.delete_ip_allowlist_entry(entry_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"deleted": True}


# ─── Phase 6.1: Audit Log Viewer ────────────────────────────────────────────

@app.get("/api/admin/audit-log", tags=["enterprise"])
async def audit_log_viewer(
    request: Request,
    user_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
    until: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=1000),
):
    _check_api_rate_limit(request)
    _require_admin(request)
    events = storage.search_audit_events(
        user_id=user_id, event_type=event_type,
        since=since, until=until, limit=limit,
    )
    return [e.to_dict() for e in events]


# ─── Phase 6.2: Organization Management ─────────────────────────────────────

@app.post("/api/orgs", status_code=201, tags=["enterprise"])
async def create_organization(request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    slug = body.get("slug", "").strip() or name.lower().replace(" ", "-")
    import re as _re
    slug = _re.sub(r"[^a-z0-9-]", "", slug)
    org = Organization(name=name, slug=slug, owner_id=uid,
                       settings_json=json.dumps(body.get("settings", {})))
    try:
        org = storage.create_org(org)
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="Organization slug already exists")
        raise
    storage.add_org_member(org.id, uid, role="owner")
    return org.to_dict()


@app.get("/api/orgs", tags=["enterprise"])
async def list_organizations(request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    return [o.to_dict() for o in storage.list_orgs()]


@app.get("/api/orgs/{org_id}", tags=["enterprise"])
async def get_organization(org_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    org = storage.get_org(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org.to_dict()


@app.put("/api/orgs/{org_id}", tags=["enterprise"])
async def update_organization(org_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    org = storage.get_org(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    body = await request.json()
    if "name" in body:
        org.name = body["name"].strip()
    if "slug" in body:
        org.slug = body["slug"].strip()
    if "settings" in body:
        org.settings_json = json.dumps(body["settings"])
    org = storage.update_org(org)
    return org.to_dict()


@app.post("/api/orgs/{org_id}/members", status_code=201, tags=["enterprise"])
async def add_org_member(org_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    org = storage.get_org(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    body = await request.json()
    user_id = body.get("user_id")
    role = body.get("role", "member")
    if not user_id:
        raise HTTPException(status_code=422, detail="user_id is required")
    result = storage.add_org_member(org_id, user_id, role)
    return result


@app.get("/api/orgs/{org_id}/members", tags=["enterprise"])
async def list_org_members(org_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_user(request)
    return storage.list_org_members(org_id)


# ─── Phase 6.2: Usage Analytics ─────────────────────────────────────────────

@app.get("/api/admin/analytics", tags=["enterprise"])
async def usage_analytics(
    request: Request,
    org_id: Optional[int] = Query(default=None),
    since: Optional[str] = Query(default=None),
):
    _check_api_rate_limit(request)
    _require_admin(request)
    return storage.get_usage_analytics(org_id=org_id, since=since)


# ─── Phase 6.2: SCIM User Provisioning ──────────────────────────────────────

@app.get("/api/scim/v2/Users", tags=["scim"])
async def scim_list_users(request: Request, startIndex: int = Query(default=1), count: int = Query(default=100)):
    _check_api_rate_limit(request)
    _require_admin(request)
    with storage._conn() as con:
        rows = con.execute("SELECT * FROM users ORDER BY id LIMIT ? OFFSET ?", (count, startIndex - 1)).fetchall()
        total = con.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    resources = []
    for r in rows:
        resources.append({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": str(r["id"]),
            "userName": r["email"],
            "active": not bool(r["locked_until"]),
            "emails": [{"value": r["email"], "primary": True}],
            "meta": {"resourceType": "User", "created": r["created_at"]},
        })
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": startIndex,
        "itemsPerPage": count,
        "Resources": resources,
    }


@app.post("/api/scim/v2/Users", status_code=201, tags=["scim"])
async def scim_create_user(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    body = await request.json()
    email = body.get("userName", "")
    if not email:
        emails = body.get("emails", [])
        if emails:
            email = emails[0].get("value", "")
    if not email:
        raise HTTPException(status_code=422, detail="userName or emails required")
    import secrets as _secrets
    from teb.models import User as _User
    user = _User(email=email, password_hash=auth.hash_password(_secrets.token_urlsafe(16)))
    existing = storage.get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="User already exists")
    user = storage.create_user(user)
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "active": True,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None},
    }


@app.get("/api/scim/v2/Users/{user_id}", tags=["scim"])
async def scim_get_user(user_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "active": user.locked_until is None,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None},
    }


@app.put("/api/scim/v2/Users/{user_id}", tags=["scim"])
async def scim_update_user(user_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    new_email = body.get("userName")
    active = body.get("active")
    if new_email:
        with storage._conn() as con:
            con.execute("UPDATE users SET email = ? WHERE id = ?", (new_email, user_id))
    if active is False:
        from datetime import timedelta
        storage.lock_user(user_id, datetime.now(timezone.utc) + timedelta(days=3650))
    elif active is True:
        with storage._conn() as con:
            con.execute("UPDATE users SET locked_until = NULL WHERE id = ?", (user_id,))
    user = storage.get_user(user_id)
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "active": user.locked_until is None,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None},
    }


@app.delete("/api/scim/v2/Users/{user_id}", status_code=204, tags=["scim"])
async def scim_delete_user(user_id: int, request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    with storage._conn() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return None


# ─── Phase 6.2: Custom Branding ─────────────────────────────────────────────

@app.get("/api/admin/branding", tags=["enterprise"])
async def get_branding(request: Request, org_id: int = Query(default=1)):
    _check_api_rate_limit(request)
    _require_admin(request)
    cfg = storage.get_branding_config(org_id)
    if not cfg:
        return BrandingConfig(org_id=org_id).to_dict()
    return cfg.to_dict()


@app.put("/api/admin/branding", tags=["enterprise"])
async def update_branding(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    cfg = BrandingConfig(
        org_id=org_id,
        logo_url=body.get("logo_url", ""),
        primary_color=body.get("primary_color", "#1a1a2e"),
        secondary_color=body.get("secondary_color", "#16213e"),
        app_name=body.get("app_name", "teb"),
        favicon_url=body.get("favicon_url", ""),
    )
    cfg = storage.upsert_branding_config(cfg)
    return cfg.to_dict()


# ─── Phase 6.2: Compliance Reports ──────────────────────────────────────────

@app.get("/api/admin/compliance/report", tags=["enterprise"])
async def compliance_report(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    return storage.get_compliance_report()


@app.get("/api/admin/compliance/export", tags=["enterprise"])
async def compliance_export(request: Request, format: str = Query(default="json")):
    _check_api_rate_limit(request)
    _require_admin(request)
    report = storage.get_compliance_report()
    if format == "json":
        return JSONResponse(content=report, headers={
            "Content-Disposition": "attachment; filename=compliance_report.json"
        })
    return report


# ─── Phase 6.3: Database Status ─────────────────────────────────────────────

@app.get("/api/admin/database/status", tags=["enterprise"])
async def database_status(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    status = storage.get_database_status()
    from teb import pg_migrate
    status["migration_plan"] = pg_migrate.migrate_to_postgres()
    return status


# ─── Phase 6.3: Cache Stats ─────────────────────────────────────────────────

@app.get("/api/admin/cache/stats", tags=["enterprise"])
async def cache_stats(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    from teb.cache import get_cache
    cache = get_cache()
    stats = cache.stats()
    stats["redis_url_configured"] = bool(config.REDIS_URL)
    stats["redis_instructions"] = (
        "Set REDIS_URL environment variable (e.g. redis://localhost:6379/0) "
        "and install the 'redis' package to enable Redis caching."
    )
    return stats


# ─── Prometheus-compatible metrics ───────────────────────────────────────────

@app.get("/api/admin/metrics", tags=["enterprise"])
async def admin_metrics(request: Request):
    """Prometheus-compatible metrics: active users, goals, tasks, AI latency, executor success rate."""
    _check_api_rate_limit(request)
    _require_admin(request)

    user_count = 0
    goal_count = 0
    task_count = 0
    done_goals = 0
    done_tasks = 0
    try:
        with storage._conn() as con:
            user_count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            goal_count = con.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
            done_goals = con.execute("SELECT COUNT(*) FROM goals WHERE status='done'").fetchone()[0]
            task_count = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            done_tasks = con.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
    except Exception:
        pass

    # Execution memory stats (if available)
    exec_stats = {}
    try:
        from teb.memory import get_memory_stats
        exec_stats = get_memory_stats()
    except Exception:
        pass

    # Success graph stats
    graph_stats = {}
    try:
        from teb.success_graph import get_graph_stats
        graph_stats = get_graph_stats()
    except Exception:
        pass

    uptime = round(time.monotonic() - _APP_START_TIME, 1)

    return {
        "uptime_seconds": uptime,
        "users_total": user_count,
        "goals_total": goal_count,
        "goals_completed": done_goals,
        "goal_completion_rate": round(done_goals / max(goal_count, 1), 3),
        "tasks_total": task_count,
        "tasks_completed": done_tasks,
        "task_completion_rate": round(done_tasks / max(task_count, 1), 3),
        "executor": {
            "total_calls": exec_stats.get("total_calls", 0),
            "success_rate": exec_stats.get("success_rate", 0),
            "avg_latency_ms": exec_stats.get("avg_latency_ms", 0),
        },
        "success_graph": {
            "nodes": graph_stats.get("nodes", 0),
            "edges": graph_stats.get("edges", 0),
            "observations": graph_stats.get("total_observations", 0),
        },
        "request_metrics": _metrics,
    }


# ─── Phase 6.3: CDN Config ──────────────────────────────────────────────────

@app.get("/api/admin/cdn/config", tags=["enterprise"])
async def cdn_config(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    return {
        "cdn_url": config.TEB_CDN_URL or None,
        "configured": bool(config.TEB_CDN_URL),
        "usage": "When TEB_CDN_URL is set, static asset URLs in the HTML template are prefixed with this URL.",
        "static_assets": [
            "static/style.css",
            "static/app.js",
            "static/manifest.json",
            "static/views/kanban.js",
            "static/views/calendar.js",
            "static/views/timeline.js",
            "static/views/gantt.js",
            "static/views/table.js",
            "static/views/workload.js",
            "static/views/mindmap.js",
            "static/views/charts.js",
        ],
    }


# ─── Phase 6.3: Horizontal Scaling Config ───────────────────────────────────

@app.get("/api/admin/scaling/config", tags=["enterprise"])
async def scaling_config(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    return {
        "stateless": True,
        "recommendations": [
            "Set TEB_JWT_SECRET to a fixed value so tokens work across instances.",
            "Migrate from SQLite to PostgreSQL for shared database access.",
            "Set REDIS_URL for shared caching across instances.",
            "Use a load balancer (nginx, ALB, or Kubernetes Ingress) in front of multiple teb instances.",
            "Store uploaded files in object storage (S3, GCS) instead of local filesystem.",
            "Use sticky sessions or token-based auth (already implemented via JWT).",
        ],
        "current_config": {
            "database": "sqlite" if "sqlite" in config.DATABASE_URL else "postgresql",
            "cache": "redis" if config.REDIS_URL else "memory",
            "jwt_secret_set": bool(os.getenv("TEB_JWT_SECRET")),
            "region": config.REGION,
        },
    }


# ─── Phase 6.3: Multi-Region Support ────────────────────────────────────────

@app.get("/api/admin/regions", tags=["enterprise"])
async def list_regions(request: Request):
    _check_api_rate_limit(request)
    _require_admin(request)
    regions_env = os.getenv("TEB_REGIONS", "")
    configured_regions = [r.strip() for r in regions_env.split(",") if r.strip()] if regions_env else [config.REGION]
    return {
        "current_region": config.REGION,
        "configured_regions": configured_regions,
        "multi_region_enabled": len(configured_regions) > 1,
        "setup_instructions": (
            "Set TEB_REGION to identify this instance's region. "
            "Set TEB_REGIONS to a comma-separated list of all regions. "
            "Each region should have its own database and cache, with "
            "cross-region replication configured at the database level."
        ),
    }


# ─── Phase 7: Documentation & Community endpoints ────────────────────────────


@app.get("/api/docs/changelog", tags=["documentation"])
async def get_changelog():
    """Return the project changelog."""
    changelog_path = Path(__file__).parent.parent / "CHANGELOG.md"
    if changelog_path.exists():
        return {"content": changelog_path.read_text(encoding="utf-8")}
    return {"content": "No changelog available."}


@app.get("/api/community/links", tags=["community"])
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


@app.get("/api/templates/gallery", tags=["community"])
async def list_template_gallery_endpoint(category: str = ""):
    entries = storage.list_template_gallery(category)
    return {"templates": [e.to_dict() for e in entries]}


@app.get("/api/templates/gallery/{entry_id}", tags=["community"])
async def get_template_gallery_entry_endpoint(entry_id: int):
    entry = storage.get_template_gallery_entry(entry_id)
    if not entry:
        raise HTTPException(404, "Template not found")
    return entry.to_dict()


@app.post("/api/templates/gallery", tags=["community"])
async def create_template_gallery_entry_endpoint(body: _TemplateGalleryBody, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    user = storage.get_user(uid)
    from teb.models import TemplateGalleryEntry
    entry = TemplateGalleryEntry(
        name=body.name, description=body.description,
        author=user.email if user else "", category=body.category,
        template_json=json.dumps(body.template),
    )
    eid = storage.create_template_gallery_entry(entry)
    return {"id": eid}


@app.get("/api/community/plugins", tags=["community"])
async def community_plugins():
    """List community-built plugins."""
    return {"plugins": [], "message": "Community plugin directory — submit yours via PR!"}


class _BlogPostBody(BaseModel):
    title: str
    slug: str
    content: str = ""
    published: bool = False


@app.get("/api/blog", tags=["community"])
async def list_blog_posts_endpoint():
    posts = storage.list_blog_posts(published_only=True)
    return {"posts": [p.to_dict() for p in posts]}


@app.get("/api/blog/{slug}", tags=["community"])
async def get_blog_post_endpoint(slug: str):
    post = storage.get_blog_post_by_slug(slug)
    if not post:
        raise HTTPException(404, "Post not found")
    return post.to_dict()


@app.post("/api/blog", tags=["community"])
async def create_blog_post_endpoint(body: _BlogPostBody, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
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


@app.get("/api/roadmap", tags=["community"])
async def list_roadmap_endpoint(status: str = ""):
    items = storage.list_roadmap_items(status)
    return {"items": [i.to_dict() for i in items]}


@app.post("/api/roadmap", tags=["community"])
async def create_roadmap_endpoint(body: _RoadmapBody, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(403, "Admin only")
    from teb.models import RoadmapItem
    item = RoadmapItem(title=body.title, description=body.description,
                       status=body.status, category=body.category, target_date=body.target_date)
    iid = storage.create_roadmap_item(item)
    return {"id": iid}


@app.put("/api/roadmap/{item_id}", tags=["community"])
async def update_roadmap_endpoint(item_id: int, body: _RoadmapBody, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(403, "Admin only")
    storage.update_roadmap_item(item_id, title=body.title, description=body.description,
                                status=body.status, category=body.category, target_date=body.target_date)
    return {"updated": True}


@app.post("/api/roadmap/{item_id}/vote", tags=["community"])
async def vote_roadmap_endpoint(item_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    ok = storage.cast_feature_vote(uid, item_id)
    return {"voted": ok}


@app.delete("/api/roadmap/{item_id}/vote", tags=["community"])
async def unvote_roadmap_endpoint(item_id: int, request: Request):
    _check_api_rate_limit(request)
    uid = _require_user(request)
    ok = storage.remove_feature_vote(uid, item_id)
    return {"removed": ok}


# ═══════════════════════════════════════════════════════════════════════════════
# Bridging Plan: Risk, Scheduling, Reporting, Workload, Gamification Social
# ═══════════════════════════════════════════════════════════════════════════════


# ─── Phase 1: Risk Assessment & Triage ───────────────────────────────────────

@app.get("/api/tasks/{task_id}/risk", tags=["risk"])
async def get_task_risk(task_id: int, request: Request):
    """Get risk assessment for a specific task."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_task_for_user(task_id, uid)
    result = decomposer.estimate_risk(task_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.post("/api/goals/{goal_id}/triage", tags=["risk"])
async def triage_goal_tasks(goal_id: int, request: Request):
    """Auto-prioritize all tasks in a goal using AI (with template fallback)."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    results = decomposer.triage_tasks(goal_id)
    return {"goal_id": goal_id, "triage": results, "count": len(results)}


# ─── Phase 2: Persistent Auto-Scheduling ────────────────────────────────────

@app.post("/api/goals/{goal_id}/auto-schedule", tags=["scheduling"])
async def auto_schedule_goal(goal_id: int, request: Request):
    """Auto-schedule tasks into time blocks and persist the schedule."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
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


@app.get("/api/users/me/schedule", tags=["scheduling"])
async def get_user_schedule(request: Request):
    """Get all scheduled tasks for the current user."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    schedules = storage.list_task_schedules(user_id=uid)
    return {"schedules": [s.to_dict() for s in schedules], "count": len(schedules)}


# ─── Phase 3: Automated Progress Reporting ───────────────────────────────────
from teb import reporting  # noqa: E402


@app.post("/api/goals/{goal_id}/report", tags=["reporting"])
async def generate_report(goal_id: int, request: Request):
    """Generate a progress report for a goal."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    try:
        report = reporting.generate_progress_report(goal_id, uid)
        # Emit SSE event
        from teb import events
        events.emit_report_generated(uid, goal_id, report.id or 0, report.summary)
        return report.to_dict()
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@app.get("/api/goals/{goal_id}/reports", tags=["reporting"])
async def list_reports(goal_id: int, request: Request):
    """List all progress reports for a goal."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    reports = storage.list_progress_reports(goal_id)
    return {"reports": [r.to_dict() for r in reports], "count": len(reports)}


# ─── Phase 4: Workload Balancing ─────────────────────────────────────────────
from teb import workload  # noqa: E402


@app.get("/api/users/me/workload", tags=["workload"])
async def get_workload(request: Request):
    """Get workload analysis for the current user."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    return workload.get_user_capacity(uid)


@app.post("/api/goals/{goal_id}/rebalance", tags=["workload"])
async def rebalance_goal(goal_id: int, request: Request):
    """Analyze and suggest workload rebalancing for a goal."""
    _check_api_rate_limit(request)
    uid = _require_user(request)
    _get_goal_for_user(goal_id, uid)
    return workload.balance_workload(goal_id, uid)


# ─── Phase 6: Social Gamification ────────────────────────────────────────────


# ─── Success Graph ────────────────────────────────────────────────────────────

@app.get("/api/success-graph/stats", tags=["success-graph"])
async def success_graph_stats(request: Request, goal_type: Optional[str] = Query(default=None)):
    """Get statistics about the success path graph."""
    _require_user(request)
    from teb.success_graph import get_graph_stats
    return get_graph_stats(goal_type)


@app.get("/api/success-graph/path", tags=["success-graph"])
async def success_graph_best_path(request: Request, goal_type: str = Query(...)):
    """Get the highest-weight execution path for a goal type."""
    _require_user(request)
    from teb.success_graph import get_best_path
    path = get_best_path(goal_type)
    return {"goal_type": goal_type, "path": path, "steps": len(path)}


@app.get("/api/success-graph/paths", tags=["success-graph"])
async def success_graph_top_paths(
    request: Request,
    goal_type: str = Query(...),
    top_k: int = Query(default=3, ge=1, le=10),
):
    """Get the top-K proven execution paths for a goal type."""
    _require_user(request)
    from teb.success_graph import get_top_paths
    paths = get_top_paths(goal_type, top_k=top_k)
    return {"goal_type": goal_type, "paths": paths, "count": len(paths)}


# ─── execution endpoints (extracted to teb/routers/execution.py) ──
