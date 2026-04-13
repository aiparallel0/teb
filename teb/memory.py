"""
Execution Memory — persistent memory for API calls made by the executor.

Stores every API call with endpoint, payload hash, response code, latency,
and success/failure. Before making a call, the executor can check memory
to see if similar calls have succeeded or failed recently.

If the last N calls to an endpoint all failed, auto-escalate to human
review instead of retrying blindly.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from teb import storage

logger = logging.getLogger(__name__)

# ─── Configuration ────────────────────────────────────────────────────────────

FAILURE_THRESHOLD = 3   # consecutive failures before auto-escalation
MEMORY_LOOKBACK = 50    # how many recent calls to check per endpoint


# ─── Schema ───────────────────────────────────────────────────────────────────

def _ensure_memory_tables() -> None:
    """Create execution memory tables if they don't exist."""
    with storage._conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS execution_memory (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                goal_id       INTEGER,
                task_id       INTEGER,
                endpoint      TEXT    NOT NULL,
                method        TEXT    NOT NULL DEFAULT 'GET',
                payload_hash  TEXT    NOT NULL DEFAULT '',
                status_code   INTEGER,
                success       INTEGER NOT NULL DEFAULT 0,
                latency_ms    REAL    NOT NULL DEFAULT 0.0,
                error_message TEXT    DEFAULT '',
                response_hash TEXT    DEFAULT '',
                created_at    TEXT    NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_em_endpoint ON execution_memory(endpoint);
            CREATE INDEX IF NOT EXISTS idx_em_goal ON execution_memory(goal_id);
            CREATE INDEX IF NOT EXISTS idx_em_task ON execution_memory(task_id);
            CREATE INDEX IF NOT EXISTS idx_em_created ON execution_memory(created_at DESC);
        """)


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    """A single API call record in execution memory."""
    endpoint: str
    method: str = "GET"
    payload_hash: str = ""
    status_code: Optional[int] = None
    success: bool = False
    latency_ms: float = 0.0
    error_message: str = ""
    response_hash: str = ""
    goal_id: Optional[int] = None
    task_id: Optional[int] = None
    id: Optional[int] = None
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "endpoint": self.endpoint,
            "method": self.method,
            "status_code": self.status_code,
            "success": self.success,
            "latency_ms": round(self.latency_ms, 1),
            "error_message": self.error_message,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "created_at": self.created_at,
        }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _hash_payload(payload: Any) -> str:
    """Create a stable hash of a request payload for deduplication."""
    if payload is None:
        return ""
    try:
        canonical = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode()).hexdigest()[:16]
    except (TypeError, ValueError):
        return hashlib.sha256(str(payload).encode()).hexdigest()[:16]


# ─── Core operations ─────────────────────────────────────────────────────────

def record_call(
    endpoint: str,
    method: str = "GET",
    payload: Any = None,
    status_code: Optional[int] = None,
    success: bool = False,
    latency_ms: float = 0.0,
    error_message: str = "",
    response_body: Any = None,
    goal_id: Optional[int] = None,
    task_id: Optional[int] = None,
) -> int:
    """Record an API call in execution memory. Returns the record ID."""
    _ensure_memory_tables()
    now = datetime.now(timezone.utc).isoformat()
    payload_hash = _hash_payload(payload)
    response_hash = _hash_payload(response_body) if response_body else ""

    with storage._conn() as con:
        cur = con.execute(
            """INSERT INTO execution_memory
            (goal_id, task_id, endpoint, method, payload_hash, status_code,
             success, latency_ms, error_message, response_hash, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (goal_id, task_id, endpoint, method, payload_hash, status_code,
             1 if success else 0, latency_ms, error_message or "", response_hash, now),
        )
        return cur.lastrowid  # type: ignore[return-value]


@dataclass
class ExecutionAdvice:
    """Advice from execution memory about whether to proceed with a call."""
    proceed: bool = True
    reason: str = ""
    cached_status: Optional[int] = None
    recent_success_rate: float = 1.0
    avg_latency_ms: float = 0.0
    consecutive_failures: int = 0

    def to_dict(self) -> dict:
        return {
            "proceed": self.proceed,
            "reason": self.reason,
            "cached_status": self.cached_status,
            "recent_success_rate": round(self.recent_success_rate, 2),
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "consecutive_failures": self.consecutive_failures,
        }


def should_execute(
    endpoint: str,
    method: str = "GET",
    payload: Any = None,
) -> ExecutionAdvice:
    """
    Check execution memory before making an API call.

    Returns advice on whether to proceed:
    - If the last N calls to this endpoint all failed, recommend escalation.
    - If a recent identical call succeeded, provide the cached status.
    - Always provides success rate and latency stats.
    """
    _ensure_memory_tables()

    with storage._conn() as con:
        # Get recent calls to this endpoint
        rows = con.execute(
            """SELECT status_code, success, latency_ms, error_message, payload_hash
            FROM execution_memory
            WHERE endpoint = ? AND method = ?
            ORDER BY created_at DESC
            LIMIT ?""",
            (endpoint, method, MEMORY_LOOKBACK),
        ).fetchall()

    if not rows:
        return ExecutionAdvice(
            proceed=True,
            reason="No previous calls recorded for this endpoint.",
        )

    # Calculate stats
    total = len(rows)
    successes = sum(1 for r in rows if r["success"])
    success_rate = successes / total if total > 0 else 0.0
    avg_latency = sum(r["latency_ms"] for r in rows) / total if total > 0 else 0.0

    # Count consecutive failures from most recent
    consecutive_failures = 0
    for r in rows:
        if not r["success"]:
            consecutive_failures += 1
        else:
            break

    # Check if we should escalate
    if consecutive_failures >= FAILURE_THRESHOLD:
        last_error = rows[0]["error_message"] if rows else ""
        return ExecutionAdvice(
            proceed=False,
            reason=f"Last {consecutive_failures} calls to {endpoint} failed. "
                   f"Latest error: {last_error}. Escalating to human review.",
            recent_success_rate=success_rate,
            avg_latency_ms=avg_latency,
            consecutive_failures=consecutive_failures,
        )

    # Check for cached identical call
    payload_hash = _hash_payload(payload)
    if payload_hash:
        for r in rows:
            if r["payload_hash"] == payload_hash and r["success"]:
                return ExecutionAdvice(
                    proceed=True,
                    reason="Identical call succeeded recently.",
                    cached_status=r["status_code"],
                    recent_success_rate=success_rate,
                    avg_latency_ms=avg_latency,
                    consecutive_failures=consecutive_failures,
                )

    return ExecutionAdvice(
        proceed=True,
        reason=f"Endpoint has {success_rate:.0%} success rate over {total} recent calls.",
        recent_success_rate=success_rate,
        avg_latency_ms=avg_latency,
        consecutive_failures=consecutive_failures,
    )


def get_memory_for_goal(goal_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    """Get execution memory entries for a specific goal."""
    _ensure_memory_tables()

    with storage._conn() as con:
        rows = con.execute(
            """SELECT id, goal_id, task_id, endpoint, method, status_code,
                      success, latency_ms, error_message, created_at
            FROM execution_memory
            WHERE goal_id = ?
            ORDER BY created_at DESC
            LIMIT ?""",
            (goal_id, limit),
        ).fetchall()

    return [dict(r) for r in rows]


def get_memory_stats(goal_id: Optional[int] = None) -> Dict[str, Any]:
    """Get aggregate statistics about execution memory."""
    _ensure_memory_tables()

    with storage._conn() as con:
        if goal_id:
            total = con.execute(
                "SELECT COUNT(*) FROM execution_memory WHERE goal_id = ?", (goal_id,)
            ).fetchone()[0]
            successes = con.execute(
                "SELECT COUNT(*) FROM execution_memory WHERE goal_id = ? AND success = 1", (goal_id,)
            ).fetchone()[0]
            avg_latency = con.execute(
                "SELECT COALESCE(AVG(latency_ms), 0) FROM execution_memory WHERE goal_id = ?", (goal_id,)
            ).fetchone()[0]
        else:
            total = con.execute("SELECT COUNT(*) FROM execution_memory").fetchone()[0]
            successes = con.execute("SELECT COUNT(*) FROM execution_memory WHERE success = 1").fetchone()[0]
            avg_latency = con.execute("SELECT COALESCE(AVG(latency_ms), 0) FROM execution_memory").fetchone()[0]

        # Most called endpoints
        top_endpoints = con.execute(
            "SELECT endpoint, COUNT(*) as calls, "
            "SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) as successes "
            "FROM execution_memory GROUP BY endpoint ORDER BY calls DESC LIMIT 10"
        ).fetchall()

    return {
        "total_calls": total,
        "total_successes": successes,
        "success_rate": round(successes / max(total, 1), 2),
        "avg_latency_ms": round(avg_latency, 1),
        "top_endpoints": [
            {"endpoint": r["endpoint"], "calls": r["calls"],
             "success_rate": round(r["successes"] / max(r["calls"], 1), 2)}
            for r in top_endpoints
        ],
    }
