"""Phase 4 intelligence features: rescheduling, focus recommendations,
AI writing, template generation, meeting notes parsing, status reports,
smart tagging, workflow suggestions, cross-goal insights, skill gap analysis,
and stagnation prevention.

All functions use rule-based heuristics that work without an AI API key.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from teb import storage
from teb.models import GoalTemplate, Task


# ---------------------------------------------------------------------------
# 1. Automatic re-scheduling
# ---------------------------------------------------------------------------

def auto_reschedule(goal_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """When tasks are blocked, shift dependent tasks' due_dates forward."""
    tasks = storage.list_tasks(goal_id=goal_id)
    task_map = {t.id: t for t in tasks}
    blocked_ids = _get_blocked_task_ids(goal_id)
    rescheduled: List[Dict[str, Any]] = []
    now = datetime.now(timezone.utc)

    for task in tasks:
        if task.id in blocked_ids and task.due_date:
            try:
                due = datetime.fromisoformat(task.due_date)
            except (ValueError, TypeError):
                continue
            if due <= now:
                new_due = now + timedelta(days=3)
                task.due_date = new_due.strftime("%Y-%m-%d")
                storage.update_task(task)
                rescheduled.append({
                    "task_id": task.id,
                    "title": task.title,
                    "old_due_date": due.strftime("%Y-%m-%d"),
                    "new_due_date": task.due_date,
                    "reason": "blocked",
                })

        # Shift dependents of incomplete tasks
        deps = json.loads(task.depends_on) if task.depends_on else []
        for dep_id in deps:
            dep_task = task_map.get(dep_id)
            if dep_task and dep_task.status not in ("done", "skipped"):
                if task.due_date:
                    try:
                        due = datetime.fromisoformat(task.due_date)
                    except (ValueError, TypeError):
                        continue
                    if due <= now:
                        new_due = now + timedelta(days=3)
                        task.due_date = new_due.strftime("%Y-%m-%d")
                        storage.update_task(task)
                        rescheduled.append({
                            "task_id": task.id,
                            "title": task.title,
                            "old_due_date": due.strftime("%Y-%m-%d"),
                            "new_due_date": task.due_date,
                            "reason": f"dependency {dep_id} incomplete",
                        })
                break

    return {
        "goal_id": goal_id,
        "rescheduled_count": len(rescheduled),
        "rescheduled_tasks": rescheduled,
    }


def _get_blocked_task_ids(goal_id: int) -> set:
    """Find task IDs with open blockers or incomplete dependencies."""
    tasks = storage.list_tasks(goal_id=goal_id)
    done_ids = {t.id for t in tasks if t.status in ("done", "skipped")}
    blocked: set = set()

    for task in tasks:
        if task.id is None:
            continue
        # Check blockers table
        blockers = storage.list_task_blockers(task.id, status="open")
        if blockers:
            blocked.add(task.id)
            continue
        # Check incomplete dependencies
        deps = json.loads(task.depends_on) if task.depends_on else []
        for dep_id in deps:
            if dep_id not in done_ids:
                blocked.add(task.id)
                break

    return blocked


def get_blocked_tasks(goal_id: int) -> List[Dict[str, Any]]:
    """Storage helper: find tasks with unresolved blockers or incomplete deps."""
    tasks = storage.list_tasks(goal_id=goal_id)
    done_ids = {t.id for t in tasks if t.status in ("done", "skipped")}
    result: List[Dict[str, Any]] = []

    for task in tasks:
        if task.id is None:
            continue
        reasons: List[str] = []
        blockers = storage.list_task_blockers(task.id, status="open")
        if blockers:
            reasons.append(f"{len(blockers)} open blocker(s)")
        deps = json.loads(task.depends_on) if task.depends_on else []
        incomplete_deps = [d for d in deps if d not in done_ids]
        if incomplete_deps:
            reasons.append(f"depends on incomplete tasks: {incomplete_deps}")
        if reasons:
            result.append({
                "task_id": task.id,
                "title": task.title,
                "status": task.status,
                "reasons": reasons,
            })

    return result


# ---------------------------------------------------------------------------
# 2. Focus time recommendations
# ---------------------------------------------------------------------------

