from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ─── User / Auth ─────────────────────────────────────────────────────────────

import dataclasses
import json as _json_mod
from datetime import date


class TebModel:
    """Base mixin for all teb dataclasses. Provides auto-serialization.

    Subclasses can customise serialisation via class-level attributes:

    * ``_exclude_fields``       – field names omitted from ``to_dict()``
    * ``_sensitive_fields``     – field names replaced by ``{name}_set: bool``
    * ``_json_fields``          – ``{field: (output_key, empty_default)}``
    * ``_tag_fields``           – comma-separated string fields → lists
    * ``_empty_to_none_fields`` – empty strings become ``None``
    """

    _exclude_fields: frozenset = frozenset()
    _sensitive_fields: frozenset = frozenset()
    _json_fields: dict = {}
    _tag_fields: frozenset = frozenset()
    _empty_to_none_fields: frozenset = frozenset()

    def to_dict(self) -> dict:
        result: dict = {}
        for f in dataclasses.fields(self):
            name = f.name
            # JSON fields take priority (they may rename the key)
            if name in self._json_fields:
                out_key, empty_default = self._json_fields[name]
                val = getattr(self, name)
                if val:
                    try:
                        result[out_key] = _json_mod.loads(val)
                    except (ValueError, TypeError):
                        result[out_key] = val
                else:
                    result[out_key] = type(empty_default)()  # fresh copy
                continue
            if name in self._exclude_fields:
                continue
            if name in self._sensitive_fields:
                result[f"{name}_set"] = bool(getattr(self, name))
                continue

            val = getattr(self, name)

            if name in self._tag_fields:
                result[name] = (
                    [t.strip() for t in val.split(",") if t.strip()] if val else []
                )
                continue

            if name in self._empty_to_none_fields:
                result[name] = val if val else None
                continue

            if isinstance(val, datetime):
                result[name] = val.isoformat()
            elif isinstance(val, date) and not isinstance(val, datetime):
                result[name] = val.isoformat()
            else:
                result[name] = val

        return result

    @classmethod
    def from_row(cls, row) -> "TebModel":
        """Create instance from ``sqlite3.Row`` with type coercion."""
        field_map = {f.name: f for f in dataclasses.fields(cls)}
        available = set(row.keys())
        kwargs: dict = {}
        for name, fld in field_map.items():
            if name not in available:
                continue
            val = row[name]
            type_str = str(fld.type)
            if fld.type is bool or type_str == "bool":
                val = bool(val) if val is not None else False
            elif "datetime" in type_str.lower():
                if val is not None and isinstance(val, str):
                    try:
                        val = datetime.fromisoformat(val)
                    except ValueError:
                        pass
            kwargs[name] = val
        return cls(**kwargs)


@dataclass
class User(TebModel):
    _exclude_fields = frozenset({'password_hash', 'failed_login_attempts', 'locked_until'})

    email: str
    password_hash: str = ""
    id: Optional[int] = None
    role: str = "user"                # user | admin
    email_verified: bool = False
    failed_login_attempts: int = 0
    locked_until: Optional[datetime] = None
    created_at: Optional[datetime] = None



@dataclass
class Goal(TebModel):
    _tag_fields = frozenset({'tags'})

    title: str
    description: str
    id: Optional[int] = None
    user_id: Optional[int] = None     # FK to users; None for legacy/unscoped goals
    parent_goal_id: Optional[int] = None  # FK to goals; None for top-level goals
    status: str = "drafting"          # drafting | clarifying | decomposed | in_progress | done
    answers: dict = field(default_factory=dict)
    auto_execute: bool = False        # when True, tasks are auto-picked by the execution loop
    tags: str = ""                     # comma-separated tags for AI routing and categorization
    version: int = 1                   # optimistic concurrency control
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



@dataclass
class Task(TebModel):
    _json_fields = {'depends_on': ('depends_on', [])}
    _tag_fields = frozenset({'tags'})
    _empty_to_none_fields = frozenset({'due_date'})

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
    assigned_to: Optional[int] = None  # FK to users; task assignment
    priority: str = "normal"             # high | normal | low
    version: int = 1                   # optimistic concurrency control
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



