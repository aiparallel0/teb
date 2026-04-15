"""Router for agents endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import integrations
from teb import browser
from teb import agents, browser, executor
from teb import state_machine
from teb.models import (
    AgentFlow, AgentHandoff, AgentMessage, AgentSchedule, BrowserAction, ExecutionCheckpoint,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["agents"])


# ─── Multi-Agent Delegation ─────────────────────────────────────────────────

@router.get("/api/agents")
async def list_agent_types():
    """List all available agent types and their capabilities."""
    return [a.to_dict() for a in agents.list_agents()]


@router.post("/api/agents/register", status_code=201)
async def register_agent_endpoint(request: Request):
    """Register a new agent type dynamically (admin only)."""
    deps.require_admin(request)
    body = await request.json()
    agent_type = body.get("agent_type", "")
    if not agent_type or not body.get("name"):
        raise HTTPException(status_code=422, detail="agent_type and name are required")

    spec = agents.AgentSpec(
        agent_type=agent_type,
        name=body.get("name", ""),
        description=body.get("description", ""),
        expertise=body.get("expertise", []),
        system_prompt=body.get("system_prompt", "You are a helpful agent."),
        can_delegate_to=body.get("can_delegate_to", []),
    )
    agents.register_agent(spec)
    return spec.to_dict()


@router.post("/api/goals/{goal_id}/orchestrate")
async def orchestrate_goal(goal_id: int, request: Request):
    """
    Run multi-agent orchestration on a goal.

    The coordinator agent analyzes the goal, delegates to specialists
    (marketing, web_dev, outreach, research, finance), each specialist
    produces concrete tasks and may sub-delegate to others.

    All handoffs are logged and all tasks are created in the database.
    """
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)

    # Validate DAG before orchestration
    existing_tasks = storage.list_tasks(goal_id=goal_id)
    if existing_tasks:
        from teb import dag as _dag_mod  # noqa: E402
        dag_validation = _dag_mod.validate_dag(existing_tasks)
        if not dag_validation.is_valid:
            raise HTTPException(status_code=400, detail=f"DAG validation failed: {'; '.join(dag_validation.errors)}")

    # Clear any previous tasks for a clean orchestration
    storage.delete_tasks_for_goal(goal_id)

    result = agents.orchestrate_goal(goal)
    return result


@router.get("/api/goals/{goal_id}/handoffs")
async def list_handoffs(goal_id: int, request: Request):
    """View the agent delegation chain for a goal."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    handoffs = storage.list_handoffs(goal_id)
    return [h.to_dict() for h in handoffs]


