"""
Success Path Graph — teb's compounding learning moat.

Each unique task-title becomes a node. Each "task A completed before task B"
becomes a directed edge with weight = frequency. When decomposing a new goal,
we query the graph for the highest-weight path, providing AI with "proven
execution sequences" so that teb gets better at decomposing goals the more
goals are completed on the platform.
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from teb import storage

logger = logging.getLogger(__name__)


# ─── Data model ───────────────────────────────────────────────────────────────

@dataclass
class GraphEdge:
    """A directed edge in the success graph: from_task → to_task."""
    from_title: str
    to_title: str
    weight: int = 1          # frequency (how many times this sequence was observed)
    avg_minutes: float = 0.0  # average actual minutes for the from_task
    goal_type: str = ""       # template category

    def to_dict(self) -> dict:
        return {
            "from": self.from_title,
            "to": self.to_title,
            "weight": self.weight,
            "avg_minutes": round(self.avg_minutes, 1),
            "goal_type": self.goal_type,
        }


@dataclass
class GraphNode:
    """A node in the success graph representing a unique task title."""
    title: str
    frequency: int = 0        # how many times this task appeared in completed goals
    avg_minutes: float = 0.0  # average estimated_minutes
    success_rate: float = 0.0  # fraction of times this task was completed (vs skipped)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "frequency": self.frequency,
            "avg_minutes": round(self.avg_minutes, 1),
            "success_rate": round(self.success_rate, 2),
        }


# ─── Schema creation ─────────────────────────────────────────────────────────

def _ensure_graph_tables() -> None:
    """Create success graph tables if they don't exist."""
    with storage._conn() as con:
        con.executescript("""
            CREATE TABLE IF NOT EXISTS success_graph_nodes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                title       TEXT    NOT NULL,
                goal_type   TEXT    NOT NULL DEFAULT '',
                frequency   INTEGER NOT NULL DEFAULT 0,
                avg_minutes REAL    NOT NULL DEFAULT 0.0,
                success_rate REAL   NOT NULL DEFAULT 0.0,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                UNIQUE(title, goal_type)
            );
            CREATE INDEX IF NOT EXISTS idx_sgn_goal_type ON success_graph_nodes(goal_type);

            CREATE TABLE IF NOT EXISTS success_graph_edges (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                from_title  TEXT    NOT NULL,
                to_title    TEXT    NOT NULL,
                goal_type   TEXT    NOT NULL DEFAULT '',
                weight      INTEGER NOT NULL DEFAULT 1,
                avg_minutes REAL    NOT NULL DEFAULT 0.0,
                created_at  TEXT    NOT NULL,
                updated_at  TEXT    NOT NULL,
                UNIQUE(from_title, to_title, goal_type)
            );
            CREATE INDEX IF NOT EXISTS idx_sge_goal_type ON success_graph_edges(goal_type);
            CREATE INDEX IF NOT EXISTS idx_sge_from ON success_graph_edges(from_title, goal_type);
        """)


# ─── Graph updates ────────────────────────────────────────────────────────────

def update_graph_from_completed_goal(
    goal_type: str,
    completed_tasks: List[Dict[str, Any]],
) -> int:
    """
    Update the success graph with edges from a completed goal's task sequence.

    Args:
        goal_type: The template/category name for this goal.
        completed_tasks: List of task dicts with at least 'title', 'status',
                        'estimated_minutes', 'order_index'. Sorted by completion order.

    Returns:
        Number of edges updated.
    """
    _ensure_graph_tables()

    # Filter to completed/skipped tasks, sorted by order
    tasks = sorted(completed_tasks, key=lambda t: t.get("order_index", 0))
    if len(tasks) < 2:
        return 0

    now = datetime.now(timezone.utc).isoformat()
    edges_updated = 0

    with storage._conn() as con:
        # Update nodes
        for t in tasks:
            title = t.get("title", "").strip()
            if not title:
                continue
            minutes = t.get("estimated_minutes", 30)
            is_done = t.get("status") == "done"

            existing = con.execute(
                "SELECT id, frequency, avg_minutes, success_rate FROM success_graph_nodes "
                "WHERE title = ? AND goal_type = ?",
                (title, goal_type),
            ).fetchone()

            if existing:
                new_freq = existing["frequency"] + 1
                # Running average for minutes
                new_avg = (existing["avg_minutes"] * existing["frequency"] + minutes) / new_freq
                # Running average for success rate
                new_rate = (existing["success_rate"] * existing["frequency"] + (1.0 if is_done else 0.0)) / new_freq
                con.execute(
                    "UPDATE success_graph_nodes SET frequency = ?, avg_minutes = ?, "
                    "success_rate = ?, updated_at = ? WHERE id = ?",
                    (new_freq, new_avg, new_rate, now, existing["id"]),
                )
            else:
                con.execute(
                    "INSERT INTO success_graph_nodes (title, goal_type, frequency, avg_minutes, "
                    "success_rate, created_at, updated_at) VALUES (?, ?, 1, ?, ?, ?, ?)",
                    (title, goal_type, minutes, 1.0 if is_done else 0.0, now, now),
                )

        # Update edges (sequential pairs)
        for i in range(len(tasks) - 1):
            from_title = tasks[i].get("title", "").strip()
            to_title = tasks[i + 1].get("title", "").strip()
            if not from_title or not to_title:
                continue

            from_minutes = tasks[i].get("estimated_minutes", 30)

            existing = con.execute(
                "SELECT id, weight, avg_minutes FROM success_graph_edges "
                "WHERE from_title = ? AND to_title = ? AND goal_type = ?",
                (from_title, to_title, goal_type),
            ).fetchone()

            if existing:
                new_weight = existing["weight"] + 1
                new_avg = (existing["avg_minutes"] * existing["weight"] + from_minutes) / new_weight
                con.execute(
                    "UPDATE success_graph_edges SET weight = ?, avg_minutes = ?, updated_at = ? WHERE id = ?",
                    (new_weight, new_avg, now, existing["id"]),
                )
            else:
                con.execute(
                    "INSERT INTO success_graph_edges (from_title, to_title, goal_type, weight, "
                    "avg_minutes, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
                    (from_title, to_title, goal_type, from_minutes, now, now),
                )
            edges_updated += 1

    logger.info("Updated success graph for %s: %d edges", goal_type, edges_updated)
    return edges_updated