@dataclass
class ApiCredential(TebModel):
    """An external API registered by the user for automated task execution."""
    _exclude_fields = frozenset({'user_id'})
    _sensitive_fields = frozenset({'auth_value'})

    name: str                          # human-readable name, e.g. "Namecheap", "Stripe"
    base_url: str                      # e.g. "https://api.namecheap.com"
    auth_header: str = "Authorization" # header name for auth
    auth_value: str = ""               # the credential (Bearer token, API key, etc.)
    description: str = ""              # what this API can do
    id: Optional[int] = None
    user_id: Optional[int] = None      # FK to users; None for legacy/unscoped credentials
    created_at: Optional[datetime] = None



@dataclass
class ExecutionLog(TebModel):
    """A record of an automated action performed on behalf of the user."""
    task_id: int
    credential_id: Optional[int]       # which API credential was used (None for non-API actions)
    action: str                        # short description of what was done
    request_summary: str = ""          # summary of the outgoing request (no secrets)
    response_summary: str = ""         # summary of the API response
    status: str = "success"            # success | error
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Active Coaching Models ──────────────────────────────────────────────────

@dataclass
class CheckIn(TebModel):
    """A daily check-in: what the user accomplished and any blockers."""
    goal_id: int
    done_summary: str = ""
    blockers: str = ""
    mood: str = "neutral"              # positive | neutral | frustrated | stuck
    feedback: str = ""                 # coaching response from the system
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class OutcomeMetric(TebModel):
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
        d = super().to_dict()
        if self.target_value > 0:
            d["achievement_pct"] = min(100, round((self.current_value / self.target_value) * 100))
        else:
            d["achievement_pct"] = 0
        return d


@dataclass
class NudgeEvent(TebModel):
    """A nudge or alert triggered by stagnation detection."""
    goal_id: int
    nudge_type: str                    # stagnation | reminder | encouragement | blocker_help
    message: str
    acknowledged: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Persistent User Profile ─────────────────────────────────────────────────

@dataclass
class UserProfile(TebModel):
    """Persistent user profile that accumulates across goals."""
    _exclude_fields = frozenset({'user_id'})

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



# ─── Knowledge Base ──────────────────────────────────────────────────────────

@dataclass
class SuccessPath(TebModel):
    """A recorded successful execution path that can be reused for similar goals."""
    _json_fields = {'steps_json': ('steps', [])}

    goal_type: str                     # template name that succeeded
    steps_json: str = "[]"             # JSON array of step summaries
    outcome_summary: str = ""          # what was achieved
    source_goal_id: Optional[int] = None
    times_reused: int = 0
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Proactive Suggestions ───────────────────────────────────────────────────

@dataclass
class ProactiveSuggestion(TebModel):
    """An AI- or rule-generated suggestion for actions the user didn't think of."""
    goal_id: int
    suggestion: str
    rationale: str = ""
    category: str = "general"          # optimization | opportunity | risk | learning
    status: str = "pending"            # pending | accepted | dismissed
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Multi-Agent Delegation ─────────────────────────────────────────────────

@dataclass
class AgentHandoff(TebModel):
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



# ─── Agent Activity ─────────────────────────────────────────────────────────

@dataclass
class AgentActivity(TebModel):
    """A record of agent execution activity for a goal."""
    id: int
    goal_id: int
    agent_type: str
    action: str
    detail: str
    status: str  # "running" | "done" | "error"
    created_at: str



# ─── Agent Messages (inter-agent collaboration) ────────────────────────────

@dataclass
class AgentMessage(TebModel):
    """A message exchanged between agents during orchestration for deeper collaboration."""
    goal_id: int
    from_agent: str
    to_agent: str
    message_type: str = "info"         # info | request | response | context
    content: str = ""
    in_reply_to: Optional[int] = None  # id of message this replies to
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Browser Actions ────────────────────────────────────────────────────────

@dataclass
class BrowserAction(TebModel):
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



# ─── Integration Registry ───────────────────────────────────────────────────

@dataclass
class Integration(TebModel):
    """A pre-built integration with a known service (Stripe, Namecheap, etc.)."""
    _json_fields = {'capabilities': ('capabilities', []), 'common_endpoints': ('common_endpoints', [])}

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



# ─── Financial Execution ────────────────────────────────────────────────────

