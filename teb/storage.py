import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List, Optional

from teb.config import get_db_path
from teb.models import ApiCredential, ExecutionLog, Goal, Task

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