def get_focus_recommendations(user_id: int, db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Analyze task completion patterns to suggest optimal focus blocks."""
    goals = storage.list_goals(user_id=user_id)
    hour_counts: Counter = Counter()
    day_counts: Counter = Counter()

    for goal in goals:
        tasks = storage.list_tasks(goal_id=goal.id)
        for task in tasks:
            if task.status == "done" and task.updated_at:
                ts = task.updated_at if isinstance(task.updated_at, datetime) else datetime.fromisoformat(str(task.updated_at))
                hour_counts[ts.hour] += 1
                day_counts[ts.strftime("%A")] += 1

    recommendations: List[Dict[str, Any]] = []

    if hour_counts:
        top_hours = hour_counts.most_common(3)
        for hour, count in top_hours:
            end_hour = (hour + 2) % 24
            recommendations.append({
                "type": "peak_productivity_window",
                "start_hour": hour,
                "end_hour": end_hour,
                "label": f"{hour:02d}:00 - {end_hour:02d}:00",
                "confidence": min(count / max(sum(hour_counts.values()), 1), 1.0),
                "reason": f"You completed {count} tasks around this time",
            })
    else:
        # Default recommendations when no data
        for start, label in [(9, "Morning focus"), (14, "Afternoon focus")]:
            recommendations.append({
                "type": "suggested_window",
                "start_hour": start,
                "end_hour": start + 2,
                "label": f"{label}: {start:02d}:00 - {start + 2:02d}:00",
                "confidence": 0.5,
                "reason": "Default recommendation (not enough data yet)",
            })

    if day_counts:
        top_day = day_counts.most_common(1)[0]
        recommendations.append({
            "type": "best_day",
            "day": top_day[0],
            "tasks_completed": top_day[1],
            "reason": f"You are most productive on {top_day[0]}s",
        })

    return recommendations


# ---------------------------------------------------------------------------
# 3. AI writing assistant
# ---------------------------------------------------------------------------

_WRITING_TEMPLATES = {
    "improve_clarity": "Consider rephrasing for clarity:\n- Use active voice\n- Break long sentences into shorter ones\n- Lead with the action verb",
    "add_structure": "Suggested structure:\n1. Objective: [What needs to be achieved]\n2. Context: [Why this matters]\n3. Steps: [Specific actions]\n4. Success criteria: [How to know it's done]",
    "action_items": "Extracted action items:\n- [ ] Define the scope\n- [ ] Identify dependencies\n- [ ] Set a deadline\n- [ ] Assign an owner",
    "expand": "Consider adding:\n- Acceptance criteria\n- Estimated effort\n- Potential risks or blockers\n- Related tasks or references",
}


def assist_writing(context: str, prompt_text: str) -> Dict[str, Any]:
    """Provide template-based writing suggestions."""
    prompt_lower = prompt_text.lower()
    suggestions: List[str] = []

    if any(w in prompt_lower for w in ("clear", "clarity", "rewrite", "improve")):
        suggestions.append(_WRITING_TEMPLATES["improve_clarity"])
    if any(w in prompt_lower for w in ("structure", "organize", "format")):
        suggestions.append(_WRITING_TEMPLATES["add_structure"])
    if any(w in prompt_lower for w in ("action", "todo", "task", "item")):
        suggestions.append(_WRITING_TEMPLATES["action_items"])
    if any(w in prompt_lower for w in ("expand", "detail", "more", "elaborate")):
        suggestions.append(_WRITING_TEMPLATES["expand"])

    if not suggestions:
        # Default: provide structure + clarity suggestions
        suggestions.append(_WRITING_TEMPLATES["add_structure"])
        suggestions.append(_WRITING_TEMPLATES["improve_clarity"])

    # Context-aware additions
    enhanced = f"Based on context: \"{context[:100]}{'...' if len(context) > 100 else ''}\"\n\n"
    enhanced += "\n\n---\n\n".join(suggestions)

    return {
        "suggestions": enhanced,
        "templates_used": len(suggestions),
        "context_length": len(context),
    }


# ---------------------------------------------------------------------------
# 4. Template generation from description
# ---------------------------------------------------------------------------

def generate_template_from_description(description: str) -> Dict[str, Any]:
    """Parse a description to generate a GoalTemplate with tasks."""
    lines = description.strip().split("\n")
    title = lines[0].strip() if lines else "Generated Template"
    # Remove markdown heading markers
    title = re.sub(r"^#+\s*", "", title)

    tasks: List[Dict[str, str]] = []

    for line in lines[1:]:
        line = line.strip()
        if not line:
            continue
        # Numbered items: "1. ...", "1) ..."
        m = re.match(r"^\d+[\.\)]\s*(.+)", line)
        if m:
            tasks.append({"title": m.group(1).strip(), "description": ""})
            continue
        # Bullet points: "- ...", "* ...", "• ..."
        m = re.match(r"^[-*•]\s*(.+)", line)
        if m:
            tasks.append({"title": m.group(1).strip(), "description": ""})
            continue
        # Key phrases with colons
        m = re.match(r"^(\w[\w\s]{2,30}):\s*(.+)", line)
        if m:
            tasks.append({"title": m.group(1).strip(), "description": m.group(2).strip()})
            continue

    if not tasks:
        # Fallback: split by sentences
        sentences = re.split(r"[.!?]+", description)
        for s in sentences:
            s = s.strip()
            if len(s) > 5:
                tasks.append({"title": s[:80], "description": ""})

    template = GoalTemplate(
        title=title[:200],
        description=description[:500],
        tasks_json=json.dumps(tasks),
        estimated_days=max(len(tasks) * 2, 1),
    )

    return {
        "template": template.to_dict(),
        "task_count": len(tasks),
        "tasks": tasks,
    }


# ---------------------------------------------------------------------------
# 5. Meeting notes to tasks
# ---------------------------------------------------------------------------

_ACTION_PATTERNS = [
    re.compile(r"^[-*]\s*\[\s*\]\s*(.+)", re.IGNORECASE),          # - [ ] task
    re.compile(r"^TODO:\s*(.+)", re.IGNORECASE),                    # TODO: task
    re.compile(r"^ACTION:\s*(.+)", re.IGNORECASE),                  # ACTION: task
    re.compile(r"^ACTION ITEM:\s*(.+)", re.IGNORECASE),             # ACTION ITEM: task
    re.compile(r"@(\w+)\s+will\s+(.+)", re.IGNORECASE),            # @name will do X
    re.compile(r"^TASK:\s*(.+)", re.IGNORECASE),                    # TASK: task
    re.compile(r"^FOLLOW[- ]?UP:\s*(.+)", re.IGNORECASE),          # FOLLOW-UP: task
]


def extract_tasks_from_notes(notes_text: str) -> List[Dict[str, Any]]:
    """Parse meeting notes for action items."""
    lines = notes_text.strip().split("\n")
    tasks: List[Dict[str, Any]] = []

    for line in lines:
        line = line.strip()
        if not line:
            continue

        for pattern in _ACTION_PATTERNS:
            m = pattern.search(line)
            if m:
                groups = m.groups()
                if len(groups) == 2:
                    # @name will pattern
                    assigned_to = groups[0]
                    title = groups[1].strip().rstrip(".")
                    tasks.append({
                        "title": title,
                        "assigned_to": assigned_to,
                        "source_line": line,
                    })
                else:
                    title = groups[0].strip().rstrip(".")
                    tasks.append({
                        "title": title,
                        "assigned_to": None,
                        "source_line": line,
                    })
                break

    return tasks


# ---------------------------------------------------------------------------
# 6. Status report generation
# ---------------------------------------------------------------------------

def generate_status_report(goal_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Compile recent progress into a readable status report."""
    goal = storage.get_goal(goal_id)
    if not goal:
        return {"error": "Goal not found"}

    tasks = storage.list_tasks(goal_id=goal_id)
    now = datetime.now(timezone.utc)
    week_ago = now - timedelta(days=7)

    completed_this_week = []
    in_progress = []
    blocked = []
    upcoming_deadlines = []

    for task in tasks:
        if task.status == "done" and task.updated_at:
            ts = task.updated_at if isinstance(task.updated_at, datetime) else datetime.fromisoformat(str(task.updated_at))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            if ts >= week_ago:
                completed_this_week.append(task.title)

        if task.status == "in_progress":
            in_progress.append(task.title)

        if task.id is not None:
            blockers = storage.list_task_blockers(task.id, status="open")
            if blockers:
                blocked.append({
                    "task": task.title,
                    "blockers": [b.description for b in blockers],
                })

        if task.due_date:
            try:
                due = datetime.fromisoformat(task.due_date)
                if due.tzinfo is None:
                    due = due.replace(tzinfo=timezone.utc)
                if now <= due <= now + timedelta(days=7) and task.status not in ("done", "skipped"):
                    upcoming_deadlines.append({
                        "task": task.title,
                        "due_date": task.due_date,
                    })
            except (ValueError, TypeError):
                pass

    total = len(tasks)
    done = sum(1 for t in tasks if t.status in ("done", "skipped"))
    pct = round(done / total * 100) if total else 0

    report_text = f"# Status Report: {goal.title}\n\n"
    report_text += f"**Overall Progress:** {done}/{total} tasks ({pct}%)\n\n"

    if completed_this_week:
        report_text += "## Completed This Week\n"
        for t in completed_this_week:
            report_text += f"- ✅ {t}\n"
        report_text += "\n"

    if in_progress:
        report_text += "## In Progress\n"
        for t in in_progress:
            report_text += f"- 🔄 {t}\n"
        report_text += "\n"

    if blocked:
        report_text += "## Blockers\n"
        for b in blocked:
            report_text += f"- 🚫 {b['task']}: {', '.join(b['blockers'])}\n"
        report_text += "\n"

    if upcoming_deadlines:
        report_text += "## Upcoming Deadlines\n"
        for d in upcoming_deadlines:
            report_text += f"- 📅 {d['task']} (due {d['due_date']})\n"
        report_text += "\n"

    return {
        "goal_id": goal_id,
        "goal_title": goal.title,
        "report_text": report_text,
        "summary": {
            "total_tasks": total,
            "completed_tasks": done,
            "progress_percent": pct,
            "completed_this_week": len(completed_this_week),
            "in_progress_count": len(in_progress),
            "blocked_count": len(blocked),
            "upcoming_deadlines": len(upcoming_deadlines),
        },
    }


# ---------------------------------------------------------------------------
# 7. Smart tagging
# ---------------------------------------------------------------------------

_TAG_KEYWORDS: Dict[str, List[str]] = {
    "design": ["design", "ui", "ux", "mockup", "wireframe", "layout", "figma", "sketch"],
    "backend": ["database", "db", "sql", "api", "server", "backend", "migration", "schema"],
    "frontend": ["frontend", "css", "html", "react", "vue", "angular", "component", "ui"],
    "testing": ["test", "testing", "qa", "quality", "coverage", "unittest", "integration test"],
    "bugfix": ["bug", "fix", "patch", "issue", "error", "crash", "broken"],
    "documentation": ["docs", "documentation", "readme", "wiki", "guide", "tutorial"],
    "devops": ["deploy", "ci", "cd", "pipeline", "docker", "kubernetes", "infra"],
    "meetings": ["meeting", "standup", "retro", "review", "sync", "discussion"],
    "research": ["research", "investigate", "explore", "evaluate", "analyze", "spike"],
    "security": ["security", "auth", "password", "encryption", "vulnerability", "ssl"],
    "performance": ["performance", "optimize", "speed", "cache", "latency", "benchmark"],
    "planning": ["plan", "roadmap", "strategy", "scope", "estimate", "timeline"],
}


def suggest_tags(text: str) -> List[str]:
    """Analyze text and suggest relevant tags based on keyword matching."""
    text_lower = text.lower()
    words = set(re.findall(r"\w+", text_lower))
    tag_scores: Counter = Counter()

    for tag, keywords in _TAG_KEYWORDS.items():
        for keyword in keywords:
            if keyword in text_lower or keyword in words:
                tag_scores[tag] += 1

    # Return tags that had at least one keyword match, sorted by relevance
    return [tag for tag, _score in tag_scores.most_common() if _score > 0]


# ---------------------------------------------------------------------------
# 8. Personalized workflows
# ---------------------------------------------------------------------------

def get_workflow_suggestions(user_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Analyze user's past goal/task patterns to suggest workflow templates."""
    goals = storage.list_goals(user_id=user_id)
    status_transitions: Counter = Counter()
    durations: List[int] = []
    all_tags: Counter = Counter()
    task_count_per_goal: List[int] = []

    for goal in goals:
        tasks = storage.list_tasks(goal_id=goal.id)
        task_count_per_goal.append(len(tasks))

        for task in tasks:
            status_transitions[task.status] += 1
            if task.estimated_minutes:
                durations.append(task.estimated_minutes)
            if task.tags:
                for tag in task.tags.split(","):
                    tag = tag.strip().lower()
                    if tag:
                        all_tags[tag] += 1

    avg_tasks = round(sum(task_count_per_goal) / max(len(task_count_per_goal), 1), 1)
    avg_duration = round(sum(durations) / max(len(durations), 1)) if durations else 30

    suggestions: List[Dict[str, Any]] = []

    # Suggest workflow based on completion rate
    done = status_transitions.get("done", 0)
    total = sum(status_transitions.values()) or 1
    completion_rate = done / total

    if completion_rate < 0.3:
        suggestions.append({
            "type": "smaller_tasks",
            "title": "Break tasks into smaller pieces",
            "description": f"Your completion rate is {completion_rate:.0%}. Try tasks under {avg_duration // 2} minutes.",
        })

    if avg_tasks > 10:
        suggestions.append({
            "type": "milestone_driven",
            "title": "Use milestone-driven workflow",
            "description": f"Your goals average {avg_tasks} tasks. Group them into milestones of 3-5 tasks.",
        })

    top_tags = all_tags.most_common(3)
    if top_tags:
        suggestions.append({
            "type": "tag_based_workflow",
            "title": "Tag-based task batching",
            "description": f"Your top tags are {', '.join(t[0] for t in top_tags)}. Batch similar tasks together.",
        })

    if not suggestions:
        suggestions.append({
            "type": "default",
            "title": "Standard workflow",
            "description": "Plan → Execute → Review cycle with weekly check-ins.",
        })

    return {
        "user_id": user_id,
        "stats": {
            "total_goals": len(goals),
            "avg_tasks_per_goal": avg_tasks,
            "avg_task_duration_minutes": avg_duration,
            "completion_rate": round(completion_rate, 2),
            "top_tags": [{"tag": t[0], "count": t[1]} for t in top_tags] if top_tags else [],
        },
        "suggestions": suggestions,
    }


# ---------------------------------------------------------------------------
# 9. Cross-goal insights
# ---------------------------------------------------------------------------

def get_cross_goal_insights(user_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Find patterns across a user's goals."""
    goals = storage.list_goals(user_id=user_id)
    hour_counts: Counter = Counter()
    day_counts: Counter = Counter()
    tag_counts: Counter = Counter()
    status_counts: Counter = Counter()
    goal_titles: List[str] = []

    for goal in goals:
        goal_titles.append(goal.title)
        tasks = storage.list_tasks(goal_id=goal.id)
        for task in tasks:
            status_counts[task.status] += 1
            if task.tags:
                for tag in task.tags.split(","):
                    tag = tag.strip().lower()
                    if tag:
                        tag_counts[tag] += 1
            if task.status == "done" and task.updated_at:
                ts = task.updated_at if isinstance(task.updated_at, datetime) else datetime.fromisoformat(str(task.updated_at))
                hour_counts[ts.hour] += 1
                day_counts[ts.strftime("%A")] += 1

    # Find similar goals by word overlap
    similar_goals: List[Dict[str, Any]] = []
    words_per_goal = []
    for title in goal_titles:
        words_per_goal.append(set(re.findall(r"\w+", title.lower())))

    for i in range(len(words_per_goal)):
        for j in range(i + 1, len(words_per_goal)):
            if not words_per_goal[i] or not words_per_goal[j]:
                continue
            overlap = words_per_goal[i] & words_per_goal[j]
            union = words_per_goal[i] | words_per_goal[j]
            sim = len(overlap) / len(union) if union else 0
            if sim >= 0.3:
                similar_goals.append({
                    "goal_a": goal_titles[i],
                    "goal_b": goal_titles[j],
                    "similarity": round(sim, 2),
                })

    return {
        "user_id": user_id,
        "total_goals": len(goals),
        "productivity": {
            "most_productive_hours": [{"hour": h, "tasks": c} for h, c in hour_counts.most_common(3)],
            "most_productive_days": [{"day": d, "tasks": c} for d, c in day_counts.most_common(3)],
        },
        "common_task_types": [{"tag": t, "count": c} for t, c in tag_counts.most_common(5)],
        "status_distribution": dict(status_counts),
        "similar_goals": similar_goals,
    }


# ---------------------------------------------------------------------------
# 10. Skill gap analysis
# ---------------------------------------------------------------------------

def analyze_skill_gaps(user_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Identify areas where tasks tend to stall by comparing tags of
    completed vs stalled/failed tasks."""
    goals = storage.list_goals(user_id=user_id)
    completed_tags: Counter = Counter()
    stalled_tags: Counter = Counter()
    completed_count = 0
    stalled_count = 0

    for goal in goals:
        tasks = storage.list_tasks(goal_id=goal.id)
        for task in tasks:
            tags = [t.strip().lower() for t in task.tags.split(",") if t.strip()] if task.tags else ["untagged"]

            if task.status in ("done", "skipped"):
                completed_count += 1
                for tag in tags:
                    completed_tags[tag] += 1
            elif task.status in ("failed",):
                stalled_count += 1
                for tag in tags:
                    stalled_tags[tag] += 1
            elif task.status in ("todo", "in_progress"):
                # Check if stale (no update in >5 days)
                if task.updated_at:
                    ts = task.updated_at if isinstance(task.updated_at, datetime) else datetime.fromisoformat(str(task.updated_at))
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    if datetime.now(timezone.utc) - ts > timedelta(days=5):
                        stalled_count += 1
                        for tag in tags:
                            stalled_tags[tag] += 1

    # Identify gaps: tags that appear more in stalled than completed
    skill_gaps: List[Dict[str, Any]] = []
    all_tags = set(completed_tags.keys()) | set(stalled_tags.keys())

    for tag in all_tags:
        if tag == "untagged":
            continue
        done = completed_tags.get(tag, 0)
        stuck = stalled_tags.get(tag, 0)
        total = done + stuck
        if total == 0:
            continue
        stall_rate = stuck / total
        if stall_rate > 0.4 and stuck >= 1:
            skill_gaps.append({
                "area": tag,
                "stall_rate": round(stall_rate, 2),
                "stalled_tasks": stuck,
                "completed_tasks": done,
                "recommendation": f"Consider training or getting help with '{tag}' tasks",
            })

    skill_gaps.sort(key=lambda x: x["stall_rate"], reverse=True)

    return {
        "user_id": user_id,
        "total_completed": completed_count,
        "total_stalled": stalled_count,
        "skill_gaps": skill_gaps,
        "strengths": [
            {"area": tag, "completed": count}
            for tag, count in completed_tags.most_common(5)
            if tag != "untagged" and stalled_tags.get(tag, 0) == 0
        ],
    }


# ---------------------------------------------------------------------------
# 11. Stagnation prevention
# ---------------------------------------------------------------------------

def detect_stagnation(goal_id: int, db_path: Optional[str] = None) -> Dict[str, Any]:
    """Check for stagnation: tasks with no updates, goals with no completions,
    tasks stuck in same status. Returns detailed report with severity levels."""
    goal = storage.get_goal(goal_id)
    if not goal:
        return {"error": "Goal not found"}

    tasks = storage.list_tasks(goal_id=goal_id)
    now = datetime.now(timezone.utc)
    issues: List[Dict[str, Any]] = []

    recent_completions = 0

    for task in tasks:
        if task.status in ("done", "skipped"):
            if task.updated_at:
                ts = task.updated_at if isinstance(task.updated_at, datetime) else datetime.fromisoformat(str(task.updated_at))
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                if now - ts <= timedelta(days=7):
                    recent_completions += 1
            continue

        if task.updated_at:
            ts = task.updated_at if isinstance(task.updated_at, datetime) else datetime.fromisoformat(str(task.updated_at))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            days_since_update = (now - ts).days

            # Tasks with no updates in >3 days
            if days_since_update > 3:
                severity = "low" if days_since_update <= 5 else ("medium" if days_since_update <= 7 else "high")
                issues.append({
                    "type": "no_recent_update",
                    "task_id": task.id,
                    "task_title": task.title,
                    "days_since_update": days_since_update,
                    "severity": severity,
                    "recommendation": "Review this task and update its status or add a comment",
                })

            # Tasks stuck in same status for >5 days
            if days_since_update > 5 and task.status in ("in_progress", "todo"):
                severity = "medium" if days_since_update <= 10 else "high"
                issues.append({
                    "type": "stuck_in_status",
                    "task_id": task.id,
                    "task_title": task.title,
                    "current_status": task.status,
                    "days_in_status": days_since_update,
                    "severity": severity,
                    "recommendation": f"Task has been '{task.status}' for {days_since_update} days. Consider breaking it down or reassigning.",
                })

    # Goal with no completions in >7 days
    active_tasks = [t for t in tasks if t.status not in ("done", "skipped")]
    if active_tasks and recent_completions == 0:
        issues.append({
            "type": "no_recent_completions",
            "goal_id": goal_id,
            "goal_title": goal.title,
            "severity": "high",
            "recommendation": "No tasks completed in the last 7 days. Consider reviewing priorities or breaking tasks into smaller pieces.",
        })

    # Overall severity
    severities = [i["severity"] for i in issues]
    if "high" in severities:
        overall = "high"
    elif "medium" in severities:
        overall = "medium"
    elif "low" in severities:
        overall = "low"
    else:
        overall = "none"

    return {
        "goal_id": goal_id,
        "goal_title": goal.title,
        "overall_severity": overall,
        "issue_count": len(issues),
        "issues": issues,
        "recent_completions_7d": recent_completions,
        "total_active_tasks": len(active_tasks),
    }
