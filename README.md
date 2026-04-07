# teb — Task Execution Bridge

**teb** bridges the gap between your broad, vague goals and small, actionable tasks you can actually complete — and can **execute them autonomously** via registered APIs.

AI gives generic answers. teb asks the right clarifying questions, produces a focused, time-boxed action plan tailored to _you_, and can execute tasks via external APIs with AI-powered automation.

---

## The Problem

"I want to earn money online" → AI returns 500-word fluff.
teb asks: *Do you have technical skills? How many hours/week? Do you need income in 30 days?*
Then produces 6 concrete, ordered tasks with realistic time estimates.

**The execution gap:** Even with a perfect plan, people lack the will or knowledge to act. teb closes this gap by letting AI agents execute tasks autonomously via registered APIs — registering domains, creating accounts, sending emails, or calling any REST service on your behalf.

---

## Quick Start

```bash
pip install -r requirements.txt
uvicorn teb.main:app --reload
# Open http://localhost:8000
```

---

## Installation

```bash
git clone <repo>
cd teb
pip install -r requirements.txt
```

---

## Usage

### Web UI

```bash
uvicorn teb.main:app --reload --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000` in your browser.

1. Enter your goal (e.g. "learn Python", "get fit", "earn money online")
2. Answer a few clarifying questions
3. Get a personalised task tree — check off tasks as you complete them

### REST API

```
POST   /api/goals                     Create a new goal
GET    /api/goals                     List all goals
GET    /api/goals/{id}                Get goal + tasks
POST   /api/goals/{id}/decompose      Decompose goal into tasks
GET    /api/goals/{id}/next_question  Get next clarifying question
POST   /api/goals/{id}/clarify        Submit answer to a clarifying question
GET    /api/goals/{id}/focus          Get the single next task to work on (focus mode)
GET    /api/goals/{id}/progress       Get completion stats and estimated time remaining
GET    /api/tasks?goal_id=&status=    List tasks (filterable)
POST   /api/tasks                     Create a custom task manually
PATCH  /api/tasks/{id}                Update task status/notes/title/order
DELETE /api/tasks/{id}                Delete a task and its children
POST   /api/tasks/{id}/decompose      Break a task into smaller sub-tasks (max depth 3)
POST   /api/tasks/{id}/execute        Execute a task autonomously via registered APIs
GET    /api/tasks/{id}/executions     View execution log for a task
POST   /api/credentials               Register an external API credential
GET    /api/credentials               List all registered API credentials
DELETE /api/credentials/{id}          Remove an API credential
```

### Task Execution Example

```bash
# 1. Register an external API
curl -X POST http://localhost:8000/api/credentials \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Namecheap",
    "base_url": "https://api.namecheap.com",
    "auth_header": "X-Api-Key",
    "auth_value": "your-api-key-here",
    "description": "Domain registration and DNS management API"
  }'

# 2. Create a goal and decompose it
curl -X POST http://localhost:8000/api/goals \
  -H 'Content-Type: application/json' \
  -d '{"title": "launch my website", "description": "register a domain and set up hosting"}'

curl -X POST http://localhost:8000/api/goals/1/decompose

# 3. Execute a task autonomously (AI plans the API calls)
curl -X POST http://localhost:8000/api/tasks/1/execute

# 4. View the execution log
curl http://localhost:8000/api/tasks/1/executions
```

### Example: Goal → Decompose → Execute

```bash
curl -X POST http://localhost:8000/api/goals \
  -H 'Content-Type: application/json' \
  -d '{"title": "learn Python", "description": "complete beginner"}'

# Decompose immediately (skipping clarifying questions)
curl -X POST http://localhost:8000/api/goals/1/decompose

# Mark a task done
curl -X PATCH http://localhost:8000/api/tasks/3 \
  -H 'Content-Type: application/json' \
  -d '{"status": "done"}'
```

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `OPENAI_API_KEY` | _(none)_ | Enables AI-powered decomposition and execution |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `TEB_MODEL` | `gpt-4o-mini` | Model to use for AI decomposition/execution |
| `DATABASE_URL` | `sqlite:///teb.db` | SQLite database path |
| `MAX_TASKS_PER_GOAL` | `20` | Cap on tasks per goal (AI mode) |
| `TEB_EXECUTOR_TIMEOUT` | `30` | HTTP timeout (seconds) for API execution |
| `TEB_EXECUTOR_MAX_RETRIES` | `2` | Max retries for failed API calls |

Without `OPENAI_API_KEY`, teb operates in **template mode** — fully offline, instant. Task execution requires `OPENAI_API_KEY` for AI-powered API call planning.

---

## Architecture

```
teb/
├── main.py        FastAPI app + REST endpoints
├── models.py      Goal, Task, ApiCredential, ExecutionLog dataclasses
├── storage.py     SQLite data access layer (raw sqlite3)
├── decomposer.py  Template-based + AI decomposition engine
├── executor.py    AI-powered task execution engine (API calls via httpx)
├── config.py      Environment variable configuration
├── templates/
│   └── index.html Single-page frontend
└── static/
    ├── app.js     Vanilla JS frontend logic
    └── style.css  CSS styling
tests/
├── test_decomposer.py  Unit tests for decomposition logic
├── test_executor.py    Unit tests for execution engine
└── test_api.py         Integration tests for API endpoints
```

### How Execution Works

1. **Register APIs**: User adds API credentials (Namecheap, Stripe, GitHub, etc.)
2. **AI Plans**: When `POST /api/tasks/{id}/execute` is called, AI analyzes the task + available APIs and produces a step-by-step execution plan
3. **Execute**: Each step (API call) is executed sequentially via httpx
4. **Log**: Every action is recorded in `execution_logs` — what was called, what happened, success/failure
5. **Status**: Task is marked `done` on success, `failed` on error

### Task Statuses

| Status | Meaning |
|---|---|
| `todo` | Not started |
| `in_progress` | User is working on it |
| `executing` | Being executed autonomously by teb |
| `done` | Completed (manually or by execution) |
| `failed` | Automated execution failed |
| `skipped` | User chose to skip |

### Decomposition Templates

| Template | Trigger keywords |
|---|---|
| `make_money_online` | money/income/earn + online/internet |
| `learn_skill` | learn/study/master/understand |
| `get_fit` | fit/workout/exercise/gym/weight |
| `build_project` | build/create/develop + app/website/tool |
| `generic` | everything else |

---

## Running Tests

```bash
pytest tests/ -v
```

---

## License

MIT