@dataclass
class SpendingBudget(TebModel):
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
        d = super().to_dict()
        d["remaining_daily"] = max(0, self.daily_limit - self.spent_today)
        d["remaining_total"] = max(0, self.total_limit - self.spent_total)
        return d


@dataclass
class SpendingRequest(TebModel):
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



# ─── Messaging Configuration ────────────────────────────────────────────────

@dataclass
class MessagingConfig(TebModel):
    """Configuration for external messaging channels (Telegram, webhooks)."""
    _json_fields = {'config_json': ('config', {})}

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



# ─── Goal Milestone ──────────────────────────────────────────────────────────

@dataclass
class Milestone(TebModel):
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



# ─── Agent Goal Memory ──────────────────────────────────────────────────────

@dataclass
class AgentGoalMemory(TebModel):
    """Per-goal working memory for a specialist agent — persists across invocations."""
    agent_type: str
    goal_id: int
    context_json: str = "{}"
    summary: str = ""
    invocation_count: int = 0
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



# ─── Audit Event ─────────────────────────────────────────────────────────────

@dataclass
class AuditEvent(TebModel):
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



# ─── Goal Template ───────────────────────────────────────────────────────────

@dataclass
class GoalTemplate(TebModel):
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
        d = super().to_dict()
        d["rating"] = round(self.rating_sum / self.rating_count, 1) if self.rating_count > 0 else 0
        del d["rating_sum"]
        return d


# ─── Execution Context (Sandbox) ─────────────────────────────────────────────

@dataclass
class ExecutionContext(TebModel):
    """Isolated execution sandbox for a goal."""
    goal_id: int
    browser_profile_dir: str = ""
    temp_dir: str = ""
    credential_scope: str = "[]"       # JSON array of credential IDs allowed
    status: str = "active"             # active | completed | cleaned_up
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



# ─── Plugin Manifest ─────────────────────────────────────────────────────────

@dataclass
class PluginManifest(TebModel):
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



# ─── Task Comments (agent transparency) ─────────────────────────────────────

@dataclass
class TaskComment(TebModel):
    """A comment on a task — from a human, agent, or system."""
    task_id: int
    content: str
    author_type: str = "system"        # human | agent | system
    author_id: str = ""                # user_id, agent_type, or "system"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Task Artifacts (execution outputs) ─────────────────────────────────────

@dataclass
class TaskArtifact(TebModel):
    """A file, URL, screenshot, or code artifact produced during task execution."""
    _json_fields = {'metadata_json': ('metadata', {})}

    task_id: int
    artifact_type: str                 # file | url | screenshot | code | api_response
    title: str = ""
    content_url: str = ""              # URL or file path to the artifact
    metadata_json: str = "{}"          # additional metadata (size, mime type, etc.)
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Webhook Configuration ──────────────────────────────────────────────────

@dataclass
class WebhookConfig(TebModel):
    """Webhook that fires on goal/task/milestone events for external systems."""
    _sensitive_fields = frozenset({'secret'})
    _json_fields = {'events': ('events', [])}

    user_id: int
    url: str
    events: str = "[]"                 # JSON array of event types to listen for
    secret: str = ""                   # shared secret for HMAC signature verification
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



# ─── Execution Checkpoints ──────────────────────────────────────────────────

@dataclass
class ExecutionCheckpoint(TebModel):
    """Persistent checkpoint for resumable goal execution."""
    _json_fields = {'state_json': ('state', {})}

    goal_id: int
    task_id: int
    step_index: int = 0
    state_json: str = "{}"
    status: str = "active"             # active | completed | failed | resumed
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Gamification (WP-04) ───────────────────────────────────────────────────

@dataclass
class UserXP(TebModel):
    """User experience points, level, and streak tracking."""
    _empty_to_none_fields = frozenset({'last_activity_date'})

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
        d = super().to_dict()
        d["xp_to_next_level"] = self.xp_to_next_level
        del d["updated_at"]
        return d


@dataclass
class Achievement(TebModel):
    """User achievement / badge."""
    user_id: int
    achievement_type: str
    title: str = ""
    description: str = ""
    id: Optional[int] = None
    earned_at: Optional[datetime] = None



# ─── Agent Scheduling & Flows (WP-02) ───────────────────────────────────────

