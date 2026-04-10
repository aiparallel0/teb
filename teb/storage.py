import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, List, Optional, Set

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


_BUSY_TIMEOUT_MS = 5000  # Wait up to 5 seconds on lock contention
_MAX_RETRIES = 3         # Retry on SQLITE_BUSY up to 3 times


@contextmanager
def _conn() -> Generator[sqlite3.Connection, None, None]:
    con = sqlite3.connect(_db_path(), timeout=_BUSY_TIMEOUT_MS / 1000)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA wal_autocheckpoint=1000")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _with_retry(fn):
    """Decorator that retries a storage function on SQLITE_BUSY / OperationalError."""
    import functools
    import time as _time

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                return fn(*args, **kwargs)
            except sqlite3.OperationalError as exc:
                last_exc = exc
                if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                    _time.sleep(0.1 * (2 ** attempt))  # exponential backoff: 0.1, 0.2, 0.4s
                    continue
                raise
        raise last_exc  # type: ignore[misc]
    return wrapper


def init_db() -> None:
    with _conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT    NOT NULL UNIQUE,
                password_hash TEXT    NOT NULL,
                role          TEXT    NOT NULL DEFAULT 'user',
                email_verified INTEGER NOT NULL DEFAULT 0,
                failed_login_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until  TEXT    DEFAULT NULL,
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS refresh_tokens (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                token_hash  TEXT    NOT NULL UNIQUE,
                expires_at  TEXT    NOT NULL,
                revoked     INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);

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

            CREATE TABLE IF NOT EXISTS agent_memory (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_type  TEXT    NOT NULL,
                goal_type   TEXT    NOT NULL DEFAULT '',
                memory_key  TEXT    NOT NULL,
                memory_value TEXT   NOT NULL,
                confidence  REAL   NOT NULL DEFAULT 1.0,
                times_used  INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_agent_memory_type ON agent_memory(agent_type, goal_type);

            CREATE TABLE IF NOT EXISTS user_behavior (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
                behavior_type TEXT   NOT NULL,
                pattern_key   TEXT   NOT NULL,
                pattern_value TEXT   NOT NULL DEFAULT '',
                occurrences   INTEGER NOT NULL DEFAULT 1,
                created_at    TEXT   NOT NULL,
                updated_at    TEXT   NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_user_behavior_user ON user_behavior(user_id, behavior_type);

            CREATE TABLE IF NOT EXISTS payment_accounts (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider     TEXT    NOT NULL,
                account_id   TEXT    NOT NULL DEFAULT '',
                config_json  TEXT    NOT NULL DEFAULT '{}',
                enabled      INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL,
                updated_at   TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_payment_accounts_user ON payment_accounts(user_id);

            CREATE TABLE IF NOT EXISTS payment_transactions (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id       INTEGER NOT NULL REFERENCES payment_accounts(id) ON DELETE CASCADE,
                spending_request_id INTEGER REFERENCES spending_requests(id),
                provider_tx_id   TEXT    NOT NULL DEFAULT '',
                amount           REAL    NOT NULL DEFAULT 0,
                currency         TEXT    NOT NULL DEFAULT 'USD',
                status           TEXT    NOT NULL DEFAULT 'pending',
                description      TEXT    NOT NULL DEFAULT '',
                provider_response TEXT   NOT NULL DEFAULT '{}',
                retry_count      INTEGER NOT NULL DEFAULT 0,
                created_at       TEXT    NOT NULL,
                updated_at       TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_payment_tx_account ON payment_transactions(account_id);
            CREATE INDEX IF NOT EXISTS idx_payment_tx_status ON payment_transactions(status);

            CREATE TABLE IF NOT EXISTS discovered_services (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                service_name    TEXT    NOT NULL UNIQUE,
                category        TEXT    NOT NULL DEFAULT '',
                description     TEXT    NOT NULL DEFAULT '',
                url             TEXT    NOT NULL DEFAULT '',
                capabilities    TEXT    NOT NULL DEFAULT '[]',
                discovered_by   TEXT    NOT NULL DEFAULT 'system',
                relevance_score REAL   NOT NULL DEFAULT 0,
                times_recommended INTEGER NOT NULL DEFAULT 0,
                created_at      TEXT    NOT NULL,
                updated_at      TEXT    NOT NULL
            );
        """)

        # ─── Lightweight schema migrations ────────────────────────────────
        _run_migrations(con)


def _run_migrations(con: sqlite3.Connection) -> None:
    """Add columns that may be missing on databases created before multi-user auth or
    messaging user scoping.  Uses PRAGMA table_info so it is safe to call repeatedly."""

    def _has_column(table: str, column: str) -> bool:
        cols = con.execute(f"PRAGMA table_info({table})").fetchall()
        return any(c["name"] == column for c in cols)

    # goals.user_id (added in PR#8)
    if not _has_column("goals", "user_id"):
        con.execute("ALTER TABLE goals ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        con.execute("CREATE INDEX IF NOT EXISTS idx_goals_user ON goals(user_id)")

    # user_profiles.user_id
    if not _has_column("user_profiles", "user_id"):
        con.execute("ALTER TABLE user_profiles ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")

    # messaging_configs.user_id (scope configs to user)
    if not _has_column("messaging_configs", "user_id"):
        con.execute("ALTER TABLE messaging_configs ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        con.execute("CREATE INDEX IF NOT EXISTS idx_messaging_configs_user ON messaging_configs(user_id)")

    # users.role (RBAC)
    if not _has_column("users", "role"):
        con.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    # users.email_verified
    if not _has_column("users", "email_verified"):
        con.execute("ALTER TABLE users ADD COLUMN email_verified INTEGER NOT NULL DEFAULT 0")

    # users.failed_login_attempts
    if not _has_column("users", "failed_login_attempts"):
        con.execute("ALTER TABLE users ADD COLUMN failed_login_attempts INTEGER NOT NULL DEFAULT 0")

    # users.locked_until
    if not _has_column("users", "locked_until"):
        con.execute("ALTER TABLE users ADD COLUMN locked_until TEXT DEFAULT NULL")

    # api_credentials.user_id (scope credentials to user)
    if not _has_column("api_credentials", "user_id"):
        con.execute("ALTER TABLE api_credentials ADD COLUMN user_id INTEGER REFERENCES users(id) ON DELETE CASCADE")
        con.execute("CREATE INDEX IF NOT EXISTS idx_api_credentials_user ON api_credentials(user_id)")

    # goals.auto_execute (autonomous execution opt-in)
    if not _has_column("goals", "auto_execute"):
        con.execute("ALTER TABLE goals ADD COLUMN auto_execute INTEGER NOT NULL DEFAULT 0")

    # spending_budgets.autopilot_enabled
    if not _has_column("spending_budgets", "autopilot_enabled"):
        con.execute("ALTER TABLE spending_budgets ADD COLUMN autopilot_enabled INTEGER NOT NULL DEFAULT 0")

    # spending_budgets.autopilot_threshold
    if not _has_column("spending_budgets", "autopilot_threshold"):
        con.execute("ALTER TABLE spending_budgets ADD COLUMN autopilot_threshold REAL NOT NULL DEFAULT 50.0")

    # deployments table
    con.execute("""
        CREATE TABLE IF NOT EXISTS deployments (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            goal_id          INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            service          TEXT    NOT NULL,
            project_name     TEXT    NOT NULL DEFAULT '',
            repository_url   TEXT    NOT NULL DEFAULT '',
            deploy_url       TEXT    NOT NULL DEFAULT '',
            status           TEXT    NOT NULL DEFAULT 'pending',
            provider_data    TEXT    NOT NULL DEFAULT '{}',
            last_health_check TEXT   DEFAULT NULL,
            health_status    TEXT    NOT NULL DEFAULT 'unknown',
            created_at       TEXT    NOT NULL,
            updated_at       TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_deployments_goal ON deployments(goal_id)")

    # provisioning_logs table
    con.execute("""
        CREATE TABLE IF NOT EXISTS provisioning_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            service_name    TEXT    NOT NULL,
            action          TEXT    NOT NULL DEFAULT 'signup',
            status          TEXT    NOT NULL DEFAULT 'pending',
            result_data     TEXT    NOT NULL DEFAULT '{}',
            error           TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_provisioning_logs_task ON provisioning_logs(task_id)")

    # refresh_tokens table
    con.execute("""
        CREATE TABLE IF NOT EXISTS refresh_tokens (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            token_hash  TEXT    NOT NULL UNIQUE,
            expires_at  TEXT    NOT NULL,
            revoked     INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id)")

    # payment_transactions.retry_count (for failed transaction recovery)
    if not _has_column("payment_transactions", "retry_count"):
        con.execute("ALTER TABLE payment_transactions ADD COLUMN retry_count INTEGER NOT NULL DEFAULT 0")
        con.execute("CREATE INDEX IF NOT EXISTS idx_payment_tx_status ON payment_transactions(status)")


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
    g.auto_execute = bool(row["auto_execute"]) if "auto_execute" in row.keys() else False
    g.created_at = datetime.fromisoformat(row["created_at"])
    g.updated_at = datetime.fromisoformat(row["updated_at"])
    return g


def create_goal(goal: Goal) -> Goal:
    now = datetime.now(timezone.utc).isoformat()
    with _conn() as con:
        cur = con.execute(
            "INSERT INTO goals (user_id, title, description, status, answers, auto_execute, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (goal.user_id, goal.title, goal.description, goal.status,
             json.dumps(goal.answers), int(goal.auto_execute), now, now),
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
        con.execute(
            "UPDATE goals SET title=?, description=?, status=?, answers=?, auto_execute=?, updated_at=? WHERE id=?",
            (goal.title, goal.description, goal.status, json.dumps(goal.answers),
             int(goal.auto_execute), now, goal.id),
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


@_with_retry
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


@_with_retry
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