@router.get("/api/goals/{goal_id}/messages")
async def list_goal_messages(goal_id: int, request: Request, agent: Optional[str] = Query(default=None)):
    """View inter-agent messages for a goal, optionally filtered by agent."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    messages = storage.list_agent_messages(goal_id, agent_type=agent)
    return [m.to_dict() for m in messages]


@router.get("/api/goals/{goal_id}/agent-activity")
async def get_agent_activity(goal_id: int, request: Request):
    """Get combined agent activity for a goal — handoffs, messages, and task map.

    Returns a unified view of all agent orchestration activity, suitable
    for rendering an agent activity timeline in the UI.
    """
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    handoffs = storage.list_handoffs(goal_id)
    messages = storage.list_agent_messages(goal_id)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Build agent summary
    agent_types = set()
    for h in handoffs:
        agent_types.add(h.from_agent)
        agent_types.add(h.to_agent)

    # Map tasks to agents via handoffs
    task_agent_map: dict[int, str] = {}
    for h in handoffs:
        if h.task_id is not None:
            task_agent_map[h.task_id] = h.to_agent

    return {
        "goal_id": goal_id,
        "agents_involved": sorted(agent_types),
        "handoffs": [h.to_dict() for h in handoffs],
        "messages": [m.to_dict() for m in messages],
        "activity": [a.to_dict() for a in storage.get_agent_activity(goal_id)],
        "task_agent_map": task_agent_map,
        "total_tasks_created": len(tasks),
        "tasks_by_agent": {
            agent: sum(1 for tid, a in task_agent_map.items() if a == agent)
            for agent in agent_types
        },
    }


# ─── Browser Automation ─────────────────────────────────────────────────────

@router.post("/api/tasks/{task_id}/browser")
async def browser_execute_task(task_id: int, request: Request):
    """
    Generate and execute a browser automation plan for a task.

    Uses AI to create a step-by-step browser plan, then executes via
    Playwright (if available) or returns the plan as a guided walkthrough.
    """
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)

    if task.status in ("done", "skipped"):
        raise HTTPException(status_code=409, detail="Task is already completed")

    # Get relevant integrations for plan generation
    task_text = f"{task.title} {task.description}"
    matching = integrations.find_matching_integrations(task_text)
    from teb.models import Integration as IntModel
    integration_objs = [
        IntModel(service_name=m["service_name"], category=m["category"],
                 base_url=m["base_url"])
        for m in matching
    ]

    plan = browser.generate_browser_plan(task, integration_objs)

    if not plan.can_automate:
        return {
            "task_id": task_id,
            "executed": False,
            "reason": plan.reason,
            "plan": plan.to_dict(),
            "actions": [],
            "playwright_available": browser.is_playwright_available(),
        }

    # Mark task as executing
    task.status = "executing"
    storage.update_task(task)

    # Execute the browser plan
    results = browser.execute_browser_plan(plan)

    # Log each step as a browser action
    actions: list[dict] = []
    all_success = True
    for result in results:
        action = BrowserAction(
            task_id=task_id,
            action_type=result.step.action_type,
            target=result.step.target,
            value=result.extracted_text or result.step.value,
            status="success" if result.success else "error",
            error=result.error,
            screenshot_path=result.screenshot_path,
        )
        saved = storage.create_browser_action(action)
        actions.append(saved.to_dict())
        if not result.success:
            all_success = False

    # Update task status
    task.status = "done" if all_success else "failed"
    storage.update_task(task)

    return {
        "task_id": task_id,
        "executed": True,
        "success": all_success,
        "plan": plan.to_dict(),
        "actions": actions,
        "playwright_available": browser.is_playwright_available(),
    }


@router.get("/api/tasks/{task_id}/browser_actions")
async def get_browser_actions(task_id: int, request: Request):
    """View browser automation actions for a task."""
    uid = deps.require_user(request)
    task = deps.get_task_for_user(task_id, uid)
    actions = storage.list_browser_actions(task_id)
    return {"task_id": task_id, "actions": [a.to_dict() for a in actions]}


# ─── Execution State Machine (WP-01) ─────────────────────────────────────────
from teb import state_machine  # noqa: E402


@router.post("/api/goals/{goal_id}/resume")
async def resume_goal_execution(goal_id: int, request: Request):
    """Resume goal execution from the last checkpoint."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    state = state_machine.resume_execution(goal_id)
    if not state:
        raise HTTPException(status_code=404, detail="No active checkpoint to resume from")
    return {"resumed": True, "state": state.to_dict()}


@router.get("/api/goals/{goal_id}/checkpoints")
async def list_goal_checkpoints(goal_id: int, request: Request):
    """List execution checkpoints for a goal."""
    uid = deps.require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    return state_machine.get_execution_summary(goal_id)


# ─── Agent Flows & Schedules (WP-02) ─────────────────────────────────────────
from teb.models import AgentFlow, AgentSchedule  # noqa: E402


@router.post("/api/goals/{goal_id}/flows")
async def create_agent_flow_endpoint(goal_id: int, request: Request):
    """Create an event-driven agent flow for a goal."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    body = await request.json()
    steps = body.get("steps", [])
    if not steps:
        raise HTTPException(status_code=400, detail="Steps are required")
    flow = AgentFlow(goal_id=goal_id, steps_json=json.dumps(steps), status="pending")
    flow = storage.create_agent_flow(flow)
    return flow.to_dict()


@router.get("/api/goals/{goal_id}/flows")
async def list_agent_flows_endpoint(goal_id: int, request: Request):
    """List agent flows for a goal."""
    uid = deps.require_user(request)
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    flows = storage.list_agent_flows(goal_id)
    return [f.to_dict() for f in flows]


@router.post("/api/agents/{agent_type}/schedule")
async def configure_agent_schedule(agent_type: str, request: Request):
    """Configure heartbeat schedule for an agent on a goal."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    goal_id = body.get("goal_id")
    if not goal_id:
        raise HTTPException(status_code=400, detail="goal_id is required")
    goal = storage.get_goal(goal_id)
    if not goal or goal.user_id != uid:
        raise HTTPException(status_code=404, detail="Goal not found")
    schedule = AgentSchedule(agent_type=agent_type, goal_id=goal_id,
                             interval_hours=body.get("interval_hours", 8))
    schedule = storage.create_agent_schedule(schedule)
    return schedule.to_dict()