@dataclass
class AgentSchedule(TebModel):
    """Configurable heartbeat schedule for an agent on a specific goal."""
    _empty_to_none_fields = frozenset({'next_run_at'})

    agent_type: str
    goal_id: int
    interval_hours: int = 8
    next_run_at: str = ""
    paused: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class AgentFlow(TebModel):
    """Event-driven agent pipeline: when one agent completes, trigger the next."""
    _json_fields = {'steps_json': ('steps', [])}

    goal_id: int
    steps_json: str = "[]"
    current_step: int = 0
    status: str = "pending"            # pending | running | completed | failed
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Time Tracking (WP-08) ──────────────────────────────────────────────────

@dataclass
class TimeEntry(TebModel):
    """Time tracking entry for a task."""
    _empty_to_none_fields = frozenset({'started_at', 'ended_at'})

    task_id: int
    user_id: int
    started_at: str = ""
    ended_at: str = ""
    duration_minutes: int = 0
    note: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Task Recurrence (WP-10) ────────────────────────────────────────────────

@dataclass
class RecurrenceRule(TebModel):
    """Repeating task rule — daily, weekly, or monthly."""
    _empty_to_none_fields = frozenset({'next_due', 'end_date'})

    task_id: int
    frequency: str = "weekly"          # daily | weekly | monthly
    interval: int = 1                  # every N frequency units
    next_due: str = ""                 # ISO date for next occurrence
    end_date: str = ""                 # optional ISO date to stop recurrence
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Goal Collaboration (WP-11) ─────────────────────────────────────────────

@dataclass
class GoalCollaborator(TebModel):
    """User collaboration on a shared goal."""
    goal_id: int
    user_id: int
    role: str = "viewer"               # viewer | editor | admin
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Custom Fields (WP-12) ──────────────────────────────────────────────────

@dataclass
class CustomField(TebModel):
    """User-defined metadata on a task.

    Supports basic types (text, number, date, url) and relational types:
    - **relation**: links to another task by ID.  ``field_value`` is the target task ID.
    - **rollup**: aggregates a numeric field across related tasks.
      ``field_value`` stores the relation field name; ``config_json``
      stores ``{"aggregation": "sum|count|avg|min|max", "target_field": "..."}``
    - **formula**: computed from other fields on the same task.
      ``field_value`` stores the expression string (e.g. ``"days_until_due"``).
      ``config_json`` stores ``{"formula_type": "days_until_due|field_diff|concat", ...}``
    """
    _json_fields = {'config_json': ('config', {})}

    task_id: int
    field_name: str
    field_value: str = ""
    field_type: str = "text"           # text | number | date | url | relation | rollup | formula
    config_json: str = "{}"            # JSON config for relation/rollup/formula types
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Goal Progress Snapshots (WP-14) ────────────────────────────────────────

@dataclass
class ProgressSnapshot(TebModel):
    """Periodic snapshot of goal completion percentage."""
    goal_id: int
    total_tasks: int = 0
    completed_tasks: int = 0
    percentage: float = 0.0
    id: Optional[int] = None
    captured_at: Optional[datetime] = None



# ─── Notification Preferences (WP-16) ───────────────────────────────────────

@dataclass
class NotificationPreference(TebModel):
    """Per-user notification settings."""
    user_id: int
    channel: str = "in_app"            # in_app | email | slack | telegram
    event_type: str = "all"            # all | task_completed | goal_completed | mention | nudge
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── API Key Management (WP-17) ─────────────────────────────────────────────

@dataclass
class PersonalApiKey(TebModel):
    """Personal API key for programmatic access."""
    _exclude_fields = frozenset({'key_hash'})
    _empty_to_none_fields = frozenset({'last_used_at'})

    user_id: int
    name: str
    key_hash: str = ""
    key_prefix: str = ""               # first 8 chars for identification
    last_used_at: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Task Blockers (WP-19) ──────────────────────────────────────────────────

@dataclass
class TaskBlocker(TebModel):
    """Explicit blocker on a task with resolution tracking."""
    _empty_to_none_fields = frozenset({'resolved_at'})

    task_id: int
    description: str
    blocker_type: str = "internal"     # internal | external | dependency | resource
    status: str = "open"               # open | resolved
    resolved_at: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Dashboard Widgets (WP-20) ──────────────────────────────────────────────

