"""Notification endpoints: list, mark read, count."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from teb import storage
from teb.routers.deps import require_user

router = APIRouter(prefix="/api/notifications", tags=["notifications"])

# Rate limiter injected from main.py
_check_api_rate_limit = None


def set_rate_limiter(fn) -> None:
    global _check_api_rate_limit
    _check_api_rate_limit = fn


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.get("")
async def list_notifications(
    request: Request,
    unread_only: bool = Query(False),
    limit: int = Query(50),
) -> list:
    """List notifications for the current user."""
    uid = require_user(request)
    if _check_api_rate_limit:
        _check_api_rate_limit(request)
    return [n.to_dict() for n in storage.list_user_notifications(uid, unread_only=unread_only, limit=limit)]


@router.post("/{notif_id}/read")
async def mark_read(notif_id: int, request: Request) -> dict:
    """Mark a single notification as read."""
    uid = require_user(request)
    if _check_api_rate_limit:
        _check_api_rate_limit(request)
    ok = storage.mark_notification_read(notif_id, uid)
    if not ok:
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"read": True}


@router.post("/read-all")
async def mark_all_read(request: Request) -> dict:
    """Mark all notifications as read."""
    uid = require_user(request)
    if _check_api_rate_limit:
        _check_api_rate_limit(request)
    count = storage.mark_all_notifications_read(uid)
    return {"marked": count}


@router.get("/count")
async def count_unread(request: Request) -> dict:
    """Get unread notification count."""
    uid = require_user(request)
    if _check_api_rate_limit:
        _check_api_rate_limit(request)
    return {"unread": storage.count_unread_notifications(uid)}
