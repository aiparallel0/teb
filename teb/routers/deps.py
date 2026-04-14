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


def get_collaborator_role(goal_id: int, user_id: int) -> Optional[str]:
    """Return the collaborator role for a user on a goal, or None."""
    collabs = storage.list_collaborators(goal_id)
    for c in collabs:
        if c.user_id == user_id:
            return c.role
    return None


def get_goal_for_user(goal_id: int, user_id: int, require_role: Optional[str] = None) -> Goal:
    """Fetch a goal and verify the requesting user owns it or is a collaborator.

    If ``require_role`` is set, the user must be the owner **or** have that
    collaborator role (or higher).  Role hierarchy: admin > editor > viewer.
    """
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Owner always has full access
    if goal.user_id is None or goal.user_id == user_id:
        return goal

    # Check collaborator access
    collab_role = get_collaborator_role(goal_id, user_id)
    if collab_role is None:
        raise HTTPException(status_code=403, detail="Not authorized")

    if require_role:
        _ROLE_LEVEL = {"viewer": 0, "editor": 1, "admin": 2}
        needed = _ROLE_LEVEL.get(require_role, 0)
        actual = _ROLE_LEVEL.get(collab_role, 0)
        if actual < needed:
            raise HTTPException(status_code=403, detail=f"Requires {require_role} access")

    return goal


def get_task_for_user(task_id: int, user_id: int, require_role: Optional[str] = None) -> Task:
    """Fetch a task and verify the requesting user owns its goal or is a collaborator."""
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.goal_id is not None:
        get_goal_for_user(task.goal_id, user_id, require_role=require_role)
    return task


# ─── Rate limiting ────────────────────────────────────────────────────────────
# The actual rate-limit implementation lives in main.py (in-memory buckets).
# Routers call this via the setter-injected function.

_check_api_rate_limit_fn = None


def set_api_rate_limiter(fn):
    """Inject the rate-limit function from main.py."""
    global _check_api_rate_limit_fn
    _check_api_rate_limit_fn = fn


def check_api_rate_limit(request: "Request") -> None:
    """Apply the general API rate limit. No-op if not configured."""
    if _check_api_rate_limit_fn:
        _check_api_rate_limit_fn(request)
