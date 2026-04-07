# teb — Task Execution Bridge

**teb** bridges the gap between your broad, vague goals and small, actionable tasks you can actually complete — then actively coaches you to **achieve real outcomes**.

AI gives generic answers. teb asks the right clarifying questions, produces a focused action plan tailored to _you_, tracks your actual results, and nudges you when you stall.

---

## ⭐ Outcome Achievement — The North Star

> **Task completion means nothing if outcomes don't follow.**

teb's entire design is oriented around one principle: **measurable outcome achievement**.
Every feature exists to move you closer to a real, tangible result — not just a checked-off todo list.

- **Outcome Metrics**: Track what actually matters — revenue earned, skills acquired, projects shipped. Not just "tasks done."
- **Daily Check-ins**: 2-minute active coaching sessions that detect when you're stuck, frustrated, or drifting.
- **Stagnation Detection**: The system watches your progress and proactively nudges you when momentum drops.
- **Focused Verticals**: Deep support for the two domains where outcomes are most measurable — **making money** and **learning skills**.

If teb's users don't achieve their stated goals, teb has failed — regardless of how many tasks they completed.

---

## The Problem

"I want to earn money online" → AI returns 500-word fluff.
teb asks: *Do you have technical skills? How many hours/week? Do you need income in 30 days?*
Then produces 6 concrete, ordered tasks with realistic time estimates.
Then tracks whether you actually earned any money.

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

# ─── Active Coaching ──────────────────────────────────────────────────────
POST   /api/goals/{id}/checkin        Submit daily check-in (what did you do? blockers?)
GET    /api/goals/{id}/checkins       List check-in history
GET    /api/goals/{id}/nudge          Get active nudge (stagnation detection)
POST   /api/nudges/{id}/acknowledge   Acknowledge a nudge

# ─── Outcome Tracking ────────────────────────────────────────────────────
POST   /api/goals/{id}/outcomes       Create an outcome metric
GET    /api/goals/{id}/outcomes       List outcome metrics for a goal
PATCH  /api/outcomes/{id}             Update an outcome metric value
GET    /api/goals/{id}/outcome_suggestions  Get suggested metrics for goal's vertical
```

Example:

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
├── models.py      Goal, Task, CheckIn, OutcomeMetric, NudgeEvent dataclasses
├── storage.py     SQLite data access layer (raw sqlite3)
├── decomposer.py  Template-based + AI decomposition engine + active coaching
├── config.py      Environment variable configuration
├── templates/
│   └── index.html Single-page frontend
└── static/
    ├── app.js     Vanilla JS frontend logic
    └── style.css  CSS styling
tests/
├── test_decomposer.py  Unit tests for decomposition + coaching logic
├── test_api.py         Integration tests for all API endpoints
└── test_checkin.py     Tests for check-in, nudge, and outcome systems
```

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
