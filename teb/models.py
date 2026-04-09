from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ─── User / Auth ─────────────────────────────────────────────────────────────

@dataclass
class User:
    email: str
    password_hash: str = ""
    id: Optional[int] = None
    role: str = "user"                # user | admin
    email_verified: bool = False
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "email": self.email,
            "role": self.role,
            "email_verified": self.email_verified,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class Goal:
    title: str
    description: str
    id: Optional[int] = None
    user_id: Optional[int] = None     # FK to users; None for legacy/unscoped goals
    status: str = "drafting"          # drafting | clarifying | decomposed | in_progress | done
    answers: dict = field(default_factory=dict)
    auto_execute: bool = False        # when True, tasks are auto-picked by the execution loop
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "answers": self.answers,
            "auto_execute": self.auto_execute,
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
    user_id: Optional[int] = None     # FK to users; legacy profiles have None
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


# ─── Multi-Agent Delegation ─────────────────────────────────────────────────

@dataclass
class AgentHandoff:
    """A record of one agent delegating work to another in a goal's orchestration."""
    goal_id: int
    from_agent: str                    # agent type that delegated (e.g. "coordinator")
    to_agent: str                      # agent type that received (e.g. "web_dev")
    task_id: Optional[int] = None      # task created by the delegation (if any)
    input_summary: str = ""            # what was asked
    output_summary: str = ""           # what was produced
    status: str = "pending"            # pending | in_progress | completed | failed
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "task_id": self.task_id,
            "input_summary": self.input_summary,
            "output_summary": self.output_summary,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Agent Messages (inter-agent collaboration) ────────────────────────────

@dataclass
class AgentMessage:
    """A message exchanged between agents during orchestration for deeper collaboration."""
    goal_id: int
    from_agent: str
    to_agent: str
    message_type: str = "info"         # info | request | response | context
    content: str = ""
    in_reply_to: Optional[int] = None  # id of message this replies to
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "from_agent": self.from_agent,
            "to_agent": self.to_agent,
            "message_type": self.message_type,
            "content": self.content,
            "in_reply_to": self.in_reply_to,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Browser Actions ────────────────────────────────────────────────────────

@dataclass
class BrowserAction:
    """A record of a browser automation action performed on behalf of the user."""
    task_id: int
    action_type: str                   # navigate | click | type | extract | screenshot | wait
    target: str = ""                   # URL, CSS selector, or description
    value: str = ""                    # text to type, or extracted content
    status: str = "pending"            # pending | success | error
    error: str = ""
    screenshot_path: str = ""          # path to screenshot if taken
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "action_type": self.action_type,
            "target": self.target,
            "value": self.value,
            "status": self.status,
            "error": self.error,
            "screenshot_path": self.screenshot_path,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Integration Registry ───────────────────────────────────────────────────

@dataclass
class Integration:
    """A pre-built integration with a known service (Stripe, Namecheap, etc.)."""
    service_name: str                  # e.g. "stripe", "namecheap", "vercel"
    category: str = "general"          # payment | hosting | domain | email | social | analytics | ai
    base_url: str = ""
    auth_type: str = "api_key"         # api_key | bearer | oauth2
    auth_header: str = "Authorization"
    docs_url: str = ""
    capabilities: str = ""             # JSON array of capability strings
    common_endpoints: str = ""         # JSON array of endpoint pattern objects
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "service_name": self.service_name,
            "category": self.category,
            "base_url": self.base_url,
            "auth_type": self.auth_type,
            "auth_header": self.auth_header,
            "docs_url": self.docs_url,
            "capabilities": _json.loads(self.capabilities) if self.capabilities else [],
            "common_endpoints": _json.loads(self.common_endpoints) if self.common_endpoints else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Financial Execution ────────────────────────────────────────────────────

@dataclass
class SpendingBudget:
    """Budget configuration for autonomous financial execution."""
    goal_id: int
    daily_limit: float = 0.0           # max spend per day in dollars
    total_limit: float = 0.0           # max total spend for this goal
    category: str = "general"          # general | hosting | domain | marketing | tools | services
    require_approval: bool = True      # whether each transaction needs manual approval
    spent_today: float = 0.0
    spent_total: float = 0.0
    autopilot_enabled: bool = False    # when True, auto-approve spending below threshold
    autopilot_threshold: float = 50.0  # max $ auto-approved per transaction
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "daily_limit": self.daily_limit,
            "total_limit": self.total_limit,
            "category": self.category,
            "require_approval": self.require_approval,
            "spent_today": self.spent_today,
            "spent_total": self.spent_total,
            "autopilot_enabled": self.autopilot_enabled,
            "autopilot_threshold": self.autopilot_threshold,
            "remaining_daily": max(0, self.daily_limit - self.spent_today),
            "remaining_total": max(0, self.total_limit - self.spent_total),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class SpendingRequest:
    """A request to spend money as part of task execution."""
    task_id: int
    budget_id: int
    amount: float
    currency: str = "USD"
    description: str = ""              # what the money is for
    service: str = ""                  # which service (stripe, namecheap, etc.)
    status: str = "pending"            # pending | approved | denied | executed | failed
    denial_reason: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "budget_id": self.budget_id,
            "amount": self.amount,
            "currency": self.currency,
            "description": self.description,
            "service": self.service,
            "status": self.status,
            "denial_reason": self.denial_reason,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Messaging Configuration ────────────────────────────────────────────────

@dataclass
class MessagingConfig:
    """Configuration for external messaging channels (Telegram, webhooks)."""
    channel: str                       # telegram | webhook
    config_json: str = "{}"            # channel-specific config (bot token, chat id, webhook url, etc.)
    enabled: bool = True
    notify_nudges: bool = True         # send nudge notifications
    notify_tasks: bool = True          # send task completion notifications
    notify_spending: bool = True       # send spending approval requests
    notify_checkins: bool = False      # send check-in reminders
    user_id: Optional[int] = None      # FK to users; scopes config to owner
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "channel": self.channel,
            "config": _json.loads(self.config_json) if self.config_json else {},
            "enabled": self.enabled,
            "notify_nudges": self.notify_nudges,
            "notify_tasks": self.notify_tasks,
            "notify_spending": self.notify_spending,
            "notify_checkins": self.notify_checkins,
            "user_id": self.user_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
