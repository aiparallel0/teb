"""Router for financial endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import decomposer
from teb import messaging
from teb import payments
from teb.models import (
    SpendingBudget, SpendingRequest, Task,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["financial"])


# ─── Schemas ──────────────────────────────────────────────────────────────────

class BudgetCreate(BaseModel):
    goal_id: int
    daily_limit: float = Field(50.0, ge=0, le=1000000)
    total_limit: float = Field(500.0, ge=0, le=10000000)
    category: str = Field("general", max_length=100)
    require_approval: bool = True
    autopilot_enabled: bool = False
    autopilot_threshold: float = Field(50.0, ge=0, le=1000000)



class BudgetUpdate(BaseModel):
    daily_limit: Optional[float] = Field(None, ge=0, le=1000000)
    total_limit: Optional[float] = Field(None, ge=0, le=10000000)
    require_approval: Optional[bool] = None
    autopilot_enabled: Optional[bool] = None
    autopilot_threshold: Optional[float] = Field(None, ge=0, le=1000000)



class SpendingRequestCreate(BaseModel):
    task_id: int
    amount: float = Field(..., ge=0, le=1000000)
    description: str = Field("", max_length=1000)
    service: str = Field("", max_length=200)
    currency: str = Field("USD", max_length=10)



class SpendingAction(BaseModel):
    action: str = Field(..., description="approve or deny")
    reason: str = Field("", max_length=1000)

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("approve", "deny"):
            raise ValueError("action must be 'approve' or 'deny'")
        return v




# ─── Financial Execution Pipeline ───────────────────────────────────────────

@router.post("/api/budgets", status_code=201)
async def create_budget(body: BudgetCreate, request: Request):
    """Create a spending budget for a goal."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(body.goal_id, uid)

    if body.daily_limit < 0 or body.total_limit < 0:
        raise HTTPException(status_code=422, detail="Limits must be non-negative")

    valid_categories = {"general", "hosting", "domain", "marketing", "tools", "services"}
    if body.category not in valid_categories:
        raise HTTPException(status_code=422, detail=f"category must be one of {valid_categories}")

    budget = SpendingBudget(
        goal_id=body.goal_id,
        daily_limit=body.daily_limit,
        total_limit=body.total_limit,
        category=body.category,
        require_approval=body.require_approval,
        autopilot_enabled=body.autopilot_enabled,
        autopilot_threshold=body.autopilot_threshold,
    )
    budget = storage.create_spending_budget(budget)
    return budget.to_dict()


@router.get("/api/goals/{goal_id}/budgets")
async def list_budgets(goal_id: int, request: Request):
    """List all spending budgets for a goal."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    budgets = storage.list_spending_budgets(goal_id)
    return [b.to_dict() for b in budgets]


@router.patch("/api/budgets/{budget_id}")
async def update_budget(budget_id: int, body: BudgetUpdate, request: Request):
    """Update a spending budget's limits or approval requirement."""
    uid = deps.require_user(request)
    budget = storage.get_spending_budget(budget_id)
    if not budget:
        raise HTTPException(status_code=404, detail="Budget not found")
    if budget.goal_id is not None:
        deps.get_goal_for_user(budget.goal_id, uid)  # ownership check

    if body.daily_limit is not None:
        if body.daily_limit < 0:
            raise HTTPException(status_code=422, detail="daily_limit must be non-negative")
        budget.daily_limit = body.daily_limit
    if body.total_limit is not None:
        if body.total_limit < 0:
            raise HTTPException(status_code=422, detail="total_limit must be non-negative")
        budget.total_limit = body.total_limit
    if body.require_approval is not None:
        budget.require_approval = body.require_approval
    if body.autopilot_enabled is not None:
        budget.autopilot_enabled = body.autopilot_enabled
    if body.autopilot_threshold is not None:
        if body.autopilot_threshold < 0:
            raise HTTPException(status_code=422, detail="autopilot_threshold must be non-negative")
        budget.autopilot_threshold = body.autopilot_threshold

    budget = storage.update_spending_budget(budget)
    return budget.to_dict()


