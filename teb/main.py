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

from teb import agents, auth, browser, config, decomposer, deployer, executor, intelligence, integrations, messaging, payments, provisioning, scheduler, storage, transcribe
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
from teb.routers.goals import router as goals_router
from teb.routers.tasks import router as tasks_router
from teb.routers.agents import router as agents_router
from teb.routers.integrations import router as integrations_router
from teb.routers.collaboration import router as collaboration_router
from teb.routers.gamification import router as gamification_router
from teb.routers.intelligence import router as intelligence_router
from teb.routers.plugins import router as plugins_router
from teb.routers.enterprise import router as enterprise_router
from teb.routers.community import router as community_router
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
app.include_router(goals_router)
app.include_router(tasks_router)
app.include_router(agents_router)
app.include_router(integrations_router)
app.include_router(collaboration_router)
app.include_router(gamification_router)
app.include_router(intelligence_router)
app.include_router(plugins_router)
app.include_router(enterprise_router)
app.include_router(community_router)

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


# ─── goals endpoints (extracted to teb/routers/goals.py) ──
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

    result = payments.process_webhook(provider, body, signature)
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
    result = payments.recover_failed_transactions()
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

# ─── tasks endpoints (extracted to teb/routers/tasks.py) ──
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


# ─── gamification endpoints (extracted to teb/routers/gamification.py) ──
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


# ─── gamification endpoints (extracted to teb/routers/gamification.py) ──
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


# ─── collaboration endpoints (extracted to teb/routers/collaboration.py) ──
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


# ─── collaboration endpoints (extracted to teb/routers/collaboration.py) ──
# ─── API Rate Limit Dashboard ───────────────────────────────────────────────

@app.get("/api/integrations/rate-limits", tags=["integrations"])
async def get_rate_limit_usage(request: Request):
    """Get API rate limit usage for the current user."""
    uid = _require_user(request)
    _check_api_rate_limit(request)
    usage = storage.get_api_rate_limit_usage(uid)
    return usage


# ─── plugins endpoints (extracted to teb/routers/plugins.py) ──
# ─── execution endpoints (extracted to teb/routers/execution.py) ──
