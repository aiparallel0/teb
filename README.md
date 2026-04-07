# teb — Task Execution Bridge

**teb** bridges the gap between your broad, vague goals and small, actionable tasks you can actually complete — and can **execute them autonomously** via registered APIs.

AI gives generic answers. teb asks the right clarifying questions, produces a focused, time-boxed action plan tailored to _you_, and can execute tasks via external APIs with AI-powered automation.

---

## The Problem

"I want to earn money online" → AI returns 500-word fluff.
teb asks: *Do you have technical skills? How many hours/week? Do you need income in 30 days?*
Then produces 6 concrete, ordered tasks with realistic time estimates.
Then tracks whether you actually earned any money.

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

1. Enter your goal (e.g. "learn Python", "earn money online")
2. Answer a few clarifying questions
3. Get a personalised task tree — check off tasks as you complete them
4. **Do daily check-ins** to get active coaching and stay on track
5. **Track outcome metrics** to measure real results, not just activity

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
  -d '{"title": "earn $500 freelancing online", "description": "complete beginner"}'

# Decompose
curl -X POST http://localhost:8000/api/goals/1/decompose

# Daily check-in
curl -X POST http://localhost:8000/api/goals/1/checkin \
  -H 'Content-Type: application/json' \
  -d '{"done_summary": "Created Upwork profile", "blockers": ""}'

# Track outcome
curl -X POST http://localhost:8000/api/goals/1/outcomes \
  -H 'Content-Type: application/json' \
  -d '{"label": "Revenue earned", "target_value": 500, "unit": "$"}'

curl -X PATCH http://localhost:8000/api/outcomes/1 \
  -H 'Content-Type: application/json' \
  -d '{"current_value": 150}'
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

### Focused Verticals

teb provides deepest support for two verticals where outcome measurement is most concrete:

1. **Money** — revenue earned, clients acquired, proposals sent
2. **Learning** — modules completed, practice hours, projects built

---

## Active Coaching System

### Daily Check-in (2 minutes)
- "What did you accomplish today?"
- "What's blocking you?"
- System detects mood (positive/neutral/frustrated/stuck) and provides tailored coaching feedback

### Stagnation Detection
- No check-in in 48+ hours → nudge
- Too many tasks in-progress simultaneously → focus advice
- Zero tasks completed → encouragement
- Persistent blockers → reframing suggestions

### Outcome Tracking
- Suggested metrics auto-populated based on goal vertical
- Progress bars with achievement percentage
- Outcome-focused rather than activity-focused

---

## Financial Autonomy Analysis

The vision includes giving AI access to financial resources for autonomous task execution.
Below is the analysis of the three proposed trust models:

### Hard Spending Caps per Task/Goal

| | Analysis |
|---|---|
| **Strengths** | Simple to implement; clear boundaries; prevents runaway spending; easy to audit |
| **Weaknesses** | Rigid — may block legitimate purchases; requires manual cap adjustment; doesn't account for variable costs |
| **Opportunities** | Could auto-adjust caps based on outcome metrics (earned $500 → unlock $50 budget); tiered caps by goal type |
| **Threats** | User may set caps too low (AI can't function) or too high (risk exposure); malicious API calls could drain to cap |
| **Verdict** | ✅ **Must-have as baseline.** Every financial integration needs a hard cap as the last line of defense. |

### Human Approval Above Threshold

| | Analysis |
|---|---|
| **Strengths** | Balances autonomy with control; builds trust incrementally; catches unexpected expenses |
| **Weaknesses** | Adds friction; user may auto-approve without reading; async approval creates delays |
| **Opportunities** | Smart thresholds that learn from user behavior; batch approval for routine purchases; progressive trust (lower threshold → higher threshold over time) |
| **Threats** | Approval fatigue leads to rubber-stamping; real-time purchases (domain auctions) may miss window |
| **Verdict** | ✅ **Essential layer.** Combine with caps: auto-approve under $X, require approval $X–$Y, block above $Y. |

### Sandbox Budget Mode (Simulate First)

| | Analysis |
|---|---|
| **Strengths** | Zero financial risk during learning; reveals AI's spending logic before real money moves; great for demos |
| **Weaknesses** | Simulation may not match reality (API pricing changes, availability); delays real progress |
| **Opportunities** | A/B test strategies in simulation; let user review simulated spend history before going live; gamification layer |
| **Threats** | Users may never leave sandbox (analysis paralysis); maintaining accurate simulation adds complexity |
| **Verdict** | ✅ **Valuable for onboarding.** New users start in sandbox; graduate to real spending after reviewing simulated results. |

### Financial API Comparison

| API/Service | Type | Best For | Pros | Cons |
|---|---|---|---|---|
| **Stripe** | Payments | Receiving money, subscriptions | Industry standard; excellent API; handles compliance | Not for spending/purchasing; payment processing fees |
| **Plaid** | Banking | Account visibility, transaction tracking | Read access to real bank data; categorization | Read-mostly; limited write/transfer capabilities |
| **Wise (TransferWise) API** | Transfers | International payments, freelancer payouts | Multi-currency; low fees; good API | Not for purchasing services; transfer delays |
| **Mercury API** | Business banking | Startup/freelancer banking | API-first bank; programmable transfers | US-only; requires business account |
| **Privacy.com** | Virtual cards | Controlled spending with disposable cards | Per-merchant limits; instant virtual cards; pause/close | US-only; $1000/month limit on free tier |
| **Crypto wallets** | Programmable money | Autonomous micro-transactions | No KYC for small amounts; instant; programmable | Volatility; limited merchant acceptance; complexity |

**Recommended approach for MVP:** Privacy.com virtual cards for spending (hard per-card limits) + Stripe for receiving income. This gives the AI a controlled debit mechanism with built-in caps while enabling income tracking.

---

## Running Tests

```bash
pytest tests/ -v
```

---

## Roadmap

1. ✅ Template-based goal decomposition with clarifying questions
2. ✅ AI-powered decomposition (OpenAI)
3. ✅ Active coaching (daily check-in + stagnation detection + nudges)
4. ✅ Outcome tracking with vertical-specific metrics
5. 🔲 Task execution engine (API orchestration)
6. 🔲 Financial autonomy layer (sandbox → approval → auto-spend)
7. 🔲 Multi-agent architecture (Researcher → Executor → Coach)
8. 🔲 Persistent project cache across sessions

---

## License

MIT
