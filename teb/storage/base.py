"""Storage infrastructure: DB connection, retry logic, schema initialization."""
from __future__ import annotations

import json
import re
import sqlite3
import time as _time
from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Generator, List, Optional, Set

from teb.config import get_db_path, SECRET_KEY

# Module-level path override (set by tests via set_db_path)
_DB_PATH: Optional[str] = None

_BUSY_TIMEOUT_MS = 5000  # Wait up to 5 seconds on lock contention
_MAX_RETRIES = 3         # Retry on SQLITE_BUSY up to 3 times

# Units that indicate monetary (revenue/earnings) outcome metrics
_REVENUE_UNITS = {'$', 'usd', 'dollar', 'dollars', 'revenue', 'income', 'earnings'}

def _db_path() -> str:
    return _DB_PATH if _DB_PATH is not None else get_db_path()



# Callbacks to reset module-level state when DB path changes (e.g. in tests)
_reset_callbacks: list = []


def register_reset_callback(fn) -> None:
    """Register a function to call when set_db_path resets the database."""
    _reset_callbacks.append(fn)


def set_db_path(path: str) -> None:
    """Override the database path (used in tests)."""
    global _DB_PATH
    _DB_PATH = path
    for cb in _reset_callbacks:
        cb()


_BUSY_TIMEOUT_MS = 5000  # Wait up to 5 seconds on lock contention
_MAX_RETRIES = 3         # Retry on SQLITE_BUSY up to 3 times

