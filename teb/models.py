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
    parent_goal_id: Optional[int] = None  # FK to goals; None for top-level goals
    status: str = "drafting"          # drafting | clarifying | decomposed | in_progress | done
    answers: dict = field(default_factory=dict)
    auto_execute: bool = False        # when True, tasks are auto-picked by the execution loop
    tags: str = ""                     # comma-separated tags for AI routing and categorization
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "parent_goal_id": self.parent_goal_id,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "answers": self.answers,
            "auto_execute": self.auto_execute,
            "tags": [t.strip() for t in self.tags.split(",") if t.strip()] if self.tags else [],
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
    due_date: str = ""                 # ISO date string (e.g. "2025-06-15")
    depends_on: str = "[]"             # JSON array of task IDs this task depends on
    tags: str = ""                     # comma-separated tags for AI routing and categorization
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "parent_id": self.parent_id,
            "title": self.title,
            "description": self.description,
            "estimated_minutes": self.estimated_minutes,
            "status": self.status,
            "order_index": self.order_index,
            "due_date": self.due_date if self.due_date else None,
            "depends_on": _json.loads(self.depends_on) if self.depends_on else [],
            "tags": [t.strip() for t in self.tags.split(",") if t.strip()] if self.tags else [],
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
    user_id: Optional[int] = None      # FK to users; None for legacy/unscoped credentials
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


# ─── Goal Milestone ──────────────────────────────────────────────────────────

@dataclass
class Milestone:
    """A measurable milestone within a goal hierarchy."""
    goal_id: int
    title: str
    target_metric: str = ""
    target_value: float = 0.0
    current_value: float = 0.0
    deadline: str = ""
    status: str = "pending"            # pending | in_progress | achieved | missed
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "title": self.title,
            "target_metric": self.target_metric,
            "target_value": self.target_value,
            "current_value": self.current_value,
            "deadline": self.deadline,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ─── Agent Goal Memory ──────────────────────────────────────────────────────

@dataclass
class AgentGoalMemory:
    """Per-goal working memory for a specialist agent — persists across invocations."""
    agent_type: str
    goal_id: int
    context_json: str = "{}"
    summary: str = ""
    invocation_count: int = 0
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_type": self.agent_type,
            "goal_id": self.goal_id,
            "context_json": self.context_json,
            "summary": self.summary,
            "invocation_count": self.invocation_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ─── Audit Event ─────────────────────────────────────────────────────────────

@dataclass
class AuditEvent:
    """Immutable audit trail event for full lifecycle tracing."""
    goal_id: Optional[int]
    event_type: str                    # goal_created | clarifying_answered | decomposed |
                                       # task_assigned | agent_invoked | api_called |
                                       # result_captured | outcome_measured | milestone_achieved |
                                       # spending_approved | spending_denied | template_exported
    actor_type: str = "system"         # human | agent | system
    actor_id: str = ""                 # user_id, agent_type, or "system"
    context_json: str = "{}"           # arbitrary context for the event
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "event_type": self.event_type,
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "context_json": self.context_json,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Goal Template ───────────────────────────────────────────────────────────

@dataclass
class GoalTemplate:
    """A shareable goal template — sanitized success path for re-use."""
    title: str
    description: str = ""
    goal_type: str = "generic"
    category: str = "general"
    skill_level: str = "any"           # beginner | intermediate | advanced | any
    tasks_json: str = "[]"             # serialized task list template
    milestones_json: str = "[]"        # serialized milestone template
    services_json: str = "[]"          # recommended services
    outcome_type: str = ""             # what kind of outcome this produces
    estimated_days: int = 0
    rating_sum: float = 0.0
    rating_count: int = 0
    times_used: int = 0
    source_goal_id: Optional[int] = None
    author_id: Optional[int] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "goal_type": self.goal_type,
            "category": self.category,
            "skill_level": self.skill_level,
            "tasks_json": self.tasks_json,
            "milestones_json": self.milestones_json,
            "services_json": self.services_json,
            "outcome_type": self.outcome_type,
            "estimated_days": self.estimated_days,
            "rating": round(self.rating_sum / self.rating_count, 1) if self.rating_count > 0 else 0,
            "rating_count": self.rating_count,
            "times_used": self.times_used,
            "source_goal_id": self.source_goal_id,
            "author_id": self.author_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Execution Context (Sandbox) ─────────────────────────────────────────────

@dataclass
class ExecutionContext:
    """Isolated execution sandbox for a goal."""
    goal_id: int
    browser_profile_dir: str = ""
    temp_dir: str = ""
    credential_scope: str = "[]"       # JSON array of credential IDs allowed
    status: str = "active"             # active | completed | cleaned_up
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "browser_profile_dir": self.browser_profile_dir,
            "temp_dir": self.temp_dir,
            "credential_scope": self.credential_scope,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ─── Plugin Manifest ─────────────────────────────────────────────────────────

