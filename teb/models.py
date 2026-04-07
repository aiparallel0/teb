from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Goal:
    title: str
    description: str
    id: Optional[int] = None
    status: str = "drafting"          # drafting | clarifying | decomposed | in_progress | done
    answers: dict = field(default_factory=dict)
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "answers": self.answers,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class Task:
    goal_id: int
    title: str
    description: str
    estimated_minutes: int = 30
    id: Optional[int] = None
    parent_id: Optional[int] = None
    status: str = "todo"              # todo | in_progress | done | skipped | executing | failed
    order_index: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "parent_id": self.parent_id,
            "title": self.title,
            "description": self.description,
            "estimated_minutes": self.estimated_minutes,
            "status": self.status,
            "order_index": self.order_index,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ApiCredential:
    """An external API registered by the user for automated task execution."""
    name: str                          # human-readable name, e.g. "Namecheap", "Stripe"
    base_url: str                      # e.g. "https://api.namecheap.com"
    auth_header: str = "Authorization" # header name for auth
    auth_value: str = ""               # the credential (Bearer token, API key, etc.)
    description: str = ""              # what this API can do
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "base_url": self.base_url,
            "auth_header": self.auth_header,
            "auth_value_set": bool(self.auth_value),  # never expose the raw secret
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class ExecutionLog:
    """A record of an automated action performed on behalf of the user."""
    task_id: int
    credential_id: Optional[int]       # which API credential was used (None for non-API actions)
    action: str                        # short description of what was done
    request_summary: str = ""          # summary of the outgoing request (no secrets)
    response_summary: str = ""         # summary of the API response
    status: str = "success"            # success | error
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "credential_id": self.credential_id,
            "action": self.action,
            "request_summary": self.request_summary,
            "response_summary": self.response_summary,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Active Coaching Models ──────────────────────────────────────────────────

@dataclass
class CheckIn:
    """A daily check-in: what the user accomplished and any blockers."""
    goal_id: int
    done_summary: str = ""
    blockers: str = ""
    mood: str = "neutral"              # positive | neutral | frustrated | stuck
    feedback: str = ""                 # coaching response from the system
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "done_summary": self.done_summary,
            "blockers": self.blockers,
            "mood": self.mood,
            "feedback": self.feedback,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class OutcomeMetric:
    """A measurable outcome metric attached to a goal (e.g. revenue earned)."""
    goal_id: int
    label: str
    target_value: float = 0.0
    current_value: float = 0.0
    unit: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        if self.target_value > 0:
            pct = min(100, round((self.current_value / self.target_value) * 100))
        else:
            pct = 0
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "label": self.label,
            "target_value": self.target_value,
            "current_value": self.current_value,
            "unit": self.unit,
            "achievement_pct": pct,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class NudgeEvent:
    """A nudge or alert triggered by stagnation detection."""
    goal_id: int
    nudge_type: str                    # stagnation | reminder | encouragement | blocker_help
    message: str
    acknowledged: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "nudge_type": self.nudge_type,
            "message": self.message,
            "acknowledged": self.acknowledged,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Persistent User Profile ─────────────────────────────────────────────────

@dataclass
class UserProfile:
    """Persistent user profile that accumulates across goals."""
    id: Optional[int] = None
    skills: str = ""                   # comma-separated list of skills
    available_hours_per_day: float = 1.0
    experience_level: str = "unknown"  # beginner | intermediate | advanced | unknown
    interests: str = ""
    preferred_learning_style: str = ""
    goals_completed: int = 0
    total_tasks_completed: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "skills": self.skills,
            "available_hours_per_day": self.available_hours_per_day,
            "experience_level": self.experience_level,
            "interests": self.interests,
            "preferred_learning_style": self.preferred_learning_style,
            "goals_completed": self.goals_completed,
            "total_tasks_completed": self.total_tasks_completed,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ─── Knowledge Base ──────────────────────────────────────────────────────────

@dataclass
class SuccessPath:
    """A recorded successful execution path that can be reused for similar goals."""
    goal_type: str                     # template name that succeeded
    steps_json: str = "[]"             # JSON array of step summaries
    outcome_summary: str = ""          # what was achieved
    source_goal_id: Optional[int] = None
    times_reused: int = 0
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json
        return {
            "id": self.id,
            "goal_type": self.goal_type,
            "steps": json.loads(self.steps_json) if self.steps_json else [],
            "outcome_summary": self.outcome_summary,
            "source_goal_id": self.source_goal_id,
            "times_reused": self.times_reused,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Proactive Suggestions ───────────────────────────────────────────────────

@dataclass
class ProactiveSuggestion:
    """An AI- or rule-generated suggestion for actions the user didn't think of."""
    goal_id: int
    suggestion: str
    rationale: str = ""
    category: str = "general"          # optimization | opportunity | risk | learning
    status: str = "pending"            # pending | accepted | dismissed
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "suggestion": self.suggestion,
            "rationale": self.rationale,
            "category": self.category,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
