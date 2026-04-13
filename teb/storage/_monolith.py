"""Domain storage functions — pending gradual extraction into submodules."""
from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Generator, List, Optional, Set

from teb.config import get_db_path, SECRET_KEY
from teb.storage.base import (
    _REVENUE_UNITS,
    _conn,
    register_reset_callback,
    _db_path,
    _decrypt_value,
    _encrypt_value,
    _get_fernet,
    _run_migrations,
    _safe_add_column,
    _with_retry,
    init_db,
    set_db_path,
)

from teb.models import (
    Achievement,
    ActivityFeedEntry,
    AgentFlow,
    AgentGoalMemory,
    AgentHandoff,
    AgentMessage,
    AgentSchedule,
    ApiCredential,
    AuditEvent,
    BrandingConfig,
    BrowserAction,
    CheckIn,
    CommentReaction,
    ContentBlock,
    CustomField,
    CustomFieldDefinition,
    DashboardLayout,
    DashboardWidget,
    DirectMessage,
    EmailNotificationConfig,
    ExecutionCheckpoint,
    ExecutionContext,
    ExecutionLog,
    Goal,
    GoalChatMessage,
    GoalCollaborator,
    GoalTemplate,
    IPAllowlist,
    Integration,
    IntegrationListing,
    IntegrationTemplate,
    MessagingConfig,
    Milestone,
    Notification,
    NotificationPreference,
    NudgeEvent,
    OAuthConnection,
    Organization,
    OutcomeMetric,
    PersonalApiKey,
    PluginListing,
    PluginManifest,
    PluginView,
    ProactiveSuggestion,
    ProgressSnapshot,
    PushSubscription,
    RecurrenceRule,
    SSOConfig,
    SavedView,
    ScheduledReport,
    SpendingBudget,
    SpendingRequest,
    Streak,
    SuccessPath,
    Task,
    TaskArtifact,
    TaskBlocker,
    TaskComment,
    TaskRisk,
    TaskSchedule,
    TeamChallenge,
    Theme,
    TimeEntry,
    LeaderboardEntry,
    ProgressReport,
    User,
    UserProfile,
    UserXP,
    WebhookConfig,
    WebhookRule,
    Workspace,
    WorkspaceMember,
)

def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        role=row["role"] if "role" in row.keys() else "user",
        email_verified=bool(row["email_verified"]) if "email_verified" in row.keys() else False,
        failed_login_attempts=row["failed_login_attempts"] if "failed_login_attempts" in row.keys() else 0,
        locked_until=(
            datetime.fromisoformat(row["locked_until"])
            if "locked_until" in row.keys() and row["locked_until"]
            else None
        ),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_user(user: User) -> User:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO users (email, password_hash, role, email_verified, created_at) VALUES (?, ?, ?, ?, ?)",
            (user.email, user.password_hash, user.role, int(user.email_verified), now),
        )
        user.id = cur.lastrowid
        user.created_at = datetime.fromisoformat(now)
    return user


def get_user_by_email(email: str) -> Optional[User]:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()
    return _row_to_user(row) if row else None


def get_user(user_id: int) -> Optional[User]:
    with _conn() as con:
        row = con.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return _row_to_user(row) if row else None


def update_user(user: User) -> User:
    """Update user fields (role, email_verified, login attempts, lock)."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE users SET role = ?, email_verified = ?, failed_login_attempts = ?,
               locked_until = ? WHERE id = ?""",
            (user.role, int(user.email_verified), user.failed_login_attempts,
             user.locked_until.isoformat() if user.locked_until else None, user.id),
        )
    return user


def list_all_users() -> List[User]:
    """Admin: return all users ordered by created_at DESC."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM users ORDER BY created_at DESC").fetchall()
    return [_row_to_user(r) for r in rows]


def delete_user(user_id: int) -> None:
    """Admin: delete user and all their data (goals, tasks, credentials, etc.)."""
    with _conn() as con:
        # Delete unscoped credentials that belong to this user
        con.execute("DELETE FROM api_credentials WHERE user_id = ?", (user_id,))
        con.execute("DELETE FROM messaging_configs WHERE user_id = ?", (user_id,))
        # goals → tasks/check_ins/etc. cascade automatically (FK ON DELETE CASCADE)
        con.execute("DELETE FROM goals WHERE user_id = ?", (user_id,))
        # user_profiles, user_behavior, refresh_tokens, payment_accounts cascade
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


def get_system_stats() -> dict:
    """Admin: return aggregate platform statistics."""
    with _conn() as con:
        total_users = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        total_goals = con.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
        active_goals = con.execute(
            "SELECT COUNT(*) FROM goals WHERE status='in_progress'"
        ).fetchone()[0]
        goals_done = con.execute(
            "SELECT COUNT(*) FROM goals WHERE status='done'"
        ).fetchone()[0]
        total_tasks = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        tasks_done = con.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='done'"
        ).fetchone()[0]
        total_executions = con.execute("SELECT COUNT(*) FROM execution_logs").fetchone()[0]
        spending_approved = con.execute(
            "SELECT COUNT(*) FROM spending_requests WHERE status='approved'"
        ).fetchone()[0]
    return {
        "total_users": total_users,
        "total_goals": total_goals,
        "active_goals": active_goals,
        "goals_done": goals_done,
        "total_tasks": total_tasks,
        "tasks_done": tasks_done,
        "total_executions": total_executions,
        "spending_approved": spending_approved,
    }


def get_database_health() -> dict:
    """Return database health diagnostics: size, table count, WAL status, schema version."""
    import os as _os
    with _conn() as con:
        # Table count
        table_count = con.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table'"
        ).fetchone()[0]

        # WAL mode check
        journal_mode = con.execute("PRAGMA journal_mode").fetchone()[0]

        # Integrity check (quick variant — only checks first page)
        integrity = con.execute("PRAGMA quick_check(1)").fetchone()[0]

        # Schema version
        schema_row = con.execute(
            "SELECT version, applied_at FROM schema_versions ORDER BY id DESC LIMIT 1"
        ).fetchone()
        schema_version = schema_row["version"] if schema_row else "unknown"
        schema_applied_at = schema_row["applied_at"] if schema_row else None

    # File size
    db_file = _db_path()
    try:
        db_size_mb = round(_os.path.getsize(db_file) / (1024 * 1024), 2)
    except OSError:
        db_size_mb = -1

    return {
        "status": "ok" if integrity == "ok" else "degraded",
        "table_count": table_count,
        "journal_mode": journal_mode,
        "integrity": integrity,
        "schema_version": schema_version,
        "schema_applied_at": schema_applied_at,
        "size_mb": db_size_mb,
    }


def get_schema_versions() -> list:
    """Return all recorded schema version history."""
    with _conn() as con:
        rows = con.execute(
            "SELECT version, description, applied_at FROM schema_versions ORDER BY id"
        ).fetchall()
    return [
        {"version": r["version"], "description": r["description"], "applied_at": r["applied_at"]}
        for r in rows
    ]


def record_failed_login(user_id: int) -> int:
    """Increment failed login attempts. Returns new count."""
    with _conn() as con:
        con.execute(
            "UPDATE users SET failed_login_attempts = failed_login_attempts + 1 WHERE id = ?",
            (user_id,),
        )
        row = con.execute("SELECT failed_login_attempts FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["failed_login_attempts"] if row else 0


def reset_failed_logins(user_id: int) -> None:
    """Reset failed login attempts after successful login."""
    with _conn() as con:
        con.execute(
            "UPDATE users SET failed_login_attempts = 0, locked_until = NULL WHERE id = ?",
            (user_id,),
        )


def lock_user(user_id: int, until: datetime) -> None:
    """Lock user account until a given time."""
    with _conn() as con:
        con.execute(
            "UPDATE users SET locked_until = ? WHERE id = ?",
            (until.isoformat(), user_id),
        )


# ─── Refresh Tokens ──────────────────────────────────────────────────────────

def create_refresh_token(user_id: int, token_hash: str, expires_at: datetime) -> int:
    """Store a refresh token. Returns the token row id."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO refresh_tokens (user_id, token_hash, expires_at, revoked, created_at)
               VALUES (?, ?, ?, 0, ?)""",
            (user_id, token_hash, expires_at.isoformat(), now),
        )
    return cur.lastrowid  # type: ignore[return-value]


def get_refresh_token(token_hash: str) -> Optional[dict]:
    """Lookup a refresh token by hash."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ? AND revoked = 0", (token_hash,)
        ).fetchone()
    if not row:
        return None
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "token_hash": row["token_hash"],
        "expires_at": row["expires_at"],
        "revoked": bool(row["revoked"]),
        "created_at": row["created_at"],
    }


def revoke_refresh_token(token_hash: str) -> None:
    """Revoke a refresh token."""
    with _conn() as con:
        con.execute("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = ?", (token_hash,))


def revoke_all_refresh_tokens(user_id: int) -> None:
    """Revoke all refresh tokens for a user (logout everywhere)."""
    with _conn() as con:
        con.execute("UPDATE refresh_tokens SET revoked = 1 WHERE user_id = ?", (user_id,))


# ─── Goals ────────────────────────────────────────────────────────────────────

def _row_to_goal(row: sqlite3.Row) -> Goal:
    g = Goal(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        answers=json.loads(row["answers"]),
    )
    g.user_id = row["user_id"] if "user_id" in row.keys() else None
    g.parent_goal_id = row["parent_goal_id"] if "parent_goal_id" in row.keys() else None
    g.auto_execute = bool(row["auto_execute"]) if "auto_execute" in row.keys() else False
    g.tags = row["tags"] if "tags" in row.keys() else ""
    g.version = row["version"] if "version" in row.keys() else 1
    g.created_at = datetime.fromisoformat(row["created_at"])
    g.updated_at = datetime.fromisoformat(row["updated_at"])
    return g


def create_goal(goal: Goal) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO goals (user_id, parent_goal_id, title, description, status, answers, auto_execute, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (goal.user_id, goal.parent_goal_id, goal.title, goal.description, goal.status,
             json.dumps(goal.answers), int(goal.auto_execute), goal.tags, now, now),
        )
        goal.id = cur.lastrowid
        goal.created_at = datetime.fromisoformat(now)
        goal.updated_at = datetime.fromisoformat(now)
    return goal


def get_goal(goal_id: int) -> Optional[Goal]:
    with _conn() as con:
        row = con.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return _row_to_goal(row) if row else None


def list_goals(user_id: Optional[int] = None) -> List[Goal]:
    if user_id is not None:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM goals WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
            ).fetchall()
    else:
        with _conn() as con:
            rows = con.execute(
                "SELECT * FROM goals WHERE user_id IS NULL ORDER BY created_at DESC"
            ).fetchall()
    return [_row_to_goal(r) for r in rows]


@_with_retry
def update_goal(goal: Goal) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "UPDATE goals SET title=?, description=?, status=?, answers=?, auto_execute=?, tags=?, version=version+1, updated_at=? "
            "WHERE id=? AND version=?",
            (goal.title, goal.description, goal.status, json.dumps(goal.answers),
             int(goal.auto_execute), goal.tags, now, goal.id, goal.version),
        )
        if cur.rowcount == 0:
            existing = con.execute("SELECT id FROM goals WHERE id=?", (goal.id,)).fetchone()
            if existing:
                raise VersionConflictError("Goal has been modified by another request")
    goal.version += 1
    goal.updated_at = datetime.fromisoformat(now)
    return goal


class VersionConflictError(Exception):
    """Raised when an optimistic concurrency version check fails."""
    pass


# ─── Tasks ────────────────────────────────────────────────────────────────────

def _row_to_task(row: sqlite3.Row) -> Task:
    t = Task(
        id=row["id"],
        goal_id=row["goal_id"],
        parent_id=row["parent_id"],
        title=row["title"],
        description=row["description"],
        estimated_minutes=row["estimated_minutes"],
        status=row["status"],
        order_index=row["order_index"],
    )
    t.due_date = row["due_date"] if "due_date" in row.keys() else ""
    t.depends_on = row["depends_on"] if "depends_on" in row.keys() else "[]"
    t.tags = row["tags"] if "tags" in row.keys() else ""
    t.assigned_to = row["assigned_to"] if "assigned_to" in row.keys() else None
    t.priority = row["priority"] if "priority" in row.keys() else "normal"
    t.version = row["version"] if "version" in row.keys() else 1
    t.created_at = datetime.fromisoformat(row["created_at"])
    t.updated_at = datetime.fromisoformat(row["updated_at"])
    return t


@_with_retry
def create_task(task: Task) -> Task:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO tasks (goal_id, parent_id, title, description, estimated_minutes, "
            "status, order_index, due_date, depends_on, tags, priority, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.goal_id, task.parent_id, task.title, task.description,
                task.estimated_minutes, task.status, task.order_index,
                task.due_date, task.depends_on, task.tags, task.priority, now, now,
            ),
        )
        task.id = cur.lastrowid
        task.created_at = datetime.fromisoformat(now)
        task.updated_at = datetime.fromisoformat(now)
    return task


def get_task(task_id: int) -> Optional[Task]:
    with _conn() as con:
        row = con.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    return _row_to_task(row) if row else None


def list_tasks(goal_id: Optional[int] = None, status: Optional[str] = None) -> List[Task]:
    query = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY order_index ASC, id ASC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_task(r) for r in rows]


@_with_retry
def update_task(task: Task) -> Task:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "UPDATE tasks SET title=?, description=?, estimated_minutes=?, status=?, "
            "order_index=?, parent_id=?, due_date=?, depends_on=?, tags=?, assigned_to=?, priority=?, version=version+1, updated_at=? "
            "WHERE id=? AND version=?",
            (
                task.title, task.description, task.estimated_minutes,
                task.status, task.order_index, task.parent_id,
                task.due_date, task.depends_on, task.tags, task.assigned_to, task.priority, now, task.id, task.version,
            ),
        )
        if cur.rowcount == 0:
            existing = con.execute("SELECT id FROM tasks WHERE id=?", (task.id,)).fetchone()
            if existing:
                raise VersionConflictError("Task has been modified by another request")
    task.version += 1
    task.updated_at = datetime.fromisoformat(now)
    return task