@dataclass
class DashboardWidget(TebModel):
    """User-configurable dashboard widget."""
    _json_fields = {'config_json': ('config', {})}

    user_id: int
    widget_type: str                   # progress_chart | recent_tasks | streak | xp_bar | activity_feed | calendar
    position: int = 0
    config_json: str = "{}"
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 2: Collaboration ────────────────────────────────────────────────

@dataclass
class Workspace(TebModel):
    """Team workspace container."""
    name: str
    owner_id: int
    description: str = ""
    invite_code: str = ""
    plan: str = "free"                 # free | pro | enterprise
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class WorkspaceMember(TebModel):
    """User membership in a workspace."""
    workspace_id: int
    user_id: int
    role: str = "member"               # owner | admin | member | viewer
    id: Optional[int] = None
    joined_at: Optional[datetime] = None



@dataclass
class Notification(TebModel):
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



@dataclass
class ActivityFeedEntry(TebModel):
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



@dataclass
class CommentReaction(TebModel):
    """Emoji reaction on a comment."""
    comment_id: int
    user_id: int
    emoji: str = "👍"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 2: Direct Messaging ─────────────────────────────────────────────

@dataclass
class DirectMessage(TebModel):
    """Direct message between two users."""
    sender_id: int
    recipient_id: int
    content: str
    read: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 2: Goal-Scoped Chat ─────────────────────────────────────────────

@dataclass
class GoalChatMessage(TebModel):
    """Chat message scoped to a goal."""
    goal_id: int
    user_id: int
    content: str
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 2: Email Notification Preferences ───────────────────────────────

@dataclass
class EmailNotificationConfig(TebModel):
    """Email notification preferences for a user."""
    user_id: int
    digest_frequency: str = "none"     # none | daily | weekly
    notify_on_mention: bool = True
    notify_on_assignment: bool = True
    notify_on_comment: bool = True
    id: Optional[int] = None



# ─── Phase 2: Push Subscriptions ───────────────────────────────────────────

@dataclass
class PushSubscription(TebModel):
    """Web push notification subscription."""
    user_id: int
    endpoint: str
    p256dh: str = ""
    auth: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 6: Enterprise Security ──────────────────────────────────────────

@dataclass
class SSOConfig(TebModel):
    """SSO/SAML configuration for an organization."""
    _sensitive_fields = frozenset({'certificate'})

    org_id: int
    provider: str = ""                 # okta | azure_ad | google | onelogin | custom
    entity_id: str = ""
    sso_url: str = ""
    certificate: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class IPAllowlist(TebModel):
    """IP allowlist entry for an organization."""
    org_id: int
    cidr_range: str = ""
    description: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class Organization(TebModel):
    """Organization / tenant for multi-org enterprise support."""
    _json_fields = {'settings_json': ('settings', {})}

    name: str
    slug: str = ""
    owner_id: Optional[int] = None
    settings_json: str = "{}"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class BrandingConfig(TebModel):
    """Custom branding configuration for an organization."""
    org_id: int
    logo_url: str = ""
    primary_color: str = "#1a1a2e"
    secondary_color: str = "#16213e"
    app_name: str = "teb"
    favicon_url: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class UserSession(TebModel):
    """Active user session tracking."""
    _exclude_fields = frozenset({'session_token'})
    _empty_to_none_fields = frozenset({'last_activity'})

    user_id: int
    session_token: str
    ip_address: str = ""
    user_agent: str = ""
    is_active: bool = True
    last_activity: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class TwoFactorConfig(TebModel):
    """2FA configuration per user."""
    _exclude_fields = frozenset({'totp_secret', 'backup_codes_hash'})

    user_id: int
    totp_secret: str = ""
    is_enabled: bool = False
    backup_codes_hash: str = ""        # JSON array of hashed backup codes
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 3: Saved Views ───────────────────────────────────────────────────

@dataclass
class SavedView(TebModel):
    """User-saved view configuration (filters, sort, group-by)."""
    _json_fields = {'filters_json': ('filters', {}), 'sort_json': ('sort', {})}
    _empty_to_none_fields = frozenset({'group_by'})

    user_id: int
    name: str
    view_type: str = "list"            # list | kanban | table | gantt | workload | timeline | calendar | mindmap
    filters_json: str = "{}"
    sort_json: str = "{}"
    group_by: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 3: Dashboard Layouts ─────────────────────────────────────────────

