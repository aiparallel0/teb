"""
Semantic Search Across All Entities (WP-05).

Unified search using SQLite FTS5 with LIKE fallback.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from teb import storage

logger = logging.getLogger(__name__)


def init_search_index() -> None:
    db_path = storage._db_path()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        con.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
                entity_type, entity_id UNINDEXED, title, content,
                created_at UNINDEXED, user_id UNINDEXED
            )
        """)
        con.commit()
    finally:
        con.close()


def reindex_all(user_id: Optional[int] = None) -> Dict[str, int]:
    db_path = storage._db_path()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    counts: Dict[str, int] = {}
    try:
        if user_id:
            con.execute("DELETE FROM search_index WHERE user_id = ?", (str(user_id),))
        else:
            con.execute("DELETE FROM search_index")
        if user_id:
            goals = con.execute("SELECT * FROM goals WHERE user_id = ?", (user_id,)).fetchall()
        else:
            goals = con.execute("SELECT * FROM goals").fetchall()
        for g in goals:
            con.execute(
                "INSERT INTO search_index (entity_type, entity_id, title, content, created_at, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("goal", str(g["id"]), g["title"], g["description"] or "", g["created_at"], str(g.get("user_id") or "")),
            )
        counts["goals"] = len(goals)
        if user_id:
            tasks = con.execute("SELECT t.* FROM tasks t JOIN goals g ON t.goal_id = g.id WHERE g.user_id = ?", (user_id,)).fetchall()
        else:
            tasks = con.execute("SELECT * FROM tasks").fetchall()
        for t in tasks:
            con.execute(
                "INSERT INTO search_index (entity_type, entity_id, title, content, created_at, user_id) VALUES (?, ?, ?, ?, ?, ?)",
                ("task", str(t["id"]), t["title"], t["description"] or "", t["created_at"], str(user_id or "")),
            )
        counts["tasks"] = len(tasks)
        con.commit()
    finally:
        con.close()
    return counts


def quick_search(query: str, user_id: Optional[int] = None, limit: int = 20, semantic: bool = False) -> List[Dict[str, Any]]:
    if not query or not query.strip():
        return []
    # Fallback: LIKE search across goals and tasks
    results = []
    like = f"%{query}%"
    db_path = storage._db_path()
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        sql = "SELECT id, title, description, 'goal' as entity_type FROM goals WHERE title LIKE ? OR description LIKE ?"
        params: list = [like, like]
        if user_id:
            sql += " AND user_id = ?"
            params.append(user_id)
        sql += " LIMIT ?"
        params.append(limit)
        for row in con.execute(sql, params).fetchall():
            results.append({"entity_type": "goal", "entity_id": row["id"], "title": row["title"],
                            "snippet": (row["description"] or "")[:200], "rank": 0})
        sql2 = "SELECT id, title, description, 'task' as entity_type FROM tasks WHERE title LIKE ? OR description LIKE ? LIMIT ?"
        for row in con.execute(sql2, [like, like, limit]).fetchall():
            results.append({"entity_type": "task", "entity_id": row["id"], "title": row["title"],
                            "snippet": (row["description"] or "")[:200], "rank": 0})
    finally:
        con.close()

    results = results[:limit]

    # If semantic=True, try AI-based re-ranking
    if semantic and results:
        results = _semantic_rerank(query, results)

    return results


def _semantic_rerank(query: str, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Re-rank search results using AI for semantic relevance."""
    try:
        from teb import config
        if not config.get_ai_provider():
            return results

        from teb.ai_client import ai_chat
        import json

        items = [{"title": r["title"], "snippet": r["snippet"][:100]} for r in results[:20]]
        system_prompt = """You are a search relevance ranker. Given a query and search results,
return a JSON array of indices (0-based) ordered by semantic relevance to the query.
Only return the JSON array of integers, nothing else."""

        user_prompt = f"Query: {query}\nResults: {json.dumps(items)}"
        response = ai_chat(system_prompt, user_prompt, json_mode=True)
        indices = json.loads(response)

        if isinstance(indices, list) and all(isinstance(i, int) for i in indices):
            reranked = []
            seen = set()
            for idx in indices:
                if 0 <= idx < len(results) and idx not in seen:
                    r = results[idx].copy()
                    r["rank"] = len(reranked)
                    reranked.append(r)
                    seen.add(idx)
            # Add any results not in the reranked list
            for i, r in enumerate(results):
                if i not in seen:
                    r_copy = r.copy()
                    r_copy["rank"] = len(reranked)
                    reranked.append(r_copy)
            return reranked
    except Exception:
        pass

    return results
