# TEB System Architecture

An overview of TEB's technical design, components, and data flow.

---

## High-Level Architecture

```
┌────────────┐      HTTP / WS      ┌──────────────┐
│  Browser /  │ ◄──────────────────► │  FastAPI App  │
│  API Client │                     │  (main.py)    │
└────────────┘                     └──────┬───────┘
                                          │
                         ┌────────────────┼────────────────┐
                         │                │                │
                   ┌─────▼─────┐   ┌─────▼─────┐   ┌─────▼─────┐
                   │  Storage   │   │    AI      │   │  Execution│
                   │  (SQLite)  │   │  Pipeline  │   │  Engine   │
                   └───────────┘   └───────────┘   └───────────┘
```

## Components

### FastAPI Application (`teb/main.py`)

The central HTTP server built with [FastAPI](https://fastapi.tiangolo.com/).
It exposes a REST API and serves the single-page frontend.

Key responsibilities:

- Request routing and authentication
- Input validation
- Rate limiting
- Session management (cookie-based)
- Admin endpoints

### Storage Layer (`teb/storage.py`)

All persistence goes through the storage module. It wraps a single **SQLite**
database using Python's built-in `sqlite3` module.

Design decisions:

- **Schema-on-start** – `init_db()` runs `CREATE TABLE IF NOT EXISTS` for
  every table on startup, making migrations automatic.
- **`_conn()` context manager** – provides a connection with WAL mode and
  foreign keys enabled.
- **Row converter functions** – `_row_to_user()`, `_row_to_goal()`, etc.
  translate `sqlite3.Row` objects into dataclass instances.
- **Safe migrations** – `_safe_add_column()` adds columns idempotently for
  schema evolution.

### Models (`teb/models.py`)

Plain Python `@dataclass` classes. Each model has:

- Typed fields with sensible defaults
- An `id: Optional[int]` primary key
- A `created_at: Optional[datetime]` timestamp
- A `to_dict()` method for JSON serialisation

### Frontend

| File                     | Purpose                          |
|--------------------------|----------------------------------|
| `teb/templates/index.html` | Main HTML shell (Jinja2)       |
| `teb/static/app.js`      | All client-side logic           |
| `teb/static/style.css`   | All styles                      |
| `teb/static/sw.js`       | Service worker for offline/PWA  |

The frontend is a single-page application that communicates with the API via
`fetch()`. No build step is required.

### AI Pipeline

Goal decomposition and coaching suggestions are powered by an AI pipeline:

1. The user triggers decomposition on a goal.
2. The backend builds a prompt with goal context.
3. The AI model returns structured task suggestions.
4. Results are presented for user review.

### Execution Engine

The execution engine runs tasks automatically:

1. An **ExecutionContext** is created with environment variables and config.
2. Each step is logged in **ExecutionLog** entries.
3. **ExecutionCheckpoints** allow long-running tasks to be resumed.
4. Outputs are stored as **TaskArtifacts**.

### Browser Automation

Built-in browser automation allows TEB to interact with web pages:

- Actions are defined declaratively (navigate, click, type, screenshot).
- Each action is recorded as a `BrowserAction` row for auditability.
- Results (screenshots, extracted text) are attached to the task.

### Agent System

Agents are autonomous workers:

- **AgentMessage** – message log between user and agent
- **AgentSchedule** – cron-like triggers
- **AgentFlow** – multi-step orchestration graphs
- **AgentHandoff** – transfer work between agents
- **AgentGoalMemory** – persistent context across runs

## Data Flow

```
User action
    │
    ▼
FastAPI endpoint  ──►  Validate input
    │
    ▼
Storage function  ──►  SQLite read / write
    │
    ▼
Return JSON response  ──►  Frontend renders
```

## Database Schema

All tables are created in `init_db()`. Key tables:

| Table             | Purpose                          |
|-------------------|----------------------------------|
| users             | User accounts                    |
| goals             | Top-level goals                  |
| tasks             | Actionable items inside goals    |
| milestones        | Intermediate goal checkpoints    |
| check_ins         | Regular status updates           |
| execution_logs    | Execution pipeline records       |
| browser_actions   | Browser automation log           |
| agent_messages    | Agent communication              |
| webhooks          | Outgoing webhook configs         |
| integrations      | Third-party connections          |
| plugins           | Installed plugin manifests       |
| community_links   | Community resource links         |
| template_gallery  | Shared goal templates            |
| blog_posts        | Blog / changelog content         |
| roadmap_items     | Public roadmap entries           |
| feature_votes     | User votes on roadmap items      |

## Deployment

TEB can be deployed via:

- **Docker** – `Dockerfile` and `docker-compose.yml` included
- **Direct** – `pip install teb && teb`
- **Behind a proxy** – set `BASE_PATH` for sub-path hosting

See the [Quick-Start Guide](quickstart.md) for details.