# Units that indicate monetary (revenue/earnings) outcome metrics
_REVENUE_UNITS = {'$', 'usd', 'dollar', 'dollars', 'revenue', 'income', 'earnings'}


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

            -- Phase 5: Ecosystem tables

            CREATE TABLE IF NOT EXISTS integration_listings (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT    NOT NULL,
                category    TEXT    NOT NULL DEFAULT '',
                description TEXT    NOT NULL DEFAULT '',
                icon_url    TEXT    NOT NULL DEFAULT '',
                auth_type   TEXT    NOT NULL DEFAULT 'api_key',
                enabled     INTEGER NOT NULL DEFAULT 1,
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS oauth_connections (
                id                       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                provider                 TEXT    NOT NULL,
                access_token_encrypted   TEXT    NOT NULL DEFAULT '',
                refresh_token_encrypted  TEXT    NOT NULL DEFAULT '',
                expires_at               TEXT    DEFAULT NULL,
                created_at               TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_oauth_conn_user ON oauth_connections(user_id);

            CREATE TABLE IF NOT EXISTS integration_templates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                name            TEXT    NOT NULL,
                description     TEXT    NOT NULL DEFAULT '',
                source_service  TEXT    NOT NULL DEFAULT '',
                target_service  TEXT    NOT NULL DEFAULT '',
                mapping_json    TEXT    NOT NULL DEFAULT '{}',
                created_at      TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS webhook_rules (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name         TEXT    NOT NULL DEFAULT '',
                event_type   TEXT    NOT NULL DEFAULT '',
                filter_json  TEXT    NOT NULL DEFAULT '{}',
                target_url   TEXT    NOT NULL DEFAULT '',
                headers_json TEXT    NOT NULL DEFAULT '{}',
                active       INTEGER NOT NULL DEFAULT 1,
                created_at   TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_webhook_rules_user ON webhook_rules(user_id);

            CREATE TABLE IF NOT EXISTS plugin_listings (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                name          TEXT    NOT NULL,
                description   TEXT    NOT NULL DEFAULT '',
                author        TEXT    NOT NULL DEFAULT '',
                version       TEXT    NOT NULL DEFAULT '0.1.0',
                downloads     INTEGER NOT NULL DEFAULT 0,
                rating        REAL    NOT NULL DEFAULT 0,
                manifest_json TEXT    NOT NULL DEFAULT '{}',
                created_at    TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS custom_field_definitions (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id    INTEGER NOT NULL,
                field_type   TEXT    NOT NULL DEFAULT 'text',
                label        TEXT    NOT NULL DEFAULT '',
                options_json TEXT    NOT NULL DEFAULT '[]',
                created_at   TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS plugin_views (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                plugin_id   INTEGER NOT NULL,
                name        TEXT    NOT NULL DEFAULT '',
                view_type   TEXT    NOT NULL DEFAULT 'board',
                config_json TEXT    NOT NULL DEFAULT '{}',
                created_at  TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS themes (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                name               TEXT    NOT NULL,
                author             TEXT    NOT NULL DEFAULT '',
                css_variables_json TEXT    NOT NULL DEFAULT '{}',
                is_active          INTEGER NOT NULL DEFAULT 0,
                created_at         TEXT    NOT NULL
            );

            CREATE TABLE IF NOT EXISTS zapier_subscriptions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                event_type  TEXT    NOT NULL,
                target_url  TEXT    NOT NULL,
                created_at  TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_zapier_sub_user ON zapier_subscriptions(user_id);

            CREATE TABLE IF NOT EXISTS api_usage_log (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                integration  TEXT    NOT NULL DEFAULT '',
                endpoint     TEXT    NOT NULL DEFAULT '',
                created_at   TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_api_usage_user ON api_usage_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_api_usage_time ON api_usage_log(created_at);

            -- Schema version tracking for migration history
            CREATE TABLE IF NOT EXISTS schema_versions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                version     TEXT    NOT NULL,
                description TEXT    NOT NULL DEFAULT '',
                applied_at  TEXT    NOT NULL
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

    # tasks.priority (high | normal | low)
    if not _has_column("tasks", "priority"):
        con.execute("ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'normal'")

    # agent_activity table
    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_activity (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id INTEGER NOT NULL,
            agent_type TEXT NOT NULL,
            action TEXT NOT NULL,
            detail TEXT DEFAULT '',
            status TEXT DEFAULT 'running',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_agent_activity_goal ON agent_activity(goal_id)")

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

    # ─── Bridging Plan tables (Steps 1-8) ─────────────────────────────────

    # goals.parent_goal_id (Step 3: Goal Hierarchy)
    if not _has_column("goals", "parent_goal_id"):
        con.execute("ALTER TABLE goals ADD COLUMN parent_goal_id INTEGER REFERENCES goals(id) ON DELETE CASCADE")
        con.execute("CREATE INDEX IF NOT EXISTS idx_goals_parent ON goals(parent_goal_id)")

    # milestones table (Step 3: Goal Hierarchy)
    con.execute("""
        CREATE TABLE IF NOT EXISTS milestones (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            title           TEXT    NOT NULL,
            target_metric   TEXT    NOT NULL DEFAULT '',
            target_value    REAL    NOT NULL DEFAULT 0,
            current_value   REAL    NOT NULL DEFAULT 0,
            deadline        TEXT    NOT NULL DEFAULT '',
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_milestones_goal ON milestones(goal_id)")

    # agent_goal_memory table (Step 2: Persistent Agent Memory)
    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_goal_memory (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_type       TEXT    NOT NULL,
            goal_id          INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            context_json     TEXT    NOT NULL DEFAULT '{}',
            summary          TEXT    NOT NULL DEFAULT '',
            invocation_count INTEGER NOT NULL DEFAULT 0,
            created_at       TEXT    NOT NULL,
            updated_at       TEXT    NOT NULL
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_goal_memory_unique ON agent_goal_memory(agent_type, goal_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_agent_goal_memory_goal ON agent_goal_memory(goal_id)")

    # audit_events table (Step 6: Structured Audit Trail)
    con.execute("""
        CREATE TABLE IF NOT EXISTS audit_events (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id         INTEGER REFERENCES goals(id) ON DELETE CASCADE,
            event_type      TEXT    NOT NULL,
            actor_type      TEXT    NOT NULL DEFAULT 'system',
            actor_id        TEXT    NOT NULL DEFAULT '',
            context_json    TEXT    NOT NULL DEFAULT '{}',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_goal ON audit_events(goal_id, created_at ASC)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_audit_events_type ON audit_events(event_type)")

    # goal_templates table (Step 5: Goal Template Marketplace)
    con.execute("""
        CREATE TABLE IF NOT EXISTS goal_templates (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            title             TEXT    NOT NULL,
            description       TEXT    NOT NULL DEFAULT '',
            goal_type         TEXT    NOT NULL DEFAULT 'generic',
            category          TEXT    NOT NULL DEFAULT 'general',
            skill_level       TEXT    NOT NULL DEFAULT 'any',
            tasks_json        TEXT    NOT NULL DEFAULT '[]',
            milestones_json   TEXT    NOT NULL DEFAULT '[]',
            services_json     TEXT    NOT NULL DEFAULT '[]',
            outcome_type      TEXT    NOT NULL DEFAULT '',
            estimated_days    INTEGER NOT NULL DEFAULT 0,
            rating_sum        REAL    NOT NULL DEFAULT 0,
            rating_count      INTEGER NOT NULL DEFAULT 0,
            times_used        INTEGER NOT NULL DEFAULT 0,
            source_goal_id    INTEGER REFERENCES goals(id) ON DELETE SET NULL,
            author_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at        TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_goal_templates_type ON goal_templates(goal_type, category)")

    # execution_contexts table (Step 8: Execution Sandbox Isolation)
    con.execute("""
        CREATE TABLE IF NOT EXISTS execution_contexts (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id             INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            browser_profile_dir TEXT    NOT NULL DEFAULT '',
            temp_dir            TEXT    NOT NULL DEFAULT '',
            credential_scope    TEXT    NOT NULL DEFAULT '[]',
            status              TEXT    NOT NULL DEFAULT 'active',
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_execution_contexts_goal ON execution_contexts(goal_id)")

    # plugins table (Step 1: Execution Plugin System)
    con.execute("""
        CREATE TABLE IF NOT EXISTS plugins (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            name                 TEXT    NOT NULL UNIQUE,
            version              TEXT    NOT NULL DEFAULT '0.1.0',
            description          TEXT    NOT NULL DEFAULT '',
            task_types           TEXT    NOT NULL DEFAULT '[]',
            required_credentials TEXT    NOT NULL DEFAULT '[]',
            module_path          TEXT    NOT NULL DEFAULT '',
            enabled              INTEGER NOT NULL DEFAULT 1,
            created_at           TEXT    NOT NULL
        )
    """)

    # ─── Bridging Plan Phase 1: Usability Foundations ──────────────────────

    # tasks.due_date, tasks.depends_on, tasks.tags (Phase 1, Step 1+2)
    if not _has_column("tasks", "due_date"):
        con.execute("ALTER TABLE tasks ADD COLUMN due_date TEXT NOT NULL DEFAULT ''")
    if not _has_column("tasks", "depends_on"):
        con.execute("ALTER TABLE tasks ADD COLUMN depends_on TEXT NOT NULL DEFAULT '[]'")
    if not _has_column("tasks", "tags"):
        con.execute("ALTER TABLE tasks ADD COLUMN tags TEXT NOT NULL DEFAULT ''")

    # goals.tags (Phase 1, Step 2)
    if not _has_column("goals", "tags"):
        con.execute("ALTER TABLE goals ADD COLUMN tags TEXT NOT NULL DEFAULT ''")

    # task_comments table (Phase 1, Step 3)
    con.execute("""
        CREATE TABLE IF NOT EXISTS task_comments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            content         TEXT    NOT NULL,
            author_type     TEXT    NOT NULL DEFAULT 'system',
            author_id       TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_task_comments_task ON task_comments(task_id, created_at ASC)")

    # task_artifacts table (Phase 1, Step 4)
    con.execute("""
        CREATE TABLE IF NOT EXISTS task_artifacts (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            artifact_type   TEXT    NOT NULL,
            title           TEXT    NOT NULL DEFAULT '',
            content_url     TEXT    NOT NULL DEFAULT '',
            metadata_json   TEXT    NOT NULL DEFAULT '{}',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_task_artifacts_task ON task_artifacts(task_id, created_at ASC)")

    # ─── Bridging Plan Phase 2: Webhooks for external systems ─────────────

    # webhook_configs table (Phase 2, Step 7)
    con.execute("""
        CREATE TABLE IF NOT EXISTS webhook_configs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            url             TEXT    NOT NULL,
            events          TEXT    NOT NULL DEFAULT '[]',
            secret          TEXT    NOT NULL DEFAULT '',
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL,
            updated_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_webhook_configs_user ON webhook_configs(user_id)")

    # ─── MEGA Enhancement: Execution Checkpoints (WP-01) ─────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS execution_checkpoints (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            step_index      INTEGER NOT NULL DEFAULT 0,
            state_json      TEXT    NOT NULL DEFAULT '{}',
            status          TEXT    NOT NULL DEFAULT 'active',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_goal ON execution_checkpoints(goal_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_checkpoints_task ON execution_checkpoints(task_id)")

    # ─── MEGA Enhancement: Agent Schedules & Flows (WP-02) ───────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_schedules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_type      TEXT    NOT NULL,
            goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            interval_hours  INTEGER NOT NULL DEFAULT 8,
            next_run_at     TEXT    NOT NULL DEFAULT '',
            paused          INTEGER NOT NULL DEFAULT 0,
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_agent_schedules_goal ON agent_schedules(goal_id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_agent_schedules_unique ON agent_schedules(agent_type, goal_id)")

    con.execute("""
        CREATE TABLE IF NOT EXISTS agent_flows (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            steps_json      TEXT    NOT NULL DEFAULT '[]',
            current_step    INTEGER NOT NULL DEFAULT 0,
            status          TEXT    NOT NULL DEFAULT 'pending',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_agent_flows_goal ON agent_flows(goal_id)")

    # ─── MEGA Enhancement: Gamification (WP-04) ─────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS user_xp (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            total_xp            INTEGER NOT NULL DEFAULT 0,
            level               INTEGER NOT NULL DEFAULT 1,
            current_streak      INTEGER NOT NULL DEFAULT 0,
            longest_streak      INTEGER NOT NULL DEFAULT 0,
            last_activity_date  TEXT    NOT NULL DEFAULT '',
            created_at          TEXT    NOT NULL,
            updated_at          TEXT    NOT NULL
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_xp_user ON user_xp(user_id)")

    con.execute("""
        CREATE TABLE IF NOT EXISTS achievements (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            achievement_type    TEXT    NOT NULL,
            title               TEXT    NOT NULL DEFAULT '',
            description         TEXT    NOT NULL DEFAULT '',
            earned_at           TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_achievements_user ON achievements(user_id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_achievements_unique ON achievements(user_id, achievement_type)")

    # ─── MEGA Enhancement: Time Tracking (WP-08) ─────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS time_entries (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            started_at      TEXT    NOT NULL DEFAULT '',
            ended_at        TEXT    NOT NULL DEFAULT '',
            duration_minutes INTEGER NOT NULL DEFAULT 0,
            note            TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_task ON time_entries(task_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_time_entries_user ON time_entries(user_id)")

    # ─── MEGA Enhancement: Recurrence Rules (WP-10) ──────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS recurrence_rules (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            frequency       TEXT    NOT NULL DEFAULT 'weekly',
            interval_val    INTEGER NOT NULL DEFAULT 1,
            next_due        TEXT    NOT NULL DEFAULT '',
            end_date        TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_recurrence_task ON recurrence_rules(task_id)")

    # ─── MEGA Enhancement: Goal Collaborators (WP-11) ────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS goal_collaborators (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role            TEXT    NOT NULL DEFAULT 'viewer',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_collaborators_goal ON goal_collaborators(goal_id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_collaborators_unique ON goal_collaborators(goal_id, user_id)")

    # ─── MEGA Enhancement: Custom Fields (WP-12) ────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS custom_fields (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            field_name      TEXT    NOT NULL,
            field_value     TEXT    NOT NULL DEFAULT '',
            field_type      TEXT    NOT NULL DEFAULT 'text',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_custom_fields_task ON custom_fields(task_id)")

    # ─── MEGA Enhancement: Progress Snapshots (WP-14) ───────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS progress_snapshots (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
            total_tasks     INTEGER NOT NULL DEFAULT 0,
            completed_tasks INTEGER NOT NULL DEFAULT 0,
            percentage      REAL    NOT NULL DEFAULT 0.0,
            captured_at     TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_snapshots_goal ON progress_snapshots(goal_id)")

    # ─── MEGA Enhancement: Notification Preferences (WP-16) ─────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS notification_preferences (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            channel         TEXT    NOT NULL DEFAULT 'in_app',
            event_type      TEXT    NOT NULL DEFAULT 'all',
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_notif_prefs_user ON notification_preferences(user_id)")

    # ─── MEGA Enhancement: Personal API Keys (WP-17) ────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS personal_api_keys (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name            TEXT    NOT NULL,
            key_hash        TEXT    NOT NULL,
            key_prefix      TEXT    NOT NULL DEFAULT '',
            last_used_at    TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_api_keys_user ON personal_api_keys(user_id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_api_keys_hash ON personal_api_keys(key_hash)")

    # ─── MEGA Enhancement: Task Blockers (WP-19) ────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS task_blockers (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
            description     TEXT    NOT NULL,
            blocker_type    TEXT    NOT NULL DEFAULT 'internal',
            status          TEXT    NOT NULL DEFAULT 'open',
            resolved_at     TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_blockers_task ON task_blockers(task_id)")

    # ─── MEGA Enhancement: Dashboard Widgets (WP-20) ────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_widgets (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            widget_type     TEXT    NOT NULL,
            position        INTEGER NOT NULL DEFAULT 0,
            config_json     TEXT    NOT NULL DEFAULT '{}',
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_widgets_user ON dashboard_widgets(user_id)")

    # ─── Phase 2: Collaboration Tables ───────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS workspaces (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            name        TEXT NOT NULL,
            owner_id    INTEGER NOT NULL,
            description TEXT DEFAULT '',
            invite_code TEXT DEFAULT '',
            plan        TEXT DEFAULT 'free',
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS workspace_members (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            workspace_id INTEGER NOT NULL,
            user_id      INTEGER NOT NULL,
            role         TEXT DEFAULT 'member',
            joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ws_members_ws ON workspace_members(workspace_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_ws_members_user ON workspace_members(user_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL,
            title             TEXT NOT NULL,
            body              TEXT DEFAULT '',
            notification_type TEXT DEFAULT 'info',
            source_type       TEXT DEFAULT '',
            source_id         INTEGER,
            read              INTEGER DEFAULT 0,
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS activity_feed (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL,
            action       TEXT NOT NULL,
            entity_type  TEXT NOT NULL,
            entity_id    INTEGER NOT NULL,
            entity_title TEXT DEFAULT '',
            details      TEXT DEFAULT '',
            workspace_id INTEGER,
            goal_id      INTEGER,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_feed(user_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS comment_reactions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            comment_id INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            emoji      TEXT DEFAULT '👍',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_reactions_comment ON comment_reactions(comment_id)")

    # ─── Phase 2: Direct Messages ────────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS direct_messages (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id    INTEGER NOT NULL,
            recipient_id INTEGER NOT NULL,
            content      TEXT NOT NULL,
            read         INTEGER DEFAULT 0,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_dm_sender ON direct_messages(sender_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_dm_recipient ON direct_messages(recipient_id)")

    # ─── Phase 2: Goal Chat Messages ─────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS goal_chat_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            goal_id    INTEGER NOT NULL,
            user_id    INTEGER NOT NULL,
            content    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_goal_chat_goal ON goal_chat_messages(goal_id)")

    # ─── Phase 2: Email Notification Config ──────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS email_notification_config (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id              INTEGER NOT NULL UNIQUE,
            digest_frequency     TEXT DEFAULT 'none',
            notify_on_mention    INTEGER DEFAULT 1,
            notify_on_assignment INTEGER DEFAULT 1,
            notify_on_comment    INTEGER DEFAULT 1
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_email_config_user ON email_notification_config(user_id)")

    # ─── Phase 2: Push Subscriptions ─────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS push_subscriptions (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            endpoint   TEXT NOT NULL,
            p256dh     TEXT DEFAULT '',
            auth       TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_push_user ON push_subscriptions(user_id)")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_push_endpoint ON push_subscriptions(endpoint)")

    # ── Phase 6: Enterprise Security tables ──
    con.execute("""
        CREATE TABLE IF NOT EXISTS user_sessions (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id        INTEGER NOT NULL,
            session_token  TEXT NOT NULL,
            ip_address     TEXT DEFAULT '',
            user_agent     TEXT DEFAULT '',
            is_active      INTEGER DEFAULT 1,
            last_activity  TEXT DEFAULT '',
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_sessions_user ON user_sessions(user_id)")
    con.execute("""
        CREATE TABLE IF NOT EXISTS two_factor_config (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id           INTEGER NOT NULL UNIQUE,
            totp_secret       TEXT DEFAULT '',
            is_enabled        INTEGER DEFAULT 0,
            backup_codes_hash TEXT DEFAULT '',
            created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_2fa_user ON two_factor_config(user_id)")

    # ── Phase 6: SSO Config ──
    con.execute("""
        CREATE TABLE IF NOT EXISTS sso_configs (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id      INTEGER NOT NULL,
            provider    TEXT    NOT NULL DEFAULT '',
            entity_id   TEXT    NOT NULL DEFAULT '',
            sso_url     TEXT    NOT NULL DEFAULT '',
            certificate TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_sso_org ON sso_configs(org_id)")

    # ── Phase 6: IP Allowlist ──
    con.execute("""
        CREATE TABLE IF NOT EXISTS ip_allowlist (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id      INTEGER NOT NULL,
            cidr_range  TEXT    NOT NULL DEFAULT '',
            description TEXT    NOT NULL DEFAULT '',
            created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_ip_allowlist_org ON ip_allowlist(org_id)")

    # ── Phase 6: Organizations ──
    con.execute("""
        CREATE TABLE IF NOT EXISTS organizations (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT    NOT NULL,
            slug          TEXT    NOT NULL UNIQUE,
            owner_id      INTEGER,
            settings_json TEXT    NOT NULL DEFAULT '{}',
            created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_org_slug ON organizations(slug)")

    # ── Phase 6: Organization Members ──
    con.execute("""
        CREATE TABLE IF NOT EXISTS org_members (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id  INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            role    TEXT    NOT NULL DEFAULT 'member',
            UNIQUE(org_id, user_id)
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_org_members_org ON org_members(org_id)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_org_members_user ON org_members(user_id)")

    # ── Phase 6: Branding Config ──
    con.execute("""
        CREATE TABLE IF NOT EXISTS branding_configs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id          INTEGER NOT NULL UNIQUE,
            logo_url        TEXT    NOT NULL DEFAULT '',
            primary_color   TEXT    NOT NULL DEFAULT '#1a1a2e',
            secondary_color TEXT    NOT NULL DEFAULT '#16213e',
            app_name        TEXT    NOT NULL DEFAULT 'teb',
            favicon_url     TEXT    NOT NULL DEFAULT '',
            created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_branding_org ON branding_configs(org_id)")

    # ─── Phase 3: Saved Views ────────────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS saved_views (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT    NOT NULL,
            view_type    TEXT    NOT NULL DEFAULT 'list',
            filters_json TEXT    NOT NULL DEFAULT '{}',
            sort_json    TEXT    NOT NULL DEFAULT '{}',
            group_by     TEXT    NOT NULL DEFAULT '',
            created_at   TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_saved_views_user ON saved_views(user_id)")

    # ─── Phase 3: Dashboard Layouts ──────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS dashboard_layouts (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            name         TEXT    NOT NULL,
            widgets_json TEXT    NOT NULL DEFAULT '[]',
            created_at   TEXT    NOT NULL
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_dashboard_layouts_user ON dashboard_layouts(user_id)")

    # ─── Phase 3: Scheduled Reports ──────────────────────────────────────
    con.execute("""
        CREATE TABLE IF NOT EXISTS scheduled_reports (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            report_type     TEXT    NOT NULL DEFAULT 'progress',
            frequency       TEXT    NOT NULL DEFAULT 'weekly',
            recipients_json TEXT    NOT NULL DEFAULT '[]',
            created_at      TEXT    NOT NULL,
            last_sent_at    TEXT    NOT NULL DEFAULT ''
        )
    """)
    con.execute("CREATE INDEX IF NOT EXISTS idx_scheduled_reports_user ON scheduled_reports(user_id)")

    # ─── Safe column migrations (version, assigned_to) ───────────────────
    _safe_add_column(con, "goals", "version", "INTEGER NOT NULL DEFAULT 1")
    _safe_add_column(con, "tasks", "version", "INTEGER NOT NULL DEFAULT 1")
    _safe_add_column(con, "tasks", "assigned_to", "INTEGER DEFAULT NULL")

    # ─── Record current schema version ───────────────────────────────────
    _CURRENT_SCHEMA_VERSION = "2.0.0"
    row = con.execute(
        "SELECT version FROM schema_versions ORDER BY id DESC LIMIT 1"
    ).fetchone()
    last_version = row["version"] if row else None
    if last_version != _CURRENT_SCHEMA_VERSION:
        con.execute(
            "INSERT INTO schema_versions (version, description, applied_at) VALUES (?, ?, ?)",
            (_CURRENT_SCHEMA_VERSION, "Add schema_versions table, security headers, input validation, pagination, health probes", datetime.now(timezone.utc).isoformat()),
        )



def _safe_add_column(con: sqlite3.Connection, table: str, column: str, col_type: str) -> None:
    """Add a column to a table if it doesn't already exist."""
    cols = {row[1] for row in con.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_type}")


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

