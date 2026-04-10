"""
Gamification Engine (WP-04).

XP for task completion, levels, streaks, and achievement badges.
"""

from __future__ import annotations

import logging
from typing import Dict, List

from teb import storage
from teb.models import Achievement

logger = logging.getLogger(__name__)


def xp_for_task(estimated_minutes: int) -> int:
    if estimated_minutes <= 15:
        return 10
    if estimated_minutes <= 30:
        return 25
    if estimated_minutes <= 60:
        return 50
    if estimated_minutes <= 120:
        return 75
    return 100


def award_task_xp(user_id: int, estimated_minutes: int) -> Dict:
    xp = xp_for_task(estimated_minutes)
    old_xp = storage.get_or_create_user_xp(user_id)
    old_level = old_xp.level
    updated = storage.update_user_xp(user_id, xp)
    return {
        "xp_earned": xp,
        "total_xp": updated.total_xp,
        "level": updated.level,
        "leveled_up": updated.level > old_level,
        "streak": updated.current_streak,
    }


ACHIEVEMENT_DEFS = {
    "first_task": {"title": "First Steps", "description": "Completed your first task"},
    "task_10": {"title": "Getting Started", "description": "Completed 10 tasks"},
    "task_50": {"title": "Momentum Builder", "description": "Completed 50 tasks"},
    "task_100": {"title": "Century Club", "description": "Completed 100 tasks"},
    "first_goal": {"title": "Goal Achiever", "description": "Completed your first goal"},
    "goal_5": {"title": "Goal Machine", "description": "Completed 5 goals"},
    "streak_3": {"title": "Consistent", "description": "Maintained a 3-day streak"},
    "streak_7": {"title": "Week Warrior", "description": "Maintained a 7-day streak"},
    "streak_30": {"title": "Monthly Master", "description": "Maintained a 30-day streak"},
    "level_5": {"title": "Rising Star", "description": "Reached level 5"},
    "level_10": {"title": "Veteran", "description": "Reached level 10"},
}


def check_achievements(user_id: int) -> List[Achievement]:
    earned = []
    uxp = storage.get_or_create_user_xp(user_id)
    existing = {a.achievement_type for a in storage.list_achievements(user_id)}
    checks = [
        ("streak_3", uxp.current_streak >= 3 or uxp.longest_streak >= 3),
        ("streak_7", uxp.current_streak >= 7 or uxp.longest_streak >= 7),
        ("streak_30", uxp.current_streak >= 30 or uxp.longest_streak >= 30),
        ("level_5", uxp.level >= 5),
        ("level_10", uxp.level >= 10),
    ]
    for ach_type, condition in checks:
        if condition and ach_type not in existing:
            defn = ACHIEVEMENT_DEFS.get(ach_type, {})
            ach = Achievement(user_id=user_id, achievement_type=ach_type,
                              title=defn.get("title", ach_type), description=defn.get("description", ""))
            storage.create_achievement(ach)
            earned.append(ach)
    return earned
