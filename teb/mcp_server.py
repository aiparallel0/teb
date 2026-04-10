"""
MCP (Model Context Protocol) Server Exposure (Step 7).

Exposes teb capabilities as MCP tools so AI coding assistants can interact
with teb directly. Provides tool definitions and a handler for executing
tool calls.

Tools exposed:
- create_goal: Create a new goal in teb
- list_goals: List active goals
- get_goal_status: Get detailed goal status
- complete_task: Mark a task as complete
- get_suggestions: Get AI suggestions for a goal
- list_milestones: Get milestones for a goal
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from teb import storage
from teb.models import Goal

logger = logging.getLogger(__name__)


# ─── MCP Tool Definitions ────────────────────────────────────────────────────

MCP_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "create_goal",
        "description": "Create a new goal in teb. The goal will be decomposed into actionable tasks.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "title": {"type": "string", "description": "Short title for the goal"},
                "description": {"type": "string", "description": "Detailed description of what you want to achieve"},
            },
            "required": ["title"],
        },
    },
    {
        "name": "list_goals",
        "description": "List all active goals in teb for the current user.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "Filter by status: drafting, clarifying, decomposed, in_progress, done"},
            },
        },
    },
    {
        "name": "get_goal_status",
        "description": "Get detailed status of a specific goal including tasks, milestones, and progress.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "The goal ID to check"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "complete_task",
        "description": "Mark a specific task as completed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "integer", "description": "The task ID to complete"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_suggestions",
        "description": "Get AI-generated suggestions for a goal — things the user might not have thought of.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "The goal ID to get suggestions for"},
            },
            "required": ["goal_id"],
        },
    },
    {
        "name": "list_milestones",
        "description": "List milestones for a goal with their current progress.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "goal_id": {"type": "integer", "description": "The goal ID"},
            },
            "required": ["goal_id"],
        },
    },
]


# ─── MCP Tool Handlers ──────────────────────────────────────────────────────

def handle_tool_call(tool_name: str, arguments: Dict[str, Any],
                     user_id: Optional[int] = None) -> Dict[str, Any]:
    """Handle an MCP tool call and return the result."""
    handlers = {
        "create_goal": _handle_create_goal,
        "list_goals": _handle_list_goals,
        "get_goal_status": _handle_get_goal_status,
        "complete_task": _handle_complete_task,
        "get_suggestions": _handle_get_suggestions,
        "list_milestones": _handle_list_milestones,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return {"error": f"Unknown tool: {tool_name}"}

    try:
        return handler(arguments, user_id)
    except Exception as e:
        logger.error("MCP tool call failed: %s(%s) -> %s", tool_name, arguments, e)
        return {"error": "Tool execution failed"}


def _handle_create_goal(args: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    goal = Goal(
        title=args.get("title", ""),
        description=args.get("description", ""),
        user_id=user_id,
    )
    goal = storage.create_goal(goal)
    return {"goal_id": goal.id, "title": goal.title, "status": goal.status,
            "message": f"Goal '{goal.title}' created. Use the teb dashboard to decompose it into tasks."}


def _handle_list_goals(args: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    goals = storage.list_goals(user_id=user_id)
    status_filter = args.get("status")
    if status_filter:
        goals = [g for g in goals if g.status == status_filter]
    return {
        "goals": [
            {"id": g.id, "title": g.title, "status": g.status,
             "task_count": len(storage.list_tasks(goal_id=g.id))}
            for g in goals
        ],
        "total": len(goals),
    }


def _handle_get_goal_status(args: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    goal_id = args.get("goal_id")
    if not goal_id:
        return {"error": "goal_id is required"}

    goal = storage.get_goal(goal_id)
    if not goal:
        return {"error": "Goal not found"}
    if user_id and goal.user_id and goal.user_id != user_id:
        return {"error": "Not authorized"}

    tasks = storage.list_tasks(goal_id=goal_id)
    done_tasks = sum(1 for t in tasks if t.status == "done")
    milestones = storage.list_milestones(goal_id)
    metrics = storage.list_outcome_metrics(goal_id)

    return {
        "goal": goal.to_dict(),
        "tasks": {"total": len(tasks), "done": done_tasks,
                  "completion_pct": round(done_tasks / len(tasks) * 100, 1) if tasks else 0},
        "milestones": [m.to_dict() for m in milestones],
        "metrics": [{"label": m.label, "current": m.current_value,
                     "target": m.target_value, "unit": m.unit} for m in metrics],
    }


def _handle_complete_task(args: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    task_id = args.get("task_id")
    if not task_id:
        return {"error": "task_id is required"}

    task = storage.get_task(task_id)
    if not task:
        return {"error": "Task not found"}

    # Verify ownership
    if user_id and task.goal_id:
        goal = storage.get_goal(task.goal_id)
        if goal and goal.user_id and goal.user_id != user_id:
            return {"error": "Not authorized"}

    task.status = "done"
    storage.update_task(task)
    return {"task_id": task.id, "title": task.title, "status": "done",
            "message": f"Task '{task.title}' marked as done."}


def _handle_get_suggestions(args: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    goal_id = args.get("goal_id")
    if not goal_id:
        return {"error": "goal_id is required"}

    suggestions = storage.list_suggestions(goal_id, status="pending")
    return {
        "suggestions": [
            {"id": s.id, "suggestion": s.suggestion, "rationale": s.rationale,
             "category": s.category}
            for s in suggestions
        ],
    }


def _handle_list_milestones(args: Dict[str, Any], user_id: Optional[int]) -> Dict[str, Any]:
    goal_id = args.get("goal_id")
    if not goal_id:
        return {"error": "goal_id is required"}

    milestones = storage.list_milestones(goal_id)
    return {
        "milestones": [m.to_dict() for m in milestones],
    }


# ─── MCP Server Info ─────────────────────────────────────────────────────────

def get_server_info() -> Dict[str, Any]:
    """Return MCP server metadata."""
    return {
        "name": "teb",
        "version": "1.0.0",
        "description": "Task Execution Bridge — turn goals into executed outcomes",
        "tools": MCP_TOOLS,
    }