@dataclass
class DashboardLayout(TebModel):
    """User-configurable dashboard layout with positioned widgets."""
    _json_fields = {'widgets_json': ('widgets', [])}

    user_id: int
    name: str
    widgets_json: str = "[]"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 3: Scheduled Reports ─────────────────────────────────────────────

@dataclass
class ScheduledReport(TebModel):
    """Configuration for a scheduled report delivery."""
    _json_fields = {'recipients_json': ('recipients', [])}

    user_id: int
    report_type: str = "progress"      # progress | burndown | time_tracking
    frequency: str = "weekly"           # daily | weekly | monthly
    recipients_json: str = "[]"
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    last_sent_at: Optional[datetime] = None



# ─── Phase 5: Ecosystem ──────────────────────────────────────────────────────

@dataclass
class IntegrationListing(TebModel):
    """A published integration in the integration directory/marketplace."""
    name: str
    category: str = ""
    description: str = ""
    icon_url: str = ""
    auth_type: str = "api_key"   # api_key | oauth | none
    enabled: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class OAuthConnection(TebModel):
    """Stored OAuth connection for a user+provider pair."""
    _exclude_fields = frozenset({'access_token_encrypted', 'refresh_token_encrypted'})

    user_id: int
    provider: str
    access_token_encrypted: str = ""
    refresh_token_encrypted: str = ""
    expires_at: Optional[datetime] = None
    id: Optional[int] = None
    created_at: Optional[datetime] = None


    def to_dict(self) -> dict:
        d = super().to_dict()
        d["connected"] = bool(self.access_token_encrypted)
        return d


@dataclass
class IntegrationTemplate(TebModel):
    """Pre-built integration mapping between two services."""
    _json_fields = {'mapping_json': ('mapping', {})}

    name: str
    description: str = ""
    source_service: str = ""
    target_service: str = ""
    mapping_json: str = "{}"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class WebhookRule(TebModel):
    """User-defined webhook routing rule with filters."""
    _json_fields = {'filter_json': ('filter', {}), 'headers_json': ('headers', {})}

    user_id: int
    name: str = ""
    event_type: str = ""
    filter_json: str = "{}"
    target_url: str = ""
    headers_json: str = "{}"
    active: bool = True
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class PluginListing(TebModel):
    """A plugin available in the plugin marketplace."""
    _json_fields = {'manifest_json': ('manifest', {})}

    name: str
    description: str = ""
    author: str = ""
    version: str = "0.1.0"
    downloads: int = 0
    rating: float = 0.0
    manifest_json: str = "{}"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class CustomFieldDefinition(TebModel):
    """Plugin-defined custom field type."""
    _json_fields = {'options_json': ('options', [])}

    plugin_id: int
    field_type: str = "text"
    label: str = ""
    options_json: str = "[]"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class PluginView(TebModel):
    """Custom view provided by a plugin."""
    _json_fields = {'config_json': ('config', {})}

    plugin_id: int
    name: str = ""
    view_type: str = "board"
    config_json: str = "{}"
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class Theme(TebModel):
    """UI theme with customizable CSS variables."""
    _json_fields = {'css_variables_json': ('css_variables', {})}

    name: str
    author: str = ""
    css_variables_json: str = "{}"
    is_active: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 7: Community models ───────────────────────────────────────────────



@dataclass
class TemplateGalleryEntry(TebModel):
    """User-contributed goal/project template."""
    _json_fields = {'template_json': ('template', {})}

    name: str
    description: str = ""
    author: str = ""
    category: str = ""
    template_json: str = "{}"
    downloads: int = 0
    rating: float = 0.0
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class BlogPost(TebModel):
    """Blog post for product updates and tutorials."""
    title: str
    slug: str
    content: str = ""
    author: str = ""
    published: bool = False
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class RoadmapItem(TebModel):
    """Public roadmap feature item."""
    title: str
    description: str = ""
    status: str = "planned"  # planned | in_progress | completed
    votes: int = 0
    category: str = ""
    target_date: str = ""
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class FeatureVote(TebModel):
    """User vote on a roadmap item."""
    user_id: int
    roadmap_item_id: int
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 1: Risk Assessment ───────────────────────────────────────────────

