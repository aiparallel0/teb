"""AI-powered task scheduling, prioritization, and capacity planning."""

from __future__ import annotations

import json
import math
import re
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from teb.models import Task


def _parse_depends(task: Task) -> List[int]:
    """Parse the depends_on JSON string into a list of ints."""
    try:
        deps = json.loads(task.depends_on) if task.depends_on else []
        return [int(d) for d in deps]
    except (json.JSONDecodeError, TypeError, ValueError):
        return []


def _parse_due_date(task: Task) -> Optional[datetime]:
    """Parse the due_date string into a datetime, or None."""
    if not task.due_date:
        return None
    try:
        return datetime.fromisoformat(task.due_date)
    except (ValueError, TypeError):
        return None


def _parse_tags(task: Task) -> List[str]:
    """Parse comma-separated tags string into a list."""
    if not task.tags:
        return []
    return [t.strip().lower() for t in task.tags.split(",") if t.strip()]


def _topological_sort(tasks: List[Task]) -> List[Task]:
    """Topological sort tasks by depends_on. Handles cycles gracefully by
    appending cyclic tasks at the end."""
    task_map = {t.id: t for t in tasks if t.id is not None}
    in_degree: Dict[int, int] = {tid: 0 for tid in task_map}
    adj: Dict[int, List[int]] = defaultdict(list)

    for t in tasks:
        if t.id is None:
            continue
        for dep_id in _parse_depends(t):
            if dep_id in task_map:
                adj[dep_id].append(t.id)
                in_degree[t.id] = in_degree.get(t.id, 0) + 1

    queue = deque(tid for tid, deg in in_degree.items() if deg == 0)
    result: List[Task] = []

    while queue:
        tid = queue.popleft()
        result.append(task_map[tid])
        for neighbor in adj[tid]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)

    # Append any remaining (cyclic) tasks
    visited = {t.id for t in result}
    for t in tasks:
        if t.id not in visited:
            result.append(t)

    return result


def auto_schedule_tasks(
    tasks: List[Task],
    work_hours_per_day: int = 8,
    start_date: Optional[datetime] = None,
) -> List[Dict]:
    """
    Auto-schedule tasks into time blocks respecting dependencies and capacity.
    Returns list of {task_id, scheduled_start, scheduled_end, day_slot}.
    """
    if not tasks:
        return []

    now = start_date or datetime.now(timezone.utc)
    sorted_tasks = _topological_sort(tasks)
    capacity_per_day = work_hours_per_day * 60  # minutes

    schedule: List[Dict] = []
    current_day = 0
    used_today = 0

    for task in sorted_tasks:
        if task.status == "done":
            continue

        minutes = max(task.estimated_minutes, 1)

        # If this task won't fit in the current day, move to the next
        if used_today + minutes > capacity_per_day:
            current_day += 1
            used_today = 0

        # For very large tasks, they still get scheduled (possibly spanning days)
        day_start = now + timedelta(days=current_day)
        slot_start = day_start.replace(hour=9, minute=0, second=0, microsecond=0) + timedelta(minutes=used_today)
        slot_end = slot_start + timedelta(minutes=minutes)

        schedule.append({
            "task_id": task.id,
            "title": task.title,
            "scheduled_start": slot_start.isoformat(),
            "scheduled_end": slot_end.isoformat(),
            "day_slot": current_day + 1,
            "estimated_minutes": minutes,
        })
        used_today += minutes

    return schedule


def smart_prioritize(tasks: List[Task]) -> List[Dict]:
    """
    ML-lite priority scoring based on deadlines, dependencies, effort, and status.
    Returns tasks sorted by priority score with explanations.
    Score = deadline_urgency * 0.4 + dependency_impact * 0.3 + effort_efficiency * 0.2 + staleness * 0.1
    """
    if not tasks:
        return []

    now = datetime.now(timezone.utc)
    task_ids = {t.id for t in tasks if t.id is not None}

    # Precompute dependency counts: how many tasks depend on each task
    dependents_count: Dict[int, int] = defaultdict(int)
    for t in tasks:
        for dep_id in _parse_depends(t):
            if dep_id in task_ids:
                dependents_count[dep_id] += 1

    results: List[Dict] = []
    for task in tasks:
        if task.status == "done":
            continue

        explanations = []

        # 1. Deadline urgency (0-1): closer deadlines = higher score
        due = _parse_due_date(task)
        if due:
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            days_left = (due - now).total_seconds() / 86400
            if days_left <= 0:
                deadline_urgency = 1.0
                explanations.append("overdue")
            elif days_left <= 1:
                deadline_urgency = 0.95
                explanations.append("due today/tomorrow")
            elif days_left <= 3:
                deadline_urgency = 0.8
                explanations.append("due within 3 days")
            elif days_left <= 7:
                deadline_urgency = 0.5
                explanations.append("due this week")
            else:
                deadline_urgency = max(0.1, 1.0 - (days_left / 30))
        else:
            deadline_urgency = 0.3
            explanations.append("no deadline set")

        # 2. Dependency impact (0-1): more dependents = higher priority
        dep_count = dependents_count.get(task.id, 0) if task.id else 0
        dependency_impact = min(1.0, dep_count / max(len(tasks), 1) * 3)
        if dep_count > 0:
            explanations.append(f"blocks {dep_count} task(s)")

        # 3. Effort efficiency (0-1): shorter tasks get slightly higher score (quick wins)
        effort_efficiency = max(0.1, 1.0 - (task.estimated_minutes / 480))
        if task.estimated_minutes <= 30:
            explanations.append("quick win")

        # 4. Staleness (0-1): older tasks get higher priority
        if task.created_at:
            age_days = (now - task.created_at).total_seconds() / 86400
            staleness = min(1.0, age_days / 14)
            if age_days > 7:
                explanations.append("aging task")
        else:
            staleness = 0.3

        score = round(
            deadline_urgency * 0.4
            + dependency_impact * 0.3
            + effort_efficiency * 0.2
            + staleness * 0.1,
            3,
        )

        results.append({
            "task_id": task.id,
            "title": task.title,
            "priority_score": score,
            "explanation": "; ".join(explanations) if explanations else "standard priority",
            "deadline_urgency": round(deadline_urgency, 3),
            "dependency_impact": round(dependency_impact, 3),
            "effort_efficiency": round(effort_efficiency, 3),
            "staleness": round(staleness, 3),
        })

    results.sort(key=lambda x: x["priority_score"], reverse=True)
    return results


