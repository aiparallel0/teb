# teb — Task Execution Bridge

**teb** bridges the gap between your broad, vague goals and small, actionable tasks you can actually complete.

AI gives generic answers. teb asks the right clarifying questions and produces a focused, time-boxed action plan tailored to _you_.

---

## The Problem

"I want to earn money online" → AI returns 500-word fluff.
teb asks: *Do you have technical skills? How many hours/week? Do you need income in 30 days?*
Then produces 6 concrete, ordered tasks with realistic time estimates.

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
PATCH  /api/tasks/{id}                Update task status/notes
POST   /api/tasks/{id}/decompose      Break a task into smaller sub-tasks
```

Example:

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
| `OPENAI_API_KEY` | _(none)_ | Enables AI-powered decomposition |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `TEB_MODEL` | `gpt-4o-mini` | Model to use for AI decomposition |
| `DATABASE_URL` | `sqlite:///teb.db` | SQLite database path |
| `MAX_TASKS_PER_GOAL` | `20` | Cap on tasks per goal (AI mode) |

Without `OPENAI_API_KEY`, teb operates in **template mode** — fully offline, instant.

---

## Architecture

```
teb/
├── main.py        FastAPI app + REST endpoints
├── models.py      Goal and Task dataclasses
├── storage.py     SQLite data access layer (raw sqlite3)
├── decomposer.py  Template-based + AI decomposition engine
├── config.py      Environment variable configuration
├── templates/
│   └── index.html Single-page frontend
└── static/
    ├── app.js     Vanilla JS frontend logic
    └── style.css  CSS styling
tests/
├── test_decomposer.py  Unit tests for decomposition logic
└── test_api.py         Integration tests for API endpoints
```

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