@dataclass
class TaskRisk(TebModel):
    """Risk assessment for a task."""
    _json_fields = {'risk_factors': ('risk_factors', [])}
    _empty_to_none_fields = frozenset({'assessed_at'})

    task_id: int
    goal_id: int
    risk_score: float = 0.0            # 0.0 (no risk) to 1.0 (critical)
    risk_factors: str = "[]"           # JSON array of factor strings
    estimated_delay: int = 0           # estimated delay in minutes
    assessed_at: str = ""              # ISO timestamp
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 2: Task Scheduling ───────────────────────────────────────────────

@dataclass
class TaskSchedule(TebModel):
    """Persistent schedule for a task."""
    _empty_to_none_fields = frozenset({'scheduled_start', 'scheduled_end'})

    task_id: int
    goal_id: int
    user_id: int
    scheduled_start: str = ""          # ISO datetime
    scheduled_end: str = ""            # ISO datetime
    calendar_slot: int = 1             # day number in the schedule
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 3: Progress Reports ──────────────────────────────────────────────

@dataclass
class ProgressReport(TebModel):
    """Auto-generated progress report for a goal."""
    _json_fields = {'metrics_json': ('metrics', []), 'blockers_json': ('blockers', []), 'next_actions_json': ('next_actions', [])}

    goal_id: int
    user_id: int
    summary: str = ""
    metrics_json: str = "{}"           # JSON with completion %, velocity, etc.
    blockers_json: str = "[]"          # JSON array of blocker descriptions
    next_actions_json: str = "[]"      # JSON array of next action strings
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Phase 6: Social Gamification ───────────────────────────────────────────

@dataclass
class Streak(TebModel):
    """User completion streak tracking."""
    _empty_to_none_fields = frozenset({'last_activity_date'})

    user_id: int
    current_streak: int = 0
    longest_streak: int = 0
    last_activity_date: str = ""       # ISO date (YYYY-MM-DD)
    streak_type: str = "daily"         # daily | weekly
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None



@dataclass
class LeaderboardEntry(TebModel):
    """User position on a leaderboard."""
    user_id: int
    score: int = 0
    rank: int = 0
    period: str = "weekly"             # weekly | monthly | all_time
    id: Optional[int] = None
    created_at: Optional[datetime] = None



@dataclass
class TeamChallenge(TebModel):
    """Team challenge for social accountability."""
    _json_fields = {'participants_json': ('participants', [])}
    _empty_to_none_fields = frozenset({'start_date', 'end_date'})

    title: str
    description: str = ""
    goal_type: str = "tasks_completed" # tasks_completed | xp_earned | streak_days
    target_value: int = 10
    current_value: int = 0
    status: str = "active"             # active | completed | expired
    creator_id: Optional[int] = None
    participants_json: str = "[]"      # JSON array of user IDs
    start_date: str = ""               # ISO date
    end_date: str = ""                 # ISO date
    id: Optional[int] = None
    created_at: Optional[datetime] = None



# ─── Content Blocks (recursive block-based content) ─────────────────────────

@dataclass
class ContentBlock(TebModel):
    """A single block in a recursive content tree.

    Every task or goal description can be represented as a tree of typed blocks,
    enabling rich text, embeds, code blocks, checklists, and nested structures.

    Block types:
        paragraph, heading, code, quote, callout, checklist_item,
        bullet_list, numbered_list, image, embed, divider, toggle

    Properties (stored as JSON):
        - level: heading level (1-3) for heading blocks
        - language: programming language for code blocks
        - checked: boolean for checklist_item blocks
        - url: URL for image and embed blocks
        - color: callout/highlight color
        - caption: image/embed caption
    """
    _json_fields = {'properties_json': ('properties', {})}

    entity_type: str                    # "task" | "goal" | "comment"
    entity_id: int                      # FK to tasks.id, goals.id, or comments.id
    block_type: str = "paragraph"       # paragraph | heading | code | quote | callout | checklist_item | bullet_list | numbered_list | image | embed | divider | toggle
    content: str = ""                   # text content of this block
    properties_json: str = "{}"         # JSON dict of type-specific properties
    parent_block_id: Optional[int] = None  # FK to content_blocks.id for nested blocks
    order_index: int = 0                # position among siblings
    id: Optional[int] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