def estimate_completion(
    tasks: List[Task],
    velocity_tasks_per_day: float = 3.0,
) -> Dict:
    """
    Predict goal completion date based on remaining work and velocity.
    Returns {estimated_completion_date, remaining_tasks, remaining_hours, confidence}.
    """
    remaining = [t for t in tasks if t.status not in ("done", "skipped")]
    done = [t for t in tasks if t.status in ("done", "skipped")]

    remaining_count = len(remaining)
    total_count = len(tasks)
    remaining_minutes = sum(t.estimated_minutes for t in remaining)
    remaining_hours = round(remaining_minutes / 60, 1)

    if remaining_count == 0:
        return {
            "estimated_completion_date": datetime.now(timezone.utc).isoformat(),
            "remaining_tasks": 0,
            "remaining_hours": 0,
            "confidence": 1.0,
            "percent_complete": 100.0,
        }

    velocity = max(velocity_tasks_per_day, 0.1)
    days_needed = math.ceil(remaining_count / velocity)
    est_date = datetime.now(timezone.utc) + timedelta(days=days_needed)

    # Confidence decreases with more remaining work and increases with progress
    progress = len(done) / total_count if total_count > 0 else 0
    confidence = round(min(0.95, 0.4 + progress * 0.5), 2)

    return {
        "estimated_completion_date": est_date.isoformat(),
        "remaining_tasks": remaining_count,
        "remaining_hours": remaining_hours,
        "confidence": confidence,
        "percent_complete": round(progress * 100, 1),
    }


def detect_risks(tasks: List[Task]) -> List[Dict]:
    """
    Identify at-risk tasks: overdue, blocked, stagnant, overloaded.
    Returns list of {task_id, risk_type, severity, description, suggestion}.
    """
    if not tasks:
        return []

    now = datetime.now(timezone.utc)
    task_status = {t.id: t.status for t in tasks if t.id is not None}
    risks: List[Dict] = []

    for task in tasks:
        if task.status in ("done", "skipped"):
            continue

        # 1. Overdue: past due date
        due = _parse_due_date(task)
        if due:
            if due.tzinfo is None:
                due = due.replace(tzinfo=timezone.utc)
            if due < now:
                days_overdue = (now - due).days
                severity = "critical" if days_overdue > 3 else "high"
                risks.append({
                    "task_id": task.id,
                    "title": task.title,
                    "risk_type": "overdue",
                    "severity": severity,
                    "description": f"Task is {days_overdue} day(s) overdue",
                    "suggestion": "Reprioritize or reschedule this task immediately",
                })

        # 2. Blocked: depends on incomplete tasks
        deps = _parse_depends(task)
        blocking_ids = [d for d in deps if task_status.get(d) not in ("done", "skipped", None)]
        if blocking_ids:
            risks.append({
                "task_id": task.id,
                "title": task.title,
                "risk_type": "blocked",
                "severity": "high",
                "description": f"Blocked by {len(blocking_ids)} incomplete dependency(ies): {blocking_ids}",
                "suggestion": "Complete blocking tasks first or remove dependency",
            })

        # 3. Stagnant: created long ago but not started
        if task.created_at and task.status == "todo":
            age_days = (now - task.created_at).total_seconds() / 86400
            if age_days > 7:
                severity = "high" if age_days > 14 else "medium"
                risks.append({
                    "task_id": task.id,
                    "title": task.title,
                    "risk_type": "stagnant",
                    "severity": severity,
                    "description": f"Task created {int(age_days)} days ago but not started",
                    "suggestion": "Start this task, delegate it, or remove if no longer needed",
                })

        # 4. Overloaded: very large estimated effort
        if task.estimated_minutes > 240:
            risks.append({
                "task_id": task.id,
                "title": task.title,
                "risk_type": "overloaded",
                "severity": "medium",
                "description": f"Task estimated at {task.estimated_minutes} minutes ({task.estimated_minutes / 60:.1f}h)",
                "suggestion": "Consider breaking this task into smaller subtasks",
            })

    return risks