# ─── Graph queries ────────────────────────────────────────────────────────────

def get_best_path(goal_type: str, max_steps: int = 15) -> List[Dict[str, Any]]:
    """
    Find the highest-weight path through the success graph for a goal type.

    Uses a greedy approach: start from the node with no incoming high-weight
    edges (START-like), then follow the highest-weight outgoing edge at each step.

    Returns a list of step dicts with title, avg_minutes, frequency, and
    confidence (edge weight / max weight).
    """
    _ensure_graph_tables()

    with storage._conn() as con:
        edges = con.execute(
            "SELECT from_title, to_title, weight, avg_minutes FROM success_graph_edges "
            "WHERE goal_type = ? ORDER BY weight DESC",
            (goal_type,),
        ).fetchall()

        nodes = con.execute(
            "SELECT title, frequency, avg_minutes, success_rate FROM success_graph_nodes "
            "WHERE goal_type = ?",
            (goal_type,),
        ).fetchall()

    if not edges:
        return []

    # Build adjacency map
    adj: Dict[str, List[Tuple[str, int, float]]] = defaultdict(list)
    incoming: Dict[str, int] = defaultdict(int)
    max_weight = 1

    for e in edges:
        adj[e["from_title"]].append((e["to_title"], e["weight"], e["avg_minutes"]))
        incoming[e["to_title"]] += e["weight"]
        max_weight = max(max_weight, e["weight"])

    # Node info lookup
    node_info = {}
    for n in nodes:
        node_info[n["title"]] = {
            "frequency": n["frequency"],
            "avg_minutes": n["avg_minutes"],
            "success_rate": n["success_rate"],
        }

    # Find start node: node with outgoing edges but minimal incoming weight
    all_from = set(adj.keys())
    all_to = set(incoming.keys())
    start_candidates = all_from - all_to
    if not start_candidates:
        # Fallback: node with least incoming weight
        start_candidates = all_from
    
    start = min(start_candidates, key=lambda n: incoming.get(n, 0))

    # Greedy path traversal
    path = []
    visited = set()
    current = start

    while current and len(path) < max_steps:
        info = node_info.get(current, {})
        path.append({
            "title": current,
            "avg_minutes": info.get("avg_minutes", 30),
            "frequency": info.get("frequency", 0),
            "success_rate": info.get("success_rate", 0),
        })
        visited.add(current)

        # Find best next step
        neighbors = adj.get(current, [])
        best_next = None
        best_weight = 0
        for to_title, weight, _ in neighbors:
            if to_title not in visited and weight > best_weight:
                best_next = to_title
                best_weight = weight
        
        if best_next:
            path[-1]["confidence"] = round(best_weight / max_weight, 2)
        
        current = best_next

    return path


def get_top_paths(goal_type: str, top_k: int = 3) -> List[List[Dict[str, Any]]]:
    """
    Get the top-K distinct execution paths for a goal type.

    Returns multiple paths by varying the starting node, useful for
    injecting into AI prompts as "proven execution sequences."
    """
    _ensure_graph_tables()

    with storage._conn() as con:
        nodes = con.execute(
            "SELECT title, frequency FROM success_graph_nodes "
            "WHERE goal_type = ? ORDER BY frequency DESC LIMIT ?",
            (goal_type, top_k * 2),
        ).fetchall()

    if not nodes:
        return []

    # Get the best path (always include)
    best = get_best_path(goal_type)
    if not best:
        return []

    paths = [best]

    # Generate alternative paths by exploring different start points
    # (simplified: just return the single best path for now)
    # Future: implement k-shortest-paths algorithm

    return paths[:top_k]


def get_graph_stats(goal_type: Optional[str] = None) -> Dict[str, Any]:
    """Get statistics about the success graph."""
    _ensure_graph_tables()

    with storage._conn() as con:
        if goal_type:
            node_count = con.execute(
                "SELECT COUNT(*) FROM success_graph_nodes WHERE goal_type = ?",
                (goal_type,),
            ).fetchone()[0]
            edge_count = con.execute(
                "SELECT COUNT(*) FROM success_graph_edges WHERE goal_type = ?",
                (goal_type,),
            ).fetchone()[0]
            total_weight = con.execute(
                "SELECT COALESCE(SUM(weight), 0) FROM success_graph_edges WHERE goal_type = ?",
                (goal_type,),
            ).fetchone()[0]
        else:
            node_count = con.execute("SELECT COUNT(*) FROM success_graph_nodes").fetchone()[0]
            edge_count = con.execute("SELECT COUNT(*) FROM success_graph_edges").fetchone()[0]
            total_weight = con.execute("SELECT COALESCE(SUM(weight), 0) FROM success_graph_edges").fetchone()[0]

        goal_types = [r[0] for r in con.execute(
            "SELECT DISTINCT goal_type FROM success_graph_nodes ORDER BY goal_type"
        ).fetchall()]

    return {
        "nodes": node_count,
        "edges": edge_count,
        "total_observations": total_weight,
        "goal_types": goal_types,
    }
