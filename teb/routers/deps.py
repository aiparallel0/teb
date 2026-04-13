"""Shared dependencies for all API routers."""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from teb import auth, storage
from teb.models import Goal, Task

# ─── Pagination ───────────────────────────────────────────────────────────────

_DEFAULT_PAGE_SIZE = 50
_MAX_PAGE_SIZE = 100


def paginate(
    items: list,
    page: int = 1,
    per_page: int = _DEFAULT_PAGE_SIZE,
) -> Dict[str, Any]:
    """Apply pagination to a list and return a standardized paginated response."""
    page = max(1, page)
    per_page = max(1, min(per_page, _MAX_PAGE_SIZE))
    total = len(items)
    pages = max(1, (total + per_page - 1) // per_page)
    start = (page - 1) * per_page
    end = start + per_page
    return {
        "data": items[start:end],
        "pagination": {
            "page": page,
            "per_page": per_page,
            "total": total,
            "pages": pages,
        },
    }


# ─── Error response ──────────────────────────────────────────────────────────

def error_response(
    status_code: int,
    code: str,
    message: str,
    details: Optional[list] = None,
    request_id: Optional[str] = None,
) -> JSONResponse:
    """Return a standardized error response."""
    body: Dict[str, Any] = {
        "error": {
            "code": code,
            "message": message,
        }
    }
    if details:
        body["error"]["details"] = details
    if request_id:
        body["error"]["request_id"] = request_id
    return JSONResponse(status_code=status_code, content=body)


# ─── Auth helpers ─────────────────────────────────────────────────────────────

def get_user_id(request: Request) -> Optional[int]:
    """Extract user_id from the request's Authorization header (Bearer token)."""
    header = request.headers.get("authorization", "")
    if header.startswith("Bearer "):
        token = header[7:]
        return auth.decode_token(token)
    return None


def require_user(request: Request) -> int:
    """Extract user_id or raise 401."""
    uid = get_user_id(request)
    if uid is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    return uid


def require_admin(request: Request) -> int:
    """Extract user_id and verify admin role, or raise 401/403."""
    uid = require_user(request)
    user = storage.get_user(uid)
    if not user or user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return uid


def get_goal_for_user(goal_id: int, user_id: int) -> Goal:
    """Fetch a goal and verify the requesting user owns it (or it has no owner)."""
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    if goal.user_id is not None and goal.user_id != user_id:
        raise HTTPException(status_code=403, detail="Not authorized")
    return goal


def get_task_for_user(task_id: int, user_id: int) -> Task:
    """Fetch a task and verify the requesting user owns its goal."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.goal_id is not None:
        goal = storage.get_goal(task.goal_id)
        if goal and goal.user_id is not None and goal.user_id != user_id:
            raise HTTPException(status_code=403, detail="Not authorized")
    return task