def suggest_focus_blocks(
    tasks: List[Task],
    available_hours: int = 4,
) -> List[Dict]:
    """
    Suggest optimal work blocks for deep focus.
    Groups related tasks (by tags/dependencies), respects energy levels.
    Returns list of {block_name, tasks: [...], total_minutes, reason}.
    """
    if not tasks:
        return []

    available_minutes = available_hours * 60
    actionable = [t for t in tasks if t.status not in ("done", "skipped")]
    if not actionable:
        return []

    # Group tasks by tags
    tag_groups: Dict[str, List[Task]] = defaultdict(list)
    untagged: List[Task] = []

    for t in actionable:
        tags = _parse_tags(t)
        if tags:
            for tag in tags:
                tag_groups[tag].append(t)
        else:
            untagged.append(t)

    blocks: List[Dict] = []
    used_task_ids: set = set()

    # Block 1: Quick wins (< 30 min each) — good for morning warm-up
    quick_wins = [t for t in actionable if t.estimated_minutes <= 30 and t.id not in used_task_ids]
    if quick_wins:
        block_tasks = []
        block_minutes = 0
        for t in quick_wins:
            if block_minutes + t.estimated_minutes <= min(available_minutes, 90):
                block_tasks.append({"task_id": t.id, "title": t.title, "minutes": t.estimated_minutes})
                used_task_ids.add(t.id)
                block_minutes += t.estimated_minutes
        if block_tasks:
            blocks.append({
                "block_name": "Quick Wins",
                "tasks": block_tasks,
                "total_minutes": block_minutes,
                "reason": "Short tasks to build momentum and warm up",
            })

    # Block 2+: Tag-based deep focus blocks
    sorted_tags = sorted(tag_groups.keys(), key=lambda tg: len(tag_groups[tg]), reverse=True)
    for tag in sorted_tags:
        group = [t for t in tag_groups[tag] if t.id not in used_task_ids]
        if not group:
            continue
        block_tasks = []
        block_minutes = 0
        for t in sorted(group, key=lambda x: x.estimated_minutes):
            if block_minutes + t.estimated_minutes <= available_minutes:
                block_tasks.append({"task_id": t.id, "title": t.title, "minutes": t.estimated_minutes})
                used_task_ids.add(t.id)
                block_minutes += t.estimated_minutes
        if block_tasks:
            blocks.append({
                "block_name": f"Deep Focus: {tag}",
                "tasks": block_tasks,
                "total_minutes": block_minutes,
                "reason": f"Related tasks grouped by '{tag}' for uninterrupted flow",
            })

    # Block 3: Remaining untagged tasks
    remaining = [t for t in untagged if t.id not in used_task_ids]
    if remaining:
        block_tasks = []
        block_minutes = 0
        for t in remaining:
            if block_minutes + t.estimated_minutes <= available_minutes:
                block_tasks.append({"task_id": t.id, "title": t.title, "minutes": t.estimated_minutes})
                used_task_ids.add(t.id)
                block_minutes += t.estimated_minutes
        if block_tasks:
            blocks.append({
                "block_name": "General Tasks",
                "tasks": block_tasks,
                "total_minutes": block_minutes,
                "reason": "Remaining tasks for flexible scheduling",
            })

    return blocks


def _tokenize(text: str) -> set:
    """Simple word tokenizer for similarity comparison."""
    return set(re.findall(r'\w+', text.lower()))


def detect_duplicates(tasks: List[Task], threshold: float = 0.7) -> List[Dict]:
    """
    Find potential duplicate tasks using word overlap similarity (Jaccard index).
    Returns list of {task_id_a, task_id_b, similarity, suggestion}.
    """
    if len(tasks) < 2:
        return []

    duplicates: List[Dict] = []
    task_tokens = []
    for t in tasks:
        tokens = _tokenize(t.title + " " + t.description)
        task_tokens.append((t, tokens))

    for i in range(len(task_tokens)):
        for j in range(i + 1, len(task_tokens)):
            t_a, tokens_a = task_tokens[i]
            t_b, tokens_b = task_tokens[j]

            if not tokens_a or not tokens_b:
                continue

            intersection = tokens_a & tokens_b
            union = tokens_a | tokens_b
            similarity = len(intersection) / len(union) if union else 0

            if similarity >= threshold:
                duplicates.append({
                    "task_id_a": t_a.id,
                    "task_id_b": t_b.id,
                    "title_a": t_a.title,
                    "title_b": t_b.title,
                    "similarity": round(similarity, 3),
                    "suggestion": "Consider merging these tasks or removing the duplicate",
                })

    duplicates.sort(key=lambda x: x["similarity"], reverse=True)
    return duplicates