def delete_tasks_for_goal(goal_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM tasks WHERE goal_id = ?", (goal_id,))


@_with_retry
def delete_goal(goal_id: int) -> None:
    """Delete a goal and all its related data (tasks, checkins, outcomes, budgets)."""
    with _conn() as con:
        con.execute("DELETE FROM tasks WHERE goal_id = ?", (goal_id,))
        # Guard against tables that may not exist in older schemas
        for table in ("checkins", "outcome_metrics", "spending_budgets", "spending_requests"):
            try:
                con.execute(f"DELETE FROM {table} WHERE goal_id = ?", (goal_id,))
            except Exception:
                pass
        con.execute("DELETE FROM goals WHERE id = ?", (goal_id,))


def delete_task(task_id: int) -> None:
    """Delete a task and its children (CASCADE handles children)."""
    with _conn() as con:
        con.execute("DELETE FROM tasks WHERE id = ?", (task_id,))


# ─── API Credentials ─────────────────────────────────────────────────────────

def _row_to_credential(row: sqlite3.Row) -> ApiCredential:
    return ApiCredential(
        id=row["id"],
        name=row["name"],
        base_url=row["base_url"],
        auth_header=row["auth_header"],
        auth_value=_decrypt_value(row["auth_value"]),
        description=row["description"],
        user_id=row["user_id"] if "user_id" in row.keys() else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_credential(cred: ApiCredential) -> ApiCredential:
    now = datetime.now(timezone.utc).isoformat()
    encrypted_value = _encrypt_value(cred.auth_value)
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO api_credentials (name, base_url, auth_header, auth_value, description, user_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (cred.name, cred.base_url, cred.auth_header, encrypted_value, cred.description, cred.user_id, now),
        )
        cred.id = cur.lastrowid
        cred.created_at = datetime.fromisoformat(now)
    return cred


def get_credential(cred_id: int) -> Optional[ApiCredential]:
    with _conn() as con:
        row = con.execute("SELECT * FROM api_credentials WHERE id = ?", (cred_id,)).fetchone()
    return _row_to_credential(row) if row else None


def list_credentials(user_id: Optional[int] = None) -> List[ApiCredential]:
    """List credentials. If user_id is given, returns only that user's credentials
    plus any legacy unscoped credentials (user_id IS NULL)."""
    with _conn() as con:
        if user_id is not None:
            rows = con.execute(
                "SELECT * FROM api_credentials WHERE user_id = ? OR user_id IS NULL ORDER BY created_at DESC",
                (user_id,),
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM api_credentials ORDER BY created_at DESC").fetchall()
    return [_row_to_credential(r) for r in rows]


def delete_credential(cred_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM api_credentials WHERE id = ?", (cred_id,))


# ─── Execution Logs ──────────────────────────────────────────────────────────

def _row_to_execution_log(row: sqlite3.Row) -> ExecutionLog:
    return ExecutionLog(
        id=row["id"],
        task_id=row["task_id"],
        credential_id=row["credential_id"],
        action=row["action"],
        request_summary=row["request_summary"],
        response_summary=row["response_summary"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@_with_retry
def create_execution_log(log: ExecutionLog) -> ExecutionLog:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO execution_logs (task_id, credential_id, action, request_summary, "
            "response_summary, status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (log.task_id, log.credential_id, log.action, log.request_summary,
             log.response_summary, log.status, now),
        )
        log.id = cur.lastrowid
        log.created_at = datetime.fromisoformat(now)
    return log


def list_execution_logs(task_id: int) -> List[ExecutionLog]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM execution_logs WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    return [_row_to_execution_log(r) for r in rows]


# ─── Check-ins ───────────────────────────────────────────────────────────────

def _row_to_checkin(row: sqlite3.Row) -> CheckIn:
    return CheckIn(
        id=row["id"],
        goal_id=row["goal_id"],
        done_summary=row["done_summary"],
        blockers=row["blockers"],
        mood=row["mood"],
        feedback=row["feedback"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_checkin(ci: CheckIn) -> CheckIn:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO check_ins (goal_id, done_summary, blockers, mood, feedback, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ci.goal_id, ci.done_summary, ci.blockers, ci.mood, ci.feedback, now),
        )
        ci.id = cur.lastrowid
        ci.created_at = datetime.fromisoformat(now)
    return ci


def list_checkins(goal_id: int, limit: Optional[int] = None) -> List[CheckIn]:
    query = "SELECT * FROM check_ins WHERE goal_id = ? ORDER BY created_at DESC"
    params: list = [goal_id]
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_checkin(r) for r in rows]


def get_last_checkin(goal_id: int) -> Optional[CheckIn]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM check_ins WHERE goal_id = ? ORDER BY created_at DESC LIMIT 1",
            (goal_id,),
        ).fetchone()
    return _row_to_checkin(row) if row else None


# ─── Outcome Metrics ─────────────────────────────────────────────────────────

def _row_to_outcome_metric(row: sqlite3.Row) -> OutcomeMetric:
    return OutcomeMetric(
        id=row["id"],
        goal_id=row["goal_id"],
        label=row["label"],
        target_value=row["target_value"],
        current_value=row["current_value"],
        unit=row["unit"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_outcome_metric(om: OutcomeMetric) -> OutcomeMetric:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO outcome_metrics (goal_id, label, target_value, current_value, unit, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (om.goal_id, om.label, om.target_value, om.current_value, om.unit, now, now),
        )
        om.id = cur.lastrowid
        om.created_at = datetime.fromisoformat(now)
        om.updated_at = datetime.fromisoformat(now)
    return om


def get_outcome_metric(metric_id: int) -> Optional[OutcomeMetric]:
    with _conn() as con:
        row = con.execute("SELECT * FROM outcome_metrics WHERE id = ?", (metric_id,)).fetchone()
    return _row_to_outcome_metric(row) if row else None


def list_outcome_metrics(goal_id: int) -> List[OutcomeMetric]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM outcome_metrics WHERE goal_id = ? ORDER BY created_at ASC",
            (goal_id,),
        ).fetchall()
    return [_row_to_outcome_metric(r) for r in rows]


def update_outcome_metric(om: OutcomeMetric) -> OutcomeMetric:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE outcome_metrics SET label=?, target_value=?, current_value=?, unit=?, updated_at=? WHERE id=?",
            (om.label, om.target_value, om.current_value, om.unit, now, om.id),
        )
    om.updated_at = datetime.fromisoformat(now)
    return om


# ─── Nudge Events ────────────────────────────────────────────────────────────

def _row_to_nudge(row: sqlite3.Row) -> NudgeEvent:
    return NudgeEvent(
        id=row["id"],
        goal_id=row["goal_id"],
        nudge_type=row["nudge_type"],
        message=row["message"],
        acknowledged=bool(row["acknowledged"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_nudge(ne: NudgeEvent) -> NudgeEvent:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO nudge_events (goal_id, nudge_type, message, acknowledged, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (ne.goal_id, ne.nudge_type, ne.message, int(ne.acknowledged), now),
        )
        ne.id = cur.lastrowid
        ne.created_at = datetime.fromisoformat(now)
    return ne


def list_nudges(goal_id: int, unacknowledged_only: bool = False) -> List[NudgeEvent]:
    query = "SELECT * FROM nudge_events WHERE goal_id = ?"
    params: list = [goal_id]
    if unacknowledged_only:
        query += " AND acknowledged = 0"
    query += " ORDER BY created_at DESC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_nudge(r) for r in rows]


def get_nudge(nudge_id: int) -> Optional[NudgeEvent]:
    """Get a single nudge event by ID."""
    with _conn() as con:
        row = con.execute("SELECT * FROM nudge_events WHERE id = ?", (nudge_id,)).fetchone()
    return _row_to_nudge(row) if row else None


def acknowledge_nudge(nudge_id: int) -> Optional[NudgeEvent]:
    with _conn() as con:
        row = con.execute("SELECT * FROM nudge_events WHERE id = ?", (nudge_id,)).fetchone()
        if not row:
            return None
        con.execute("UPDATE nudge_events SET acknowledged = 1 WHERE id = ?", (nudge_id,))
    ne = _row_to_nudge(row)
    ne.acknowledged = True
    return ne


# ─── User Profiles ───────────────────────────────────────────────────────────

def _row_to_user_profile(row: sqlite3.Row) -> UserProfile:
    return UserProfile(
        id=row["id"],
        user_id=row["user_id"] if "user_id" in row.keys() else None,
        skills=row["skills"],
        available_hours_per_day=row["available_hours_per_day"],
        experience_level=row["experience_level"],
        interests=row["interests"],
        preferred_learning_style=row["preferred_learning_style"],
        goals_completed=row["goals_completed"],
        total_tasks_completed=row["total_tasks_completed"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def get_or_create_profile(user_id: Optional[int] = None) -> UserProfile:
    """Get the user profile, creating it if it doesn't exist.

    If user_id is provided, returns the profile for that user.
    Otherwise, returns the singleton profile (legacy behavior).
    """
    with _conn() as con:
        if user_id is not None:
            row = con.execute(
                "SELECT * FROM user_profiles WHERE user_id = ? LIMIT 1", (user_id,)
            ).fetchone()
        else:
            row = con.execute("SELECT * FROM user_profiles ORDER BY id LIMIT 1").fetchone()
        if row:
            return _row_to_user_profile(row)
        now = datetime.now(timezone.utc).isoformat()
        cur = con.execute(
            "INSERT INTO user_profiles (user_id, created_at, updated_at) VALUES (?, ?, ?)",
            (user_id, now, now),
        )
        profile = UserProfile(id=cur.lastrowid, user_id=user_id)
        profile.created_at = datetime.fromisoformat(now)
        profile.updated_at = datetime.fromisoformat(now)
    return profile


def update_profile(profile: UserProfile) -> UserProfile:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE user_profiles SET skills=?, available_hours_per_day=?, experience_level=?, "
            "interests=?, preferred_learning_style=?, goals_completed=?, "
            "total_tasks_completed=?, updated_at=? WHERE id=?",
            (
                profile.skills, profile.available_hours_per_day, profile.experience_level,
                profile.interests, profile.preferred_learning_style, profile.goals_completed,
                profile.total_tasks_completed, now, profile.id,
            ),
        )
    profile.updated_at = datetime.fromisoformat(now)
    return profile


# ─── Success Paths ───────────────────────────────────────────────────────────

def _row_to_success_path(row: sqlite3.Row) -> SuccessPath:
    return SuccessPath(
        id=row["id"],
        goal_type=row["goal_type"],
        steps_json=row["steps_json"],
        outcome_summary=row["outcome_summary"],
        source_goal_id=row["source_goal_id"],
        times_reused=row["times_reused"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_success_path(sp: SuccessPath) -> SuccessPath:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO success_paths (goal_type, steps_json, outcome_summary, source_goal_id, times_reused, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sp.goal_type, sp.steps_json, sp.outcome_summary, sp.source_goal_id, sp.times_reused, now),
        )
        sp.id = cur.lastrowid
        sp.created_at = datetime.fromisoformat(now)
    return sp


def list_success_paths(goal_type: Optional[str] = None) -> List[SuccessPath]:
    query = "SELECT * FROM success_paths"
    params: list = []
    if goal_type:
        query += " WHERE goal_type = ?"
        params.append(goal_type)
    query += " ORDER BY times_reused DESC, created_at DESC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_success_path(r) for r in rows]


def increment_success_path_reuse(path_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE success_paths SET times_reused = times_reused + 1 WHERE id = ?",
            (path_id,),
        )


# ─── Proactive Suggestions ──────────────────────────────────────────────────

def _row_to_suggestion(row: sqlite3.Row) -> ProactiveSuggestion:
    return ProactiveSuggestion(
        id=row["id"],
        goal_id=row["goal_id"],
        suggestion=row["suggestion"],
        rationale=row["rationale"],
        category=row["category"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_suggestion(ps: ProactiveSuggestion) -> ProactiveSuggestion:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO proactive_suggestions (goal_id, suggestion, rationale, category, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ps.goal_id, ps.suggestion, ps.rationale, ps.category, ps.status, now),
        )
        ps.id = cur.lastrowid
        ps.created_at = datetime.fromisoformat(now)
    return ps


def list_suggestions(goal_id: int, status: Optional[str] = None) -> List[ProactiveSuggestion]:
    query = "SELECT * FROM proactive_suggestions WHERE goal_id = ?"
    params: list = [goal_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_suggestion(r) for r in rows]


def update_suggestion_status(suggestion_id: int, status: str) -> Optional[ProactiveSuggestion]:
    with _conn() as con:
        row = con.execute("SELECT * FROM proactive_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
        if not row:
            return None
        con.execute(
            "UPDATE proactive_suggestions SET status = ? WHERE id = ?",
            (status, suggestion_id),
        )
    ps = _row_to_suggestion(row)
    ps.status = status
    return ps


# ─── Agent Handoffs ──────────────────────────────────────────────────────────

def _row_to_handoff(row: sqlite3.Row) -> AgentHandoff:
    return AgentHandoff(
        id=row["id"],
        goal_id=row["goal_id"],
        from_agent=row["from_agent"],
        to_agent=row["to_agent"],
        task_id=row["task_id"],
        input_summary=row["input_summary"],
        output_summary=row["output_summary"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


@_with_retry
def create_handoff(handoff: AgentHandoff) -> AgentHandoff:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO agent_handoffs
               (goal_id, from_agent, to_agent, task_id, input_summary, output_summary, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (handoff.goal_id, handoff.from_agent, handoff.to_agent,
             handoff.task_id, handoff.input_summary, handoff.output_summary,
             handoff.status, now),
        )
        handoff.id = cur.lastrowid
        handoff.created_at = datetime.fromisoformat(now)
    return handoff


def update_handoff(handoff: AgentHandoff) -> AgentHandoff:
    with _conn() as con:
        con.execute(
            """UPDATE agent_handoffs
               SET task_id = ?, output_summary = ?, status = ?
               WHERE id = ?""",
            (handoff.task_id, handoff.output_summary, handoff.status, handoff.id),
        )
    return handoff


def list_handoffs(goal_id: int) -> List[AgentHandoff]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_handoffs WHERE goal_id = ? ORDER BY created_at ASC",
            (goal_id,),
        ).fetchall()
    return [_row_to_handoff(r) for r in rows]


# ─── Agent Messages ──────────────────────────────────────────────────────────

def _row_to_agent_message(row: sqlite3.Row) -> AgentMessage:
    return AgentMessage(
        id=row["id"],
        goal_id=row["goal_id"],
        from_agent=row["from_agent"],
        to_agent=row["to_agent"],
        message_type=row["message_type"],
        content=row["content"],
        in_reply_to=row["in_reply_to"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


def create_agent_message(msg: AgentMessage) -> AgentMessage:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO agent_messages
               (goal_id, from_agent, to_agent, message_type, content, in_reply_to, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (msg.goal_id, msg.from_agent, msg.to_agent,
             msg.message_type, msg.content, msg.in_reply_to, now),
        )
        msg.id = cur.lastrowid
        msg.created_at = datetime.fromisoformat(now)
    return msg


def list_agent_messages(goal_id: int, agent_type: Optional[str] = None) -> List[AgentMessage]:
    """List agent messages for a goal, optionally filtered to messages involving a specific agent."""
    if agent_type:
        query = ("SELECT * FROM agent_messages WHERE goal_id = ? "
                 "AND (from_agent = ? OR to_agent = ?) ORDER BY created_at ASC")
        params: list = [goal_id, agent_type, agent_type]
    else:
        query = "SELECT * FROM agent_messages WHERE goal_id = ? ORDER BY created_at ASC"
        params = [goal_id]
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_agent_message(r) for r in rows]


# ─── Agent Activity ──────────────────────────────────────────────────────────

@_with_retry
def store_agent_activity(goal_id: int, agent_type: str, action: str, detail: str = "", status: str = "running") -> int:
    """Record an agent activity entry. Returns the activity ID."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO agent_activity (goal_id, agent_type, action, detail, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (goal_id, agent_type, action, detail, status, now),
        )
        return cur.lastrowid  # type: ignore[return-value]


@_with_retry
def update_agent_activity_status(activity_id: int, status: str, detail: str = "") -> None:
    """Update the status (and optional detail) of an agent activity entry."""
    with _conn() as con:
        if detail:
            con.execute(
                "UPDATE agent_activity SET status = ?, detail = ? WHERE id = ?",
                (status, detail, activity_id),
            )
        else:
            con.execute(
                "UPDATE agent_activity SET status = ? WHERE id = ?",
                (status, activity_id),
            )


def get_agent_activity(goal_id: int, limit: int = 50) -> list:
    """Get recent agent activity for a goal."""
    from teb.models import AgentActivity
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_activity WHERE goal_id = ? ORDER BY created_at DESC LIMIT ?",
            (goal_id, limit),
        ).fetchall()
    return [
        AgentActivity(
            id=r["id"],
            goal_id=r["goal_id"],
            agent_type=r["agent_type"],
            action=r["action"],
            detail=r["detail"] or "",
            status=r["status"] or "running",
            created_at=r["created_at"] or "",
        )
        for r in rows
    ]


# ─── Browser Actions ────────────────────────────────────────────────────────

def _row_to_browser_action(row: sqlite3.Row) -> BrowserAction:
    return BrowserAction(
        id=row["id"],
        task_id=row["task_id"],
        action_type=row["action_type"],
        target=row["target"],
        value=row["value"],
        status=row["status"],
        error=row["error"],
        screenshot_path=row["screenshot_path"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


def create_browser_action(action: BrowserAction) -> BrowserAction:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO browser_actions
               (task_id, action_type, target, value, status, error, screenshot_path, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (action.task_id, action.action_type, action.target,
             action.value, action.status, action.error,
             action.screenshot_path, now),
        )
        action.id = cur.lastrowid
        action.created_at = datetime.fromisoformat(now)
    return action


def update_browser_action(action: BrowserAction) -> BrowserAction:
    with _conn() as con:
        con.execute(
            """UPDATE browser_actions
               SET status = ?, value = ?, error = ?, screenshot_path = ?
               WHERE id = ?""",
            (action.status, action.value, action.error, action.screenshot_path, action.id),
        )
    return action


def list_browser_actions(task_id: int) -> List[BrowserAction]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM browser_actions WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    return [_row_to_browser_action(r) for r in rows]


# ─── Integrations ────────────────────────────────────────────────────────────

def _row_to_integration(row: sqlite3.Row) -> Integration:
    return Integration(
        id=row["id"],
        service_name=row["service_name"],
        category=row["category"],
        base_url=row["base_url"],
        auth_type=row["auth_type"],
        auth_header=row["auth_header"],
        docs_url=row["docs_url"],
        capabilities=row["capabilities"],
        common_endpoints=row["common_endpoints"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


def create_integration(integration: Integration) -> Integration:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO integrations
               (service_name, category, base_url, auth_type, auth_header,
                docs_url, capabilities, common_endpoints, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (integration.service_name, integration.category, integration.base_url,
             integration.auth_type, integration.auth_header, integration.docs_url,
             integration.capabilities, integration.common_endpoints, now),
        )
        integration.id = cur.lastrowid
        integration.created_at = datetime.fromisoformat(now)
    return integration


def get_integration(service_name: str) -> Optional[Integration]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM integrations WHERE service_name = ?", (service_name,),
        ).fetchone()
    return _row_to_integration(row) if row else None


def list_integrations(category: Optional[str] = None) -> List[Integration]:
    if category:
        query = "SELECT * FROM integrations WHERE category = ? ORDER BY service_name"
        params: list = [category]
    else:
        query = "SELECT * FROM integrations ORDER BY service_name"
        params = []
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_integration(r) for r in rows]


def delete_integration(integration_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM integrations WHERE id = ?", (integration_id,))


# ─── Spending Budgets ────────────────────────────────────────────────────────

def _row_to_spending_budget(row: sqlite3.Row) -> SpendingBudget:
    return SpendingBudget(
        id=row["id"],
        goal_id=row["goal_id"],
        daily_limit=row["daily_limit"],
        total_limit=row["total_limit"],
        category=row["category"],
        require_approval=bool(row["require_approval"]),
        spent_today=row["spent_today"],
        spent_total=row["spent_total"],
        autopilot_enabled=bool(row["autopilot_enabled"]) if "autopilot_enabled" in row.keys() else False,
        autopilot_threshold=row["autopilot_threshold"] if "autopilot_threshold" in row.keys() else 50.0,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_spending_budget(budget: SpendingBudget) -> SpendingBudget:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO spending_budgets
               (goal_id, daily_limit, total_limit, category, require_approval,
                spent_today, spent_total, autopilot_enabled, autopilot_threshold,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (budget.goal_id, budget.daily_limit, budget.total_limit,
             budget.category, int(budget.require_approval),
             budget.spent_today, budget.spent_total,
             int(budget.autopilot_enabled), budget.autopilot_threshold,
             now, now),
        )
        budget.id = cur.lastrowid
        budget.created_at = datetime.fromisoformat(now)
        budget.updated_at = datetime.fromisoformat(now)
    return budget


def get_spending_budget(budget_id: int) -> Optional[SpendingBudget]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM spending_budgets WHERE id = ?", (budget_id,),
        ).fetchone()
    return _row_to_spending_budget(row) if row else None


def list_spending_budgets(goal_id: int) -> List[SpendingBudget]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM spending_budgets WHERE goal_id = ? ORDER BY category ASC",
            (goal_id,),
        ).fetchall()
    budgets = [_row_to_spending_budget(r) for r in rows]
    # Auto-reset stale daily counters on every listing
    return [maybe_reset_daily_spending(b) for b in budgets]


def find_spending_budget(goal_id: int, category: str) -> Optional[SpendingBudget]:
    """Find a budget for a specific goal and category, falling back to 'general'."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM spending_budgets WHERE goal_id = ? AND category = ?",
            (goal_id, category),
        ).fetchone()
        if row:
            return _row_to_spending_budget(row)
        # Fall back to general budget
        row = con.execute(
            "SELECT * FROM spending_budgets WHERE goal_id = ? AND category = 'general'",
            (goal_id,),
        ).fetchone()
    return _row_to_spending_budget(row) if row else None


def update_spending_budget(budget: SpendingBudget) -> SpendingBudget:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE spending_budgets
               SET daily_limit=?, total_limit=?, category=?, require_approval=?,
                   spent_today=?, spent_total=?, autopilot_enabled=?, autopilot_threshold=?,
                   updated_at=?
               WHERE id=?""",
            (budget.daily_limit, budget.total_limit, budget.category,
             int(budget.require_approval), budget.spent_today, budget.spent_total,
             int(budget.autopilot_enabled), budget.autopilot_threshold,
             now, budget.id),
        )
    budget.updated_at = datetime.fromisoformat(now)
    return budget


def reset_daily_spending(goal_id: int) -> None:
    """Reset spent_today to 0 for all budgets of a goal (call daily)."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE spending_budgets SET spent_today = 0, updated_at = ? WHERE goal_id = ?",
            (now, goal_id),
        )


def maybe_reset_daily_spending(budget: SpendingBudget) -> SpendingBudget:
    """Check-on-request: reset spent_today if the last update was on a previous day."""
    if budget.updated_at:
        last_date = budget.updated_at.date() if isinstance(budget.updated_at, datetime) else None
        today = datetime.now(timezone.utc).date()
        if last_date and last_date < today and budget.spent_today > 0:
            budget.spent_today = 0.0
            update_spending_budget(budget)
    return budget


# ─── Spending Requests ───────────────────────────────────────────────────────

def _row_to_spending_request(row: sqlite3.Row) -> SpendingRequest:
    return SpendingRequest(
        id=row["id"],
        task_id=row["task_id"],
        budget_id=row["budget_id"],
        amount=row["amount"],
        currency=row["currency"],
        description=row["description"],
        service=row["service"],
        status=row["status"],
        denial_reason=row["denial_reason"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_spending_request(req: SpendingRequest) -> SpendingRequest:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO spending_requests
               (task_id, budget_id, amount, currency, description, service, status, denial_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (req.task_id, req.budget_id, req.amount, req.currency,
             req.description, req.service, req.status, req.denial_reason, now),
        )
        req.id = cur.lastrowid
        req.created_at = datetime.fromisoformat(now)
    return req


def get_spending_request(request_id: int) -> Optional[SpendingRequest]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM spending_requests WHERE id = ?", (request_id,),
        ).fetchone()
    return _row_to_spending_request(row) if row else None


def list_spending_requests(
    task_id: Optional[int] = None,
    budget_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[SpendingRequest]:
    query = "SELECT * FROM spending_requests WHERE 1=1"
    params: list = []
    if task_id is not None:
        query += " AND task_id = ?"
        params.append(task_id)
    if budget_id is not None:
        query += " AND budget_id = ?"
        params.append(budget_id)
    if status is not None:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_spending_request(r) for r in rows]


def update_spending_request(req: SpendingRequest) -> SpendingRequest:
    with _conn() as con:
        con.execute(
            """UPDATE spending_requests
               SET status = ?, denial_reason = ?
               WHERE id = ?""",
            (req.status, req.denial_reason, req.id),
        )
    return req


# ─── Messaging Configs ───────────────────────────────────────────────────────

def _row_to_messaging_config(row: sqlite3.Row) -> MessagingConfig:
    return MessagingConfig(
        id=row["id"],
        channel=row["channel"],
        config_json=row["config_json"],
        enabled=bool(row["enabled"]),
        notify_nudges=bool(row["notify_nudges"]),
        notify_tasks=bool(row["notify_tasks"]),
        notify_spending=bool(row["notify_spending"]),
        notify_checkins=bool(row["notify_checkins"]),
        user_id=row["user_id"] if "user_id" in row.keys() else None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_messaging_config(cfg: MessagingConfig) -> MessagingConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO messaging_configs
               (channel, config_json, enabled, notify_nudges, notify_tasks,
                notify_spending, notify_checkins, user_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cfg.channel, cfg.config_json, int(cfg.enabled),
             int(cfg.notify_nudges), int(cfg.notify_tasks),
             int(cfg.notify_spending), int(cfg.notify_checkins),
             cfg.user_id, now, now),
        )
        cfg.id = cur.lastrowid
        cfg.created_at = datetime.fromisoformat(now)
        cfg.updated_at = datetime.fromisoformat(now)
    return cfg


def get_messaging_config(config_id: int) -> Optional[MessagingConfig]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM messaging_configs WHERE id = ?", (config_id,),
        ).fetchone()
    return _row_to_messaging_config(row) if row else None


def list_messaging_configs(enabled_only: bool = False, user_id: Optional[int] = None) -> List[MessagingConfig]:
    query = "SELECT * FROM messaging_configs"
    conditions: List[str] = []
    params: list = []
    if enabled_only:
        conditions.append("enabled = 1")
    if user_id is not None:
        conditions.append("user_id = ?")
        params.append(user_id)
    if conditions:
        query += " WHERE " + " AND ".join(conditions)
    query += " ORDER BY created_at ASC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_messaging_config(r) for r in rows]


def update_messaging_config(cfg: MessagingConfig) -> MessagingConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE messaging_configs
               SET channel=?, config_json=?, enabled=?, notify_nudges=?,
                   notify_tasks=?, notify_spending=?, notify_checkins=?, updated_at=?
               WHERE id=?""",
            (cfg.channel, cfg.config_json, int(cfg.enabled),
             int(cfg.notify_nudges), int(cfg.notify_tasks),
             int(cfg.notify_spending), int(cfg.notify_checkins), now, cfg.id),
        )
    cfg.updated_at = datetime.fromisoformat(now)
    return cfg


def delete_messaging_config(config_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM messaging_configs WHERE id = ?", (config_id,))


# ─── Telegram Sessions ───────────────────────────────────────────────────────

def get_telegram_session(chat_id: str) -> Optional[dict]:
    """Get the session state for a Telegram chat."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM telegram_sessions WHERE chat_id = ?", (chat_id,)
        ).fetchone()
    if row:
        return {
            "chat_id": row["chat_id"],
            "goal_id": row["goal_id"],
            "state": row["state"],
            "pending_question_key": row["pending_question_key"],
        }
    return None


def upsert_telegram_session(
    chat_id: str,
    goal_id: Optional[int],
    state: str,
    pending_question_key: Optional[str] = None,
) -> None:
    """Create or update the session for a Telegram chat."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """INSERT INTO telegram_sessions (chat_id, goal_id, state, pending_question_key, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(chat_id) DO UPDATE SET
                   goal_id=excluded.goal_id,
                   state=excluded.state,
                   pending_question_key=excluded.pending_question_key,
                   updated_at=excluded.updated_at""",
            (chat_id, goal_id, state, pending_question_key, now),
        )


def delete_telegram_session(chat_id: str) -> None:
    """Remove the session for a Telegram chat."""
    with _conn() as con:
        con.execute("DELETE FROM telegram_sessions WHERE chat_id = ?", (chat_id,))


def reset_all_daily_spending() -> None:
    """Reset spent_today for all budgets where the last update was on a previous day."""
    today = datetime.now(timezone.utc).date().isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE spending_budgets
               SET spent_today = 0, updated_at = ?
               WHERE spent_today > 0 AND date(updated_at) < date(?)""",
            (datetime.now(timezone.utc).isoformat(), today),
        )


# ─── Agent Memory ────────────────────────────────────────────────────────────

def create_agent_memory(agent_type: str, goal_type: str, memory_key: str, memory_value: str, confidence: float = 1.0) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO agent_memory (agent_type, goal_type, memory_key, memory_value, confidence, times_used, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 0, ?, ?)""",
            (agent_type, goal_type, memory_key, memory_value, confidence, now, now),
        )
        return {"id": cur.lastrowid, "agent_type": agent_type, "goal_type": goal_type,
                "memory_key": memory_key, "memory_value": memory_value, "confidence": confidence}


def list_agent_memories(agent_type: str, goal_type: str = "") -> list[dict]:
    with _conn() as con:
        if goal_type:
            rows = con.execute(
                "SELECT * FROM agent_memory WHERE agent_type = ? AND goal_type = ? ORDER BY confidence DESC, times_used DESC",
                (agent_type, goal_type),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM agent_memory WHERE agent_type = ? ORDER BY confidence DESC, times_used DESC",
                (agent_type,),
            ).fetchall()
    return [
        {"id": r["id"], "agent_type": r["agent_type"], "goal_type": r["goal_type"],
         "memory_key": r["memory_key"], "memory_value": r["memory_value"],
         "confidence": r["confidence"], "times_used": r["times_used"]}
        for r in rows
    ]


def increment_agent_memory_usage(memory_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE agent_memory SET times_used = times_used + 1, updated_at = ? WHERE id = ?",
            (now, memory_id),
        )


# ─── User Behavior ───────────────────────────────────────────────────────────

def record_user_behavior(user_id: int, behavior_type: str, pattern_key: str, pattern_value: str = "") -> dict:
    """Record a user behavior pattern (e.g., 'avoids': 'cli_tasks')."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        # Check for existing pattern
        existing = con.execute(
            "SELECT * FROM user_behavior WHERE user_id = ? AND behavior_type = ? AND pattern_key = ?",
            (user_id, behavior_type, pattern_key),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE user_behavior SET occurrences = occurrences + 1, pattern_value = ?, updated_at = ? WHERE id = ?",
                (pattern_value or existing["pattern_value"], now, existing["id"]),
            )
            return {"id": existing["id"], "behavior_type": behavior_type, "pattern_key": pattern_key,
                    "occurrences": existing["occurrences"] + 1}
        else:
            cur = con.execute(
                """INSERT INTO user_behavior (user_id, behavior_type, pattern_key, pattern_value, occurrences, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 1, ?, ?)""",
                (user_id, behavior_type, pattern_key, pattern_value, now, now),
            )
            return {"id": cur.lastrowid, "behavior_type": behavior_type, "pattern_key": pattern_key, "occurrences": 1}


def list_user_behaviors(user_id: int, behavior_type: Optional[str] = None) -> list[dict]:
    with _conn() as con:
        if behavior_type:
            rows = con.execute(
                "SELECT * FROM user_behavior WHERE user_id = ? AND behavior_type = ? ORDER BY occurrences DESC",
                (user_id, behavior_type),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM user_behavior WHERE user_id = ? ORDER BY occurrences DESC",
                (user_id,),
            ).fetchall()
    return [
        {"id": r["id"], "user_id": r["user_id"], "behavior_type": r["behavior_type"],
         "pattern_key": r["pattern_key"], "pattern_value": r["pattern_value"],
         "occurrences": r["occurrences"]}
        for r in rows
    ]


# ─── Payment Accounts ────────────────────────────────────────────────────────

def create_payment_account(user_id: int, provider: str, account_id: str, config_json: str = "{}") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO payment_accounts (user_id, provider, account_id, config_json, enabled, created_at, updated_at)
               VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (user_id, provider, account_id, config_json, now, now),
        )
        return {"id": cur.lastrowid, "user_id": user_id, "provider": provider,
                "account_id": account_id, "enabled": True}


def list_payment_accounts(user_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM payment_accounts WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [
        {"id": r["id"], "user_id": r["user_id"], "provider": r["provider"],
         "account_id": r["account_id"], "config_json": r["config_json"],
         "enabled": bool(r["enabled"]), "created_at": r["created_at"]}
        for r in rows
    ]


def get_payment_account(account_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM payment_accounts WHERE id = ?", (account_id,),
        ).fetchone()
    if row:
        return {"id": row["id"], "user_id": row["user_id"], "provider": row["provider"],
                "account_id": row["account_id"], "config_json": row["config_json"],
                "enabled": bool(row["enabled"])}
    return None


def create_payment_transaction(account_id: int, spending_request_id: Optional[int],
                                amount: float, currency: str, description: str,
                                provider_tx_id: str = "", status: str = "pending") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO payment_transactions
               (account_id, spending_request_id, provider_tx_id, amount, currency,
                status, description, provider_response, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, '{}', ?, ?)""",
            (account_id, spending_request_id, provider_tx_id, amount, currency,
             status, description, now, now),
        )
        return {"id": cur.lastrowid, "account_id": account_id, "amount": amount,
                "currency": currency, "status": status, "description": description}


def update_payment_transaction(tx_id: int, status: str, provider_tx_id: str = "",
                                provider_response: str = "{}") -> Optional[dict]:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE payment_transactions
               SET status = ?, provider_tx_id = ?, provider_response = ?, updated_at = ?
               WHERE id = ?""",
            (status, provider_tx_id, provider_response, now, tx_id),
        )
        row = con.execute("SELECT * FROM payment_transactions WHERE id = ?", (tx_id,)).fetchone()
    if row:
        return {"id": row["id"], "status": row["status"], "provider_tx_id": row["provider_tx_id"],
                "amount": row["amount"], "currency": row["currency"]}
    return None


def list_payment_transactions(account_id: int, limit: int = 50) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM payment_transactions WHERE account_id = ? ORDER BY created_at DESC LIMIT ?",
            (account_id, limit),
        ).fetchall()
    return [
        {"id": r["id"], "account_id": r["account_id"], "provider_tx_id": r["provider_tx_id"],
         "amount": r["amount"], "currency": r["currency"], "status": r["status"],
         "description": r["description"], "created_at": r["created_at"]}
        for r in rows
    ]


def reconcile_transaction_by_provider_id(provider_tx_id: str, status: str,
                                          provider_response: str = "{}") -> Optional[dict]:
    """Find a transaction by its provider_tx_id and update its status.

    Used by webhook reconciliation to sync provider-side status changes.
    """
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT id FROM payment_transactions WHERE provider_tx_id = ?",
            (provider_tx_id,),
        ).fetchone()
        if not row:
            return None
        tx_id = row["id"]
        con.execute(
            """UPDATE payment_transactions
               SET status = ?, provider_response = ?, updated_at = ?
               WHERE id = ?""",
            (status, provider_response, now, tx_id),
        )
        updated = con.execute(
            "SELECT * FROM payment_transactions WHERE id = ?", (tx_id,),
        ).fetchone()
    if updated:
        return {"id": updated["id"], "status": updated["status"],
                "provider_tx_id": updated["provider_tx_id"],
                "amount": updated["amount"], "currency": updated["currency"]}
    return None


def list_failed_transactions(max_retries: int = 3) -> list[dict]:
    """List failed transactions eligible for recovery (retry_count < max_retries)."""
    with _conn() as con:
        rows = con.execute(
            """SELECT * FROM payment_transactions
               WHERE status = 'failed' AND retry_count < ?
               ORDER BY created_at ASC LIMIT 50""",
            (max_retries,),
        ).fetchall()
    return [
        {"id": r["id"], "account_id": r["account_id"], "provider_tx_id": r["provider_tx_id"],
         "amount": r["amount"], "currency": r["currency"], "status": r["status"],
         "retry_count": r["retry_count"], "description": r["description"],
         "created_at": r["created_at"]}
        for r in rows
    ]


def increment_transaction_retry(tx_id: int) -> None:
    """Increment the retry_count for a failed transaction."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            """UPDATE payment_transactions
               SET retry_count = retry_count + 1, updated_at = ?
               WHERE id = ?""",
            (now, tx_id),
        )


# ─── Discovered Services ─────────────────────────────────────────────────────

def create_discovered_service(service_name: str, category: str, description: str,
                               url: str, capabilities: str = "[]",
                               discovered_by: str = "system", relevance_score: float = 0) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        # Upsert
        existing = con.execute(
            "SELECT id FROM discovered_services WHERE service_name = ?", (service_name,)
        ).fetchone()
        if existing:
            con.execute(
                """UPDATE discovered_services
                   SET category = ?, description = ?, url = ?, capabilities = ?,
                       relevance_score = ?, updated_at = ?
                   WHERE id = ?""",
                (category, description, url, capabilities, relevance_score, now, existing["id"]),
            )
            return {"id": existing["id"], "service_name": service_name, "updated": True}
        cur = con.execute(
            """INSERT INTO discovered_services
               (service_name, category, description, url, capabilities,
                discovered_by, relevance_score, times_recommended, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
            (service_name, category, description, url, capabilities,
             discovered_by, relevance_score, now, now),
        )
        return {"id": cur.lastrowid, "service_name": service_name, "updated": False}


def list_discovered_services(category: Optional[str] = None, limit: int = 50) -> list[dict]:
    with _conn() as con:
        if category:
            rows = con.execute(
                "SELECT * FROM discovered_services WHERE category = ? ORDER BY relevance_score DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM discovered_services ORDER BY relevance_score DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [
        {"id": r["id"], "service_name": r["service_name"], "category": r["category"],
         "description": r["description"], "url": r["url"],
         "capabilities": json.loads(r["capabilities"]) if r["capabilities"] else [],
         "relevance_score": r["relevance_score"], "times_recommended": r["times_recommended"]}
        for r in rows
    ]


def increment_service_recommendation(service_id: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE discovered_services SET times_recommended = times_recommended + 1, updated_at = ? WHERE id = ?",
            (now, service_id),
        )


# ─── Auto-execute Tasks (autonomous loop) ────────────────────────────────────

def list_auto_execute_tasks() -> List[Task]:
    """Return tasks whose goal has auto_execute=True and that are in 'todo' status.

    Orders by order_index so the execution loop picks up tasks in the intended
    sequence. Only returns one task per goal (the next pending task).
    """
    with _conn() as con:
        rows = con.execute(
            """SELECT t.* FROM tasks t
               JOIN goals g ON t.goal_id = g.id
               WHERE g.auto_execute = 1
                 AND g.status IN ('decomposed', 'in_progress')
                 AND t.status = 'todo'
               ORDER BY t.goal_id, t.order_index ASC, t.id ASC""",
        ).fetchall()
    # Only return the first pending task per goal
    seen_goals: Set[int] = set()
    result: List[Task] = []
    for row in rows:
        gid = row["goal_id"]
        if gid not in seen_goals:
            seen_goals.add(gid)
            result.append(_row_to_task(row))
    return result


# ─── Deployments ─────────────────────────────────────────────────────────────

def create_deployment(task_id: int, goal_id: int, service: str,
                      project_name: str = "", repository_url: str = "",
                      deploy_url: str = "", provider_data: str = "{}") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO deployments
               (task_id, goal_id, service, project_name, repository_url,
                deploy_url, status, provider_data, health_status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, 'pending', ?, 'unknown', ?, ?)""",
            (task_id, goal_id, service, project_name, repository_url,
             deploy_url, provider_data, now, now),
        )
        return {
            "id": cur.lastrowid, "task_id": task_id, "goal_id": goal_id,
            "service": service, "project_name": project_name,
            "repository_url": repository_url, "deploy_url": deploy_url,
            "status": "pending", "health_status": "unknown",
        }


def update_deployment(deploy_id: int, status: Optional[str] = None,
                      deploy_url: Optional[str] = None,
                      health_status: Optional[str] = None,
                      provider_data: Optional[str] = None) -> Optional[dict]:
    now = datetime.now(timezone.utc).isoformat()
    _ALLOWED_COLUMNS = {"status", "deploy_url", "health_status", "last_health_check",
                        "provider_data", "updated_at"}
    updates: list[str] = ["updated_at = ?"]
    params: list = [now]
    if status is not None:
        updates.append("status = ?")
        params.append(status)
    if deploy_url is not None:
        updates.append("deploy_url = ?")
        params.append(deploy_url)
    if health_status is not None:
        updates.append("health_status = ?")
        params.append(health_status)
        updates.append("last_health_check = ?")
        params.append(now)
    if provider_data is not None:
        updates.append("provider_data = ?")
        params.append(provider_data)
    # Validate all column names are in the allowed set
    for clause in updates:
        col_name = clause.split(" = ")[0].strip()
        if col_name not in _ALLOWED_COLUMNS:
            raise ValueError(f"Invalid column: {col_name}")
    params.append(deploy_id)
    set_clause = ", ".join(updates)
    with _conn() as con:
        con.execute(
            f"UPDATE deployments SET {set_clause} WHERE id = ?", params,  # noqa: S608
        )
        row = con.execute("SELECT * FROM deployments WHERE id = ?", (deploy_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "task_id": row["task_id"], "goal_id": row["goal_id"],
        "service": row["service"], "project_name": row["project_name"],
        "repository_url": row["repository_url"], "deploy_url": row["deploy_url"],
        "status": row["status"], "health_status": row["health_status"],
        "last_health_check": row["last_health_check"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def get_deployment(deploy_id: int) -> Optional[dict]:
    with _conn() as con:
        row = con.execute("SELECT * FROM deployments WHERE id = ?", (deploy_id,)).fetchone()
    if not row:
        return None
    return {
        "id": row["id"], "task_id": row["task_id"], "goal_id": row["goal_id"],
        "service": row["service"], "project_name": row["project_name"],
        "repository_url": row["repository_url"], "deploy_url": row["deploy_url"],
        "status": row["status"], "health_status": row["health_status"],
        "last_health_check": row["last_health_check"],
        "provider_data": row["provider_data"],
        "created_at": row["created_at"], "updated_at": row["updated_at"],
    }


def list_deployments(goal_id: Optional[int] = None) -> list[dict]:
    with _conn() as con:
        if goal_id is not None:
            rows = con.execute(
                "SELECT * FROM deployments WHERE goal_id = ? ORDER BY created_at DESC",
                (goal_id,),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM deployments ORDER BY created_at DESC"
            ).fetchall()
    return [
        {
            "id": r["id"], "task_id": r["task_id"], "goal_id": r["goal_id"],
            "service": r["service"], "project_name": r["project_name"],
            "repository_url": r["repository_url"], "deploy_url": r["deploy_url"],
            "status": r["status"], "health_status": r["health_status"],
            "last_health_check": r["last_health_check"],
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }
        for r in rows
    ]


# ─── Provisioning Logs ──────────────────────────────────────────────────────

def create_provisioning_log(task_id: int, service_name: str,
                            action: str = "signup", status: str = "pending",
                            result_data: str = "{}", error: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO provisioning_logs
               (task_id, service_name, action, status, result_data, error, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (task_id, service_name, action, status, result_data, error, now),
        )
        return {
            "id": cur.lastrowid, "task_id": task_id,
            "service_name": service_name, "action": action,
            "status": status, "created_at": now,
        }


def list_provisioning_logs(task_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM provisioning_logs WHERE task_id = ? ORDER BY created_at DESC",
            (task_id,),
        ).fetchall()
    return [
        {
            "id": r["id"], "task_id": r["task_id"],
            "service_name": r["service_name"], "action": r["action"],
            "status": r["status"], "result_data": r["result_data"],
            "error": r["error"], "created_at": r["created_at"],
        }
        for r in rows
    ]


# ─── ROI Dashboard ──────────────────────────────────────────────────────────

def get_goal_roi(goal_id: int) -> dict:
    """Compute ROI for a goal: money spent (spending_requests) vs money earned (outcome_metrics with unit='$').

    Returns a dict with total_spent, total_earned, roi_percent, and breakdowns.
    """
    with _conn() as con:
        # Money spent: sum of approved/executed spending requests for this goal's tasks
        spent_rows = con.execute(
            """SELECT sr.service, sr.status, sr.amount, sr.currency, sr.created_at
               FROM spending_requests sr
               JOIN tasks t ON sr.task_id = t.id
               WHERE t.goal_id = ? AND sr.status IN ('approved', 'executed')
               ORDER BY sr.created_at ASC""",
            (goal_id,),
        ).fetchall()

        total_spent = sum(r["amount"] for r in spent_rows)

        # Breakdown by category (service)
        spending_by_category: dict = {}
        for r in spent_rows:
            cat = r["service"] or "general"
            spending_by_category[cat] = spending_by_category.get(cat, 0.0) + r["amount"]

        # Spending over time (daily)
        spending_timeline: dict = {}
        for r in spent_rows:
            day = r["created_at"][:10] if r["created_at"] else "unknown"
            spending_timeline[day] = spending_timeline.get(day, 0.0) + r["amount"]

        # Money earned: outcome_metrics with monetary unit
        om_rows = con.execute(
            "SELECT * FROM outcome_metrics WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()

        total_earned = 0.0
        earnings_breakdown: list = []
        for r in om_rows:
            unit_lower = (r["unit"] or "").lower().strip()
            if unit_lower in _REVENUE_UNITS or '$' in (r["unit"] or ""):
                total_earned += r["current_value"]
                earnings_breakdown.append({
                    "label": r["label"],
                    "current_value": r["current_value"],
                    "target_value": r["target_value"],
                    "unit": r["unit"],
                })

        # ROI calculation
        if total_spent > 0:
            roi_percent = round(((total_earned - total_spent) / total_spent) * 100, 1)
        else:
            # No spending: ROI is N/A; use None for JSON safety (inf is not JSON-serializable)
            roi_percent = None if total_earned > 0 else 0.0

        # Budget utilization
        budgets = con.execute(
            "SELECT * FROM spending_budgets WHERE goal_id = ?",
            (goal_id,),
        ).fetchall()
        budget_summary = []
        for b in budgets:
            budget_summary.append({
                "category": b["category"],
                "daily_limit": b["daily_limit"],
                "total_limit": b["total_limit"],
                "spent_today": b["spent_today"],
                "spent_total": b["spent_total"],
                "utilization_pct": round((b["spent_total"] / b["total_limit"]) * 100, 1) if b["total_limit"] > 0 else 0,
            })

        # Pending requests
        pending_count = con.execute(
            """SELECT COUNT(*) FROM spending_requests sr
               JOIN tasks t ON sr.task_id = t.id
               WHERE t.goal_id = ? AND sr.status = 'pending'""",
            (goal_id,),
        ).fetchone()[0]

        # Failed transactions
        failed_count = con.execute(
            """SELECT COUNT(*) FROM spending_requests sr
               JOIN tasks t ON sr.task_id = t.id
               WHERE t.goal_id = ? AND sr.status = 'failed'""",
            (goal_id,),
        ).fetchone()[0]

    return {
        "goal_id": goal_id,
        "total_spent": round(total_spent, 2),
        "total_earned": round(total_earned, 2),
        "net_profit": round(total_earned - total_spent, 2),
        "roi_percent": roi_percent,
        "spending_by_category": spending_by_category,
        "spending_timeline": [
            {"date": d, "amount": round(a, 2)}
            for d, a in sorted(spending_timeline.items())
        ],
        "earnings_breakdown": earnings_breakdown,
        "budget_summary": budget_summary,
        "pending_requests": pending_count,
        "failed_transactions": failed_count,
    }


def get_user_roi_summary(user_id: int) -> dict:
    """Aggregate ROI across all goals for a user."""
    with _conn() as con:
        goal_rows = con.execute(
            "SELECT id, title, status FROM goals WHERE user_id = ?",
            (user_id,),
        ).fetchall()

    total_spent = 0.0
    total_earned = 0.0
    goal_summaries = []

    for g in goal_rows:
        roi = get_goal_roi(g["id"])
        total_spent += roi["total_spent"]
        total_earned += roi["total_earned"]
        goal_summaries.append({
            "goal_id": g["id"],
            "title": g["title"],
            "status": g["status"],
            "spent": roi["total_spent"],
            "earned": roi["total_earned"],
            "roi_percent": roi["roi_percent"],
        })

    if total_spent > 0:
        overall_roi = round(((total_earned - total_spent) / total_spent) * 100, 1)
    else:
        overall_roi = 0.0 if total_earned == 0 else None

    return {
        "total_spent": round(total_spent, 2),
        "total_earned": round(total_earned, 2),
        "net_profit": round(total_earned - total_spent, 2),
        "overall_roi_percent": overall_roi,
        "goals": goal_summaries,
    }


# ─── Platform-wide Aggregate Learning ───────────────────────────────────────

def get_platform_patterns() -> dict:
    """Aggregate anonymized patterns across ALL users for platform-wide learning.

    Returns:
    - Most successful goal types (highest completion rate)
    - Common failure patterns (high skip/stall rates)
    - Average time-to-complete by template type
    - Most effective task orderings from success paths
    - Service usage frequency
    """
    with _conn() as con:
        # Goal completion rates by detected template type
        all_goals = con.execute(
            "SELECT title, description, status FROM goals"
        ).fetchall()

        template_stats: dict = {}
        for g in all_goals:
            # Simple template detection from title keywords
            ttype = _detect_goal_type(g["title"], g["description"])
            if ttype not in template_stats:
                template_stats[ttype] = {"total": 0, "done": 0, "in_progress": 0}
            template_stats[ttype]["total"] += 1
            if g["status"] == "done":
                template_stats[ttype]["done"] += 1
            elif g["status"] == "in_progress":
                template_stats[ttype]["in_progress"] += 1

        goal_type_insights = []
        for ttype, stats in template_stats.items():
            completion_rate = round((stats["done"] / stats["total"]) * 100, 1) if stats["total"] > 0 else 0
            goal_type_insights.append({
                "goal_type": ttype,
                "total_goals": stats["total"],
                "completed": stats["done"],
                "completion_rate": completion_rate,
            })
        goal_type_insights.sort(key=lambda x: x["completion_rate"], reverse=True)

        # Task skip patterns (anonymized)
        skip_rows = con.execute(
            """SELECT t.title, COUNT(*) as skip_count
               FROM tasks t WHERE t.status = 'skipped'
               GROUP BY LOWER(t.title) ORDER BY skip_count DESC LIMIT 20"""
        ).fetchall()
        commonly_skipped = [{"title": r["title"], "skip_count": r[1]} for r in skip_rows]

        # Average task completion time by status
        total_tasks = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        done_tasks = con.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
        skipped_tasks = con.execute("SELECT COUNT(*) FROM tasks WHERE status='skipped'").fetchone()[0]
        failed_tasks = con.execute("SELECT COUNT(*) FROM tasks WHERE status='failed'").fetchone()[0]

        # Most used services in spending
        service_rows = con.execute(
            """SELECT service, COUNT(*) as use_count, SUM(amount) as total_amount
               FROM spending_requests WHERE status IN ('approved', 'executed')
               GROUP BY LOWER(service) ORDER BY use_count DESC LIMIT 15"""
        ).fetchall()
        popular_services = [
            {"service": r["service"], "use_count": r[1], "total_spent": round(r[2] or 0, 2)}
            for r in service_rows
        ]

        # Success path insights
        sp_rows = con.execute(
            "SELECT goal_type, times_reused, outcome_summary FROM success_paths ORDER BY times_reused DESC LIMIT 10"
        ).fetchall()
        proven_paths = [
            {"goal_type": r["goal_type"], "times_reused": r["times_reused"],
             "outcome_summary": r["outcome_summary"]}
            for r in sp_rows
        ]

        # Aggregate behavior patterns (anonymized, just counts)
        behavior_rows = con.execute(
            """SELECT behavior_type, pattern_key, SUM(occurrences) as total_occ
               FROM user_behavior
               GROUP BY behavior_type, pattern_key
               ORDER BY total_occ DESC LIMIT 20"""
        ).fetchall()
        common_behaviors = [
            {"behavior_type": r["behavior_type"], "pattern": r["pattern_key"],
             "total_occurrences": r[2]}
            for r in behavior_rows
        ]

    return {
        "goal_type_insights": goal_type_insights,
        "commonly_skipped_tasks": commonly_skipped,
        "task_stats": {
            "total": total_tasks,
            "done": done_tasks,
            "skipped": skipped_tasks,
            "failed": failed_tasks,
            "completion_rate": round((done_tasks / total_tasks) * 100, 1) if total_tasks > 0 else 0,
        },
        "popular_services": popular_services,
        "proven_paths": proven_paths,
        "common_behaviors": common_behaviors,
    }


def _detect_goal_type(title: str, description: str) -> str:
    """Simple keyword-based goal type detection for aggregate stats."""
    combined = f" {title} {description} ".lower()
    # Check 'learn' before 'earn' since 'learn' contains 'earn'
    if any(w in combined for w in (" learn ", " study ", " course ", " skill ", " tutorial ")):
        return "learn_skill"
    if any(w in combined for w in ("money", " earn ", "income", "revenue", "freelanc", " sell ", "profit")):
        return "make_money_online"
    if any(w in combined for w in (" fit ", "exercise", " gym ", "workout", "health", "weight")):
        return "get_fit"
    if any(w in combined for w in ("build", " app ", "website", "project", "develop", " code ", "create")):
        return "build_project"
    if any(w in combined for w in ("write", "book", " blog ", "content", "article")):
        return "write_book"
    return "generic"


# ═══════════════════════════════════════════════════════════════════════════════
# Bridging Plan — New Storage Functions (Steps 1-8)
# ═══════════════════════════════════════════════════════════════════════════════


# ─── Step 2: Persistent Agent Goal Memory ────────────────────────────────────

def get_or_create_agent_goal_memory(agent_type: str, goal_id: int) -> AgentGoalMemory:
    """Get existing per-goal memory for an agent, or create a fresh one."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM agent_goal_memory WHERE agent_type = ? AND goal_id = ?",
            (agent_type, goal_id),
        ).fetchone()
        if row:
            return AgentGoalMemory(
                id=row["id"], agent_type=row["agent_type"], goal_id=row["goal_id"],
                context_json=row["context_json"], summary=row["summary"],
                invocation_count=row["invocation_count"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        cur = con.execute(
            """INSERT INTO agent_goal_memory (agent_type, goal_id, context_json, summary, invocation_count, created_at, updated_at)
               VALUES (?, ?, '{}', '', 0, ?, ?)""",
            (agent_type, goal_id, now, now),
        )
        return AgentGoalMemory(
            id=cur.lastrowid, agent_type=agent_type, goal_id=goal_id,
            context_json="{}", summary="", invocation_count=0,
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )


def update_agent_goal_memory(mem: AgentGoalMemory) -> AgentGoalMemory:
    """Update agent goal memory context and increment invocation count."""
    now = datetime.now(timezone.utc).isoformat()
    mem.updated_at = datetime.fromisoformat(now)
    with _conn() as con:
        con.execute(
            """UPDATE agent_goal_memory SET context_json = ?, summary = ?,
               invocation_count = invocation_count + 1, updated_at = ?
               WHERE id = ?""",
            (mem.context_json, mem.summary, now, mem.id),
        )
    mem.invocation_count += 1
    return mem


def list_agent_goal_memories(goal_id: int) -> list[AgentGoalMemory]:
    """List all agent memories for a given goal."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_goal_memory WHERE goal_id = ? ORDER BY updated_at DESC",
            (goal_id,),
        ).fetchall()
    return [
        AgentGoalMemory(
            id=r["id"], agent_type=r["agent_type"], goal_id=r["goal_id"],
            context_json=r["context_json"], summary=r["summary"],
            invocation_count=r["invocation_count"],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )
        for r in rows
    ]


def prune_agent_goal_memory(goal_id: int, max_context_length: int = 8000) -> None:
    """Prune overly long context_json for a goal's agent memories.

    Args:
        goal_id: The goal whose agent memories to prune.
        max_context_length: Maximum allowed length for context_json in characters (default: 8000).

    Attempts to parse JSON and keep only the last entries if it's a dict or
    list. Falls back to truncation and wraps in valid JSON if parsing fails.
    """
    with _conn() as con:
        rows = con.execute(
            "SELECT id, agent_type, context_json FROM agent_goal_memory WHERE goal_id = ? AND LENGTH(context_json) > ?",
            (goal_id, max_context_length),
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for r in rows:
            raw = r["context_json"]
            try:
                data = json.loads(raw)
                if isinstance(data, dict) and len(raw) > max_context_length:
                    # Keep the most recently added keys (last N items)
                    keys = list(data.keys())
                    while len(json.dumps(data)) > max_context_length and keys:
                        del data[keys.pop(0)]
                    truncated = json.dumps(data)
                elif isinstance(data, list) and len(raw) > max_context_length:
                    while len(json.dumps(data)) > max_context_length and data:
                        data.pop(0)
                    truncated = json.dumps(data)
                else:
                    truncated = json.dumps(data)[:max_context_length]
            except (json.JSONDecodeError, TypeError):
                # Not valid JSON — truncate and wrap safely
                import logging as _log
                _log.getLogger(__name__).warning(
                    "Pruning corrupt context_json for goal_id=%s agent_type=%s",
                    goal_id, r["agent_type"],
                )
                truncated = json.dumps({"_pruned": raw[-max_context_length:]})
            con.execute(
                "UPDATE agent_goal_memory SET context_json = ?, updated_at = ? WHERE id = ?",
                (truncated, now, r["id"]),
            )


# ─── Step 3: Goal Hierarchy — Milestones ─────────────────────────────────────

def create_milestone(ms: Milestone) -> Milestone:
    now = datetime.now(timezone.utc).isoformat()
    ms.created_at = datetime.fromisoformat(now)
    ms.updated_at = datetime.fromisoformat(now)
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO milestones (goal_id, title, target_metric, target_value, current_value, deadline, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (ms.goal_id, ms.title, ms.target_metric, ms.target_value,
             ms.current_value, ms.deadline, ms.status, now, now),
        )
        ms.id = cur.lastrowid
    return ms


def get_milestone(milestone_id: int) -> Optional[Milestone]:
    with _conn() as con:
        row = con.execute("SELECT * FROM milestones WHERE id = ?", (milestone_id,)).fetchone()
    if not row:
        return None
    return Milestone(
        id=row["id"], goal_id=row["goal_id"], title=row["title"],
        target_metric=row["target_metric"], target_value=row["target_value"],
        current_value=row["current_value"], deadline=row["deadline"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def list_milestones(goal_id: int) -> list[Milestone]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM milestones WHERE goal_id = ? ORDER BY created_at ASC",
            (goal_id,),
        ).fetchall()
    return [
        Milestone(
            id=r["id"], goal_id=r["goal_id"], title=r["title"],
            target_metric=r["target_metric"], target_value=r["target_value"],
            current_value=r["current_value"], deadline=r["deadline"],
            status=r["status"],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )
        for r in rows
    ]


def update_milestone(ms: Milestone) -> Milestone:
    now = datetime.now(timezone.utc).isoformat()
    ms.updated_at = datetime.fromisoformat(now)
    with _conn() as con:
        con.execute(
            """UPDATE milestones SET title = ?, target_metric = ?, target_value = ?,
               current_value = ?, deadline = ?, status = ?, updated_at = ? WHERE id = ?""",
            (ms.title, ms.target_metric, ms.target_value, ms.current_value,
             ms.deadline, ms.status, now, ms.id),
        )
    return ms


def list_sub_goals(parent_goal_id: int) -> list[Goal]:
    """List sub-goals of a parent goal."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM goals WHERE parent_goal_id = ? ORDER BY created_at ASC",
            (parent_goal_id,),
        ).fetchall()
    return [_row_to_goal(r) for r in rows]


# ─── Step 6: Structured Audit Trail ──────────────────────────────────────────

def create_audit_event(event: AuditEvent) -> AuditEvent:
    """Create an immutable audit event. Append-only — no updates or deletes."""
    now = datetime.now(timezone.utc).isoformat()
    event.created_at = datetime.fromisoformat(now)
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO audit_events (goal_id, event_type, actor_type, actor_id, context_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (event.goal_id, event.event_type, event.actor_type,
             event.actor_id, event.context_json, now),
        )
        event.id = cur.lastrowid
    return event


def list_audit_events(goal_id: Optional[int] = None, event_type: Optional[str] = None,
                      limit: int = 100) -> list[AuditEvent]:
    """List audit events filtered by goal and/or event type."""
    clauses: list[str] = []
    params: list = []
    if goal_id is not None:
        clauses.append("goal_id = ?")
        params.append(goal_id)
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM audit_events {where} ORDER BY created_at ASC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [
        AuditEvent(
            id=r["id"], goal_id=r["goal_id"], event_type=r["event_type"],
            actor_type=r["actor_type"], actor_id=r["actor_id"],
            context_json=r["context_json"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


# ─── Step 5: Goal Template Marketplace ───────────────────────────────────────

def create_goal_template(tpl: GoalTemplate) -> GoalTemplate:
    now = datetime.now(timezone.utc).isoformat()
    tpl.created_at = datetime.fromisoformat(now)
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO goal_templates
               (title, description, goal_type, category, skill_level,
                tasks_json, milestones_json, services_json, outcome_type,
                estimated_days, rating_sum, rating_count, times_used,
                source_goal_id, author_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (tpl.title, tpl.description, tpl.goal_type, tpl.category, tpl.skill_level,
             tpl.tasks_json, tpl.milestones_json, tpl.services_json, tpl.outcome_type,
             tpl.estimated_days, tpl.rating_sum, tpl.rating_count, tpl.times_used,
             tpl.source_goal_id, tpl.author_id, now),
        )
        tpl.id = cur.lastrowid
    return tpl


def get_goal_template(template_id: int) -> Optional[GoalTemplate]:
    with _conn() as con:
        row = con.execute("SELECT * FROM goal_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        return None
    return _row_to_goal_template(row)


def list_goal_templates(goal_type: Optional[str] = None, category: Optional[str] = None,
                        skill_level: Optional[str] = None, limit: int = 50) -> list[GoalTemplate]:
    clauses: list[str] = []
    params: list = []
    if goal_type:
        clauses.append("goal_type = ?")
        params.append(goal_type)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if skill_level and skill_level != "any":
        clauses.append("(skill_level = ? OR skill_level = 'any')")
        params.append(skill_level)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM goal_templates {where} ORDER BY times_used DESC, rating_sum DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [_row_to_goal_template(r) for r in rows]


def rate_goal_template(template_id: int, rating: float) -> Optional[GoalTemplate]:
    """Add a rating (1-5) to a template."""
    rating = max(1.0, min(5.0, rating))
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE goal_templates SET rating_sum = rating_sum + ?, rating_count = rating_count + 1 WHERE id = ?",
            (rating, template_id),
        )
        row = con.execute("SELECT * FROM goal_templates WHERE id = ?", (template_id,)).fetchone()
    if not row:
        return None
    return _row_to_goal_template(row)


def increment_template_usage(template_id: int) -> None:
    with _conn() as con:
        con.execute("UPDATE goal_templates SET times_used = times_used + 1 WHERE id = ?", (template_id,))


def _row_to_goal_template(row) -> GoalTemplate:
    return GoalTemplate(
        id=row["id"], title=row["title"], description=row["description"],
        goal_type=row["goal_type"], category=row["category"],
        skill_level=row["skill_level"], tasks_json=row["tasks_json"],
        milestones_json=row["milestones_json"], services_json=row["services_json"],
        outcome_type=row["outcome_type"], estimated_days=row["estimated_days"],
        rating_sum=row["rating_sum"], rating_count=row["rating_count"],
        times_used=row["times_used"], source_goal_id=row["source_goal_id"],
        author_id=row["author_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Step 8: Execution Sandbox / Context ─────────────────────────────────────

def get_or_create_execution_context(goal_id: int) -> ExecutionContext:
    """Get or create an isolated execution context for a goal."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM execution_contexts WHERE goal_id = ?", (goal_id,),
        ).fetchone()
        if row:
            return ExecutionContext(
                id=row["id"], goal_id=row["goal_id"],
                browser_profile_dir=row["browser_profile_dir"],
                temp_dir=row["temp_dir"],
                credential_scope=row["credential_scope"],
                status=row["status"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
        # Create a new context with goal-specific directories
        import tempfile, os
        base_dir = os.path.join(tempfile.gettempdir(), "teb_sandbox")
        os.makedirs(base_dir, exist_ok=True)
        # Sanitize goal_id to prevent path injection (must be a positive integer)
        safe_id = abs(int(goal_id))
        browser_dir = os.path.join(base_dir, f"browser_{safe_id}")
        temp_dir = os.path.join(base_dir, f"temp_{safe_id}")
        # Verify paths stay within the sandbox base directory
        if not os.path.realpath(browser_dir).startswith(os.path.realpath(base_dir)):
            raise ValueError("Invalid sandbox path")
        if not os.path.realpath(temp_dir).startswith(os.path.realpath(base_dir)):
            raise ValueError("Invalid sandbox path")
        os.makedirs(browser_dir, exist_ok=True)
        os.makedirs(temp_dir, exist_ok=True)
        cur = con.execute(
            """INSERT INTO execution_contexts (goal_id, browser_profile_dir, temp_dir, credential_scope, status, created_at, updated_at)
               VALUES (?, ?, ?, '[]', 'active', ?, ?)""",
            (goal_id, browser_dir, temp_dir, now, now),
        )
        return ExecutionContext(
            id=cur.lastrowid, goal_id=goal_id,
            browser_profile_dir=browser_dir, temp_dir=temp_dir,
            credential_scope="[]", status="active",
            created_at=datetime.fromisoformat(now),
            updated_at=datetime.fromisoformat(now),
        )


def update_execution_context(ctx: ExecutionContext) -> ExecutionContext:
    now = datetime.now(timezone.utc).isoformat()
    ctx.updated_at = datetime.fromisoformat(now)
    with _conn() as con:
        con.execute(
            """UPDATE execution_contexts SET browser_profile_dir = ?, temp_dir = ?,
               credential_scope = ?, status = ?, updated_at = ? WHERE id = ?""",
            (ctx.browser_profile_dir, ctx.temp_dir,
             ctx.credential_scope, ctx.status, now, ctx.id),
        )
    return ctx


def cleanup_execution_context(goal_id: int) -> None:
    """Mark an execution context as cleaned up and remove temp files."""
    import shutil
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        row = con.execute("SELECT * FROM execution_contexts WHERE goal_id = ?", (goal_id,)).fetchone()
        if row:
            for dir_path in (row["browser_profile_dir"], row["temp_dir"]):
                if dir_path:
                    try:
                        shutil.rmtree(dir_path, ignore_errors=True)
                    except Exception:
                        pass
            con.execute(
                "UPDATE execution_contexts SET status = 'cleaned_up', updated_at = ? WHERE goal_id = ?",
                (now, goal_id),
            )


# ─── Step 1: Execution Plugin System ─────────────────────────────────────────

def create_plugin(plugin: PluginManifest) -> PluginManifest:
    now = datetime.now(timezone.utc).isoformat()
    plugin.created_at = datetime.fromisoformat(now)
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO plugins (name, version, description, task_types, required_credentials, module_path, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (plugin.name, plugin.version, plugin.description, plugin.task_types,
             plugin.required_credentials, plugin.module_path, 1 if plugin.enabled else 0, now),
        )
        plugin.id = cur.lastrowid
    return plugin


def get_plugin(name: str) -> Optional[PluginManifest]:
    with _conn() as con:
        row = con.execute("SELECT * FROM plugins WHERE name = ?", (name,)).fetchone()
    if not row:
        return None
    return _row_to_plugin(row)


def list_plugins(enabled_only: bool = False) -> list[PluginManifest]:
    with _conn() as con:
        if enabled_only:
            rows = con.execute("SELECT * FROM plugins WHERE enabled = 1 ORDER BY name").fetchall()
        else:
            rows = con.execute("SELECT * FROM plugins ORDER BY name").fetchall()
    return [_row_to_plugin(r) for r in rows]


def update_plugin(plugin: PluginManifest) -> PluginManifest:
    with _conn() as con:
        con.execute(
            """UPDATE plugins SET version = ?, description = ?, task_types = ?,
               required_credentials = ?, module_path = ?, enabled = ? WHERE id = ?""",
            (plugin.version, plugin.description, plugin.task_types,
             plugin.required_credentials, plugin.module_path,
             1 if plugin.enabled else 0, plugin.id),
        )
    return plugin


def delete_plugin(name: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM plugins WHERE name = ?", (name,))


def _row_to_plugin(row) -> PluginManifest:
    return PluginManifest(
        id=row["id"], name=row["name"], version=row["version"],
        description=row["description"], task_types=row["task_types"],
        required_credentials=row["required_credentials"],
        module_path=row["module_path"], enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Task Comments ───────────────────────────────────────────────────────────

def create_task_comment(comment: TaskComment) -> TaskComment:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO task_comments (task_id, content, author_type, author_id, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (comment.task_id, comment.content, comment.author_type, comment.author_id, now),
        )
        comment.id = cur.lastrowid
        comment.created_at = datetime.fromisoformat(now)
    return comment


def list_task_comments(task_id: int) -> List[TaskComment]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM task_comments WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    return [_row_to_task_comment(r) for r in rows]


def _row_to_task_comment(row: sqlite3.Row) -> TaskComment:
    return TaskComment(
        id=row["id"],
        task_id=row["task_id"],
        content=row["content"],
        author_type=row["author_type"],
        author_id=row["author_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def delete_task_comment(comment_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM task_comments WHERE id = ?", (comment_id,))


# ─── Task Artifacts ──────────────────────────────────────────────────────────

def create_task_artifact(artifact: TaskArtifact) -> TaskArtifact:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO task_artifacts (task_id, artifact_type, title, content_url, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (artifact.task_id, artifact.artifact_type, artifact.title,
             artifact.content_url, artifact.metadata_json, now),
        )
        artifact.id = cur.lastrowid
        artifact.created_at = datetime.fromisoformat(now)
    return artifact


def list_task_artifacts(task_id: int) -> List[TaskArtifact]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM task_artifacts WHERE task_id = ? ORDER BY created_at ASC",
            (task_id,),
        ).fetchall()
    return [_row_to_task_artifact(r) for r in rows]


def _row_to_task_artifact(row: sqlite3.Row) -> TaskArtifact:
    return TaskArtifact(
        id=row["id"],
        task_id=row["task_id"],
        artifact_type=row["artifact_type"],
        title=row["title"],
        content_url=row["content_url"],
        metadata_json=row["metadata_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def delete_task_artifact(artifact_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM task_artifacts WHERE id = ?", (artifact_id,))


# ─── Webhook Configs ─────────────────────────────────────────────────────────

def create_webhook_config(wh: WebhookConfig) -> WebhookConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO webhook_configs (user_id, url, events, secret, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (wh.user_id, wh.url, wh.events, wh.secret, int(wh.enabled), now, now),
        )
        wh.id = cur.lastrowid
        wh.created_at = datetime.fromisoformat(now)
        wh.updated_at = datetime.fromisoformat(now)
    return wh


def list_webhook_configs(user_id: int) -> List[WebhookConfig]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM webhook_configs WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_webhook_config(r) for r in rows]


def get_webhook_config(webhook_id: int) -> Optional[WebhookConfig]:
    with _conn() as con:
        row = con.execute("SELECT * FROM webhook_configs WHERE id = ?", (webhook_id,)).fetchone()
    return _row_to_webhook_config(row) if row else None


def update_webhook_config(wh: WebhookConfig) -> WebhookConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE webhook_configs SET url=?, events=?, secret=?, enabled=?, updated_at=? WHERE id=?",
            (wh.url, wh.events, wh.secret, int(wh.enabled), now, wh.id),
        )
    wh.updated_at = datetime.fromisoformat(now)
    return wh


def delete_webhook_config(webhook_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM webhook_configs WHERE id = ?", (webhook_id,))


def list_webhooks_for_event(user_id: int, event_type: str) -> List[WebhookConfig]:
    """List enabled webhooks for a user that subscribe to a given event type."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM webhook_configs WHERE user_id = ? AND enabled = 1",
            (user_id,),
        ).fetchall()
    results = []
    for row in rows:
        wh = _row_to_webhook_config(row)
        events = json.loads(wh.events) if wh.events else []
        if not events or event_type in events:
            results.append(wh)
    return results


def _row_to_webhook_config(row: sqlite3.Row) -> WebhookConfig:
    return WebhookConfig(
        id=row["id"],
        user_id=row["user_id"],
        url=row["url"],
        events=row["events"],
        secret=row["secret"],
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


# ─── Task Search ─────────────────────────────────────────────────────────────

def search_tasks(goal_id: Optional[int] = None, query: str = "",
                 tags: Optional[str] = None, status: Optional[str] = None) -> List[Task]:
    """Search tasks by title/description text, tags, and/or status."""
    sql = "SELECT * FROM tasks WHERE 1=1"
    params: list = []
    if goal_id is not None:
        sql += " AND goal_id = ?"
        params.append(goal_id)
    if query:
        sql += " AND (title LIKE ? OR description LIKE ?)"
        like = f"%{query}%"
        params.extend([like, like])
    if tags:
        # Match any of the given tags (comma-separated search)
        for tag in tags.split(","):
            tag = tag.strip()
            if tag:
                sql += " AND tags LIKE ?"
                params.append(f"%{tag}%")
    if status:
        sql += " AND status = ?"
        params.append(status)
    sql += " ORDER BY order_index ASC, id ASC"
    with _conn() as con:
        rows = con.execute(sql, params).fetchall()
    return [_row_to_task(r) for r in rows]


# ─── Dependency Graph Helpers ────────────────────────────────────────────────

def get_task_dependents(task_id: int) -> List[Task]:
    """Get all tasks that depend on the given task_id."""
    with _conn() as con:
        rows = con.execute("SELECT * FROM tasks ORDER BY order_index ASC, id ASC").fetchall()
    result = []
    for row in rows:
        t = _row_to_task(row)
        deps = json.loads(t.depends_on) if t.depends_on else []
        if task_id in deps:
            result.append(t)
    return result


def get_ready_tasks(goal_id: int) -> List[Task]:
    """Get tasks that are ready to execute — status='todo' and all dependencies are 'done'."""
    tasks = list_tasks(goal_id=goal_id)
    done_ids = {t.id for t in tasks if t.status == "done"}
    ready = []
    for t in tasks:
        if t.status != "todo":
            continue
        deps = json.loads(t.depends_on) if t.depends_on else []
        if all(d in done_ids for d in deps):
            ready.append(t)
    return ready


def validate_no_cycles(goal_id: int) -> Optional[str]:
    """Check for dependency cycles in a goal's tasks. Returns error message or None."""
    tasks = list_tasks(goal_id=goal_id)
    task_map = {t.id: t for t in tasks}

    # Build adjacency list
    graph: dict = {}
    for t in tasks:
        deps = json.loads(t.depends_on) if t.depends_on else []
        graph[t.id] = deps

    # Topological sort with cycle detection
    visited: Set[int] = set()
    in_stack: Set[int] = set()

    def _dfs(node: int) -> bool:
        if node in in_stack:
            return True  # cycle
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in graph.get(node, []):
            if dep in task_map and _dfs(dep):
                return True
        in_stack.discard(node)
        return False

    for tid in graph:
        if _dfs(tid):
            return f"Dependency cycle detected involving task {tid}"
    return None


# ─── Execution Checkpoints (WP-01) ──────────────────────────────────────────

@_with_retry
def create_checkpoint(cp: ExecutionCheckpoint) -> ExecutionCheckpoint:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO execution_checkpoints
               (goal_id, task_id, step_index, state_json, status, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cp.goal_id, cp.task_id, cp.step_index, cp.state_json, cp.status, now),
        )
        cp.id = cur.lastrowid
        cp.created_at = datetime.fromisoformat(now)
    return cp


@_with_retry
def get_checkpoint(checkpoint_id: int) -> Optional[ExecutionCheckpoint]:
    with _conn() as con:
        row = con.execute("SELECT * FROM execution_checkpoints WHERE id = ?", (checkpoint_id,)).fetchone()
    return _row_to_checkpoint(row) if row else None


@_with_retry
def list_checkpoints(goal_id: int) -> List[ExecutionCheckpoint]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM execution_checkpoints WHERE goal_id = ? ORDER BY created_at DESC",
            (goal_id,),
        ).fetchall()
    return [_row_to_checkpoint(r) for r in rows]


@_with_retry
def get_active_checkpoint(goal_id: int) -> Optional[ExecutionCheckpoint]:
    """Get the most recent active checkpoint for a goal."""
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM execution_checkpoints WHERE goal_id = ? AND status = 'active' ORDER BY created_at DESC LIMIT 1",
            (goal_id,),
        ).fetchone()
    return _row_to_checkpoint(row) if row else None


@_with_retry
def update_checkpoint(checkpoint_id: int, **kwargs) -> Optional[ExecutionCheckpoint]:
    allowed = {"step_index", "state_json", "status"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_checkpoint(checkpoint_id)
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    params = list(updates.values()) + [checkpoint_id]
    with _conn() as con:
        con.execute(f"UPDATE execution_checkpoints SET {set_clause} WHERE id = ?", params)
    return get_checkpoint(checkpoint_id)


def _row_to_checkpoint(row: sqlite3.Row) -> ExecutionCheckpoint:
    return ExecutionCheckpoint(
        id=row["id"],
        goal_id=row["goal_id"],
        task_id=row["task_id"],
        step_index=row["step_index"],
        state_json=row["state_json"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Agent Schedules & Flows (WP-02) ────────────────────────────────────────

@_with_retry
def create_agent_schedule(schedule: AgentSchedule) -> AgentSchedule:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO agent_schedules
               (agent_type, goal_id, interval_hours, next_run_at, paused, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (schedule.agent_type, schedule.goal_id, schedule.interval_hours,
             schedule.next_run_at, int(schedule.paused), now),
        )
        schedule.id = cur.lastrowid
        schedule.created_at = datetime.fromisoformat(now)
    return schedule


@_with_retry
def list_agent_schedules(goal_id: int) -> List[AgentSchedule]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_schedules WHERE goal_id = ? ORDER BY agent_type ASC",
            (goal_id,),
        ).fetchall()
    return [_row_to_agent_schedule(r) for r in rows]


def _row_to_agent_schedule(row: sqlite3.Row) -> AgentSchedule:
    return AgentSchedule(
        id=row["id"],
        agent_type=row["agent_type"],
        goal_id=row["goal_id"],
        interval_hours=row["interval_hours"],
        next_run_at=row["next_run_at"],
        paused=bool(row["paused"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@_with_retry
def create_agent_flow(flow: AgentFlow) -> AgentFlow:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO agent_flows
               (goal_id, steps_json, current_step, status, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (flow.goal_id, flow.steps_json, flow.current_step, flow.status, now),
        )
        flow.id = cur.lastrowid
        flow.created_at = datetime.fromisoformat(now)
    return flow


@_with_retry
def list_agent_flows(goal_id: int) -> List[AgentFlow]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM agent_flows WHERE goal_id = ? ORDER BY created_at DESC",
            (goal_id,),
        ).fetchall()
    return [_row_to_agent_flow(r) for r in rows]


def _row_to_agent_flow(row: sqlite3.Row) -> AgentFlow:
    return AgentFlow(
        id=row["id"],
        goal_id=row["goal_id"],
        steps_json=row["steps_json"],
        current_step=row["current_step"],
        status=row["status"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Gamification (WP-04) ───────────────────────────────────────────────────

@_with_retry
def get_or_create_user_xp(user_id: int) -> UserXP:
    with _conn() as con:
        row = con.execute("SELECT * FROM user_xp WHERE user_id = ?", (user_id,)).fetchone()
        if row:
            return _row_to_user_xp(row)
        now = datetime.now(timezone.utc).isoformat()
        cur = con.execute(
            """INSERT INTO user_xp (user_id, total_xp, level, current_streak, longest_streak, last_activity_date, created_at, updated_at)
               VALUES (?, 0, 1, 0, 0, '', ?, ?)""",
            (user_id, now, now),
        )
        return UserXP(id=cur.lastrowid, user_id=user_id, created_at=datetime.fromisoformat(now), updated_at=datetime.fromisoformat(now))


@_with_retry
def update_user_xp(user_id: int, xp_delta: int) -> UserXP:
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today_str = now.strftime("%Y-%m-%d")
    uxp = get_or_create_user_xp(user_id)
    new_xp = uxp.total_xp + xp_delta
    new_level = max(1, new_xp // 100 + 1)
    new_streak = uxp.current_streak
    new_longest = uxp.longest_streak
    if uxp.last_activity_date:
        last_date = date.fromisoformat(uxp.last_activity_date)
        today_date = date.fromisoformat(today_str)
        delta_days = (today_date - last_date).days
        if delta_days == 1:
            new_streak += 1
        elif delta_days > 1:
            new_streak = 1
    else:
        new_streak = 1
    new_longest = max(new_longest, new_streak)
    with _conn() as con:
        con.execute(
            """UPDATE user_xp SET total_xp = ?, level = ?, current_streak = ?,
               longest_streak = ?, last_activity_date = ?, updated_at = ?
               WHERE user_id = ?""",
            (new_xp, new_level, new_streak, new_longest, today_str, now_iso, user_id),
        )
    return get_or_create_user_xp(user_id)


@_with_retry
def create_achievement(ach: Achievement) -> Achievement:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM achievements WHERE user_id = ? AND achievement_type = ?",
            (ach.user_id, ach.achievement_type),
        ).fetchone()
        if existing:
            ach.id = existing["id"]
            ach.earned_at = datetime.fromisoformat(now)
            return ach
        cur = con.execute(
            """INSERT INTO achievements (user_id, achievement_type, title, description, earned_at)
               VALUES (?, ?, ?, ?, ?)""",
            (ach.user_id, ach.achievement_type, ach.title, ach.description, now),
        )
        ach.id = cur.lastrowid
        ach.earned_at = datetime.fromisoformat(now)
    return ach


@_with_retry
def list_achievements(user_id: int) -> List[Achievement]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM achievements WHERE user_id = ? ORDER BY earned_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_achievement(r) for r in rows]


def _row_to_user_xp(row: sqlite3.Row) -> UserXP:
    return UserXP(
        id=row["id"],
        user_id=row["user_id"],
        total_xp=row["total_xp"],
        level=row["level"],
        current_streak=row["current_streak"],
        longest_streak=row["longest_streak"],
        last_activity_date=row["last_activity_date"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _row_to_achievement(row: sqlite3.Row) -> Achievement:
    return Achievement(
        id=row["id"],
        user_id=row["user_id"],
        achievement_type=row["achievement_type"],
        title=row["title"],
        description=row["description"],
        earned_at=datetime.fromisoformat(row["earned_at"]),
    )


# ─── Time Tracking (WP-08) ──────────────────────────────────────────────────

@_with_retry
def create_time_entry(entry: TimeEntry) -> TimeEntry:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO time_entries (task_id, user_id, started_at, ended_at, duration_minutes, note, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (entry.task_id, entry.user_id, entry.started_at, entry.ended_at,
             entry.duration_minutes, entry.note, now),
        )
        entry.id = cur.lastrowid
        entry.created_at = datetime.fromisoformat(now)
    return entry


@_with_retry
def list_time_entries(task_id: int) -> List[TimeEntry]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM time_entries WHERE task_id = ? ORDER BY created_at DESC", (task_id,),
        ).fetchall()
    return [_row_to_time_entry(r) for r in rows]


@_with_retry
def get_task_total_time(task_id: int) -> int:
    """Return total tracked minutes for a task."""
    with _conn() as con:
        row = con.execute(
            "SELECT COALESCE(SUM(duration_minutes), 0) as total FROM time_entries WHERE task_id = ?",
            (task_id,),
        ).fetchone()
    return row["total"] if row else 0


def _row_to_time_entry(row: sqlite3.Row) -> TimeEntry:
    return TimeEntry(
        id=row["id"], task_id=row["task_id"], user_id=row["user_id"],
        started_at=row["started_at"], ended_at=row["ended_at"],
        duration_minutes=row["duration_minutes"], note=row["note"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Recurrence Rules (WP-10) ───────────────────────────────────────────────

@_with_retry
def create_recurrence_rule(rule: RecurrenceRule) -> RecurrenceRule:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO recurrence_rules (task_id, frequency, interval_val, next_due, end_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rule.task_id, rule.frequency, rule.interval, rule.next_due, rule.end_date, now),
        )
        rule.id = cur.lastrowid
        rule.created_at = datetime.fromisoformat(now)
    return rule


@_with_retry
def get_recurrence_rule(task_id: int) -> Optional[RecurrenceRule]:
    with _conn() as con:
        row = con.execute("SELECT * FROM recurrence_rules WHERE task_id = ?", (task_id,)).fetchone()
    return _row_to_recurrence(row) if row else None


@_with_retry
def delete_recurrence_rule(task_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM recurrence_rules WHERE task_id = ?", (task_id,))


def _row_to_recurrence(row: sqlite3.Row) -> RecurrenceRule:
    return RecurrenceRule(
        id=row["id"], task_id=row["task_id"], frequency=row["frequency"],
        interval=row["interval_val"], next_due=row["next_due"],
        end_date=row["end_date"], created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Goal Collaborators (WP-11) ─────────────────────────────────────────────

@_with_retry
def add_collaborator(collab: GoalCollaborator) -> GoalCollaborator:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT OR REPLACE INTO goal_collaborators (goal_id, user_id, role, created_at)
               VALUES (?, ?, ?, ?)""",
            (collab.goal_id, collab.user_id, collab.role, now),
        )
        collab.id = cur.lastrowid
        collab.created_at = datetime.fromisoformat(now)
    return collab


@_with_retry
def list_collaborators(goal_id: int) -> List[GoalCollaborator]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM goal_collaborators WHERE goal_id = ? ORDER BY created_at ASC",
            (goal_id,),
        ).fetchall()
    return [_row_to_collaborator(r) for r in rows]


@_with_retry
def remove_collaborator(goal_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM goal_collaborators WHERE goal_id = ? AND user_id = ?",
                     (goal_id, user_id))


def _row_to_collaborator(row: sqlite3.Row) -> GoalCollaborator:
    return GoalCollaborator(
        id=row["id"], goal_id=row["goal_id"], user_id=row["user_id"],
        role=row["role"], created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Custom Fields (WP-12) ──────────────────────────────────────────────────

@_with_retry
def create_custom_field(cf: CustomField) -> CustomField:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO custom_fields (task_id, field_name, field_value, field_type, config_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (cf.task_id, cf.field_name, cf.field_value, cf.field_type, cf.config_json, now),
        )
        cf.id = cur.lastrowid
        cf.created_at = datetime.fromisoformat(now)
    return cf


@_with_retry
def list_custom_fields(task_id: int) -> List[CustomField]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM custom_fields WHERE task_id = ? ORDER BY field_name ASC",
            (task_id,),
        ).fetchall()
    return [_row_to_custom_field(r) for r in rows]


@_with_retry
def delete_custom_field(field_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM custom_fields WHERE id = ?", (field_id,))


def get_custom_field(field_id: int) -> Optional[CustomField]:
    """Get a single custom field by ID."""
    with _conn() as con:
        row = con.execute("SELECT * FROM custom_fields WHERE id = ?", (field_id,)).fetchone()
    return _row_to_custom_field(row) if row else None


def resolve_custom_field_value(cf: CustomField) -> str:
    """Resolve the computed value for relation/rollup/formula custom fields.

    For basic types (text, number, date, url) returns field_value as-is.
    For relation: returns the title of the linked task.
    For rollup: aggregates a numeric field across related tasks.
    For formula: computes a built-in formula.
    """
    import json as _json

    if cf.field_type in ("text", "number", "date", "url"):
        return cf.field_value

    config = _json.loads(cf.config_json) if cf.config_json else {}

    if cf.field_type == "relation":
        # field_value holds the related task ID
        try:
            related_task = get_task(int(cf.field_value))
            return related_task.title if related_task else f"[Task #{cf.field_value} not found]"
        except (ValueError, TypeError):
            return "[Invalid relation]"

    if cf.field_type == "rollup":
        # Aggregate numeric fields from tasks related via a relation field
        relation_field = config.get("relation_field", "")
        target_field = config.get("target_field", "field_value")
        aggregation = config.get("aggregation", "count")

        # Find all relation custom fields on this task's siblings
        related_ids: list[int] = []
        fields = list_custom_fields(cf.task_id)
        for f in fields:
            if f.field_name == relation_field and f.field_type == "relation":
                try:
                    related_ids.append(int(f.field_value))
                except (ValueError, TypeError):
                    pass

        # Collect target values from related tasks
        values: list[float] = []
        for rid in related_ids:
            r_fields = list_custom_fields(rid)
            for rf in r_fields:
                if rf.field_name == target_field:
                    try:
                        values.append(float(rf.field_value))
                    except (ValueError, TypeError):
                        pass

        if aggregation == "count":
            return str(len(related_ids))
        elif aggregation == "sum":
            return str(sum(values))
        elif aggregation == "avg":
            return str(sum(values) / len(values)) if values else "0"
        elif aggregation == "min":
            return str(min(values)) if values else "0"
        elif aggregation == "max":
            return str(max(values)) if values else "0"
        return str(len(related_ids))

    if cf.field_type == "formula":
        formula_type = config.get("formula_type", "")

        if formula_type == "days_until_due":
            task = get_task(cf.task_id)
            if task and task.due_date:
                try:
                    due = datetime.fromisoformat(task.due_date)
                    now = datetime.now(timezone.utc)
                    delta = (due - now).days
                    return str(delta)
                except (ValueError, TypeError):
                    return "[Invalid date]"
            return "[No due date]"

        if formula_type == "field_diff":
            # Difference between two numeric custom fields
            field_a = config.get("field_a", "")
            field_b = config.get("field_b", "")
            fields = list_custom_fields(cf.task_id)
            val_a = val_b = 0.0
            for f in fields:
                if f.field_name == field_a:
                    try:
                        val_a = float(f.field_value)
                    except (ValueError, TypeError):
                        pass
                if f.field_name == field_b:
                    try:
                        val_b = float(f.field_value)
                    except (ValueError, TypeError):
                        pass
            return str(val_a - val_b)

        if formula_type == "concat":
            # Concatenate field values
            field_names = config.get("fields", [])
            fields = list_custom_fields(cf.task_id)
            parts = []
            field_map = {f.field_name: f.field_value for f in fields}
            separator = config.get("separator", " ")
            for name in field_names:
                parts.append(field_map.get(name, ""))
            return separator.join(parts)

        return "[Unknown formula]"

    return cf.field_value


def _row_to_custom_field(row: sqlite3.Row) -> CustomField:
    config_json = row["config_json"] if "config_json" in row.keys() else "{}"
    return CustomField(
        id=row["id"], task_id=row["task_id"], field_name=row["field_name"],
        field_value=row["field_value"], field_type=row["field_type"],
        config_json=config_json,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Progress Snapshots (WP-14) ─────────────────────────────────────────────

@_with_retry
def capture_progress_snapshot(goal_id: int) -> ProgressSnapshot:
    now = datetime.now(timezone.utc).isoformat()
    tasks = list_tasks(goal_id=goal_id)
    total = len(tasks)
    completed = sum(1 for t in tasks if t.status in ("done", "skipped"))
    pct = round((completed / total * 100) if total > 0 else 0, 2)
    snap = ProgressSnapshot(goal_id=goal_id, total_tasks=total,
                            completed_tasks=completed, percentage=pct)
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO progress_snapshots (goal_id, total_tasks, completed_tasks, percentage, captured_at)
               VALUES (?, ?, ?, ?, ?)""",
            (goal_id, total, completed, pct, now),
        )
        snap.id = cur.lastrowid
        snap.captured_at = datetime.fromisoformat(now)
    return snap


@_with_retry
def list_progress_snapshots(goal_id: int) -> List[ProgressSnapshot]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM progress_snapshots WHERE goal_id = ? ORDER BY captured_at DESC",
            (goal_id,),
        ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


def _row_to_snapshot(row: sqlite3.Row) -> ProgressSnapshot:
    return ProgressSnapshot(
        id=row["id"], goal_id=row["goal_id"], total_tasks=row["total_tasks"],
        completed_tasks=row["completed_tasks"], percentage=row["percentage"],
        captured_at=datetime.fromisoformat(row["captured_at"]),
    )


# ─── Notification Preferences (WP-16) ───────────────────────────────────────

@_with_retry
def set_notification_preference(pref: NotificationPreference) -> NotificationPreference:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM notification_preferences WHERE user_id = ? AND channel = ? AND event_type = ?",
            (pref.user_id, pref.channel, pref.event_type),
        ).fetchone()
        if existing:
            con.execute("UPDATE notification_preferences SET enabled = ? WHERE id = ?",
                         (int(pref.enabled), existing["id"]))
            pref.id = existing["id"]
        else:
            cur = con.execute(
                """INSERT INTO notification_preferences (user_id, channel, event_type, enabled, created_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (pref.user_id, pref.channel, pref.event_type, int(pref.enabled), now),
            )
            pref.id = cur.lastrowid
        pref.created_at = datetime.fromisoformat(now)
    return pref


@_with_retry
def list_notification_preferences(user_id: int) -> List[NotificationPreference]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM notification_preferences WHERE user_id = ? ORDER BY channel, event_type",
            (user_id,),
        ).fetchall()
    return [_row_to_notif_pref(r) for r in rows]


def _row_to_notif_pref(row: sqlite3.Row) -> NotificationPreference:
    return NotificationPreference(
        id=row["id"], user_id=row["user_id"], channel=row["channel"],
        event_type=row["event_type"], enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Personal API Keys (WP-17) ──────────────────────────────────────────────

@_with_retry
def create_personal_api_key(key: PersonalApiKey) -> PersonalApiKey:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO personal_api_keys (user_id, name, key_hash, key_prefix, last_used_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (key.user_id, key.name, key.key_hash, key.key_prefix, key.last_used_at, now),
        )
        key.id = cur.lastrowid
        key.created_at = datetime.fromisoformat(now)
    return key


@_with_retry
def list_personal_api_keys(user_id: int) -> List[PersonalApiKey]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM personal_api_keys WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_api_key(r) for r in rows]


@_with_retry
def get_api_key_by_hash(key_hash: str) -> Optional[PersonalApiKey]:
    with _conn() as con:
        row = con.execute("SELECT * FROM personal_api_keys WHERE key_hash = ?", (key_hash,)).fetchone()
    return _row_to_api_key(row) if row else None


@_with_retry
def delete_personal_api_key(key_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM personal_api_keys WHERE id = ? AND user_id = ?", (key_id, user_id))


def _row_to_api_key(row: sqlite3.Row) -> PersonalApiKey:
    return PersonalApiKey(
        id=row["id"], user_id=row["user_id"], name=row["name"],
        key_hash=row["key_hash"], key_prefix=row["key_prefix"],
        last_used_at=row["last_used_at"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Task Blockers (WP-19) ──────────────────────────────────────────────────

@_with_retry
def create_task_blocker(blocker: TaskBlocker) -> TaskBlocker:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO task_blockers (task_id, description, blocker_type, status, resolved_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (blocker.task_id, blocker.description, blocker.blocker_type,
             blocker.status, blocker.resolved_at, now),
        )
        blocker.id = cur.lastrowid
        blocker.created_at = datetime.fromisoformat(now)
    return blocker


@_with_retry
def list_task_blockers(task_id: int, status: Optional[str] = None) -> List[TaskBlocker]:
    query = "SELECT * FROM task_blockers WHERE task_id = ?"
    params: list = [task_id]
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY created_at DESC"
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_blocker(r) for r in rows]


@_with_retry
def resolve_task_blocker(blocker_id: int) -> Optional[TaskBlocker]:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute("UPDATE task_blockers SET status = 'resolved', resolved_at = ? WHERE id = ?",
                     (now, blocker_id))
        row = con.execute("SELECT * FROM task_blockers WHERE id = ?", (blocker_id,)).fetchone()
    return _row_to_blocker(row) if row else None


def _row_to_blocker(row: sqlite3.Row) -> TaskBlocker:
    return TaskBlocker(
        id=row["id"], task_id=row["task_id"], description=row["description"],
        blocker_type=row["blocker_type"], status=row["status"],
        resolved_at=row["resolved_at"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Dashboard Widgets (WP-20) ──────────────────────────────────────────────

@_with_retry
def create_dashboard_widget(widget: DashboardWidget) -> DashboardWidget:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO dashboard_widgets (user_id, widget_type, position, config_json, enabled, created_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (widget.user_id, widget.widget_type, widget.position,
             widget.config_json, int(widget.enabled), now),
        )
        widget.id = cur.lastrowid
        widget.created_at = datetime.fromisoformat(now)
    return widget


@_with_retry
def list_dashboard_widgets(user_id: int) -> List[DashboardWidget]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM dashboard_widgets WHERE user_id = ? ORDER BY position ASC",
            (user_id,),
        ).fetchall()
    return [_row_to_widget(r) for r in rows]


@_with_retry
def update_dashboard_widget(widget_id: int, user_id: int, **kwargs) -> Optional[DashboardWidget]:
    _ALLOWED_COLS = {"position", "config_json", "enabled", "widget_type"}
    updates = {k: v for k, v in kwargs.items() if k in _ALLOWED_COLS}
    if "enabled" in updates:
        updates["enabled"] = int(updates["enabled"])
    if not updates:
        return None
    # Build SET clause safely — column names come from a hardcoded allowlist
    col_map = {col: updates[col] for col in _ALLOWED_COLS if col in updates}
    set_parts = []
    params: list = []
    for col_name in ("position", "config_json", "enabled", "widget_type"):
        if col_name in col_map:
            set_parts.append(f"{col_name} = ?")
            params.append(col_map[col_name])
    if not set_parts:
        return None
    params.extend([widget_id, user_id])
    query = "UPDATE dashboard_widgets SET " + ", ".join(set_parts) + " WHERE id = ? AND user_id = ?"
    with _conn() as con:
        con.execute(query, params)
        row = con.execute("SELECT * FROM dashboard_widgets WHERE id = ?", (widget_id,)).fetchone()
    return _row_to_widget(row) if row else None


@_with_retry
def delete_dashboard_widget(widget_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM dashboard_widgets WHERE id = ? AND user_id = ?", (widget_id, user_id))


def _row_to_widget(row: sqlite3.Row) -> DashboardWidget:
    return DashboardWidget(
        id=row["id"], user_id=row["user_id"], widget_type=row["widget_type"],
        position=row["position"], config_json=row["config_json"],
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Phase 2: Workspace CRUD ────────────────────────────────────────────────

@_with_retry
def create_workspace(ws: Workspace) -> Workspace:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO workspaces (name, owner_id, description, invite_code, plan, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (ws.name, ws.owner_id, ws.description, ws.invite_code, ws.plan, now),
        )
        ws.id = cur.lastrowid
        ws.created_at = datetime.fromisoformat(now)
    return ws


def get_workspace(ws_id: int) -> Optional[Workspace]:
    with _conn() as con:
        row = con.execute("SELECT * FROM workspaces WHERE id = ?", (ws_id,)).fetchone()
    return _row_to_workspace(row) if row else None


def list_user_workspaces(user_id: int) -> List[Workspace]:
    with _conn() as con:
        rows = con.execute(
            "SELECT w.* FROM workspaces w "
            "JOIN workspace_members wm ON w.id = wm.workspace_id "
            "WHERE wm.user_id = ? ORDER BY w.created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_workspace(r) for r in rows]


@_with_retry
def add_workspace_member(member: WorkspaceMember) -> WorkspaceMember:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO workspace_members (workspace_id, user_id, role, joined_at) "
            "VALUES (?, ?, ?, ?)",
            (member.workspace_id, member.user_id, member.role, now),
        )
        member.id = cur.lastrowid
        member.joined_at = datetime.fromisoformat(now)
    return member


def list_workspace_members(ws_id: int) -> List[WorkspaceMember]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM workspace_members WHERE workspace_id = ? ORDER BY joined_at ASC",
            (ws_id,),
        ).fetchall()
    return [_row_to_workspace_member(r) for r in rows]


@_with_retry
def remove_workspace_member(ws_id: int, user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM workspace_members WHERE workspace_id = ? AND user_id = ?",
            (ws_id, user_id),
        )
        return cur.rowcount > 0


def get_workspace_by_invite_code(code: str) -> Optional[Workspace]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM workspaces WHERE invite_code = ?", (code,)
        ).fetchone()
    return _row_to_workspace(row) if row else None


def _row_to_workspace(row: sqlite3.Row) -> Workspace:
    return Workspace(
        id=row["id"],
        name=row["name"],
        owner_id=row["owner_id"],
        description=row["description"],
        invite_code=row["invite_code"],
        plan=row["plan"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def _row_to_workspace_member(row: sqlite3.Row) -> WorkspaceMember:
    return WorkspaceMember(
        id=row["id"],
        workspace_id=row["workspace_id"],
        user_id=row["user_id"],
        role=row["role"],
        joined_at=datetime.fromisoformat(row["joined_at"]),
    )


# ─── Phase 2: Notifications CRUD ────────────────────────────────────────────

@_with_retry
def create_notification(notif: Notification) -> Notification:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO notifications (user_id, title, body, notification_type, source_type, source_id, read, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (notif.user_id, notif.title, notif.body, notif.notification_type,
             notif.source_type, notif.source_id, int(notif.read), now),
        )
        notif.id = cur.lastrowid
        notif.created_at = datetime.fromisoformat(now)
    return notif


def list_user_notifications(user_id: int, unread_only: bool = False, limit: int = 50) -> List[Notification]:
    query = "SELECT * FROM notifications WHERE user_id = ?"
    params: list = [user_id]
    if unread_only:
        query += " AND read = 0"
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_notification(r) for r in rows]


@_with_retry
def mark_notification_read(notif_id: int, user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "UPDATE notifications SET read = 1 WHERE id = ? AND user_id = ?",
            (notif_id, user_id),
        )
        return cur.rowcount > 0


@_with_retry
def mark_all_notifications_read(user_id: int) -> int:
    with _conn() as con:
        cur = con.execute(
            "UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0",
            (user_id,),
        )
        return cur.rowcount


def count_unread_notifications(user_id: int) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) as cnt FROM notifications WHERE user_id = ? AND read = 0",
            (user_id,),
        ).fetchone()
    return row["cnt"] if row else 0


def _row_to_notification(row: sqlite3.Row) -> Notification:
    return Notification(
        id=row["id"],
        user_id=row["user_id"],
        title=row["title"],
        body=row["body"],
        notification_type=row["notification_type"],
        source_type=row["source_type"],
        source_id=row["source_id"],
        read=bool(row["read"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Phase 2: Activity Feed CRUD ────────────────────────────────────────────

@_with_retry
def create_activity_entry(entry: ActivityFeedEntry) -> ActivityFeedEntry:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO activity_feed (user_id, action, entity_type, entity_id, entity_title, details, workspace_id, goal_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.user_id, entry.action, entry.entity_type, entry.entity_id,
             entry.entity_title, entry.details, entry.workspace_id, entry.goal_id, now),
        )
        entry.id = cur.lastrowid
        entry.created_at = datetime.fromisoformat(now)
    return entry


def list_activity_feed(
    user_id: Optional[int] = None,
    goal_id: Optional[int] = None,
    workspace_id: Optional[int] = None,
    limit: int = 50,
) -> List[ActivityFeedEntry]:
    query = "SELECT * FROM activity_feed WHERE 1=1"
    params: list = []
    if user_id is not None:
        query += " AND user_id = ?"
        params.append(user_id)
    if goal_id is not None:
        query += " AND goal_id = ?"
        params.append(goal_id)
    if workspace_id is not None:
        query += " AND workspace_id = ?"
        params.append(workspace_id)
    query += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_activity_entry(r) for r in rows]


def _row_to_activity_entry(row: sqlite3.Row) -> ActivityFeedEntry:
    return ActivityFeedEntry(
        id=row["id"],
        user_id=row["user_id"],
        action=row["action"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        entity_title=row["entity_title"],
        details=row["details"],
        workspace_id=row["workspace_id"],
        goal_id=row["goal_id"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Phase 2: Comment Reactions CRUD ────────────────────────────────────────

@_with_retry
def add_comment_reaction(reaction: CommentReaction) -> CommentReaction:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO comment_reactions (comment_id, user_id, emoji, created_at) "
            "VALUES (?, ?, ?, ?)",
            (reaction.comment_id, reaction.user_id, reaction.emoji, now),
        )
        reaction.id = cur.lastrowid
        reaction.created_at = datetime.fromisoformat(now)
    return reaction


@_with_retry
def remove_comment_reaction(comment_id: int, user_id: int, emoji: str) -> bool:
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM comment_reactions WHERE comment_id = ? AND user_id = ? AND emoji = ?",
            (comment_id, user_id, emoji),
        )
        return cur.rowcount > 0


def list_comment_reactions(comment_id: int) -> List[CommentReaction]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM comment_reactions WHERE comment_id = ? ORDER BY created_at ASC",
            (comment_id,),
        ).fetchall()
    return [_row_to_comment_reaction(r) for r in rows]


def _row_to_comment_reaction(row: sqlite3.Row) -> CommentReaction:
    return CommentReaction(
        id=row["id"],
        comment_id=row["comment_id"],
        user_id=row["user_id"],
        emoji=row["emoji"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 6 — Enterprise: Sessions & 2FA CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def create_session(session) -> "UserSession":
    from teb.models import UserSession
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO user_sessions (user_id, session_token, ip_address, user_agent, is_active, last_activity) VALUES (?,?,?,?,1,?)",
            (session.user_id, session.session_token, session.ip_address, session.user_agent, session.last_activity),
        )
        session.id = cur.lastrowid
    return session


def list_user_sessions(user_id: int) -> list:
    from teb.models import UserSession
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM user_sessions WHERE user_id = ? AND is_active = 1 ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [UserSession(
        id=r["id"], user_id=r["user_id"], session_token=r["session_token"],
        ip_address=r["ip_address"], user_agent=r["user_agent"],
        is_active=bool(r["is_active"]), last_activity=r["last_activity"] or "",
        created_at=datetime.fromisoformat(r["created_at"]),
    ) for r in rows]


def revoke_session(session_id: int, user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "UPDATE user_sessions SET is_active = 0 WHERE id = ? AND user_id = ?",
            (session_id, user_id),
        )
    return cur.rowcount > 0


def revoke_all_sessions(user_id: int, except_session_id: int = None) -> int:
    with _conn() as con:
        if except_session_id:
            cur = con.execute(
                "UPDATE user_sessions SET is_active = 0 WHERE user_id = ? AND id != ?",
                (user_id, except_session_id),
            )
        else:
            cur = con.execute(
                "UPDATE user_sessions SET is_active = 0 WHERE user_id = ?",
                (user_id,),
            )
    return cur.rowcount


def update_session_activity(session_id: int) -> None:
    with _conn() as con:
        con.execute(
            "UPDATE user_sessions SET last_activity = ? WHERE id = ?",
            (datetime.now().isoformat(), session_id),
        )


def get_two_factor_config(user_id: int):
    from teb.models import TwoFactorConfig
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM two_factor_config WHERE user_id = ?", (user_id,),
        ).fetchone()
    if not row:
        return None
    return TwoFactorConfig(
        id=row["id"], user_id=row["user_id"], totp_secret=row["totp_secret"],
        is_enabled=bool(row["is_enabled"]), backup_codes_hash=row["backup_codes_hash"] or "",
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def save_two_factor_config(config) -> "TwoFactorConfig":
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM two_factor_config WHERE user_id = ?", (config.user_id,),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE two_factor_config SET totp_secret=?, is_enabled=?, backup_codes_hash=? WHERE user_id=?",
                (config.totp_secret, int(config.is_enabled), config.backup_codes_hash, config.user_id),
            )
            config.id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO two_factor_config (user_id, totp_secret, is_enabled, backup_codes_hash) VALUES (?,?,?,?)",
                (config.user_id, config.totp_secret, int(config.is_enabled), config.backup_codes_hash),
            )
            config.id = cur.lastrowid
    return config


def disable_two_factor(user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "UPDATE two_factor_config SET is_enabled = 0, totp_secret = '' WHERE user_id = ?",
            (user_id,),
        )
    return cur.rowcount > 0


# ═══════════════════════════════════════════════════════════════════════════════
# Phase 2 — Remaining Collaboration Features
# ═══════════════════════════════════════════════════════════════════════════════

# ─── Goal Sharing (GoalCollaborator CRUD) ────────────────────────────────────

@_with_retry
def share_goal(goal_id: int, user_id: int, role: str = "viewer") -> GoalCollaborator:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT OR REPLACE INTO goal_collaborators (goal_id, user_id, role, created_at) "
            "VALUES (?, ?, ?, ?)",
            (goal_id, user_id, role, now),
        )
        collab = GoalCollaborator(
            id=cur.lastrowid, goal_id=goal_id, user_id=user_id,
            role=role, created_at=datetime.fromisoformat(now),
        )
    return collab


def list_goal_collaborators(goal_id: int) -> List[GoalCollaborator]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM goal_collaborators WHERE goal_id = ? ORDER BY created_at ASC",
            (goal_id,),
        ).fetchall()
    return [_row_to_goal_collaborator(r) for r in rows]


@_with_retry
def unshare_goal(goal_id: int, user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM goal_collaborators WHERE goal_id = ? AND user_id = ?",
            (goal_id, user_id),
        )
        return cur.rowcount > 0


def _row_to_goal_collaborator(row: sqlite3.Row) -> GoalCollaborator:
    return GoalCollaborator(
        id=row["id"],
        goal_id=row["goal_id"],
        user_id=row["user_id"],
        role=row["role"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── @mentions extraction ───────────────────────────────────────────────────

_MENTION_RE = re.compile(r"@(\w+)")


def extract_mentions(text: str) -> List[str]:
    """Extract @username mentions from text. Returns list of usernames."""
    return _MENTION_RE.findall(text)


# ─── Task Assignment ────────────────────────────────────────────────────────

@_with_retry
def assign_task(task_id: int, user_id: Optional[int]) -> Task:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE tasks SET assigned_to = ?, updated_at = ? WHERE id = ?",
            (user_id, now, task_id),
        )
    task = get_task(task_id)
    return task  # type: ignore[return-value]


def list_tasks_assigned_to(user_id: int) -> List[Task]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM tasks WHERE assigned_to = ? ORDER BY order_index ASC, id ASC",
            (user_id,),
        ).fetchall()
    return [_row_to_task(r) for r in rows]


# ─── Direct Messaging ───────────────────────────────────────────────────────

@_with_retry
def send_message(msg: DirectMessage) -> DirectMessage:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO direct_messages (sender_id, recipient_id, content, read, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (msg.sender_id, msg.recipient_id, msg.content, int(msg.read), now),
        )
        msg.id = cur.lastrowid
        msg.created_at = datetime.fromisoformat(now)
    return msg


def list_conversations(user_id: int) -> List[dict]:
    """List distinct conversation partners with last message preview."""
    with _conn() as con:
        rows = con.execute(
            "SELECT CASE WHEN sender_id = ? THEN recipient_id ELSE sender_id END AS other_user_id, "
            "MAX(created_at) AS last_message_at, content AS last_content "
            "FROM direct_messages WHERE sender_id = ? OR recipient_id = ? "
            "GROUP BY other_user_id ORDER BY last_message_at DESC",
            (user_id, user_id, user_id),
        ).fetchall()
    return [{"other_user_id": r["other_user_id"], "last_message_at": r["last_message_at"],
             "last_content": r["last_content"]} for r in rows]


def list_messages(user_id: int, other_user_id: int, limit: int = 50) -> List[DirectMessage]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM direct_messages WHERE "
            "(sender_id = ? AND recipient_id = ?) OR (sender_id = ? AND recipient_id = ?) "
            "ORDER BY created_at ASC LIMIT ?",
            (user_id, other_user_id, other_user_id, user_id, limit),
        ).fetchall()
    return [_row_to_direct_message(r) for r in rows]


@_with_retry
def mark_message_read(message_id: int, user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "UPDATE direct_messages SET read = 1 WHERE id = ? AND recipient_id = ?",
            (message_id, user_id),
        )
        return cur.rowcount > 0


def _row_to_direct_message(row: sqlite3.Row) -> DirectMessage:
    return DirectMessage(
        id=row["id"],
        sender_id=row["sender_id"],
        recipient_id=row["recipient_id"],
        content=row["content"],
        read=bool(row["read"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Goal Chat Messages ─────────────────────────────────────────────────────

@_with_retry
def create_goal_chat_message(msg: GoalChatMessage) -> GoalChatMessage:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO goal_chat_messages (goal_id, user_id, content, created_at) "
            "VALUES (?, ?, ?, ?)",
            (msg.goal_id, msg.user_id, msg.content, now),
        )
        msg.id = cur.lastrowid
        msg.created_at = datetime.fromisoformat(now)
    return msg


def list_goal_chat_messages(goal_id: int, limit: int = 100) -> List[GoalChatMessage]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM goal_chat_messages WHERE goal_id = ? ORDER BY created_at ASC LIMIT ?",
            (goal_id, limit),
        ).fetchall()
    return [_row_to_goal_chat_message(r) for r in rows]


def _row_to_goal_chat_message(row: sqlite3.Row) -> GoalChatMessage:
    return GoalChatMessage(
        id=row["id"],
        goal_id=row["goal_id"],
        user_id=row["user_id"],
        content=row["content"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Email Notification Config ──────────────────────────────────────────────

def get_email_notification_config(user_id: int) -> Optional[EmailNotificationConfig]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM email_notification_config WHERE user_id = ?", (user_id,),
        ).fetchone()
    if not row:
        return None
    return _row_to_email_notification_config(row)


@_with_retry
def upsert_email_notification_config(cfg: EmailNotificationConfig) -> EmailNotificationConfig:
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM email_notification_config WHERE user_id = ?", (cfg.user_id,),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE email_notification_config SET digest_frequency=?, notify_on_mention=?, "
                "notify_on_assignment=?, notify_on_comment=? WHERE user_id=?",
                (cfg.digest_frequency, int(cfg.notify_on_mention), int(cfg.notify_on_assignment),
                 int(cfg.notify_on_comment), cfg.user_id),
            )
            cfg.id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO email_notification_config (user_id, digest_frequency, notify_on_mention, "
                "notify_on_assignment, notify_on_comment) VALUES (?, ?, ?, ?, ?)",
                (cfg.user_id, cfg.digest_frequency, int(cfg.notify_on_mention),
                 int(cfg.notify_on_assignment), int(cfg.notify_on_comment)),
            )
            cfg.id = cur.lastrowid
    return cfg


def _row_to_email_notification_config(row: sqlite3.Row) -> EmailNotificationConfig:
    return EmailNotificationConfig(
        id=row["id"],
        user_id=row["user_id"],
        digest_frequency=row["digest_frequency"],
        notify_on_mention=bool(row["notify_on_mention"]),
        notify_on_assignment=bool(row["notify_on_assignment"]),
        notify_on_comment=bool(row["notify_on_comment"]),
    )


# ─── Push Subscriptions ─────────────────────────────────────────────────────

@_with_retry
def save_push_subscription(sub: PushSubscription) -> PushSubscription:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT OR REPLACE INTO push_subscriptions (user_id, endpoint, p256dh, auth, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (sub.user_id, sub.endpoint, sub.p256dh, sub.auth, now),
        )
        sub.id = cur.lastrowid
        sub.created_at = datetime.fromisoformat(now)
    return sub


def list_push_subscriptions(user_id: int) -> List[PushSubscription]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM push_subscriptions WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_push_subscription(r) for r in rows]


@_with_retry
def delete_push_subscription(endpoint: str, user_id: int) -> bool:
    with _conn() as con:
        cur = con.execute(
            "DELETE FROM push_subscriptions WHERE endpoint = ? AND user_id = ?",
            (endpoint, user_id),
        )
        return cur.rowcount > 0


def _row_to_push_subscription(row: sqlite3.Row) -> PushSubscription:
    return PushSubscription(
        id=row["id"],
        user_id=row["user_id"],
        endpoint=row["endpoint"],
        p256dh=row["p256dh"],
        auth=row["auth"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Saved Views (Phase 3) ──────────────────────────────────────────────────

@_with_retry
def save_view(view: SavedView) -> SavedView:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO saved_views (user_id, name, view_type, filters_json, sort_json, group_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (view.user_id, view.name, view.view_type, view.filters_json,
             view.sort_json, view.group_by, now),
        )
        view.id = cur.lastrowid
        view.created_at = datetime.fromisoformat(now)
    return view


@_with_retry
def list_saved_views(user_id: int) -> List[SavedView]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM saved_views WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_saved_view(r) for r in rows]


@_with_retry
def get_saved_view(view_id: int) -> Optional[SavedView]:
    with _conn() as con:
        row = con.execute("SELECT * FROM saved_views WHERE id = ?", (view_id,)).fetchone()
    return _row_to_saved_view(row) if row else None


@_with_retry
def delete_saved_view(view_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM saved_views WHERE id = ? AND user_id = ?", (view_id, user_id))


def _row_to_saved_view(row: sqlite3.Row) -> SavedView:
    return SavedView(
        id=row["id"], user_id=row["user_id"], name=row["name"],
        view_type=row["view_type"], filters_json=row["filters_json"],
        sort_json=row["sort_json"], group_by=row["group_by"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Dashboard Layouts (Phase 3) ────────────────────────────────────────────

@_with_retry
def save_dashboard(layout: DashboardLayout) -> DashboardLayout:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO dashboard_layouts (user_id, name, widgets_json, created_at)
               VALUES (?, ?, ?, ?)""",
            (layout.user_id, layout.name, layout.widgets_json, now),
        )
        layout.id = cur.lastrowid
        layout.created_at = datetime.fromisoformat(now)
    return layout


@_with_retry
def list_dashboards(user_id: int) -> List[DashboardLayout]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM dashboard_layouts WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_dashboard_layout(r) for r in rows]


@_with_retry
def get_dashboard(dashboard_id: int) -> Optional[DashboardLayout]:
    with _conn() as con:
        row = con.execute("SELECT * FROM dashboard_layouts WHERE id = ?", (dashboard_id,)).fetchone()
    return _row_to_dashboard_layout(row) if row else None


@_with_retry
def update_dashboard(dashboard_id: int, user_id: int, **kwargs) -> Optional[DashboardLayout]:
    set_parts = []
    values = []
    for key in ("name", "widgets_json"):
        if key in kwargs:
            set_parts.append(f"{key} = ?")
            values.append(kwargs[key])
    if not set_parts:
        return get_dashboard(dashboard_id)
    values.extend([dashboard_id, user_id])
    query = "UPDATE dashboard_layouts SET " + ", ".join(set_parts) + " WHERE id = ? AND user_id = ?"
    with _conn() as con:
        con.execute(query, values)
        row = con.execute("SELECT * FROM dashboard_layouts WHERE id = ?", (dashboard_id,)).fetchone()
    return _row_to_dashboard_layout(row) if row else None


@_with_retry
def delete_dashboard(dashboard_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM dashboard_layouts WHERE id = ? AND user_id = ?", (dashboard_id, user_id))


def _row_to_dashboard_layout(row: sqlite3.Row) -> DashboardLayout:
    return DashboardLayout(
        id=row["id"], user_id=row["user_id"], name=row["name"],
        widgets_json=row["widgets_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Goal Progress Timeline (Phase 3, Item 7) ───────────────────────────────

@_with_retry
def get_goal_progress_timeline(goal_id: int) -> List[ProgressSnapshot]:
    """Return progress snapshots ordered by date (ascending) for timeline chart."""
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM progress_snapshots WHERE goal_id = ? ORDER BY captured_at ASC",
            (goal_id,),
        ).fetchall()
    return [_row_to_snapshot(r) for r in rows]


# ─── Burndown / Burnup Data (Phase 3, Item 10) ──────────────────────────────

@_with_retry
def get_burndown_data(goal_id: int) -> list:
    """Return daily counts of completed vs remaining tasks for burndown chart."""
    with _conn() as con:
        tasks = con.execute(
            "SELECT status, updated_at FROM tasks WHERE goal_id = ?", (goal_id,),
        ).fetchall()

    total = len(tasks)
    if not total:
        return []

    # Build daily cumulative completed count
    completed_dates: dict = {}
    for t in tasks:
        if t["status"] in ("done", "skipped") and t["updated_at"]:
            day = t["updated_at"][:10]
            completed_dates[day] = completed_dates.get(day, 0) + 1

    if not completed_dates:
        today = date.today().isoformat()
        return [{"date": today, "completed": 0, "remaining": total, "total": total}]

    sorted_days = sorted(completed_dates.keys())
    result = []
    cumulative = 0
    for day in sorted_days:
        cumulative += completed_dates[day]
        result.append({
            "date": day,
            "completed": cumulative,
            "remaining": total - cumulative,
            "total": total,
        })
    return result


# ─── Time Tracking Reports (Phase 3, Item 11) ───────────────────────────────

@_with_retry
def get_time_tracking_report(goal_id: int) -> dict:
    """Aggregate TimeEntry data by task and user for a goal."""
    with _conn() as con:
        rows = con.execute(
            """SELECT te.task_id, te.user_id, t.title as task_title,
                      SUM(te.duration_minutes) as total_minutes
               FROM time_entries te
               JOIN tasks t ON t.id = te.task_id
               WHERE t.goal_id = ?
               GROUP BY te.task_id, te.user_id""",
            (goal_id,),
        ).fetchall()

    by_task: dict = {}
    by_user: dict = {}
    for r in rows:
        tid = r["task_id"]
        uid = r["user_id"]
        mins = r["total_minutes"]
        title = r["task_title"]
        by_task.setdefault(tid, {"task_id": tid, "title": title, "total_minutes": 0})
        by_task[tid]["total_minutes"] += mins
        by_user.setdefault(uid, {"user_id": uid, "total_minutes": 0})
        by_user[uid]["total_minutes"] += mins

    return {
        "by_task": list(by_task.values()),
        "by_user": list(by_user.values()),
    }


# ─── Scheduled Reports (Phase 3, Item 9) ────────────────────────────────────

@_with_retry
def create_scheduled_report(report: ScheduledReport) -> ScheduledReport:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO scheduled_reports (user_id, report_type, frequency, recipients_json, created_at, last_sent_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (report.user_id, report.report_type, report.frequency,
             report.recipients_json, now, ""),
        )
        report.id = cur.lastrowid
        report.created_at = datetime.fromisoformat(now)
    return report


@_with_retry
def list_scheduled_reports(user_id: int) -> List[ScheduledReport]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM scheduled_reports WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_row_to_scheduled_report(r) for r in rows]


@_with_retry
def get_scheduled_report(report_id: int) -> Optional[ScheduledReport]:
    with _conn() as con:
        row = con.execute("SELECT * FROM scheduled_reports WHERE id = ?", (report_id,)).fetchone()
    return _row_to_scheduled_report(row) if row else None


@_with_retry
def delete_scheduled_report(report_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM scheduled_reports WHERE id = ? AND user_id = ?", (report_id, user_id))


def _row_to_scheduled_report(row: sqlite3.Row) -> ScheduledReport:
    return ScheduledReport(
        id=row["id"], user_id=row["user_id"], report_type=row["report_type"],
        frequency=row["frequency"], recipients_json=row["recipients_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
        last_sent_at=datetime.fromisoformat(row["last_sent_at"]) if row["last_sent_at"] else None,
    )


# ─── Phase 5: Integration Marketplace ────────────────────────────────────────

@_with_retry
def create_integration_listing(il: IntegrationListing) -> IntegrationListing:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO integration_listings (name, category, description, icon_url, auth_type, enabled, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (il.name, il.category, il.description, il.icon_url, il.auth_type, int(il.enabled), now),
        )
        il.id = cur.lastrowid
        il.created_at = datetime.fromisoformat(now)
    return il


@_with_retry
def list_integration_listings(category: Optional[str] = None) -> List[IntegrationListing]:
    with _conn() as con:
        if category:
            rows = con.execute(
                "SELECT * FROM integration_listings WHERE category = ? ORDER BY name", (category,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM integration_listings ORDER BY name").fetchall()
    return [_row_to_integration_listing(r) for r in rows]


@_with_retry
def get_integration_listing(listing_id: int) -> Optional[IntegrationListing]:
    with _conn() as con:
        row = con.execute("SELECT * FROM integration_listings WHERE id = ?", (listing_id,)).fetchone()
    return _row_to_integration_listing(row) if row else None


def _row_to_integration_listing(row: sqlite3.Row) -> IntegrationListing:
    return IntegrationListing(
        id=row["id"], name=row["name"], category=row["category"],
        description=row["description"], icon_url=row["icon_url"],
        auth_type=row["auth_type"], enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── OAuth Connections ───────────────────────────────────────────────────────

@_with_retry
def create_oauth_connection(oc: OAuthConnection) -> OAuthConnection:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO oauth_connections (user_id, provider, access_token_encrypted, "
            "refresh_token_encrypted, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (oc.user_id, oc.provider, oc.access_token_encrypted,
             oc.refresh_token_encrypted,
             oc.expires_at.isoformat() if oc.expires_at else None, now),
        )
        oc.id = cur.lastrowid
        oc.created_at = datetime.fromisoformat(now)
    return oc


@_with_retry
def get_oauth_connection(user_id: int, provider: str) -> Optional[OAuthConnection]:
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM oauth_connections WHERE user_id = ? AND provider = ? ORDER BY id DESC LIMIT 1",
            (user_id, provider),
        ).fetchone()
    return _row_to_oauth_connection(row) if row else None


@_with_retry
def upsert_oauth_connection(oc: OAuthConnection) -> OAuthConnection:
    """Insert or update an OAuth connection for user+provider."""
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM oauth_connections WHERE user_id = ? AND provider = ?",
            (oc.user_id, oc.provider),
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            con.execute(
                "UPDATE oauth_connections SET access_token_encrypted=?, refresh_token_encrypted=?, "
                "expires_at=? WHERE id=?",
                (oc.access_token_encrypted, oc.refresh_token_encrypted,
                 oc.expires_at.isoformat() if oc.expires_at else None, existing["id"]),
            )
            oc.id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO oauth_connections (user_id, provider, access_token_encrypted, "
                "refresh_token_encrypted, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (oc.user_id, oc.provider, oc.access_token_encrypted,
                 oc.refresh_token_encrypted,
                 oc.expires_at.isoformat() if oc.expires_at else None, now),
            )
            oc.id = cur.lastrowid
        oc.created_at = datetime.fromisoformat(now)
    return oc


def _row_to_oauth_connection(row: sqlite3.Row) -> OAuthConnection:
    return OAuthConnection(
        id=row["id"], user_id=row["user_id"], provider=row["provider"],
        access_token_encrypted=row["access_token_encrypted"],
        refresh_token_encrypted=row["refresh_token_encrypted"],
        expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Integration Templates ──────────────────────────────────────────────────

@_with_retry
def create_integration_template(t: IntegrationTemplate) -> IntegrationTemplate:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO integration_templates (name, description, source_service, target_service, mapping_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (t.name, t.description, t.source_service, t.target_service, t.mapping_json, now),
        )
        t.id = cur.lastrowid
        t.created_at = datetime.fromisoformat(now)
    return t


@_with_retry
def list_integration_templates() -> List[IntegrationTemplate]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM integration_templates ORDER BY name").fetchall()
    return [_row_to_integration_template(r) for r in rows]


@_with_retry
def get_integration_template(template_id: int) -> Optional[IntegrationTemplate]:
    with _conn() as con:
        row = con.execute("SELECT * FROM integration_templates WHERE id = ?", (template_id,)).fetchone()
    return _row_to_integration_template(row) if row else None


def _row_to_integration_template(row: sqlite3.Row) -> IntegrationTemplate:
    return IntegrationTemplate(
        id=row["id"], name=row["name"], description=row["description"],
        source_service=row["source_service"], target_service=row["target_service"],
        mapping_json=row["mapping_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Webhook Rules ──────────────────────────────────────────────────────────

@_with_retry
def create_webhook_rule(wr: WebhookRule) -> WebhookRule:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO webhook_rules (user_id, name, event_type, filter_json, target_url, headers_json, active, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (wr.user_id, wr.name, wr.event_type, wr.filter_json, wr.target_url,
             wr.headers_json, int(wr.active), now),
        )
        wr.id = cur.lastrowid
        wr.created_at = datetime.fromisoformat(now)
    return wr


@_with_retry
def list_webhook_rules(user_id: int) -> List[WebhookRule]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM webhook_rules WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [_row_to_webhook_rule(r) for r in rows]


@_with_retry
def get_webhook_rule(rule_id: int) -> Optional[WebhookRule]:
    with _conn() as con:
        row = con.execute("SELECT * FROM webhook_rules WHERE id = ?", (rule_id,)).fetchone()
    return _row_to_webhook_rule(row) if row else None


@_with_retry
def update_webhook_rule(wr: WebhookRule) -> WebhookRule:
    with _conn() as con:
        con.execute(
            "UPDATE webhook_rules SET name=?, event_type=?, filter_json=?, target_url=?, "
            "headers_json=?, active=? WHERE id=?",
            (wr.name, wr.event_type, wr.filter_json, wr.target_url,
             wr.headers_json, int(wr.active), wr.id),
        )
    return wr


@_with_retry
def delete_webhook_rule(rule_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM webhook_rules WHERE id = ? AND user_id = ?", (rule_id, user_id))


def _row_to_webhook_rule(row: sqlite3.Row) -> WebhookRule:
    return WebhookRule(
        id=row["id"], user_id=row["user_id"], name=row["name"],
        event_type=row["event_type"], filter_json=row["filter_json"],
        target_url=row["target_url"], headers_json=row["headers_json"],
        active=bool(row["active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Plugin Marketplace ─────────────────────────────────────────────────────

@_with_retry
def create_plugin_listing(pl: PluginListing) -> PluginListing:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO plugin_listings (name, description, author, version, downloads, rating, manifest_json, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (pl.name, pl.description, pl.author, pl.version, pl.downloads, pl.rating, pl.manifest_json, now),
        )
        pl.id = cur.lastrowid
        pl.created_at = datetime.fromisoformat(now)
    return pl


@_with_retry
def list_plugin_listings() -> List[PluginListing]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM plugin_listings ORDER BY downloads DESC, name").fetchall()
    return [_row_to_plugin_listing(r) for r in rows]


@_with_retry
def get_plugin_listing(listing_id: int) -> Optional[PluginListing]:
    with _conn() as con:
        row = con.execute("SELECT * FROM plugin_listings WHERE id = ?", (listing_id,)).fetchone()
    return _row_to_plugin_listing(row) if row else None


@_with_retry
def increment_plugin_downloads(listing_id: int) -> None:
    with _conn() as con:
        con.execute("UPDATE plugin_listings SET downloads = downloads + 1 WHERE id = ?", (listing_id,))


def _row_to_plugin_listing(row: sqlite3.Row) -> PluginListing:
    return PluginListing(
        id=row["id"], name=row["name"], description=row["description"],
        author=row["author"], version=row["version"], downloads=row["downloads"],
        rating=row["rating"], manifest_json=row["manifest_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Custom Field Definitions ───────────────────────────────────────────────

@_with_retry
def create_custom_field_definition(cfd: CustomFieldDefinition) -> CustomFieldDefinition:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO custom_field_definitions (plugin_id, field_type, label, options_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (cfd.plugin_id, cfd.field_type, cfd.label, cfd.options_json, now),
        )
        cfd.id = cur.lastrowid
        cfd.created_at = datetime.fromisoformat(now)
    return cfd


@_with_retry
def list_custom_field_definitions(plugin_id: Optional[int] = None) -> List[CustomFieldDefinition]:
    with _conn() as con:
        if plugin_id is not None:
            rows = con.execute(
                "SELECT * FROM custom_field_definitions WHERE plugin_id = ? ORDER BY id", (plugin_id,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM custom_field_definitions ORDER BY id").fetchall()
    return [_row_to_custom_field_definition(r) for r in rows]


def _row_to_custom_field_definition(row: sqlite3.Row) -> CustomFieldDefinition:
    return CustomFieldDefinition(
        id=row["id"], plugin_id=row["plugin_id"], field_type=row["field_type"],
        label=row["label"], options_json=row["options_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Plugin Views ───────────────────────────────────────────────────────────

@_with_retry
def create_plugin_view(pv: PluginView) -> PluginView:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO plugin_views (plugin_id, name, view_type, config_json, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (pv.plugin_id, pv.name, pv.view_type, pv.config_json, now),
        )
        pv.id = cur.lastrowid
        pv.created_at = datetime.fromisoformat(now)
    return pv


@_with_retry
def list_plugin_views(plugin_id: Optional[int] = None) -> List[PluginView]:
    with _conn() as con:
        if plugin_id is not None:
            rows = con.execute(
                "SELECT * FROM plugin_views WHERE plugin_id = ? ORDER BY id", (plugin_id,)
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM plugin_views ORDER BY id").fetchall()
    return [_row_to_plugin_view(r) for r in rows]


def _row_to_plugin_view(row: sqlite3.Row) -> PluginView:
    return PluginView(
        id=row["id"], plugin_id=row["plugin_id"], name=row["name"],
        view_type=row["view_type"], config_json=row["config_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Themes ─────────────────────────────────────────────────────────────────

@_with_retry
def create_theme(theme: Theme) -> Theme:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO themes (name, author, css_variables_json, is_active, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (theme.name, theme.author, theme.css_variables_json, int(theme.is_active), now),
        )
        theme.id = cur.lastrowid
        theme.created_at = datetime.fromisoformat(now)
    return theme


@_with_retry
def list_themes() -> List[Theme]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM themes ORDER BY name").fetchall()
    return [_row_to_theme(r) for r in rows]


@_with_retry
def get_active_theme() -> Optional[Theme]:
    with _conn() as con:
        row = con.execute("SELECT * FROM themes WHERE is_active = 1 LIMIT 1").fetchone()
    return _row_to_theme(row) if row else None


@_with_retry
def activate_theme(theme_id: int) -> None:
    with _conn() as con:
        con.execute("UPDATE themes SET is_active = 0")
        con.execute("UPDATE themes SET is_active = 1 WHERE id = ?", (theme_id,))


@_with_retry
def get_theme(theme_id: int) -> Optional[Theme]:
    with _conn() as con:
        row = con.execute("SELECT * FROM themes WHERE id = ?", (theme_id,)).fetchone()
    return _row_to_theme(row) if row else None


def _row_to_theme(row: sqlite3.Row) -> Theme:
    return Theme(
        id=row["id"], name=row["name"], author=row["author"],
        css_variables_json=row["css_variables_json"], is_active=bool(row["is_active"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


# ─── Zapier Subscriptions ───────────────────────────────────────────────────

@_with_retry
def create_zapier_subscription(user_id: int, event_type: str, target_url: str) -> int:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO zapier_subscriptions (user_id, event_type, target_url, created_at) VALUES (?, ?, ?, ?)",
            (user_id, event_type, target_url, now),
        )
        return cur.lastrowid


@_with_retry
def delete_zapier_subscription(sub_id: int, user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM zapier_subscriptions WHERE id = ? AND user_id = ?", (sub_id, user_id))


@_with_retry
def list_zapier_subscriptions(user_id: int) -> list:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM zapier_subscriptions WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
        ).fetchall()
    return [{"id": r["id"], "event_type": r["event_type"], "target_url": r["target_url"],
             "created_at": r["created_at"]} for r in rows]


# ─── API Rate Limit Usage ───────────────────────────────────────────────────

@_with_retry
def record_api_usage(user_id: int, integration: str = "", endpoint: str = "") -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO api_usage_log (user_id, integration, endpoint, created_at) VALUES (?, ?, ?, ?)",
            (user_id, integration, endpoint, now),
        )


@_with_retry
def get_api_rate_limit_usage(user_id: int) -> dict:
    """Count recent API calls per integration for the given user (last 24h)."""
    with _conn() as con:
        rows = con.execute(
            "SELECT integration, COUNT(*) as cnt FROM api_usage_log "
            "WHERE user_id = ? AND created_at >= datetime('now', '-1 day') "
            "GROUP BY integration ORDER BY cnt DESC",
            (user_id,),
        ).fetchall()
        total = con.execute(
            "SELECT COUNT(*) as cnt FROM api_usage_log "
            "WHERE user_id = ? AND created_at >= datetime('now', '-1 day')",
            (user_id,),
        ).fetchone()
    return {
        "user_id": user_id,
        "window": "24h",
        "total_calls": total["cnt"] if total else 0,
        "by_integration": [{"integration": r["integration"] or "general", "calls": r["cnt"]} for r in rows],
    }


# ─── Full Project Export ─────────────────────────────────────────────────────

@_with_retry
def export_project(goal_id: int) -> dict:
    """Export a full goal with all tasks, comments, and artifacts as JSON."""
    with _conn() as con:
        goal_row = con.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
        if not goal_row:
            return {}
        goal = _row_to_goal(goal_row)

        task_rows = con.execute("SELECT * FROM tasks WHERE goal_id = ? ORDER BY order_index", (goal_id,)).fetchall()
        tasks = [_row_to_task(r) for r in task_rows]

        task_ids = [t.id for t in tasks]
        comments = []
        artifacts = []
        for tid in task_ids:
            comment_rows = con.execute("SELECT * FROM task_comments WHERE task_id = ?", (tid,)).fetchall()
            for cr in comment_rows:
                comments.append({
                    "id": cr["id"], "task_id": cr["task_id"],
                    "content": cr["content"], "author": cr["author"],
                    "created_at": cr["created_at"],
                })
            artifact_rows = con.execute("SELECT * FROM task_artifacts WHERE task_id = ?", (tid,)).fetchall()
            for ar in artifact_rows:
                artifacts.append({
                    "id": ar["id"], "task_id": ar["task_id"],
                    "artifact_type": ar["artifact_type"], "content": ar["content"],
                    "created_at": ar["created_at"],
                })

    return {
        "goal": goal.to_dict(),
        "tasks": [t.to_dict() for t in tasks],
        "comments": comments,
        "artifacts": artifacts,
    }


# ─── Phase 6: Enterprise — SSO Config ───────────────────────────────────────

@_with_retry
def create_sso_config(cfg: SSOConfig) -> SSOConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO sso_configs (org_id, provider, entity_id, sso_url, certificate, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cfg.org_id, cfg.provider, cfg.entity_id, cfg.sso_url, cfg.certificate, now),
        )
        cfg.id = cur.lastrowid
        cfg.created_at = datetime.fromisoformat(now)
    return cfg


@_with_retry
def get_sso_config(org_id: int) -> Optional[SSOConfig]:
    with _conn() as con:
        row = con.execute("SELECT * FROM sso_configs WHERE org_id = ? ORDER BY id DESC LIMIT 1", (org_id,)).fetchone()
    if not row:
        return None
    return SSOConfig(
        id=row["id"], org_id=row["org_id"], provider=row["provider"],
        entity_id=row["entity_id"], sso_url=row["sso_url"], certificate=row["certificate"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@_with_retry
def update_sso_config(cfg: SSOConfig) -> SSOConfig:
    with _conn() as con:
        con.execute(
            "UPDATE sso_configs SET provider=?, entity_id=?, sso_url=?, certificate=? WHERE id=?",
            (cfg.provider, cfg.entity_id, cfg.sso_url, cfg.certificate, cfg.id),
        )
    return cfg


# ─── Phase 6: Enterprise — IP Allowlist ─────────────────────────────────────

@_with_retry
def create_ip_allowlist_entry(entry: IPAllowlist) -> IPAllowlist:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO ip_allowlist (org_id, cidr_range, description, created_at) VALUES (?, ?, ?, ?)",
            (entry.org_id, entry.cidr_range, entry.description, now),
        )
        entry.id = cur.lastrowid
        entry.created_at = datetime.fromisoformat(now)
    return entry


@_with_retry
def list_ip_allowlist(org_id: int) -> list[IPAllowlist]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM ip_allowlist WHERE org_id = ? ORDER BY id", (org_id,)).fetchall()
    return [
        IPAllowlist(id=r["id"], org_id=r["org_id"], cidr_range=r["cidr_range"],
                    description=r["description"], created_at=datetime.fromisoformat(r["created_at"]))
        for r in rows
    ]


@_with_retry
def delete_ip_allowlist_entry(entry_id: int, org_id: int) -> bool:
    with _conn() as con:
        cur = con.execute("DELETE FROM ip_allowlist WHERE id = ? AND org_id = ?", (entry_id, org_id))
    return cur.rowcount > 0


def check_ip_allowed(ip: str, org_id: int) -> bool:
    """Check if an IP address is allowed for the given org.

    Returns True if there are no allowlist entries (open access)
    or if the IP matches any CIDR range in the allowlist.
    """
    import ipaddress
    entries = list_ip_allowlist(org_id)
    if not entries:
        return True  # No allowlist = open access
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for entry in entries:
        try:
            network = ipaddress.ip_network(entry.cidr_range, strict=False)
            if addr in network:
                return True
        except ValueError:
            continue
    return False


# ─── Phase 6: Enterprise — Data Encryption at Rest ──────────────────────────

def encrypt_field(value: str) -> str:
    """Encrypt a field value using TEB_ENCRYPTION_KEY if set, passthrough otherwise."""
    from teb import config as _cfg
    key = _cfg.TEB_ENCRYPTION_KEY
    if not key or not value:
        return value
    try:
        from cryptography.fernet import Fernet
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.encrypt(value.encode()).decode()
    except Exception:
        return value


def decrypt_field(value: str) -> str:
    """Decrypt a field value using TEB_ENCRYPTION_KEY if set, passthrough otherwise."""
    from teb import config as _cfg
    key = _cfg.TEB_ENCRYPTION_KEY
    if not key or not value:
        return value
    try:
        from cryptography.fernet import Fernet
        f = Fernet(key.encode() if isinstance(key, str) else key)
        return f.decrypt(value.encode()).decode()
    except Exception:
        return value


# ─── Phase 6: Enterprise — Audit Log Search ─────────────────────────────────

@_with_retry
def search_audit_events(
    user_id: Optional[str] = None,
    event_type: Optional[str] = None,
    since: Optional[str] = None,
    until: Optional[str] = None,
    limit: int = 100,
) -> list[AuditEvent]:
    """Search audit events with flexible filtering."""
    clauses: list[str] = []
    params: list = []
    if user_id is not None:
        clauses.append("actor_id = ?")
        params.append(str(user_id))
    if event_type:
        clauses.append("event_type = ?")
        params.append(event_type)
    if since:
        clauses.append("created_at >= ?")
        params.append(since)
    if until:
        clauses.append("created_at <= ?")
        params.append(until)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _conn() as con:
        rows = con.execute(
            f"SELECT * FROM audit_events {where} ORDER BY created_at DESC LIMIT ?",
            params + [limit],
        ).fetchall()
    return [
        AuditEvent(
            id=r["id"], goal_id=r["goal_id"], event_type=r["event_type"],
            actor_type=r["actor_type"], actor_id=r["actor_id"],
            context_json=r["context_json"],
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


# ─── Phase 6: Enterprise — Organization Management ──────────────────────────

@_with_retry
def create_org(org: Organization) -> Organization:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO organizations (name, slug, owner_id, settings_json, created_at) VALUES (?, ?, ?, ?, ?)",
            (org.name, org.slug, org.owner_id, org.settings_json, now),
        )
        org.id = cur.lastrowid
        org.created_at = datetime.fromisoformat(now)
    return org


@_with_retry
def get_org(org_id: int) -> Optional[Organization]:
    with _conn() as con:
        row = con.execute("SELECT * FROM organizations WHERE id = ?", (org_id,)).fetchone()
    if not row:
        return None
    return Organization(
        id=row["id"], name=row["name"], slug=row["slug"],
        owner_id=row["owner_id"], settings_json=row["settings_json"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@_with_retry
def update_org(org: Organization) -> Organization:
    with _conn() as con:
        con.execute(
            "UPDATE organizations SET name=?, slug=?, settings_json=? WHERE id=?",
            (org.name, org.slug, org.settings_json, org.id),
        )
    return org


@_with_retry
def list_orgs() -> list[Organization]:
    with _conn() as con:
        rows = con.execute("SELECT * FROM organizations ORDER BY name").fetchall()
    return [
        Organization(id=r["id"], name=r["name"], slug=r["slug"],
                     owner_id=r["owner_id"], settings_json=r["settings_json"],
                     created_at=datetime.fromisoformat(r["created_at"]))
        for r in rows
    ]


@_with_retry
def add_org_member(org_id: int, user_id: int, role: str = "member") -> dict:
    with _conn() as con:
        con.execute(
            "INSERT OR REPLACE INTO org_members (org_id, user_id, role) VALUES (?, ?, ?)",
            (org_id, user_id, role),
        )
    return {"org_id": org_id, "user_id": user_id, "role": role}


@_with_retry
def list_org_members(org_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT om.user_id, om.role, u.email FROM org_members om "
            "LEFT JOIN users u ON om.user_id = u.id WHERE om.org_id = ?",
            (org_id,),
        ).fetchall()
    return [{"user_id": r["user_id"], "role": r["role"], "email": r["email"]} for r in rows]


# ─── Phase 6: Enterprise — Usage Analytics ──────────────────────────────────

@_with_retry
def get_usage_analytics(org_id: Optional[int] = None, since: Optional[str] = None) -> dict:
    """Aggregate usage analytics across the platform."""
    with _conn() as con:
        # Use parameterized queries for the since filter to prevent SQL injection
        params_base: list = []
        since_clause = ""
        if since:
            since_clause = "AND created_at >= ?"
            params_base = [since]

        active_users = con.execute(
            f"SELECT COUNT(DISTINCT user_id) as cnt FROM goals WHERE user_id IS NOT NULL {since_clause}",
            params_base,
        ).fetchone()["cnt"]

        goals_created = con.execute(
            f"SELECT COUNT(*) as cnt FROM goals WHERE 1=1 {since_clause}",
            params_base,
        ).fetchone()["cnt"]

        tasks_params: list = []
        tasks_since = ""
        if since:
            tasks_since = "AND updated_at >= ?"
            tasks_params = [since]

        tasks_completed = con.execute(
            f"SELECT COUNT(*) as cnt FROM tasks WHERE status = 'done' {tasks_since}",
            tasks_params,
        ).fetchone()["cnt"]

        api_calls = con.execute(
            f"SELECT COUNT(*) as cnt FROM api_usage_log WHERE 1=1 {since_clause}",
            params_base,
        ).fetchone()["cnt"]

    return {
        "active_users": active_users,
        "goals_created": goals_created,
        "tasks_completed": tasks_completed,
        "api_calls": api_calls,
    }


# ─── Phase 6: Enterprise — Branding Config ──────────────────────────────────

@_with_retry
def get_branding_config(org_id: int) -> Optional[BrandingConfig]:
    with _conn() as con:
        row = con.execute("SELECT * FROM branding_configs WHERE org_id = ?", (org_id,)).fetchone()
    if not row:
        return None
    return BrandingConfig(
        id=row["id"], org_id=row["org_id"], logo_url=row["logo_url"],
        primary_color=row["primary_color"], secondary_color=row["secondary_color"],
        app_name=row["app_name"], favicon_url=row["favicon_url"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


@_with_retry
def upsert_branding_config(cfg: BrandingConfig) -> BrandingConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        existing = con.execute("SELECT id FROM branding_configs WHERE org_id = ?", (cfg.org_id,)).fetchone()
        if existing:
            con.execute(
                "UPDATE branding_configs SET logo_url=?, primary_color=?, secondary_color=?, "
                "app_name=?, favicon_url=? WHERE org_id=?",
                (cfg.logo_url, cfg.primary_color, cfg.secondary_color, cfg.app_name, cfg.favicon_url, cfg.org_id),
            )
            cfg.id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO branding_configs (org_id, logo_url, primary_color, secondary_color, app_name, favicon_url, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (cfg.org_id, cfg.logo_url, cfg.primary_color, cfg.secondary_color, cfg.app_name, cfg.favicon_url, now),
            )
            cfg.id = cur.lastrowid
            cfg.created_at = datetime.fromisoformat(now)
    return cfg


# ─── Phase 6: Enterprise — Database Status ──────────────────────────────────

@_with_retry
def get_database_status() -> dict:
    """Return current database status including type, size, and table counts."""
    import os as _os
    db = _db_path()
    db_size = 0
    try:
        db_size = _os.path.getsize(db)
    except OSError:
        pass

    with _conn() as con:
        tables = con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        table_counts = {}
        for t in tables:
            name = t["name"]
            cnt = con.execute(f"SELECT COUNT(*) as cnt FROM [{name}]").fetchone()["cnt"]
            table_counts[name] = cnt

    return {
        "database_type": "sqlite",
        "database_path": db,
        "size_bytes": db_size,
        "size_mb": round(db_size / (1024 * 1024), 2),
        "table_count": len(table_counts),
        "tables": table_counts,
    }


# ─── Phase 6: Enterprise — Compliance Report ────────────────────────────────

@_with_retry
def get_compliance_report() -> dict:
    """Generate a compliance report with security settings and audit summary."""
    from teb import config as _cfg

    with _conn() as con:
        total_users = con.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
        admin_users = con.execute("SELECT COUNT(*) as cnt FROM users WHERE role = 'admin'").fetchone()["cnt"]
        locked_users = con.execute(
            "SELECT COUNT(*) as cnt FROM users WHERE locked_until IS NOT NULL AND locked_until > datetime('now')"
        ).fetchone()["cnt"]
        two_fa_enabled = con.execute(
            "SELECT COUNT(*) as cnt FROM two_factor_config WHERE is_enabled = 1"
        ).fetchone()["cnt"]
        recent_audit = con.execute(
            "SELECT COUNT(*) as cnt FROM audit_events WHERE created_at >= datetime('now', '-30 days')"
        ).fetchone()["cnt"]
        audit_types = con.execute(
            "SELECT event_type, COUNT(*) as cnt FROM audit_events GROUP BY event_type ORDER BY cnt DESC LIMIT 10"
        ).fetchall()

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "security_settings": {
            "jwt_algorithm": _cfg.JWT_ALGORITHM,
            "jwt_expire_hours": _cfg.JWT_EXPIRE_HOURS,
            "encryption_at_rest": bool(_cfg.TEB_ENCRYPTION_KEY),
            "cors_origins": _cfg.CORS_ORIGINS,
        },
        "user_access": {
            "total_users": total_users,
            "admin_users": admin_users,
            "locked_users": locked_users,
            "two_factor_enabled": two_fa_enabled,
            "two_factor_coverage": round(two_fa_enabled / max(total_users, 1) * 100, 1),
        },
        "audit_summary": {
            "events_last_30_days": recent_audit,
            "top_event_types": [{"type": r["event_type"], "count": r["cnt"]} for r in audit_types],
        },
        "data_retention": {
            "policy": "indefinite",
            "audit_events_retained": True,
        },
    }


# ─── Phase 7: Community tables & CRUD ─────────────────────────────────────────

def _ensure_phase7_tables() -> None:
    """Create Phase 7 community tables if they don't exist."""
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS template_gallery (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                author TEXT DEFAULT '',
                category TEXT DEFAULT '',
                template_json TEXT DEFAULT '{}',
                downloads INTEGER DEFAULT 0,
                rating REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS blog_posts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                slug TEXT NOT NULL UNIQUE,
                content TEXT DEFAULT '',
                author TEXT DEFAULT '',
                published INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS roadmap_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                status TEXT DEFAULT 'planned',
                votes INTEGER DEFAULT 0,
                category TEXT DEFAULT '',
                target_date TEXT DEFAULT '',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS feature_votes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                roadmap_item_id INTEGER NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, roadmap_item_id)
            );
        """)


from teb.models import TemplateGalleryEntry, BlogPost, RoadmapItem, FeatureVote


@_with_retry
def create_template_gallery_entry(entry: TemplateGalleryEntry) -> int:
    _ensure_phase7_tables()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO template_gallery (name, description, author, category, template_json) VALUES (?,?,?,?,?)",
            (entry.name, entry.description, entry.author, entry.category, entry.template_json),
        )
        return cur.lastrowid


@_with_retry
def list_template_gallery(category: str = "") -> list:
    _ensure_phase7_tables()
    with _conn() as con:
        if category:
            rows = con.execute("SELECT * FROM template_gallery WHERE category=? ORDER BY downloads DESC", (category,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM template_gallery ORDER BY downloads DESC").fetchall()
    return [TemplateGalleryEntry(id=r["id"], name=r["name"], description=r["description"], author=r["author"],
            category=r["category"], template_json=r["template_json"], downloads=r["downloads"],
            rating=r["rating"], created_at=_parse_ts(r["created_at"])) for r in rows]


@_with_retry
def get_template_gallery_entry(entry_id: int) -> "TemplateGalleryEntry | None":
    _ensure_phase7_tables()
    with _conn() as con:
        r = con.execute("SELECT * FROM template_gallery WHERE id=?", (entry_id,)).fetchone()
    if not r:
        return None
    return TemplateGalleryEntry(id=r["id"], name=r["name"], description=r["description"], author=r["author"],
            category=r["category"], template_json=r["template_json"], downloads=r["downloads"],
            rating=r["rating"], created_at=_parse_ts(r["created_at"]))


@_with_retry
def create_blog_post(post: BlogPost) -> int:
    _ensure_phase7_tables()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO blog_posts (title, slug, content, author, published) VALUES (?,?,?,?,?)",
            (post.title, post.slug, post.content, post.author, int(post.published)),
        )
        return cur.lastrowid


@_with_retry
def list_blog_posts(published_only: bool = True) -> list:
    _ensure_phase7_tables()
    with _conn() as con:
        if published_only:
            rows = con.execute("SELECT * FROM blog_posts WHERE published=1 ORDER BY created_at DESC").fetchall()
        else:
            rows = con.execute("SELECT * FROM blog_posts ORDER BY created_at DESC").fetchall()
    return [BlogPost(id=r["id"], title=r["title"], slug=r["slug"], content=r["content"],
            author=r["author"], published=bool(r["published"]),
            created_at=_parse_ts(r["created_at"])) for r in rows]


@_with_retry
def get_blog_post_by_slug(slug: str) -> "BlogPost | None":
    _ensure_phase7_tables()
    with _conn() as con:
        r = con.execute("SELECT * FROM blog_posts WHERE slug=?", (slug,)).fetchone()
    if not r:
        return None
    return BlogPost(id=r["id"], title=r["title"], slug=r["slug"], content=r["content"],
            author=r["author"], published=bool(r["published"]),
            created_at=_parse_ts(r["created_at"]))


@_with_retry
def create_roadmap_item(item: RoadmapItem) -> int:
    _ensure_phase7_tables()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO roadmap_items (title, description, status, category, target_date) VALUES (?,?,?,?,?)",
            (item.title, item.description, item.status, item.category, item.target_date),
        )
        return cur.lastrowid


@_with_retry
def list_roadmap_items(status: str = "") -> list:
    _ensure_phase7_tables()
    with _conn() as con:
        if status:
            rows = con.execute("SELECT * FROM roadmap_items WHERE status=? ORDER BY votes DESC", (status,)).fetchall()
        else:
            rows = con.execute("SELECT * FROM roadmap_items ORDER BY votes DESC").fetchall()
    return [RoadmapItem(id=r["id"], title=r["title"], description=r["description"], status=r["status"],
            votes=r["votes"], category=r["category"], target_date=r["target_date"],
            created_at=_parse_ts(r["created_at"])) for r in rows]


@_with_retry
def update_roadmap_item(item_id: int, **kwargs) -> bool:
    _ensure_phase7_tables()
    allowed = {"title", "description", "status", "category", "target_date"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return False
    set_clause = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [item_id]
    with _conn() as con:
        con.execute(f"UPDATE roadmap_items SET {set_clause} WHERE id=?", vals)
    return True


@_with_retry
def cast_feature_vote(user_id: int, roadmap_item_id: int) -> bool:
    _ensure_phase7_tables()
    with _conn() as con:
        try:
            con.execute("INSERT INTO feature_votes (user_id, roadmap_item_id) VALUES (?,?)", (user_id, roadmap_item_id))
            con.execute("UPDATE roadmap_items SET votes = votes + 1 WHERE id=?", (roadmap_item_id,))
            return True
        except Exception:
            return False


@_with_retry
def remove_feature_vote(user_id: int, roadmap_item_id: int) -> bool:
    _ensure_phase7_tables()
    with _conn() as con:
        cur = con.execute("DELETE FROM feature_votes WHERE user_id=? AND roadmap_item_id=?", (user_id, roadmap_item_id))
        if cur.rowcount > 0:
            con.execute("UPDATE roadmap_items SET votes = MAX(votes - 1, 0) WHERE id=?", (roadmap_item_id,))
            return True
    return False


# ═══════════════════════════════════════════════════════════════════════════════
# Bridging Plan: Risk Assessments, Schedules, Reports, Gamification Social
# ═══════════════════════════════════════════════════════════════════════════════

_bridging_tables_ensured = False


def _reset_bridging_flag() -> None:
    global _bridging_tables_ensured
    _bridging_tables_ensured = False


register_reset_callback(_reset_bridging_flag)


def _ensure_bridging_tables() -> None:
    global _bridging_tables_ensured
    if _bridging_tables_ensured:
        return
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS risk_assessments (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id          INTEGER NOT NULL,
                goal_id          INTEGER NOT NULL,
                risk_score       REAL    NOT NULL DEFAULT 0.0,
                risk_factors     TEXT    NOT NULL DEFAULT '[]',
                estimated_delay  INTEGER NOT NULL DEFAULT 0,
                assessed_at      TEXT    NOT NULL DEFAULT '',
                created_at       TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_risk_task ON risk_assessments(task_id);
            CREATE INDEX IF NOT EXISTS idx_risk_goal ON risk_assessments(goal_id);

            CREATE TABLE IF NOT EXISTS task_schedules (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id          INTEGER NOT NULL,
                goal_id          INTEGER NOT NULL,
                user_id          INTEGER NOT NULL,
                scheduled_start  TEXT    NOT NULL DEFAULT '',
                scheduled_end    TEXT    NOT NULL DEFAULT '',
                calendar_slot    INTEGER NOT NULL DEFAULT 1,
                created_at       TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_schedule_task ON task_schedules(task_id);
            CREATE INDEX IF NOT EXISTS idx_schedule_user ON task_schedules(user_id);
            CREATE INDEX IF NOT EXISTS idx_schedule_goal ON task_schedules(goal_id);

            CREATE TABLE IF NOT EXISTS progress_reports (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id           INTEGER NOT NULL,
                user_id           INTEGER NOT NULL,
                summary           TEXT    NOT NULL DEFAULT '',
                metrics_json      TEXT    NOT NULL DEFAULT '{}',
                blockers_json     TEXT    NOT NULL DEFAULT '[]',
                next_actions_json TEXT    NOT NULL DEFAULT '[]',
                created_at        TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_reports_goal ON progress_reports(goal_id);
            CREATE INDEX IF NOT EXISTS idx_reports_user ON progress_reports(user_id);

            CREATE TABLE IF NOT EXISTS streaks (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id             INTEGER NOT NULL,
                current_streak      INTEGER NOT NULL DEFAULT 0,
                longest_streak      INTEGER NOT NULL DEFAULT 0,
                last_activity_date  TEXT    NOT NULL DEFAULT '',
                streak_type         TEXT    NOT NULL DEFAULT 'daily',
                created_at          TEXT    NOT NULL,
                updated_at          TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_streaks_user ON streaks(user_id);

            CREATE TABLE IF NOT EXISTS leaderboard (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL,
                score       INTEGER NOT NULL DEFAULT 0,
                rank        INTEGER NOT NULL DEFAULT 0,
                period      TEXT    NOT NULL DEFAULT 'weekly',
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_lb_user ON leaderboard(user_id);
            CREATE INDEX IF NOT EXISTS idx_lb_period ON leaderboard(period);

            CREATE TABLE IF NOT EXISTS team_challenges (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                title             TEXT    NOT NULL,
                description       TEXT    NOT NULL DEFAULT '',
                goal_type         TEXT    NOT NULL DEFAULT 'tasks_completed',
                target_value      INTEGER NOT NULL DEFAULT 10,
                current_value     INTEGER NOT NULL DEFAULT 0,
                status            TEXT    NOT NULL DEFAULT 'active',
                creator_id        INTEGER,
                participants_json TEXT    NOT NULL DEFAULT '[]',
                start_date        TEXT    NOT NULL DEFAULT '',
                end_date          TEXT    NOT NULL DEFAULT '',
                created_at        TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_challenges_status ON team_challenges(status);
        """)
    _bridging_tables_ensured = True


# ─── Risk Assessment CRUD ────────────────────────────────────────────────────

@_with_retry
def create_risk_assessment(risk: TaskRisk) -> TaskRisk:
    _ensure_bridging_tables()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO risk_assessments (task_id, goal_id, risk_score, risk_factors, estimated_delay, assessed_at, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (risk.task_id, risk.goal_id, risk.risk_score, risk.risk_factors,
             risk.estimated_delay, risk.assessed_at or now, now),
        )
        risk.id = cur.lastrowid
        risk.created_at = datetime.fromisoformat(now)
    return risk


def get_risk_assessment(task_id: int) -> Optional[TaskRisk]:
    _ensure_bridging_tables()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM risk_assessments WHERE task_id = ? ORDER BY created_at DESC LIMIT 1",
            (task_id,),
        ).fetchone()
    if not row:
        return None
    return _row_to_task_risk(row)


def list_risk_assessments(goal_id: int) -> List[TaskRisk]:
    _ensure_bridging_tables()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM risk_assessments WHERE goal_id = ? ORDER BY risk_score DESC",
            (goal_id,),
        ).fetchall()
    return [_row_to_task_risk(r) for r in rows]


def _row_to_task_risk(row: sqlite3.Row) -> TaskRisk:
    return TaskRisk(
        id=row["id"],
        task_id=row["task_id"],
        goal_id=row["goal_id"],
        risk_score=row["risk_score"],
        risk_factors=row["risk_factors"],
        estimated_delay=row["estimated_delay"],
        assessed_at=row["assessed_at"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


# ─── Task Schedule CRUD ─────────────────────────────────────────────────────

@_with_retry
def create_task_schedule(sched: TaskSchedule) -> TaskSchedule:
    _ensure_bridging_tables()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO task_schedules (task_id, goal_id, user_id, scheduled_start, scheduled_end, calendar_slot, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (sched.task_id, sched.goal_id, sched.user_id,
             sched.scheduled_start, sched.scheduled_end, sched.calendar_slot, now),
        )
        sched.id = cur.lastrowid
        sched.created_at = datetime.fromisoformat(now)
    return sched


def list_task_schedules(goal_id: Optional[int] = None, user_id: Optional[int] = None) -> List[TaskSchedule]:
    _ensure_bridging_tables()
    with _conn() as con:
        if goal_id is not None:
            rows = con.execute(
                "SELECT * FROM task_schedules WHERE goal_id = ? ORDER BY scheduled_start",
                (goal_id,),
            ).fetchall()
        elif user_id is not None:
            rows = con.execute(
                "SELECT * FROM task_schedules WHERE user_id = ? ORDER BY scheduled_start",
                (user_id,),
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM task_schedules ORDER BY scheduled_start").fetchall()
    return [_row_to_task_schedule(r) for r in rows]


@_with_retry
def delete_task_schedules(goal_id: int) -> int:
    """Delete all schedules for a goal (before rescheduling)."""
    _ensure_bridging_tables()
    with _conn() as con:
        cur = con.execute("DELETE FROM task_schedules WHERE goal_id = ?", (goal_id,))
        return cur.rowcount


def _row_to_task_schedule(row: sqlite3.Row) -> TaskSchedule:
    return TaskSchedule(
        id=row["id"],
        task_id=row["task_id"],
        goal_id=row["goal_id"],
        user_id=row["user_id"],
        scheduled_start=row["scheduled_start"],
        scheduled_end=row["scheduled_end"],
        calendar_slot=row["calendar_slot"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


# ─── Progress Report CRUD ───────────────────────────────────────────────────

@_with_retry
def create_progress_report(report: ProgressReport) -> ProgressReport:
    _ensure_bridging_tables()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO progress_reports (goal_id, user_id, summary, metrics_json, blockers_json, next_actions_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (report.goal_id, report.user_id, report.summary,
             report.metrics_json, report.blockers_json, report.next_actions_json, now),
        )
        report.id = cur.lastrowid
        report.created_at = datetime.fromisoformat(now)
    return report


def list_progress_reports(goal_id: int) -> List[ProgressReport]:
    _ensure_bridging_tables()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM progress_reports WHERE goal_id = ? ORDER BY created_at DESC",
            (goal_id,),
        ).fetchall()
    return [_row_to_progress_report(r) for r in rows]


def _row_to_progress_report(row: sqlite3.Row) -> ProgressReport:
    return ProgressReport(
        id=row["id"],
        goal_id=row["goal_id"],
        user_id=row["user_id"],
        summary=row["summary"],
        metrics_json=row["metrics_json"],
        blockers_json=row["blockers_json"],
        next_actions_json=row["next_actions_json"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


# ─── Streak CRUD ─────────────────────────────────────────────────────────────

def get_or_create_streak(user_id: int, streak_type: str = "daily") -> Streak:
    _ensure_bridging_tables()
    with _conn() as con:
        row = con.execute(
            "SELECT * FROM streaks WHERE user_id = ? AND streak_type = ?",
            (user_id, streak_type),
        ).fetchone()
        if row:
            return _row_to_streak(row)
        now = datetime.now(timezone.utc).isoformat()
        cur = con.execute(
            """INSERT INTO streaks (user_id, current_streak, longest_streak, last_activity_date, streak_type, created_at, updated_at)
               VALUES (?, 0, 0, '', ?, ?, ?)""",
            (user_id, streak_type, now, now),
        )
        return Streak(id=cur.lastrowid, user_id=user_id, streak_type=streak_type,
                      created_at=datetime.fromisoformat(now), updated_at=datetime.fromisoformat(now))


@_with_retry
def update_streak(user_id: int, streak_type: str = "daily") -> Streak:
    """Update a user's streak based on current activity."""
    _ensure_bridging_tables()
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    today_str = now.strftime("%Y-%m-%d")
    streak = get_or_create_streak(user_id, streak_type)

    new_streak = streak.current_streak
    new_longest = streak.longest_streak

    if streak.last_activity_date:
        last_date = date.fromisoformat(streak.last_activity_date)
        today_date = date.fromisoformat(today_str)
        delta_days = (today_date - last_date).days
        if delta_days == 0:
            # Already recorded today, no change
            return streak
        elif delta_days == 1:
            new_streak += 1
        else:
            new_streak = 1
    else:
        new_streak = 1

    new_longest = max(new_longest, new_streak)
    with _conn() as con:
        con.execute(
            """UPDATE streaks SET current_streak = ?, longest_streak = ?,
               last_activity_date = ?, updated_at = ?
               WHERE user_id = ? AND streak_type = ?""",
            (new_streak, new_longest, today_str, now_iso, user_id, streak_type),
        )
    return get_or_create_streak(user_id, streak_type)


def _row_to_streak(row: sqlite3.Row) -> Streak:
    return Streak(
        id=row["id"],
        user_id=row["user_id"],
        current_streak=row["current_streak"],
        longest_streak=row["longest_streak"],
        last_activity_date=row["last_activity_date"],
        streak_type=row["streak_type"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
    )


# ─── Leaderboard CRUD ───────────────────────────────────────────────────────

def get_leaderboard(period: str = "weekly", limit: int = 20) -> List[LeaderboardEntry]:
    _ensure_bridging_tables()
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM leaderboard WHERE period = ? ORDER BY score DESC LIMIT ?",
            (period, limit),
        ).fetchall()
    entries = [_row_to_leaderboard(r) for r in rows]
    for i, entry in enumerate(entries):
        entry.rank = i + 1
    return entries


@_with_retry
def update_leaderboard(user_id: int, score: int, period: str = "weekly") -> LeaderboardEntry:
    _ensure_bridging_tables()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM leaderboard WHERE user_id = ? AND period = ?",
            (user_id, period),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE leaderboard SET score = ? WHERE user_id = ? AND period = ?",
                (score, user_id, period),
            )
            entry_id = existing["id"]
        else:
            cur = con.execute(
                "INSERT INTO leaderboard (user_id, score, rank, period, created_at) VALUES (?, ?, 0, ?, ?)",
                (user_id, score, period, now),
            )
            entry_id = cur.lastrowid
    return LeaderboardEntry(id=entry_id, user_id=user_id, score=score, period=period,
                            created_at=datetime.fromisoformat(now))


def _row_to_leaderboard(row: sqlite3.Row) -> LeaderboardEntry:
    return LeaderboardEntry(
        id=row["id"],
        user_id=row["user_id"],
        score=row["score"],
        rank=row["rank"],
        period=row["period"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


# ─── Team Challenge CRUD ────────────────────────────────────────────────────

@_with_retry
def create_team_challenge(challenge: TeamChallenge) -> TeamChallenge:
    _ensure_bridging_tables()
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO team_challenges (title, description, goal_type, target_value, current_value,
               status, creator_id, participants_json, start_date, end_date, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (challenge.title, challenge.description, challenge.goal_type,
             challenge.target_value, challenge.current_value, challenge.status,
             challenge.creator_id, challenge.participants_json,
             challenge.start_date, challenge.end_date, now),
        )
        challenge.id = cur.lastrowid
        challenge.created_at = datetime.fromisoformat(now)
    return challenge


def get_team_challenge(challenge_id: int) -> Optional[TeamChallenge]:
    _ensure_bridging_tables()
    with _conn() as con:
        row = con.execute("SELECT * FROM team_challenges WHERE id = ?", (challenge_id,)).fetchone()
    if not row:
        return None
    return _row_to_team_challenge(row)


def list_team_challenges(status: Optional[str] = None) -> List[TeamChallenge]:
    _ensure_bridging_tables()
    with _conn() as con:
        if status:
            rows = con.execute(
                "SELECT * FROM team_challenges WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM team_challenges ORDER BY created_at DESC").fetchall()
    return [_row_to_team_challenge(r) for r in rows]


@_with_retry
def update_team_challenge_progress(challenge_id: int, increment: int = 1) -> Optional[TeamChallenge]:
    _ensure_bridging_tables()
    with _conn() as con:
        row = con.execute("SELECT * FROM team_challenges WHERE id = ?", (challenge_id,)).fetchone()
        if not row:
            return None
        new_value = row["current_value"] + increment
        new_status = "completed" if new_value >= row["target_value"] else row["status"]
        con.execute(
            "UPDATE team_challenges SET current_value = ?, status = ? WHERE id = ?",
            (new_value, new_status, challenge_id),
        )
    return get_team_challenge(challenge_id)


def _row_to_team_challenge(row: sqlite3.Row) -> TeamChallenge:
    return TeamChallenge(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        goal_type=row["goal_type"],
        target_value=row["target_value"],
        current_value=row["current_value"],
        status=row["status"],
        creator_id=row["creator_id"],
        participants_json=row["participants_json"],
        start_date=row["start_date"],
        end_date=row["end_date"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
    )


# ─── Content Blocks (recursive block-based content model) ────────────────────


@_with_retry
def create_content_block(block: ContentBlock) -> ContentBlock:
    """Insert a new content block and return it with its assigned id."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO content_blocks
               (entity_type, entity_id, block_type, content, properties_json,
                parent_block_id, order_index, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (block.entity_type, block.entity_id, block.block_type, block.content,
             block.properties_json, block.parent_block_id, block.order_index, now, now),
        )
        block.id = cur.lastrowid
        block.created_at = datetime.fromisoformat(now)
        block.updated_at = datetime.fromisoformat(now)
    return block


def list_content_blocks(entity_type: str, entity_id: int, parent_block_id: Optional[int] = None) -> List[ContentBlock]:
    """List content blocks for an entity, optionally filtered by parent.

    When parent_block_id is None and no parent filter is intended, returns ALL
    blocks for the entity.  Pass parent_block_id=0 to get only root-level blocks
    (those with parent_block_id IS NULL).
    """
    with _conn() as con:
        if parent_block_id == 0:
            # Root-level blocks only
            rows = con.execute(
                """SELECT * FROM content_blocks
                   WHERE entity_type = ? AND entity_id = ? AND parent_block_id IS NULL
                   ORDER BY order_index""",
                (entity_type, entity_id),
            ).fetchall()
        elif parent_block_id is not None:
            rows = con.execute(
                """SELECT * FROM content_blocks
                   WHERE entity_type = ? AND entity_id = ? AND parent_block_id = ?
                   ORDER BY order_index""",
                (entity_type, entity_id, parent_block_id),
            ).fetchall()
        else:
            rows = con.execute(
                """SELECT * FROM content_blocks
                   WHERE entity_type = ? AND entity_id = ?
                   ORDER BY order_index""",
                (entity_type, entity_id),
            ).fetchall()
    return [_row_to_content_block(r) for r in rows]


def get_content_block(block_id: int) -> Optional[ContentBlock]:
    """Get a single content block by id."""
    with _conn() as con:
        row = con.execute("SELECT * FROM content_blocks WHERE id = ?", (block_id,)).fetchone()
    return _row_to_content_block(row) if row else None


@_with_retry
def update_content_block(block_id: int, **kwargs) -> Optional[ContentBlock]:
    """Update a content block's fields.  Allowed fields: block_type, content,
    properties_json, parent_block_id, order_index."""
    allowed = {"block_type", "content", "properties_json", "parent_block_id", "order_index"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_content_block(block_id)
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in updates)
    values = list(updates.values()) + [block_id]
    with _conn() as con:
        con.execute(f"UPDATE content_blocks SET {set_clause} WHERE id = ?", values)
    return get_content_block(block_id)


@_with_retry
def delete_content_block(block_id: int) -> None:
    """Delete a content block and its children (via CASCADE)."""
    with _conn() as con:
        con.execute("DELETE FROM content_blocks WHERE id = ?", (block_id,))


@_with_retry
def reorder_content_blocks(entity_type: str, entity_id: int, block_ids: List[int]) -> None:
    """Reorder blocks by setting order_index according to the provided id list."""
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        for idx, bid in enumerate(block_ids):
            con.execute(
                "UPDATE content_blocks SET order_index = ?, updated_at = ? WHERE id = ? AND entity_type = ? AND entity_id = ?",
                (idx, now, bid, entity_type, entity_id),
            )


def get_content_block_tree(entity_type: str, entity_id: int) -> List[dict]:
    """Return the full block tree for an entity as nested dicts.

    Each dict has a 'children' key containing its child blocks (recursively).
    """
    all_blocks = list_content_blocks(entity_type, entity_id)
    by_parent: dict[Optional[int], list[ContentBlock]] = {}
    for b in all_blocks:
        by_parent.setdefault(b.parent_block_id, []).append(b)

    def _build_tree(parent_id: Optional[int]) -> List[dict]:
        children = by_parent.get(parent_id, [])
        result = []
        for block in sorted(children, key=lambda b: b.order_index):
            d = block.to_dict()
            d["children"] = _build_tree(block.id)
            result.append(d)
        return result

    return _build_tree(None)


def _row_to_content_block(row: sqlite3.Row) -> ContentBlock:
    return ContentBlock(
        id=row["id"],
        entity_type=row["entity_type"],
        entity_id=row["entity_id"],
        block_type=row["block_type"],
        content=row["content"],
        properties_json=row["properties_json"],
        parent_block_id=row["parent_block_id"],
        order_index=row["order_index"],
        created_at=datetime.fromisoformat(row["created_at"]) if row["created_at"] else None,
        updated_at=datetime.fromisoformat(row["updated_at"]) if row["updated_at"] else None,
    )


# ─── Cross-goal task queries (for portfolio views) ──────────────────────────


def list_user_tasks(
    user_id: int,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    assigned_to: Optional[int] = None,
    tags: Optional[str] = None,
    due_before: Optional[str] = None,
    due_after: Optional[str] = None,
    sort_field: str = "order_index",
    sort_dir: str = "asc",
    limit: int = 200,
) -> List[Task]:
    """List tasks across ALL goals owned by a user, with optional filters.

    This enables cross-goal portfolio views (e.g. 'all tasks due this week').
    """
    allowed_sort = {"order_index", "due_date", "priority", "status", "created_at", "updated_at", "title"}
    if sort_field not in allowed_sort:
        sort_field = "order_index"
    if sort_dir.lower() not in ("asc", "desc"):
        sort_dir = "asc"

    query = """
        SELECT t.* FROM tasks t
        JOIN goals g ON t.goal_id = g.id
        WHERE g.user_id = ?
    """
    params: list = [user_id]

    if status:
        query += " AND t.status = ?"
        params.append(status)
    if priority:
        query += " AND t.priority = ?"
        params.append(priority)
    if assigned_to is not None:
        query += " AND t.assigned_to = ?"
        params.append(assigned_to)
    if tags:
        # Match any tag in the comma-separated tags field
        for tag in tags.split(","):
            tag = tag.strip()
            if tag:
                query += " AND t.tags LIKE ?"
                params.append(f"%{tag}%")
    if due_before:
        query += " AND t.due_date != '' AND t.due_date <= ?"
        params.append(due_before)
    if due_after:
        query += " AND t.due_date != '' AND t.due_date >= ?"
        params.append(due_after)

    query += f" ORDER BY t.{sort_field} {sort_dir}"
    query += " LIMIT ?"
    params.append(limit)

    with _conn() as con:
        rows = con.execute(query, params).fetchall()
    return [_row_to_task(r) for r in rows]
