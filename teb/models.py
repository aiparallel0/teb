from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


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
    status: str = "todo"              # todo | in_progress | done | skipped
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
class CheckIn:
    """Daily check-in record for a goal."""
    goal_id: int
    done_summary: str               # What the user accomplished
    blockers: str = ""               # What's blocking progress
    mood: str = "neutral"            # positive | neutral | frustrated | stuck
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "done_summary": self.done_summary,
            "blockers": self.blockers,
            "mood": self.mood,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class OutcomeMetric:
    """Tracks measurable outcome for a goal (e.g. '$50 earned', '3 chapters read')."""
    goal_id: int
    label: str                       # e.g. "Revenue earned", "Chapters completed"
    current_value: float = 0.0
    target_value: float = 0.0
    unit: str = ""                   # e.g. "$", "chapters", "kg"
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        pct = 0
        if self.target_value > 0:
            pct = min(100, round(self.current_value / self.target_value * 100))
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "label": self.label,
            "current_value": self.current_value,
            "target_value": self.target_value,
            "unit": self.unit,
            "achievement_pct": pct,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class NudgeEvent:
    """System-generated nudge when stagnation is detected."""
    goal_id: int
    nudge_type: str                  # stagnation | reminder | encouragement | blocker_help
    message: str
    id: Optional[int] = None
    acknowledged: bool = False
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
