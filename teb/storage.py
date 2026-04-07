import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List, Optional

from teb.config import get_db_path
from teb.models import (
    ApiCredential,
    CheckIn,
    ExecutionLog,
    Goal,
    NudgeEvent,
    OutcomeMetric,
    ProactiveSuggestion,
    SuccessPath,
    Task,
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
            CREATE TABLE IF NOT EXISTS goals (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'drafting',
                answers     TEXT    NOT NULL DEFAULT '{}',
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );

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

            CREATE INDEX IF NOT EXISTS idx_check_ins_goal_created
                ON check_ins(goal_id, created_at DESC);
        """)


# ─── Goals ────────────────────────────────────────────────────────────────────

def _row_to_goal(row: sqlite3.Row) -> Goal:
    g = Goal(
        id=row["id"],
        title=row["title"],
        description=row["description"],
        status=row["status"],
        answers=json.loads(row["answers"]),
    )
    g.created_at = datetime.fromisoformat(row["created_at"])
    g.updated_at = datetime.fromisoformat(row["updated_at"])
    return g


def create_goal(goal: Goal) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO goals (title, description, status, answers, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (goal.title, goal.description, goal.status, json.dumps(goal.answers), now, now),
        )
        goal.id = cur.lastrowid
        goal.created_at = datetime.fromisoformat(now)
        goal.updated_at = datetime.fromisoformat(now)
    return goal


def get_goal(goal_id: int) -> Optional[Goal]:
    with _conn() as con:
        row = con.execute("SELECT * FROM goals WHERE id = ?", (goal_id,)).fetchone()
    return _row_to_goal(row) if row else None


def list_goals() -> List[Goal]:
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
        auth_value=row["auth_value"],
        description=row["description"],
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def create_credential(cred: ApiCredential) -> ApiCredential:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO api_credentials (name, base_url, auth_header, auth_value, description, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cred.name, cred.base_url, cred.auth_header, cred.auth_value, cred.description, now),
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


def get_or_create_profile() -> UserProfile:
    """Get the singleton user profile, creating it if it doesn't exist."""
    with _conn() as con:
        row = con.execute("SELECT * FROM user_profiles ORDER BY id LIMIT 1").fetchone()
        if row:
            return _row_to_user_profile(row)
        now = datetime.now(timezone.utc).isoformat()
        cur = con.execute(
            "INSERT INTO user_profiles (created_at, updated_at) VALUES (?, ?)",
            (now, now),
        )
        profile = UserProfile(id=cur.lastrowid)
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
