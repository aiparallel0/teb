"""
Automated Progress Reporting (Phase 3).

Generates progress reports for goals using task completion data.
AI-enhanced narrative reports with template fallback for bullet-point summaries.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from teb import storage
from teb.models import Goal, ProgressReport, Task

logger = logging.getLogger(__name__)


def generate_progress_report(goal_id: int, user_id: int) -> ProgressReport:
    """Generate a progress report for a goal.

    Uses AI for narrative reports when available, falls back to template-based
    bullet-point summaries.
    """
    goal = storage.get_goal(goal_id)
    if not goal:
        raise ValueError(f"Goal {goal_id} not found")

    tasks = storage.list_tasks(goal_id=goal_id)

    # Try AI-based report generation
    try:
        from teb import config
        if config.get_ai_provider():
            return _generate_ai_report(goal, tasks, user_id)
    except Exception:
        logger.debug("AI report generation failed, using template fallback")

    # Template fallback
    return _generate_template_report(goal, tasks, user_id)


def _generate_template_report(goal: Goal, tasks: List[Task], user_id: int) -> ProgressReport:
    """Generate a template-based progress report (always works, no AI needed)."""
    total = len(tasks)
    done = [t for t in tasks if t.status in ("done", "skipped")]
    in_progress = [t for t in tasks if t.status == "in_progress"]
    todo = [t for t in tasks if t.status == "todo"]
    failed = [t for t in tasks if t.status == "failed"]
    blocked = [t for t in tasks if t.status == "executing"]

    pct = round(len(done) / total * 100, 1) if total > 0 else 0
    total_estimated = sum(t.estimated_minutes for t in tasks)
    done_estimated = sum(t.estimated_minutes for t in done)
    remaining_minutes = total_estimated - done_estimated

    # Build summary
    summary_parts = [
        f"Goal: {goal.title}",
        f"Progress: {len(done)}/{total} tasks completed ({pct}%)",
    ]
    if in_progress:
        summary_parts.append(f"In Progress: {len(in_progress)} task(s)")
    if todo:
        summary_parts.append(f"Remaining: {len(todo)} task(s)")
    if failed:
        summary_parts.append(f"Failed: {len(failed)} task(s) need attention")
    summary = " | ".join(summary_parts)

    # Metrics
    metrics = {
        "total_tasks": total,
        "completed_tasks": len(done),
        "in_progress_tasks": len(in_progress),
        "remaining_tasks": len(todo),
        "failed_tasks": len(failed),
        "percent_complete": pct,
        "total_estimated_minutes": total_estimated,
        "remaining_minutes": remaining_minutes,
    }

    # Blockers
    blockers: List[str] = []
    for t in failed:
        blockers.append(f"Task '{t.title}' has failed and needs attention")
    for t in blocked:
        blockers.append(f"Task '{t.title}' is currently executing/blocked")
    # Check for tasks with unmet dependencies
    for t in todo:
        try:
            deps = json.loads(t.depends_on) if t.depends_on else []
            done_ids = {task.id for task in done}
            unmet = [d for d in deps if d not in done_ids]
            if unmet:
                blockers.append(f"Task '{t.title}' blocked by {len(unmet)} unfinished dependency(ies)")
        except (json.JSONDecodeError, ValueError):
            pass

    # Next actions
    next_actions: List[str] = []
    for t in in_progress[:3]:
        next_actions.append(f"Continue working on '{t.title}'")
    for t in todo[:3]:
        deps = []
        try:
            deps = json.loads(t.depends_on) if t.depends_on else []
        except (json.JSONDecodeError, ValueError):
            pass
        if not deps:
            next_actions.append(f"Start '{t.title}' (no dependencies)")
    if failed:
        next_actions.append(f"Investigate {len(failed)} failed task(s)")

    report = ProgressReport(
        goal_id=goal.id or 0,
        user_id=user_id,
        summary=summary,
        metrics_json=json.dumps(metrics),
        blockers_json=json.dumps(blockers),
        next_actions_json=json.dumps(next_actions),
    )
    return storage.create_progress_report(report)


def _generate_ai_report(goal: Goal, tasks: List[Task], user_id: int) -> ProgressReport:
    """Generate an AI-enhanced narrative report."""
    from teb.ai_client import ai_chat

    total = len(tasks)
    done = [t for t in tasks if t.status in ("done", "skipped")]
    in_progress = [t for t in tasks if t.status == "in_progress"]
    todo = [t for t in tasks if t.status == "todo"]
    failed = [t for t in tasks if t.status == "failed"]
    pct = round(len(done) / total * 100, 1) if total > 0 else 0

    system_prompt = """You are a project progress analyst. Generate a concise progress report
as JSON with these fields:
- "summary": a 2-3 sentence narrative summary
- "metrics": object with total_tasks, completed_tasks, percent_complete, remaining_minutes
- "blockers": array of blocker descriptions
- "next_actions": array of recommended next steps (max 5)
Return valid JSON only."""

    user_prompt = f"""Goal: {goal.title}
Description: {goal.description}
Total tasks: {total}, Done: {len(done)}, In Progress: {len(in_progress)}, Todo: {len(todo)}, Failed: {len(failed)}
Completion: {pct}%

Tasks:
{json.dumps([{"title": t.title, "status": t.status, "minutes": t.estimated_minutes} for t in tasks[:20]])}"""

    try:
        response = ai_chat(system_prompt, user_prompt, json_mode=True)
        data = json.loads(response)
        report = ProgressReport(
            goal_id=goal.id or 0,
            user_id=user_id,
            summary=data.get("summary", ""),
            metrics_json=json.dumps(data.get("metrics", {})),
            blockers_json=json.dumps(data.get("blockers", [])),
            next_actions_json=json.dumps(data.get("next_actions", [])),
        )
        return storage.create_progress_report(report)
    except Exception:
        logger.debug("AI report parsing failed, using template fallback")
        return _generate_template_report(goal, tasks, user_id)
