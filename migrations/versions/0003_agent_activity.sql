-- 0003_agent_activity.sql
CREATE TABLE IF NOT EXISTS agent_activity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    goal_id INTEGER NOT NULL,
    agent_type TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '',
    status TEXT DEFAULT 'running',
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_agent_activity_goal ON agent_activity(goal_id);
