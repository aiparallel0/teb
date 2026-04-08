import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List, Optional

from teb.config import get_db_path
from teb.models import (
    AgentHandoff,
    AgentMessage,
    ApiCredential,
    BrowserAction,
    CheckIn,
    ExecutionLog,
    Goal,
    Integration,
    MessagingConfig,
    NudgeEvent,
    OutcomeMetric,
    ProactiveSuggestion,
    SpendingBudget,
    SpendingRequest,
    SuccessPath,
    Task,
    User,
    UserProfile,
)

_DB_PATH: Optional[str] = None


def _db_path() -> str:
    return _DB_PATH if _DB_PATH is not None else get_db_path()


def set_db_path(path: str) -> None:
    """Override the database path (used in tests)."""
    global _DB_PATH
    _DB_PATH = path


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(_db_path())
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
                title       TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'drafting',
                answers     TEXT    NOT NULL DEFAULT '{}',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_goals_user ON goals(user_id);

            CREATE TABLE IF NOT EXISTS tasks (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id            INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                parent_id          INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
                title              TEXT    NOT NULL,
                description        TEXT    NOT NULL DEFAULT '',
                estimated_minutes  INTEGER NOT NULL DEFAULT 30,
                status             TEXT    NOT NULL DEFAULT 'todo',
                order_index        INTEGER NOT NULL DEFAULT 0,
                created_at         TEXT    NOT NULL,
                updated_at         TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS api_credentials (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                name         TEXT    NOT NULL,
                base_url     TEXT    NOT NULL,
                auth_header  TEXT    NOT NULL DEFAULT 'Authorization',
                auth_value   TEXT    NOT NULL DEFAULT '',
                description  TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS execution_logs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                credential_id    INTEGER REFERENCES api_credentials(id) ON DELETE SET NULL,
                action           TEXT    NOT NULL,
                request_summary  TEXT    NOT NULL DEFAULT '',
                response_summary TEXT    NOT NULL DEFAULT '',
                status           TEXT    NOT NULL DEFAULT 'success',
                created_at       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS check_ins (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                done_summary   TEXT    NOT NULL DEFAULT '',
                blockers       TEXT    NOT NULL DEFAULT '',
                mood           TEXT    NOT NULL DEFAULT 'neutral',
                feedback       TEXT    NOT NULL DEFAULT '',
                created_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS outcome_metrics (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                label          TEXT    NOT NULL,
                target_value   REAL    NOT NULL DEFAULT 0,
                current_value  REAL    NOT NULL DEFAULT 0,
                unit           TEXT    NOT NULL DEFAULT '',
                created_at     TEXT    NOT NULL,
                updated_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS nudge_events (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                nudge_type     TEXT    NOT NULL,
                message        TEXT    NOT NULL,
                acknowledged   INTEGER NOT NULL DEFAULT 0,
                created_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_profiles (
                id                      INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                 INTEGER REFERENCES users(id) ON DELETE CASCADE,
                skills                  TEXT    NOT NULL DEFAULT '',
                available_hours_per_day REAL    NOT NULL DEFAULT 1.0,
                experience_level        TEXT    NOT NULL DEFAULT 'unknown',
                interests               TEXT    NOT NULL DEFAULT '',
                preferred_learning_style TEXT   NOT NULL DEFAULT '',
                goals_completed         INTEGER NOT NULL DEFAULT 0,
                total_tasks_completed   INTEGER NOT NULL DEFAULT 0,
                created_at              TEXT    NOT NULL,
                updated_at              TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS success_paths (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_type        TEXT    NOT NULL,
                steps_json       TEXT    NOT NULL DEFAULT '[]',
                outcome_summary  TEXT    NOT NULL DEFAULT '',
                source_goal_id   INTEGER REFERENCES goals(id) ON DELETE SET NULL,
                times_reused     INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS proactive_suggestions (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                suggestion     TEXT    NOT NULL,
                rationale      TEXT    NOT NULL DEFAULT '',
                category       TEXT    NOT NULL DEFAULT 'general',
                status         TEXT    NOT NULL DEFAULT 'pending',
                created_at     TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS agent_handoffs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                from_agent      TEXT    NOT NULL,
                to_agent        TEXT    NOT NULL,
                task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
                input_summary   TEXT    NOT NULL DEFAULT '',
                output_summary  TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_check_ins_goal_created
                ON check_ins(goal_id, created_at DESC);

            CREATE INDEX IF NOT EXISTS idx_agent_handoffs_goal
                ON agent_handoffs(goal_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS agent_messages (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                from_agent      TEXT    NOT NULL,
                to_agent        TEXT    NOT NULL,
                message_type    TEXT    NOT NULL DEFAULT 'info',
                content         TEXT    NOT NULL DEFAULT '',
                in_reply_to     INTEGER REFERENCES agent_messages(id) ON DELETE SET NULL,
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_agent_messages_goal
                ON agent_messages(goal_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS browser_actions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                action_type     TEXT    NOT NULL,
                target          TEXT    NOT NULL DEFAULT '',
                value           TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                error           TEXT    NOT NULL DEFAULT '',
                screenshot_path TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_browser_actions_task
                ON browser_actions(task_id, created_at ASC);

            CREATE TABLE IF NOT EXISTS integrations (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name     TEXT    NOT NULL UNIQUE,
                category         TEXT    NOT NULL DEFAULT 'general',
                base_url         TEXT    NOT NULL DEFAULT '',
                auth_type        TEXT    NOT NULL DEFAULT 'api_key',
                auth_header      TEXT    NOT NULL DEFAULT 'Authorization',
                docs_url         TEXT    NOT NULL DEFAULT '',
                capabilities     TEXT    NOT NULL DEFAULT '[]',
                common_endpoints TEXT    NOT NULL DEFAULT '[]',
                created_at       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS spending_budgets (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id          INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
                daily_limit      REAL    NOT NULL DEFAULT 0,
                total_limit      REAL    NOT NULL DEFAULT 0,
                category         TEXT    NOT NULL DEFAULT 'general',
                require_approval INTEGER NOT NULL DEFAULT 1,
                spent_today      REAL    NOT NULL DEFAULT 0,
                spent_total      REAL    NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL,
                updated_at       TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS spending_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                budget_id       INTEGER NOT NULL REFERENCES spending_budgets(id) ON DELETE CASCADE,
                amount          REAL    NOT NULL,
                currency        TEXT    NOT NULL DEFAULT 'USD',
                description     TEXT    NOT NULL DEFAULT '',
                service         TEXT    NOT NULL DEFAULT '',
                status          TEXT    NOT NULL DEFAULT 'pending',
                denial_reason   TEXT    NOT NULL DEFAULT '',
                created_at      TEXT    NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_spending_requests_task
                ON spending_requests(task_id, created_at DESC);

            CREATE TABLE IF NOT EXISTS messaging_configs (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                channel          TEXT    NOT NULL,
                config_json      TEXT    NOT NULL DEFAULT '{}',
                enabled          INTEGER NOT NULL DEFAULT 1,
                notify_nudges    INTEGER NOT NULL DEFAULT 1,
                notify_tasks     INTEGER NOT NULL DEFAULT 1,
                notify_spending  INTEGER NOT NULL DEFAULT 1,
                notify_checkins  INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL,
                updated_at       TEXT    NOT NULL
            );
            CREATE TABLE IF NOT EXISTS telegram_sessions (
                chat_id              TEXT    PRIMARY KEY,
                goal_id              INTEGER REFERENCES goals(id) ON DELETE CASCADE,
                state                TEXT    NOT NULL DEFAULT 'idle',
                pending_question_key TEXT    DEFAULT NULL,
                updated_at           TEXT    NOT NULL
            );
        """)


# ─── Credential Encryption ───────────────────────────────────────────────────

def _get_fernet():
    """Get Fernet encryptor if TEB_SECRET_KEY is set, else None."""
    from teb import config as _cfg
    key = _cfg.SECRET_KEY
    if not key:
        return None
    try:
        from cryptography.fernet import Fernet
        return Fernet(key.encode() if isinstance(key, str) else key)
    except Exception:
        return None


def _encrypt_value(value: str) -> str:
    """Encrypt a value if encryption is configured, else return as-is."""
    f = _get_fernet()
    if f and value:
        return f.encrypt(value.encode()).decode()
    return value


def _decrypt_value(value: str) -> str:
    """Decrypt a value if encryption is configured, else return as-is."""
    f = _get_fernet()
    if f and value:
        try:
            return f.decrypt(value.encode()).decode()
        except Exception:
            # Not encrypted or wrong key — return as-is
            return value
    return value


# ─── Users ───────────────────────────────────────────────────────────────────

def _row_to_user(row: sqlite3.Row) -> User:
    return User(
        id=row["id"],
        email=row["email"],
        password_hash=row["password_hash"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_user(user: User) -> User:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
            (user.email, user.password_hash, now),
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
    g.created_at = datetime.fromisoformat(row["created_at"])
    g.updated_at = datetime.fromisoformat(row["updated_at"])
    return g


def create_goal(goal: Goal) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO goals (user_id, title, description, status, answers, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (goal.user_id, goal.title, goal.description, goal.status, json.dumps(goal.answers), now, now),
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
            rows = con.execute("SELECT * FROM goals ORDER BY created_at DESC").fetchall()
    return [_row_to_goal(r) for r in rows]


def update_goal(goal: Goal) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE goals SET title=?, description=?, status=?, answers=?, updated_at=? WHERE id=?",
            (goal.title, goal.description, goal.status, json.dumps(goal.answers), now, goal.id),
        )
    goal.updated_at = datetime.fromisoformat(now)
    return goal


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
    t.created_at = datetime.fromisoformat(row["created_at"])
    t.updated_at = datetime.fromisoformat(row["updated_at"])
    return t


def create_task(task: Task) -> Task:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO tasks (goal_id, parent_id, title, description, estimated_minutes, "
            "status, order_index, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                task.goal_id, task.parent_id, task.title, task.description,
                task.estimated_minutes, task.status, task.order_index, now, now,
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


def update_task(task: Task) -> Task:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        con.execute(
            "UPDATE tasks SET title=?, description=?, estimated_minutes=?, status=?, "
            "order_index=?, parent_id=?, updated_at=? WHERE id=?",
            (
                task.title, task.description, task.estimated_minutes,
                task.status, task.order_index, task.parent_id, now, task.id,
            ),
        )
    task.updated_at = datetime.fromisoformat(now)
    return task


def delete_tasks_for_goal(goal_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM tasks WHERE goal_id = ?", (goal_id,))


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
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_credential(cred: ApiCredential) -> ApiCredential:
    now = datetime.now(timezone.utc).isoformat()
    encrypted_value = _encrypt_value(cred.auth_value)
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO api_credentials (name, base_url, auth_header, auth_value, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cred.name, cred.base_url, cred.auth_header, encrypted_value, cred.description, now),
        )
        cred.id = cur.lastrowid
        cred.created_at = datetime.fromisoformat(now)
    return cred


def get_credential(cred_id: int) -> Optional[ApiCredential]:
    with _conn() as con:
        row = con.execute("SELECT * FROM api_credentials WHERE id = ?", (cred_id,)).fetchone()
    return _row_to_credential(row) if row else None


def list_credentials() -> List[ApiCredential]:
    with _conn() as con:
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
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_spending_budget(budget: SpendingBudget) -> SpendingBudget:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO spending_budgets
               (goal_id, daily_limit, total_limit, category, require_approval,
                spent_today, spent_total, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (budget.goal_id, budget.daily_limit, budget.total_limit,
             budget.category, int(budget.require_approval),
             budget.spent_today, budget.spent_total, now, now),
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
                   spent_today=?, spent_total=?, updated_at=?
               WHERE id=?""",
            (budget.daily_limit, budget.total_limit, budget.category,
             int(budget.require_approval), budget.spent_today, budget.spent_total,
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
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def create_messaging_config(cfg: MessagingConfig) -> MessagingConfig:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            """INSERT INTO messaging_configs
               (channel, config_json, enabled, notify_nudges, notify_tasks,
                notify_spending, notify_checkins, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cfg.channel, cfg.config_json, int(cfg.enabled),
             int(cfg.notify_nudges), int(cfg.notify_tasks),
             int(cfg.notify_spending), int(cfg.notify_checkins), now, now),
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


def list_messaging_configs(enabled_only: bool = False) -> List[MessagingConfig]:
    query = "SELECT * FROM messaging_configs"
    params: list = []
    if enabled_only:
        query += " WHERE enabled = 1"
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