@dataclass
class PluginManifest:
    """Registered execution plugin."""
    name: str
    version: str = "0.1.0"
    description: str = ""
    task_types: str = "[]"             # JSON array of task type strings this plugin handles
    required_credentials: str = "[]"   # JSON array of credential type strings needed
    module_path: str = ""              # Python import path or file path
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "task_types": self.task_types,
            "required_credentials": self.required_credentials,
            "module_path": self.module_path,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Task Comments (agent transparency) ─────────────────────────────────────

@dataclass
class TaskComment:
    """A comment on a task — from a human, agent, or system."""
    task_id: int
    content: str
    author_type: str = "system"        # human | agent | system
    author_id: str = ""                # user_id, agent_type, or "system"
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "content": self.content,
            "author_type": self.author_type,
            "author_id": self.author_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Task Artifacts (execution outputs) ─────────────────────────────────────

@dataclass
class TaskArtifact:
    """A file, URL, screenshot, or code artifact produced during task execution."""
    task_id: int
    artifact_type: str                 # file | url | screenshot | code | api_response
    title: str = ""
    content_url: str = ""              # URL or file path to the artifact
    metadata_json: str = "{}"          # additional metadata (size, mime type, etc.)
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "task_id": self.task_id,
            "artifact_type": self.artifact_type,
            "title": self.title,
            "content_url": self.content_url,
            "metadata": _json.loads(self.metadata_json) if self.metadata_json else {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Webhook Configuration ──────────────────────────────────────────────────

@dataclass
class WebhookConfig:
    """Webhook that fires on goal/task/milestone events for external systems."""
    user_id: int
    url: str
    events: str = "[]"                 # JSON array of event types to listen for
    secret: str = ""                   # shared secret for HMAC signature verification
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "url": self.url,
            "events": _json.loads(self.events) if self.events else [],
            "secret_set": bool(self.secret),
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ─── Execution Checkpoints ──────────────────────────────────────────────────

@dataclass
class ExecutionCheckpoint:
    """Persistent checkpoint for resumable goal execution."""
    goal_id: int
    task_id: int
    step_index: int = 0
    state_json: str = "{}"
    status: str = "active"             # active | completed | failed | resumed
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "task_id": self.task_id,
            "step_index": self.step_index,
            "state": _json.loads(self.state_json) if self.state_json else {},
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Gamification (WP-04) ───────────────────────────────────────────────────

@dataclass
class UserXP:
    """User experience points, level, and streak tracking."""
    user_id: int
    total_xp: int = 0
    level: int = 1
    current_streak: int = 0
    longest_streak: int = 0
    last_activity_date: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @property
    def xp_to_next_level(self) -> int:
        return (self.level * 100) - (self.total_xp % (self.level * 100))

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "total_xp": self.total_xp,
            "level": self.level,
            "current_streak": self.current_streak,
            "longest_streak": self.longest_streak,
            "last_activity_date": self.last_activity_date or None,
            "xp_to_next_level": self.xp_to_next_level,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class Achievement:
    """User achievement / badge."""
    user_id: int
    achievement_type: str
    title: str = ""
    description: str = ""
    id: Optional[int] = None
    earned_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "achievement_type": self.achievement_type,
            "title": self.title,
            "description": self.description,
            "earned_at": self.earned_at.isoformat() if self.earned_at else None,
        }


# ─── Agent Scheduling & Flows (WP-02) ───────────────────────────────────────

@dataclass
class AgentSchedule:
    """Configurable heartbeat schedule for an agent on a specific goal."""
    agent_type: str
    goal_id: int
    interval_hours: int = 8
    next_run_at: str = ""
    paused: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "agent_type": self.agent_type,
            "goal_id": self.goal_id,
            "interval_hours": self.interval_hours,
            "next_run_at": self.next_run_at if self.next_run_at else None,
            "paused": self.paused,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class AgentFlow:
    """Event-driven agent pipeline: when one agent completes, trigger the next."""
    goal_id: int
    steps_json: str = "[]"
    current_step: int = 0
    status: str = "pending"            # pending | running | completed | failed
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "steps": _json.loads(self.steps_json) if self.steps_json else [],
            "current_step": self.current_step,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Time Tracking (WP-08) ──────────────────────────────────────────────────

@dataclass
class TimeEntry:
    """Time tracking entry for a task."""
    task_id: int
    user_id: int
    started_at: str = ""
    ended_at: str = ""
    duration_minutes: int = 0
    note: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "started_at": self.started_at or None,
            "ended_at": self.ended_at or None,
            "duration_minutes": self.duration_minutes,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Task Recurrence (WP-10) ────────────────────────────────────────────────

@dataclass
class RecurrenceRule:
    """Repeating task rule — daily, weekly, or monthly."""
    task_id: int
    frequency: str = "weekly"          # daily | weekly | monthly
    interval: int = 1                  # every N frequency units
    next_due: str = ""                 # ISO date for next occurrence
    end_date: str = ""                 # optional ISO date to stop recurrence
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "frequency": self.frequency,
            "interval": self.interval,
            "next_due": self.next_due or None,
            "end_date": self.end_date or None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Goal Collaboration (WP-11) ─────────────────────────────────────────────

