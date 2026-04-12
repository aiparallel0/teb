# TEB User Guide

Comprehensive reference for every feature in TEB.

---

## Table of Contents

1. [Getting Started](#getting-started)
2. [Goals](#goals)
3. [Tasks](#tasks)
4. [AI Decomposition](#ai-decomposition)
5. [Execution Pipeline](#execution-pipeline)
6. [Browser Automation](#browser-automation)
7. [Agents](#agents)
8. [Check-Ins & Coaching](#check-ins--coaching)
9. [Dashboard](#dashboard)
10. [Kanban Board](#kanban-board)
11. [Calendar View](#calendar-view)
12. [Timeline View](#timeline-view)
13. [Integrations](#integrations)
14. [Webhooks](#webhooks)
15. [Templates](#templates)
16. [Plugins](#plugins)
17. [Workspaces & Collaboration](#workspaces--collaboration)
18. [Notifications](#notifications)
19. [Admin & Security](#admin--security)
20. [Keyboard Shortcuts](#keyboard-shortcuts)

---

## Getting Started

See the [Quick-Start Guide](quickstart.md) for installation and first-run
instructions. Once logged in you land on the **Dashboard** which summarises
your goals, upcoming tasks, and recent activity.

## Goals

Goals are the top-level planning unit. Each goal has:

- **Title** & **Description**
- **Priority** – low / medium / high / critical
- **Status** – not_started / in_progress / completed / archived
- **Due date** (optional)
- **Milestones** – intermediate checkpoints with target dates
- **Collaborators** – invite other users to share a goal

### Creating a Goal

Click **New Goal** from the dashboard or sidebar. Fill in the form and press
**Save**. You can also create goals via the REST API.

### Goal Templates

Save any goal as a template to reuse its structure later. Browse community
templates in the **Template Gallery**.

## Tasks

Tasks live inside goals and represent individual pieces of work.

| Field       | Description                              |
|-------------|------------------------------------------|
| title       | Short summary                            |
| description | Detailed notes (Markdown supported)      |
| status      | pending / in_progress / done / blocked   |
| priority    | 1 (low) – 4 (critical)                  |
| due_date    | ISO-8601 date string                     |
| assigned_to | User ID of assignee                      |
| tags        | Comma-separated labels                   |

### Task Comments & Reactions

Leave comments on tasks for discussion. React to comments with emoji.

### Task Blockers

Mark a task as blocked by another task to surface dependency issues.

### Time Tracking

Start / stop a timer on any task to log time entries.

## AI Decomposition

Select a goal and choose **AI Decompose**. The system analyses your goal
description and generates a set of suggested tasks. You can accept, edit, or
discard each suggestion before saving.

## Execution Pipeline

TEB can execute tasks automatically through its execution pipeline:

1. **Context** is gathered for the task.
2. An **Execution Log** records each step.
3. **Checkpoints** allow resuming interrupted runs.
4. Results are saved as **Task Artifacts**.

## Browser Automation

The browser automation module can interact with web pages on your behalf:

- Navigate to URLs
- Click, type, scroll
- Take screenshots
- Extract data

Actions are logged as `BrowserAction` records for auditability.

## Agents

Agents are autonomous workers that process tasks:

- **Agent Messages** – communication log for each agent
- **Agent Schedules** – run agents on a cron-like schedule
- **Agent Flows** – multi-step orchestration
- **Agent Handoffs** – transfer work between agents
- **Agent Goal Memory** – persistent context per goal

## Check-Ins & Coaching

Regular check-ins keep you accountable:

- Daily / weekly / custom cadence
- Mood & confidence tracking
- AI-generated coaching nudges and proactive suggestions

## Dashboard

The dashboard shows:

- **Progress rings** per goal
- **Upcoming tasks** sorted by due date
- **Activity feed** of recent events
- **Widgets** – customisable layout (drag & drop)

## Kanban Board

Drag tasks between columns (To Do → In Progress → Done). Customise column
names and WIP limits.

## Calendar View

See tasks and milestones on a month / week / day calendar.

## Timeline View

Gantt-style timeline showing task durations, dependencies, and milestones.

## Integrations

Connect TEB with external services:

- **OAuth Connections** – Google, GitHub, Slack, etc.
- **Integration Templates** – pre-built recipes
- **Integration Listings** – marketplace of available integrations

## Webhooks

Send HTTP callbacks when events occur. See [Webhook Docs](webhooks.md) for
payload schemas and retry behaviour.

## Templates

- **Goal Templates** – reusable goal structures
- **Template Gallery** – community-shared templates with ratings

## Plugins

Extend TEB with plugins:

- **Plugin Manifests** define metadata and entry points
- **Plugin Views** inject custom UI
- **Plugin Listings** in the marketplace

See the [Plugin Guide](plugin-guide.md) for development instructions.

## Workspaces & Collaboration

Organise users into **Workspaces**. Each workspace has members with roles
(owner / admin / member). Share goals and tasks within a workspace.

## Notifications

- **In-app** notifications
- **Email** notifications (configurable)
- **Push** notifications (Web Push API)
- **Notification preferences** per user

## Admin & Security

- **SSO / SAML** configuration
- **IP allow-lists**
- **API keys** (personal access tokens)
- **Audit log** of all significant events
- **Spending budgets** and approval workflows

## Keyboard Shortcuts

| Shortcut        | Action              |
|-----------------|---------------------|
| `g d`           | Go to Dashboard     |
| `g k`           | Go to Kanban        |
| `g c`           | Go to Calendar      |
| `n g`           | New Goal            |
| `n t`           | New Task            |
| `?`             | Show shortcut help  |
