-- 0001_baseline.sql
-- Baseline schema snapshot. All statements are idempotent (CREATE TABLE IF NOT EXISTS).
-- Run via: python -m migrations.migrate

CREATE TABLE IF NOT EXISTS users (
id            INTEGER PRIMARY KEY AUTOINCREMENT,
email         TEXT    NOT NULL UNIQUE,
password_hash TEXT    NOT NULL,
role          TEXT    NOT NULL DEFAULT 'user',
email_verified INTEGER NOT NULL DEFAULT 0,
failed_login_attempts INTEGER NOT NULL DEFAULT 0,
locked_until  TEXT    DEFAULT NULL,
created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
token_hash  TEXT    NOT NULL UNIQUE,
expires_at  TEXT    NOT NULL,
revoked     INTEGER NOT NULL DEFAULT 0,
created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS goals (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
user_id     INTEGER REFERENCES users(id) ON DELETE CASCADE,
title       TEXT    NOT NULL,
description TEXT    NOT NULL DEFAULT '',
status      TEXT    NOT NULL DEFAULT 'drafting',
answers     TEXT    NOT NULL DEFAULT '{}',
created_at  TEXT    NOT NULL,
updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS tasks (
id                 INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id            INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
parent_id          INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
title              TEXT    NOT NULL,
description        TEXT    NOT NULL DEFAULT '',
estimated_minutes  INTEGER NOT NULL DEFAULT 30,
status             TEXT    NOT NULL DEFAULT 'todo',
order_index        INTEGER NOT NULL DEFAULT 0,
created_at         TEXT    NOT NULL,
updated_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS api_credentials (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
name         TEXT    NOT NULL,
base_url     TEXT    NOT NULL,
auth_header  TEXT    NOT NULL DEFAULT 'Authorization',
auth_value   TEXT    NOT NULL DEFAULT '',
description  TEXT    NOT NULL DEFAULT '',
created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_logs (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
credential_id    INTEGER REFERENCES api_credentials(id) ON DELETE SET NULL,
action           TEXT    NOT NULL,
request_summary  TEXT    NOT NULL DEFAULT '',
response_summary TEXT    NOT NULL DEFAULT '',
status           TEXT    NOT NULL DEFAULT 'success',
created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS check_ins (
id             INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
done_summary   TEXT    NOT NULL DEFAULT '',
blockers       TEXT    NOT NULL DEFAULT '',
mood           TEXT    NOT NULL DEFAULT 'neutral',
feedback       TEXT    NOT NULL DEFAULT '',
created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS outcome_metrics (
id             INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
label          TEXT    NOT NULL,
target_value   REAL    NOT NULL DEFAULT 0,
current_value  REAL    NOT NULL DEFAULT 0,
unit           TEXT    NOT NULL DEFAULT '',
created_at     TEXT    NOT NULL,
updated_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS nudge_events (
id             INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
nudge_type     TEXT    NOT NULL,
message        TEXT    NOT NULL,
acknowledged   INTEGER NOT NULL DEFAULT 0,
created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS user_profiles (
id                      INTEGER PRIMARY KEY AUTOINCREMENT,
user_id                 INTEGER REFERENCES users(id) ON DELETE CASCADE,
skills                  TEXT    NOT NULL DEFAULT '',
available_hours_per_day REAL    NOT NULL DEFAULT 1.0,
experience_level        TEXT    NOT NULL DEFAULT 'unknown',
interests               TEXT    NOT NULL DEFAULT '',
preferred_learning_style TEXT   NOT NULL DEFAULT '',
goals_completed         INTEGER NOT NULL DEFAULT 0,
total_tasks_completed   INTEGER NOT NULL DEFAULT 0,
created_at              TEXT    NOT NULL,
updated_at              TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS success_paths (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
goal_type        TEXT    NOT NULL,
steps_json       TEXT    NOT NULL DEFAULT '[]',
outcome_summary  TEXT    NOT NULL DEFAULT '',
source_goal_id   INTEGER REFERENCES goals(id) ON DELETE SET NULL,
times_reused     INTEGER NOT NULL DEFAULT 0,
created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS proactive_suggestions (
id             INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id        INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
suggestion     TEXT    NOT NULL,
rationale      TEXT    NOT NULL DEFAULT '',
category       TEXT    NOT NULL DEFAULT 'general',
status         TEXT    NOT NULL DEFAULT 'pending',
created_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_handoffs (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
from_agent      TEXT    NOT NULL,
to_agent        TEXT    NOT NULL,
task_id         INTEGER REFERENCES tasks(id) ON DELETE SET NULL,
input_summary   TEXT    NOT NULL DEFAULT '',
output_summary  TEXT    NOT NULL DEFAULT '',
status          TEXT    NOT NULL DEFAULT 'pending',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_messages (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
from_agent      TEXT    NOT NULL,
to_agent        TEXT    NOT NULL,
message_type    TEXT    NOT NULL DEFAULT 'info',
content         TEXT    NOT NULL DEFAULT '',
in_reply_to     INTEGER REFERENCES agent_messages(id) ON DELETE SET NULL,
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS browser_actions (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
action_type     TEXT    NOT NULL,
target          TEXT    NOT NULL DEFAULT '',
value           TEXT    NOT NULL DEFAULT '',
status          TEXT    NOT NULL DEFAULT 'pending',
error           TEXT    NOT NULL DEFAULT '',
screenshot_path TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS integrations (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
service_name     TEXT    NOT NULL UNIQUE,
category         TEXT    NOT NULL DEFAULT 'general',
base_url         TEXT    NOT NULL DEFAULT '',
auth_type        TEXT    NOT NULL DEFAULT 'api_key',
auth_header      TEXT    NOT NULL DEFAULT 'Authorization',
docs_url         TEXT    NOT NULL DEFAULT '',
capabilities     TEXT    NOT NULL DEFAULT '[]',
common_endpoints TEXT    NOT NULL DEFAULT '[]',
created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS spending_budgets (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id          INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
daily_limit      REAL    NOT NULL DEFAULT 0,
total_limit      REAL    NOT NULL DEFAULT 0,
category         TEXT    NOT NULL DEFAULT 'general',
require_approval INTEGER NOT NULL DEFAULT 1,
spent_today      REAL    NOT NULL DEFAULT 0,
spent_total      REAL    NOT NULL DEFAULT 0,
created_at       TEXT    NOT NULL,
updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS spending_requests (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
budget_id       INTEGER NOT NULL REFERENCES spending_budgets(id) ON DELETE CASCADE,
amount          REAL    NOT NULL,
currency        TEXT    NOT NULL DEFAULT 'USD',
description     TEXT    NOT NULL DEFAULT '',
service         TEXT    NOT NULL DEFAULT '',
status          TEXT    NOT NULL DEFAULT 'pending',
denial_reason   TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS messaging_configs (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
channel          TEXT    NOT NULL,
config_json      TEXT    NOT NULL DEFAULT '{}',
enabled          INTEGER NOT NULL DEFAULT 1,
notify_nudges    INTEGER NOT NULL DEFAULT 1,
notify_tasks     INTEGER NOT NULL DEFAULT 1,
notify_spending  INTEGER NOT NULL DEFAULT 1,
notify_checkins  INTEGER NOT NULL DEFAULT 0,
created_at       TEXT    NOT NULL,
updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_sessions (
chat_id              TEXT    PRIMARY KEY,
goal_id              INTEGER REFERENCES goals(id) ON DELETE CASCADE,
state                TEXT    NOT NULL DEFAULT 'idle',
pending_question_key TEXT    DEFAULT NULL,
updated_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_memory (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
agent_type  TEXT    NOT NULL,
goal_type   TEXT    NOT NULL DEFAULT '',
memory_key  TEXT    NOT NULL,
memory_value TEXT   NOT NULL,
confidence  REAL   NOT NULL DEFAULT 1.0,
times_used  INTEGER NOT NULL DEFAULT 0,
created_at  TEXT    NOT NULL,
updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS user_behavior (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER REFERENCES users(id) ON DELETE CASCADE,
behavior_type TEXT   NOT NULL,
pattern_key   TEXT   NOT NULL,
pattern_value TEXT   NOT NULL DEFAULT '',
occurrences   INTEGER NOT NULL DEFAULT 1,
created_at    TEXT   NOT NULL,
updated_at    TEXT   NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_accounts (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
provider     TEXT    NOT NULL,
account_id   TEXT    NOT NULL DEFAULT '',
config_json  TEXT    NOT NULL DEFAULT '{}',
enabled      INTEGER NOT NULL DEFAULT 1,
created_at   TEXT    NOT NULL,
updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS payment_transactions (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
account_id       INTEGER NOT NULL REFERENCES payment_accounts(id) ON DELETE CASCADE,
spending_request_id INTEGER REFERENCES spending_requests(id),
provider_tx_id   TEXT    NOT NULL DEFAULT '',
amount           REAL    NOT NULL DEFAULT 0,
currency         TEXT    NOT NULL DEFAULT 'USD',
status           TEXT    NOT NULL DEFAULT 'pending',
description      TEXT    NOT NULL DEFAULT '',
provider_response TEXT   NOT NULL DEFAULT '{}',
retry_count      INTEGER NOT NULL DEFAULT 0,
created_at       TEXT    NOT NULL,
updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS discovered_services (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
service_name    TEXT    NOT NULL UNIQUE,
category        TEXT    NOT NULL DEFAULT '',
description     TEXT    NOT NULL DEFAULT '',
url             TEXT    NOT NULL DEFAULT '',
capabilities    TEXT    NOT NULL DEFAULT '[]',
discovered_by   TEXT    NOT NULL DEFAULT 'system',
relevance_score REAL   NOT NULL DEFAULT 0,
times_recommended INTEGER NOT NULL DEFAULT 0,
created_at      TEXT    NOT NULL,
updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_listings (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
name        TEXT    NOT NULL,
category    TEXT    NOT NULL DEFAULT '',
description TEXT    NOT NULL DEFAULT '',
icon_url    TEXT    NOT NULL DEFAULT '',
auth_type   TEXT    NOT NULL DEFAULT 'api_key',
enabled     INTEGER NOT NULL DEFAULT 1,
created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS oauth_connections (
id                       INTEGER PRIMARY KEY AUTOINCREMENT,
user_id                  INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
provider                 TEXT    NOT NULL,
access_token_encrypted   TEXT    NOT NULL DEFAULT '',
refresh_token_encrypted  TEXT    NOT NULL DEFAULT '',
expires_at               TEXT    DEFAULT NULL,
created_at               TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_templates (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
name            TEXT    NOT NULL,
description     TEXT    NOT NULL DEFAULT '',
source_service  TEXT    NOT NULL DEFAULT '',
target_service  TEXT    NOT NULL DEFAULT '',
mapping_json    TEXT    NOT NULL DEFAULT '{}',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_rules (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
name         TEXT    NOT NULL DEFAULT '',
event_type   TEXT    NOT NULL DEFAULT '',
filter_json  TEXT    NOT NULL DEFAULT '{}',
target_url   TEXT    NOT NULL DEFAULT '',
headers_json TEXT    NOT NULL DEFAULT '{}',
active       INTEGER NOT NULL DEFAULT 1,
created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_listings (
id            INTEGER PRIMARY KEY AUTOINCREMENT,
name          TEXT    NOT NULL,
description   TEXT    NOT NULL DEFAULT '',
author        TEXT    NOT NULL DEFAULT '',
version       TEXT    NOT NULL DEFAULT '0.1.0',
downloads     INTEGER NOT NULL DEFAULT 0,
rating        REAL    NOT NULL DEFAULT 0,
manifest_json TEXT    NOT NULL DEFAULT '{}',
created_at    TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_field_definitions (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
plugin_id    INTEGER NOT NULL,
field_type   TEXT    NOT NULL DEFAULT 'text',
label        TEXT    NOT NULL DEFAULT '',
options_json TEXT    NOT NULL DEFAULT '[]',
created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plugin_views (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
plugin_id   INTEGER NOT NULL,
name        TEXT    NOT NULL DEFAULT '',
view_type   TEXT    NOT NULL DEFAULT 'board',
config_json TEXT    NOT NULL DEFAULT '{}',
created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS themes (
id                 INTEGER PRIMARY KEY AUTOINCREMENT,
name               TEXT    NOT NULL,
author             TEXT    NOT NULL DEFAULT '',
css_variables_json TEXT    NOT NULL DEFAULT '{}',
is_active          INTEGER NOT NULL DEFAULT 0,
created_at         TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS zapier_subscriptions (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
event_type  TEXT    NOT NULL,
target_url  TEXT    NOT NULL,
created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS api_usage_log (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
integration  TEXT    NOT NULL DEFAULT '',
endpoint     TEXT    NOT NULL DEFAULT '',
created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS schema_versions (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
version     TEXT    NOT NULL,
description TEXT    NOT NULL DEFAULT '',
applied_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS deployments (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
task_id          INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
goal_id          INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
service          TEXT    NOT NULL,
project_name     TEXT    NOT NULL DEFAULT '',
repository_url   TEXT    NOT NULL DEFAULT '',
deploy_url       TEXT    NOT NULL DEFAULT '',
status           TEXT    NOT NULL DEFAULT 'pending',
provider_data    TEXT    NOT NULL DEFAULT '{}',
last_health_check TEXT   DEFAULT NULL,
health_status    TEXT    NOT NULL DEFAULT 'unknown',
created_at       TEXT    NOT NULL,
updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS provisioning_logs (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
service_name    TEXT    NOT NULL,
action          TEXT    NOT NULL DEFAULT 'signup',
status          TEXT    NOT NULL DEFAULT 'pending',
result_data     TEXT    NOT NULL DEFAULT '{}',
error           TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
token_hash  TEXT    NOT NULL UNIQUE,
expires_at  TEXT    NOT NULL,
revoked     INTEGER NOT NULL DEFAULT 0,
created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS milestones (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
title           TEXT    NOT NULL,
target_metric   TEXT    NOT NULL DEFAULT '',
target_value    REAL    NOT NULL DEFAULT 0,
current_value   REAL    NOT NULL DEFAULT 0,
deadline        TEXT    NOT NULL DEFAULT '',
status          TEXT    NOT NULL DEFAULT 'pending',
created_at      TEXT    NOT NULL,
updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_goal_memory (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
agent_type       TEXT    NOT NULL,
goal_id          INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
context_json     TEXT    NOT NULL DEFAULT '{}',
summary          TEXT    NOT NULL DEFAULT '',
invocation_count INTEGER NOT NULL DEFAULT 0,
created_at       TEXT    NOT NULL,
updated_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER REFERENCES goals(id) ON DELETE CASCADE,
event_type      TEXT    NOT NULL,
actor_type      TEXT    NOT NULL DEFAULT 'system',
actor_id        TEXT    NOT NULL DEFAULT '',
context_json    TEXT    NOT NULL DEFAULT '{}',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_templates (
id                INTEGER PRIMARY KEY AUTOINCREMENT,
title             TEXT    NOT NULL,
description       TEXT    NOT NULL DEFAULT '',
goal_type         TEXT    NOT NULL DEFAULT 'generic',
category          TEXT    NOT NULL DEFAULT 'general',
skill_level       TEXT    NOT NULL DEFAULT 'any',
tasks_json        TEXT    NOT NULL DEFAULT '[]',
milestones_json   TEXT    NOT NULL DEFAULT '[]',
services_json     TEXT    NOT NULL DEFAULT '[]',
outcome_type      TEXT    NOT NULL DEFAULT '',
estimated_days    INTEGER NOT NULL DEFAULT 0,
rating_sum        REAL    NOT NULL DEFAULT 0,
rating_count      INTEGER NOT NULL DEFAULT 0,
times_used        INTEGER NOT NULL DEFAULT 0,
source_goal_id    INTEGER REFERENCES goals(id) ON DELETE SET NULL,
author_id         INTEGER REFERENCES users(id) ON DELETE SET NULL,
created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_contexts (
id                  INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id             INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
browser_profile_dir TEXT    NOT NULL DEFAULT '',
temp_dir            TEXT    NOT NULL DEFAULT '',
credential_scope    TEXT    NOT NULL DEFAULT '[]',
status              TEXT    NOT NULL DEFAULT 'active',
created_at          TEXT    NOT NULL,
updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS plugins (
id                   INTEGER PRIMARY KEY AUTOINCREMENT,
name                 TEXT    NOT NULL UNIQUE,
version              TEXT    NOT NULL DEFAULT '0.1.0',
description          TEXT    NOT NULL DEFAULT '',
task_types           TEXT    NOT NULL DEFAULT '[]',
required_credentials TEXT    NOT NULL DEFAULT '[]',
module_path          TEXT    NOT NULL DEFAULT '',
enabled              INTEGER NOT NULL DEFAULT 1,
created_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_comments (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
content         TEXT    NOT NULL,
author_type     TEXT    NOT NULL DEFAULT 'system',
author_id       TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_artifacts (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
artifact_type   TEXT    NOT NULL,
title           TEXT    NOT NULL DEFAULT '',
content_url     TEXT    NOT NULL DEFAULT '',
metadata_json   TEXT    NOT NULL DEFAULT '{}',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS webhook_configs (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
url             TEXT    NOT NULL,
events          TEXT    NOT NULL DEFAULT '[]',
secret          TEXT    NOT NULL DEFAULT '',
enabled         INTEGER NOT NULL DEFAULT 1,
created_at      TEXT    NOT NULL,
updated_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_checkpoints (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
step_index      INTEGER NOT NULL DEFAULT 0,
state_json      TEXT    NOT NULL DEFAULT '{}',
status          TEXT    NOT NULL DEFAULT 'active',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_schedules (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
agent_type      TEXT    NOT NULL,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
interval_hours  INTEGER NOT NULL DEFAULT 8,
next_run_at     TEXT    NOT NULL DEFAULT '',
paused          INTEGER NOT NULL DEFAULT 0,
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS agent_flows (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
steps_json      TEXT    NOT NULL DEFAULT '[]',
current_step    INTEGER NOT NULL DEFAULT 0,
status          TEXT    NOT NULL DEFAULT 'pending',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS user_xp (
id                  INTEGER PRIMARY KEY AUTOINCREMENT,
user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
total_xp            INTEGER NOT NULL DEFAULT 0,
level               INTEGER NOT NULL DEFAULT 1,
current_streak      INTEGER NOT NULL DEFAULT 0,
longest_streak      INTEGER NOT NULL DEFAULT 0,
last_activity_date  TEXT    NOT NULL DEFAULT '',
created_at          TEXT    NOT NULL,
updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS achievements (
id                  INTEGER PRIMARY KEY AUTOINCREMENT,
user_id             INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
achievement_type    TEXT    NOT NULL,
title               TEXT    NOT NULL DEFAULT '',
description         TEXT    NOT NULL DEFAULT '',
earned_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS time_entries (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
started_at      TEXT    NOT NULL DEFAULT '',
ended_at        TEXT    NOT NULL DEFAULT '',
duration_minutes INTEGER NOT NULL DEFAULT 0,
note            TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS recurrence_rules (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
frequency       TEXT    NOT NULL DEFAULT 'weekly',
interval_val    INTEGER NOT NULL DEFAULT 1,
next_due        TEXT    NOT NULL DEFAULT '',
end_date        TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS goal_collaborators (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
role            TEXT    NOT NULL DEFAULT 'viewer',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS custom_fields (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
field_name      TEXT    NOT NULL,
field_value     TEXT    NOT NULL DEFAULT '',
field_type      TEXT    NOT NULL DEFAULT 'text',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS progress_snapshots (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id         INTEGER NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
total_tasks     INTEGER NOT NULL DEFAULT 0,
completed_tasks INTEGER NOT NULL DEFAULT 0,
percentage      REAL    NOT NULL DEFAULT 0.0,
captured_at     TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_preferences (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
channel         TEXT    NOT NULL DEFAULT 'in_app',
event_type      TEXT    NOT NULL DEFAULT 'all',
enabled         INTEGER NOT NULL DEFAULT 1,
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS personal_api_keys (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
name            TEXT    NOT NULL,
key_hash        TEXT    NOT NULL,
key_prefix      TEXT    NOT NULL DEFAULT '',
last_used_at    TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_blockers (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
task_id         INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
description     TEXT    NOT NULL,
blocker_type    TEXT    NOT NULL DEFAULT 'internal',
status          TEXT    NOT NULL DEFAULT 'open',
resolved_at     TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_widgets (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
widget_type     TEXT    NOT NULL,
position        INTEGER NOT NULL DEFAULT 0,
config_json     TEXT    NOT NULL DEFAULT '{}',
enabled         INTEGER NOT NULL DEFAULT 1,
created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS workspaces (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
name        TEXT NOT NULL,
owner_id    INTEGER NOT NULL,
description TEXT DEFAULT '',
invite_code TEXT DEFAULT '',
plan        TEXT DEFAULT 'free',
created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS workspace_members (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
workspace_id INTEGER NOT NULL,
user_id      INTEGER NOT NULL,
role         TEXT DEFAULT 'member',
joined_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
id                INTEGER PRIMARY KEY AUTOINCREMENT,
user_id           INTEGER NOT NULL,
title             TEXT NOT NULL,
body              TEXT DEFAULT '',
notification_type TEXT DEFAULT 'info',
source_type       TEXT DEFAULT '',
source_id         INTEGER,
read              INTEGER DEFAULT 0,
created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS activity_feed (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER NOT NULL,
action       TEXT NOT NULL,
entity_type  TEXT NOT NULL,
entity_id    INTEGER NOT NULL,
entity_title TEXT DEFAULT '',
details      TEXT DEFAULT '',
workspace_id INTEGER,
goal_id      INTEGER,
created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS comment_reactions (
id         INTEGER PRIMARY KEY AUTOINCREMENT,
comment_id INTEGER NOT NULL,
user_id    INTEGER NOT NULL,
emoji      TEXT DEFAULT '👍',
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS direct_messages (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
sender_id    INTEGER NOT NULL,
recipient_id INTEGER NOT NULL,
content      TEXT NOT NULL,
read         INTEGER DEFAULT 0,
created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS goal_chat_messages (
id         INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id    INTEGER NOT NULL,
user_id    INTEGER NOT NULL,
content    TEXT NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS email_notification_config (
id                   INTEGER PRIMARY KEY AUTOINCREMENT,
user_id              INTEGER NOT NULL UNIQUE,
digest_frequency     TEXT DEFAULT 'none',
notify_on_mention    INTEGER DEFAULT 1,
notify_on_assignment INTEGER DEFAULT 1,
notify_on_comment    INTEGER DEFAULT 1
);

CREATE TABLE IF NOT EXISTS push_subscriptions (
id         INTEGER PRIMARY KEY AUTOINCREMENT,
user_id    INTEGER NOT NULL,
endpoint   TEXT NOT NULL,
p256dh     TEXT DEFAULT '',
auth       TEXT DEFAULT '',
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS user_sessions (
id             INTEGER PRIMARY KEY AUTOINCREMENT,
user_id        INTEGER NOT NULL,
session_token  TEXT NOT NULL,
ip_address     TEXT DEFAULT '',
user_agent     TEXT DEFAULT '',
is_active      INTEGER DEFAULT 1,
last_activity  TEXT DEFAULT '',
created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS two_factor_config (
id                INTEGER PRIMARY KEY AUTOINCREMENT,
user_id           INTEGER NOT NULL UNIQUE,
totp_secret       TEXT DEFAULT '',
is_enabled        INTEGER DEFAULT 0,
backup_codes_hash TEXT DEFAULT '',
created_at        TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS sso_configs (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
org_id      INTEGER NOT NULL,
provider    TEXT    NOT NULL DEFAULT '',
entity_id   TEXT    NOT NULL DEFAULT '',
sso_url     TEXT    NOT NULL DEFAULT '',
certificate TEXT    NOT NULL DEFAULT '',
created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS ip_allowlist (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
org_id      INTEGER NOT NULL,
cidr_range  TEXT    NOT NULL DEFAULT '',
description TEXT    NOT NULL DEFAULT '',
created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS organizations (
id            INTEGER PRIMARY KEY AUTOINCREMENT,
name          TEXT    NOT NULL,
slug          TEXT    NOT NULL UNIQUE,
owner_id      INTEGER,
settings_json TEXT    NOT NULL DEFAULT '{}',
created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS org_members (
id      INTEGER PRIMARY KEY AUTOINCREMENT,
org_id  INTEGER NOT NULL,
user_id INTEGER NOT NULL,
role    TEXT    NOT NULL DEFAULT 'member',
UNIQUE(org_id, user_id)
);

CREATE TABLE IF NOT EXISTS branding_configs (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
org_id          INTEGER NOT NULL UNIQUE,
logo_url        TEXT    NOT NULL DEFAULT '',
primary_color   TEXT    NOT NULL DEFAULT '#1a1a2e',
secondary_color TEXT    NOT NULL DEFAULT '#16213e',
app_name        TEXT    NOT NULL DEFAULT 'teb',
favicon_url     TEXT    NOT NULL DEFAULT '',
created_at      TEXT    NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS saved_views (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
name         TEXT    NOT NULL,
view_type    TEXT    NOT NULL DEFAULT 'list',
filters_json TEXT    NOT NULL DEFAULT '{}',
sort_json    TEXT    NOT NULL DEFAULT '{}',
group_by     TEXT    NOT NULL DEFAULT '',
created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS dashboard_layouts (
id           INTEGER PRIMARY KEY AUTOINCREMENT,
user_id      INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
name         TEXT    NOT NULL,
widgets_json TEXT    NOT NULL DEFAULT '[]',
created_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS scheduled_reports (
id              INTEGER PRIMARY KEY AUTOINCREMENT,
user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
report_type     TEXT    NOT NULL DEFAULT 'progress',
frequency       TEXT    NOT NULL DEFAULT 'weekly',
recipients_json TEXT    NOT NULL DEFAULT '[]',
created_at      TEXT    NOT NULL,
last_sent_at    TEXT    NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS template_gallery (
id INTEGER PRIMARY KEY AUTOINCREMENT,
name TEXT NOT NULL,
description TEXT DEFAULT '',
author TEXT DEFAULT '',
category TEXT DEFAULT '',
template_json TEXT DEFAULT '{}',
downloads INTEGER DEFAULT 0,
rating REAL DEFAULT 0.0,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS blog_posts (
id INTEGER PRIMARY KEY AUTOINCREMENT,
title TEXT NOT NULL,
slug TEXT NOT NULL UNIQUE,
content TEXT DEFAULT '',
author TEXT DEFAULT '',
published INTEGER DEFAULT 0,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS roadmap_items (
id INTEGER PRIMARY KEY AUTOINCREMENT,
title TEXT NOT NULL,
description TEXT DEFAULT '',
status TEXT DEFAULT 'planned',
votes INTEGER DEFAULT 0,
category TEXT DEFAULT '',
target_date TEXT DEFAULT '',
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS feature_votes (
id INTEGER PRIMARY KEY AUTOINCREMENT,
user_id INTEGER NOT NULL,
roadmap_item_id INTEGER NOT NULL,
created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
UNIQUE(user_id, roadmap_item_id)
);

CREATE TABLE IF NOT EXISTS risk_assessments (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
task_id          INTEGER NOT NULL,
goal_id          INTEGER NOT NULL,
risk_score       REAL    NOT NULL DEFAULT 0.0,
risk_factors     TEXT    NOT NULL DEFAULT '[]',
estimated_delay  INTEGER NOT NULL DEFAULT 0,
assessed_at      TEXT    NOT NULL DEFAULT '',
created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS task_schedules (
id               INTEGER PRIMARY KEY AUTOINCREMENT,
task_id          INTEGER NOT NULL,
goal_id          INTEGER NOT NULL,
user_id          INTEGER NOT NULL,
scheduled_start  TEXT    NOT NULL DEFAULT '',
scheduled_end    TEXT    NOT NULL DEFAULT '',
calendar_slot    INTEGER NOT NULL DEFAULT 1,
created_at       TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS progress_reports (
id                INTEGER PRIMARY KEY AUTOINCREMENT,
goal_id           INTEGER NOT NULL,
user_id           INTEGER NOT NULL,
summary           TEXT    NOT NULL DEFAULT '',
metrics_json      TEXT    NOT NULL DEFAULT '{}',
blockers_json     TEXT    NOT NULL DEFAULT '[]',
next_actions_json TEXT    NOT NULL DEFAULT '[]',
created_at        TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS streaks (
id                  INTEGER PRIMARY KEY AUTOINCREMENT,
user_id             INTEGER NOT NULL,
current_streak      INTEGER NOT NULL DEFAULT 0,
longest_streak      INTEGER NOT NULL DEFAULT 0,
last_activity_date  TEXT    NOT NULL DEFAULT '',
streak_type         TEXT    NOT NULL DEFAULT 'daily',
created_at          TEXT    NOT NULL,
updated_at          TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS leaderboard (
id          INTEGER PRIMARY KEY AUTOINCREMENT,
user_id     INTEGER NOT NULL,
score       INTEGER NOT NULL DEFAULT 0,
rank        INTEGER NOT NULL DEFAULT 0,
period      TEXT    NOT NULL DEFAULT 'weekly',
created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS team_challenges (
id                INTEGER PRIMARY KEY AUTOINCREMENT,
title             TEXT    NOT NULL,
description       TEXT    NOT NULL DEFAULT '',
goal_type         TEXT    NOT NULL DEFAULT 'tasks_completed',
target_value      INTEGER NOT NULL DEFAULT 10,
current_value     INTEGER NOT NULL DEFAULT 0,
status            TEXT    NOT NULL DEFAULT 'active',
creator_id        INTEGER,
participants_json TEXT    NOT NULL DEFAULT '[]',
start_date        TEXT    NOT NULL DEFAULT '',
end_date          TEXT    NOT NULL DEFAULT '',
created_at        TEXT    NOT NULL
);