@dataclass
class GoalCollaborator:
    """User collaboration on a shared goal."""
    goal_id: int
    user_id: int
    role: str = "viewer"               # viewer | editor | admin
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "user_id": self.user_id,
            "role": self.role,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Custom Fields (WP-12) ──────────────────────────────────────────────────

@dataclass
class CustomField:
    """User-defined key-value metadata on a task."""
    task_id: int
    field_name: str
    field_value: str = ""
    field_type: str = "text"           # text | number | date | url
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "field_name": self.field_name,
            "field_value": self.field_value,
            "field_type": self.field_type,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Goal Progress Snapshots (WP-14) ────────────────────────────────────────

@dataclass
class ProgressSnapshot:
    """Periodic snapshot of goal completion percentage."""
    goal_id: int
    total_tasks: int = 0
    completed_tasks: int = 0
    percentage: float = 0.0
    id: Optional[int] = None
    captured_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "goal_id": self.goal_id,
            "total_tasks": self.total_tasks,
            "completed_tasks": self.completed_tasks,
            "percentage": self.percentage,
            "captured_at": self.captured_at.isoformat() if self.captured_at else None,
        }


# ─── Notification Preferences (WP-16) ───────────────────────────────────────

@dataclass
class NotificationPreference:
    """Per-user notification settings."""
    user_id: int
    channel: str = "in_app"            # in_app | email | slack | telegram
    event_type: str = "all"            # all | task_completed | goal_completed | mention | nudge
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "channel": self.channel,
            "event_type": self.event_type,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── API Key Management (WP-17) ─────────────────────────────────────────────

@dataclass
class PersonalApiKey:
    """Personal API key for programmatic access."""
    user_id: int
    name: str
    key_hash: str = ""
    key_prefix: str = ""               # first 8 chars for identification
    last_used_at: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "name": self.name,
            "key_prefix": self.key_prefix,
            "last_used_at": self.last_used_at or None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Task Blockers (WP-19) ──────────────────────────────────────────────────

@dataclass
class TaskBlocker:
    """Explicit blocker on a task with resolution tracking."""
    task_id: int
    description: str
    blocker_type: str = "internal"     # internal | external | dependency | resource
    status: str = "open"               # open | resolved
    resolved_at: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "description": self.description,
            "blocker_type": self.blocker_type,
            "status": self.status,
            "resolved_at": self.resolved_at or None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Dashboard Widgets (WP-20) ──────────────────────────────────────────────

@dataclass
class DashboardWidget:
    """User-configurable dashboard widget."""
    user_id: int
    widget_type: str                   # progress_chart | recent_tasks | streak | xp_bar | activity_feed | calendar
    position: int = 0
    config_json: str = "{}"
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        import json as _json
        return {
            "id": self.id,
            "user_id": self.user_id,
            "widget_type": self.widget_type,
            "position": self.position,
            "config": _json.loads(self.config_json) if self.config_json else {},
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


# ─── Phase 2: Collaboration ────────────────────────────────────────────────

@dataclass
class Workspace:
    """Team workspace container."""
    name: str
    owner_id: int
    description: str = ""
    invite_code: str = ""
    plan: str = "free"                 # free | pro | enterprise
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "owner_id": self.owner_id,
            "description": self.description,
            "invite_code": self.invite_code,
            "plan": self.plan,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class WorkspaceMember:
    """User membership in a workspace."""
    workspace_id: int
    user_id: int
    role: str = "member"               # owner | admin | member | viewer
    id: Optional[int] = None
    joined_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "workspace_id": self.workspace_id,
            "user_id": self.user_id,
            "role": self.role,
            "joined_at": self.joined_at.isoformat() if self.joined_at else None,
        }


@dataclass
class Notification:
    """In-app notification for a user."""
    user_id: int
    title: str
    body: str = ""
    notification_type: str = "info"    # info | mention | assignment | comment | completion
    source_type: str = ""              # task | goal | comment | workspace
    source_id: Optional[int] = None
    read: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "body": self.body,
            "notification_type": self.notification_type,
            "source_type": self.source_type,
            "source_id": self.source_id,
            "read": self.read,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class ActivityFeedEntry:
    """Activity feed entry for team visibility."""
    user_id: int
    action: str                        # created | updated | completed | commented | assigned
    entity_type: str                   # goal | task | comment | workspace
    entity_id: int
    entity_title: str = ""
    details: str = ""
    workspace_id: Optional[int] = None
    goal_id: Optional[int] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "action": self.action,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "entity_title": self.entity_title,
            "details": self.details,
            "workspace_id": self.workspace_id,
            "goal_id": self.goal_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class CommentReaction:
    """Emoji reaction on a comment."""
    comment_id: int
    user_id: int
    emoji: str = "👍"
    id: Optional[int] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "comment_id": self.comment_id,
            "user_id": self.user_id,
            "emoji": self.emoji,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