@router.post("/api/spending/request", status_code=201)
async def create_spending_request(body: SpendingRequestCreate, request: Request):
    """
    Request to spend money on a task.

    Validates against the goal's budget limits. If the budget requires
    approval, the request is created as 'pending'. If no approval is
    needed and the amount is within limits, it's auto-approved.
    """
    uid = deps.require_user(request)
    task = storage.get_task(body.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if task.goal_id is not None:
        deps.get_goal_for_user(task.goal_id, uid)  # ownership check

    if body.amount <= 0:
        raise HTTPException(status_code=422, detail="amount must be positive")

    # Find applicable budget
    category = _guess_spending_category(body.service)
    budget = storage.find_spending_budget(task.goal_id, category)
    if not budget:
        raise HTTPException(
            status_code=404,
            detail=f"No budget found for goal {task.goal_id}. Create one first via POST /api/budgets",
        )

    # Check-on-request daily reset
    budget = storage.maybe_reset_daily_spending(budget)

    # Validate against limits
    validation = decomposer.validate_spending(
        body.amount, budget.daily_limit, budget.total_limit,
        budget.spent_today, budget.spent_total,
    )

    if not validation["allowed"]:
        raise HTTPException(status_code=422, detail=validation["reason"])

    # Create the spending request
    initial_status = "pending" if budget.require_approval else "approved"
    req = SpendingRequest(
        task_id=body.task_id,
        budget_id=budget.id,  # type: ignore[arg-type]
        amount=body.amount,
        currency=body.currency,
        description=body.description,
        service=body.service,
        status=initial_status,
    )
    req = storage.create_spending_request(req)

    # If auto-approved, update budget spending
    if initial_status == "approved":
        budget.spent_today += body.amount
        budget.spent_total += body.amount
        storage.update_spending_budget(budget)

    # Notify about spending request (if pending approval)
    if initial_status == "pending":
        messaging.send_notification("spending_request", {
            "request_id": req.id,
            "amount": req.amount,
            "description": req.description,
            "service": req.service,
            "task_title": task.title,
        })

    return {
        "request": req.to_dict(),
        "budget_remaining": {
            "daily": max(0, budget.daily_limit - budget.spent_today),
            "total": max(0, budget.total_limit - budget.spent_total),
        },
        "auto_approved": initial_status == "approved",
    }


@router.post("/api/spending/{request_id}/action")
async def action_spending_request(request_id: int, body: SpendingAction, request: Request):
    """Approve or deny a pending spending request."""
    uid = deps.require_user(request)
    req = storage.get_spending_request(request_id)
    if not req:
        raise HTTPException(status_code=404, detail="Spending request not found")

    # Verify ownership: the requesting user must own the task's goal
    task = storage.get_task(req.task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    deps.get_task_for_user(task.id, uid)

    if req.status != "pending":
        raise HTTPException(status_code=409, detail=f"Request is already {req.status}")

    valid_actions = {"approve", "deny"}
    if body.action not in valid_actions:
        raise HTTPException(status_code=422, detail=f"action must be one of {valid_actions}")

    if body.action == "approve":
        req.status = "approved"
        # Update budget
        budget = storage.get_spending_budget(req.budget_id)
        if budget:
            budget.spent_today += req.amount
            budget.spent_total += req.amount
            storage.update_spending_budget(budget)
        messaging.send_notification("spending_approved", {
            "amount": req.amount,
            "description": req.description,
        })
    else:
        req.status = "denied"
        req.denial_reason = body.reason
        messaging.send_notification("spending_denied", {
            "amount": req.amount,
            "description": req.description,
            "reason": body.reason,
        })

    storage.update_spending_request(req)
    return req.to_dict()


@router.get("/api/goals/{goal_id}/spending")
async def list_goal_spending(goal_id: int, request: Request, status: Optional[str] = Query(default=None)):
    """List all spending requests for a goal's tasks."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    all_requests: list[dict] = []
    for task in tasks:
        if task.id is not None:
            reqs = storage.list_spending_requests(task_id=task.id, status=status)
            all_requests.extend(r.to_dict() for r in reqs)
    return all_requests


def _guess_spending_category(service: str) -> str:
    """Guess the spending category from a service name."""
    service_lower = service.lower()
    if any(w in service_lower for w in ("namecheap", "godaddy", "domain", "cloudflare")):
        return "domain"
    if any(w in service_lower for w in ("vercel", "aws", "heroku", "digitalocean", "hosting")):
        return "hosting"
    if any(w in service_lower for w in ("google ads", "facebook ads", "twitter ads", "marketing", "ads")):
        return "marketing"
    if any(w in service_lower for w in ("github", "openai", "tool", "software", "saas")):
        return "tools"
    if any(w in service_lower for w in ("stripe", "paypal", "sendgrid", "twilio")):
        return "services"
    return "general"



# ─── ROI Dashboard ──────────────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/roi")
async def get_goal_roi(goal_id: int, request: Request):
    """Get ROI dashboard for a goal: money spent by AI vs money earned.

    Returns spending breakdown by category, spending timeline, earnings
    from outcome metrics, budget utilization, and overall ROI percentage.
    """
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    deps.get_goal_for_user(goal_id, uid)
    return storage.get_goal_roi(goal_id)


@router.get("/api/users/me/roi")
async def get_user_roi(request: Request):
    """Get aggregate ROI across all of the current user's goals."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    return storage.get_user_roi_summary(uid)



# ─── Platform Insights (Aggregate Learning) ─────────────────────────────────

@router.get("/api/platform/insights")
async def get_platform_insights(request: Request):
    """Get anonymized platform-wide patterns aggregated across all users.

    Returns goal type completion rates, commonly-skipped tasks, popular
    services, proven success paths, and common behavior patterns.
    Used for platform-wide learning and improving AI decomposition.
    """
    deps.require_user(request)
    deps.check_api_rate_limit(request)
    return storage.get_platform_patterns()


