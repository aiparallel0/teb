"""Generic CRUD operations for SQLite tables backed by TebModel dataclasses."""
from __future__ import annotations

import dataclasses
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Type, TypeVar

from teb.models import TebModel
from teb.storage.base import _conn, _with_retry

T = TypeVar("T", bound=TebModel)


class CrudTable:
    """Generic CRUD operations for a SQLite table backed by a TebModel dataclass.

    Usage::

        comments = CrudTable(TaskComment, "task_comments")
        new_comment = comments.create(comment_obj)
        all_comments = comments.list_by(task_id=5)
        comments.delete(comment_id)
    """

    def __init__(
        self,
        model_class: Type[T],
        table_name: str,
        *,
        id_field: str = "id",
        writable_fields: Optional[List[str]] = None,
        auto_timestamp_fields: Optional[List[str]] = None,
        default_order: str = "id DESC",
    ):
        self.model_class = model_class
        self.table_name = table_name
        self.id_field = id_field
        self.auto_timestamp_fields = auto_timestamp_fields or []
        self.default_order = default_order

        # Infer writable fields from dataclass (exclude id and auto timestamps)
        if writable_fields is not None:
            self.writable_fields = writable_fields
        else:
            all_fields = [f.name for f in dataclasses.fields(model_class)]
            exclude = {id_field} | set(self.auto_timestamp_fields)
            self.writable_fields = [f for f in all_fields if f not in exclude]

    # ── Create ────────────────────────────────────────────────────────────

    @_with_retry
    def create(self, obj: T) -> T:
        """Insert a new row and return the object with id and timestamps set."""
        now = datetime.now(timezone.utc).isoformat()
        columns = list(self.writable_fields)
        values = [self._get_db_value(obj, col) for col in columns]

        for ts_field in self.auto_timestamp_fields:
            if hasattr(obj, ts_field):
                columns.append(ts_field)
                values.append(now)

        placeholders = ", ".join("?" for _ in columns)
        col_names = ", ".join(columns)
        sql = f"INSERT INTO {self.table_name} ({col_names}) VALUES ({placeholders})"

        with _conn() as con:
            cur = con.execute(sql, values)
            setattr(obj, self.id_field, cur.lastrowid)

        for ts_field in self.auto_timestamp_fields:
            if hasattr(obj, ts_field):
                setattr(obj, ts_field, datetime.fromisoformat(now))

        return obj

    # ── Read ──────────────────────────────────────────────────────────────

    def get(self, id_val: Any) -> Optional[T]:
        """Fetch a single row by primary key."""
        sql = f"SELECT * FROM {self.table_name} WHERE {self.id_field} = ?"
        with _conn() as con:
            row = con.execute(sql, (id_val,)).fetchone()
        return self.model_class.from_row(row) if row else None

    def list_by(
        self,
        *,
        order: Optional[str] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        **filters: Any,
    ) -> List[T]:
        """List rows matching the given column filters."""
        clauses = []
        params: list = []
        for col, val in filters.items():
            clauses.append(f"{col} = ?")
            params.append(val)

        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        order_clause = f" ORDER BY {order or self.default_order}"

        sql = f"SELECT * FROM {self.table_name}{where}{order_clause}"

        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        with _conn() as con:
            rows = con.execute(sql, params).fetchall()
        return [self.model_class.from_row(r) for r in rows]

    # ── Update ────────────────────────────────────────────────────────────

    @_with_retry
    def update(self, id_val: Any, **fields: Any) -> Optional[T]:
        """Update specific columns on a row and return the updated object."""
        if not fields:
            return self.get(id_val)

        now = datetime.now(timezone.utc).isoformat()
        set_parts = []
        params: list = []
        for col, val in fields.items():
            if isinstance(val, bool):
                val = int(val)
            set_parts.append(f"{col} = ?")
            params.append(val)

        # Auto-update updated_at if present
        if "updated_at" in self.auto_timestamp_fields and "updated_at" not in fields:
            set_parts.append("updated_at = ?")
            params.append(now)

        params.append(id_val)
        sql = f"UPDATE {self.table_name} SET {', '.join(set_parts)} WHERE {self.id_field} = ?"

        with _conn() as con:
            con.execute(sql, params)

        return self.get(id_val)

    # ── Delete ────────────────────────────────────────────────────────────

    @_with_retry
    def delete(self, id_val: Any) -> None:
        """Delete a row by primary key."""
        sql = f"DELETE FROM {self.table_name} WHERE {self.id_field} = ?"
        with _conn() as con:
            con.execute(sql, (id_val,))

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _get_db_value(obj: Any, field_name: str) -> Any:
        """Convert a model attribute to a database-safe value."""
        val = getattr(obj, field_name)
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, datetime):
            return val.isoformat()
        return val
