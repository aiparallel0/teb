-- 0002_task_priority.sql
ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'normal';
