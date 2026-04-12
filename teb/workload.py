"""
Workload Balancing (Phase 4).

Analyzes task assignments across goals and identifies overloaded users.
Suggests rebalancing based on task priority and user capacity.
Template fallback: even distribution by count.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

from teb import storage
from teb.models import Task

logger = logging.getLogger(__name__)

# Default capacity: 480 minutes (8 hours) of work per day
DEFAULT_DAILY_CAPACITY_MINUTES = 480


def get_user_capacity(user_id: int) -> Dict[str, Any]:
    """Calculate a user's current workload and capacity.

    Returns a dict with assigned tasks, total estimated time, capacity metrics.
    """
    tasks = storage.list_tasks()
    assigned = [t for t in tasks if t.assigned_to == user_id and t.status not in ("done", "skipped")]

    total_minutes = sum(t.estimated_minutes for t in assigned)
    task_count = len(assigned)

    # Group by goal
    by_goal: Dict[int, List[Task]] = defaultdict(list)
    for t in assigned:
        by_goal[t.goal_id].append(t)

    utilization = round(total_minutes / DEFAULT_DAILY_CAPACITY_MINUTES * 100, 1) if DEFAULT_DAILY_CAPACITY_MINUTES > 0 else 0

    return {
        "user_id": user_id,
        "assigned_tasks": task_count,
        "total_estimated_minutes": total_minutes,
        "daily_capacity_minutes": DEFAULT_DAILY_CAPACITY_MINUTES,
        "utilization_percent": min(utilization, 999.9),
        "overloaded": total_minutes > DEFAULT_DAILY_CAPACITY_MINUTES,
        "goals_involved": len(by_goal),
        "tasks_by_goal": {
            str(gid): [{"task_id": t.id, "title": t.title, "minutes": t.estimated_minutes}
                        for t in tlist]
            for gid, tlist in by_goal.items()
        },
    }


def balance_workload(goal_id: int, user_id: int) -> Dict[str, Any]:
    """Analyze and suggest workload rebalancing for tasks in a goal.

    Template fallback: even distribution by task count among available assignees.
    """
    tasks = storage.list_tasks(goal_id=goal_id)
    if not tasks:
        return {"goal_id": goal_id, "suggestions": [], "message": "No tasks found"}

    actionable = [t for t in tasks if t.status not in ("done", "skipped")]
    if not actionable:
        return {"goal_id": goal_id, "suggestions": [], "message": "All tasks completed"}

    # Count assignments
    assignment_counts: Dict[Optional[int], int] = defaultdict(int)
    assignment_minutes: Dict[Optional[int], int] = defaultdict(int)
    for t in actionable:
        assignment_counts[t.assigned_to] += 1
        assignment_minutes[t.assigned_to] += t.estimated_minutes

    # Find unassigned tasks
    unassigned = [t for t in actionable if t.assigned_to is None]

    # Find overloaded and underloaded users
    user_ids = [uid for uid in assignment_counts if uid is not None]
    suggestions: List[Dict[str, Any]] = []

    if not user_ids:
        # No one is assigned; suggest assignment
        suggestions.append({
            "type": "no_assignments",
            "message": f"{len(unassigned)} task(s) are unassigned",
            "task_ids": [t.id for t in unassigned],
        })
    else:
        avg_count = sum(assignment_counts[uid] for uid in user_ids) / len(user_ids) if user_ids else 0
        avg_minutes = sum(assignment_minutes[uid] for uid in user_ids) / len(user_ids) if user_ids else 0

        for uid in user_ids:
            count = assignment_counts[uid]
            minutes = assignment_minutes[uid]

            if minutes > DEFAULT_DAILY_CAPACITY_MINUTES:
                suggestions.append({
                    "type": "overloaded",
                    "user_id": uid,
                    "assigned_tasks": count,
                    "total_minutes": minutes,
                    "message": f"User {uid} has {minutes} minutes of work (capacity: {DEFAULT_DAILY_CAPACITY_MINUTES})",
                    "recommendation": "Reassign some tasks to reduce workload",
                })

            if count > avg_count * 1.5 and len(user_ids) > 1:
                suggestions.append({
                    "type": "unbalanced",
                    "user_id": uid,
                    "assigned_tasks": count,
                    "average_count": round(avg_count, 1),
                    "message": f"User {uid} has {count} tasks vs average {avg_count:.1f}",
                    "recommendation": "Redistribute tasks more evenly",
                })

    if unassigned and user_ids:
        suggestions.append({
            "type": "unassigned_tasks",
            "count": len(unassigned),
            "task_ids": [t.id for t in unassigned],
            "message": f"{len(unassigned)} task(s) are unassigned and could be distributed",
        })

    return {
        "goal_id": goal_id,
        "total_actionable": len(actionable),
        "unassigned_count": len(unassigned),
        "assignment_summary": {
            str(uid) if uid else "unassigned": {
                "tasks": cnt,
                "minutes": assignment_minutes[uid],
            }
            for uid, cnt in assignment_counts.items()
        },
        "suggestions": suggestions,
    }
