# TEB Quick-Start Guide

Get up and running with TEB in under 5 minutes.

## Prerequisites

| Requirement | Minimum |
|-------------|---------|
| Python      | 3.10+   |
| pip         | 22+     |
| SQLite      | 3.35+   |

## 1 – Install

```bash
pip install teb
```

Or from source:

```bash
git clone https://github.com/user/teb.git
cd teb
pip install -e .
```

## 2 – Start the Server

```bash
teb
# Server running on http://localhost:8000
```

Override the port with:

```bash
PORT=3000 teb
```

## 3 – Create Your Account

Open <http://localhost:8000> in your browser and register a new account. The
first user is automatically granted **admin** privileges.

## 4 – Create a Goal

1. Click **New Goal** on the dashboard.
2. Give it a title and optional description.
3. Press **Save**.

## 5 – Add Tasks

Inside a goal, click **Add Task** to break work into actionable items. Each
task can have a priority, due date, and assignee.

## 6 – Try AI Decomposition

Select a goal and click **AI Decompose**. TEB will suggest sub-tasks using
its AI pipeline so you can hit the ground running.

## 7 – Track Progress

Use the **Dashboard**, **Kanban**, **Calendar**, and **Timeline** views to
monitor your work. Check-ins and coaching nudges keep you accountable.

## Next Steps

- Read the full [User Guide](user-guide.md)
- Explore [Tutorials](tutorials.md)
- Check the [FAQ](faq.md) if you run into issues
