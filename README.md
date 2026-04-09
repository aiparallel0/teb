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

**What makes teb different:**

- **Not just planning but executing** — via API orchestration and browser automation, teb doesn't just tell you what to do, it does it for you
- **Adaptive micro-tasking** — drip-feed mode gives one task at a time and adapts based on completions, with follow-up questions at milestones instead of interrogating users upfront
- **Browser automation** — when APIs aren't available, teb generates and executes browser-based plans (navigate, click, type, extract) via Playwright
- **Not generic but experience-aware** — persistent user profiles learn your skills, pace, and style across goals; a knowledge base of successful paths means each new user benefits from what worked before
- **Success path learning** — auto-captures what works when goals are completed and feeds those patterns to new users via insights
- **Not advisory but accountable** — outcome metrics track real results (revenue earned, clients acquired), not just tasks checked off
- **Financial execution pipeline** — budget-aware task execution with per-transaction approval, daily limits, and category limits so AI can spend money on your behalf safely
- **Proactively discovers actions you didn't think of** — rule-based and AI-powered suggestion engine surfaces opportunities, optimizations, and risks
- **Multi-agent delegation with deep collaboration** — specialized AI agents (marketing, web dev, outreach, research, finance) collaborate via message passing, share context, and delegate to each other
- **External messaging** — Telegram bot and webhook integration for real-time notifications on nudges, task completions, and spending approvals
- **Pre-built integration catalog** — ships with knowledge of 10 popular services (Stripe, Namecheap, Vercel, SendGrid, GitHub, Cloudflare, Twitter, LinkedIn, Plausible, OpenAI) for smarter execution plans

---

## Quick Start

```bash
pip install -r requirements.txt
cp .env.example .env          # edit .env — set TEB_JWT_SECRET at minimum
uvicorn teb.main:app --reload
# Open http://localhost:8000
```

### Docker (one-command deployment)

```bash
cp .env.example .env  # edit TEB_JWT_SECRET and any API keys
docker compose up --build
# Open http://localhost:8000
```

---

## Installation

```bash
git clone <repo>
cd teb
pip install -r requirements.txt
cp .env.example .env  # review and edit
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
6. **Review proactive suggestions** — teb discovers actions you didn't think of

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
POST   /api/goals/{id}/checkin        Submit a daily check-in (coaching feedback returned)
GET    /api/goals/{id}/checkins       View check-in history
GET    /api/goals/{id}/nudge          Get stagnation nudge (if needed)
POST   /api/nudges/{id}/acknowledge   Acknowledge a nudge
POST   /api/goals/{id}/outcomes       Create an outcome metric
GET    /api/goals/{id}/outcomes       List outcome metrics
PATCH  /api/outcomes/{id}             Update outcome metric progress
GET    /api/goals/{id}/outcome_suggestions  Get suggested metrics for this goal
GET    /api/goals/{id}/suggestions    Get proactive action suggestions
POST   /api/suggestions/{id}          Accept or dismiss a suggestion
GET    /api/profile                   Get persistent user profile
PATCH  /api/profile                   Update user profile
GET    /api/knowledge/paths           List successful execution paths (knowledge base)
GET    /api/agents                    List all agent types and their capabilities
POST   /api/goals/{id}/orchestrate    Run multi-agent delegation on a goal
GET    /api/goals/{id}/handoffs       View agent delegation chain for a goal
GET    /api/goals/{id}/messages       View inter-agent messages (collaboration log)
POST   /api/tasks/{id}/browser        Execute a task via browser automation
GET    /api/tasks/{id}/browser_actions View browser automation actions for a task
GET    /api/integrations              List known service integrations (filterable by category)
GET    /api/integrations/catalog      Get the built-in integration catalog
GET    /api/integrations/match?q=     Find integrations matching a task description
GET    /api/integrations/{name}/endpoints  Get common API endpoints for a service
GET    /api/goals/{id}/drip           Get next adaptive drip task (one at a time)
GET    /api/goals/{id}/drip/question  Get next drip-mode clarifying question
POST   /api/goals/{id}/drip/clarify   Submit answer to drip-mode question
GET    /api/goals/{id}/insights       Get success path insights for similar goals
POST   /api/budgets                   Create a spending budget for a goal
GET    /api/goals/{id}/budgets        List spending budgets for a goal
PATCH  /api/budgets/{id}              Update budget limits
POST   /api/spending/request          Request to spend money on a task
POST   /api/spending/{id}/action      Approve or deny a spending request
GET    /api/goals/{id}/spending       List spending requests for a goal
POST   /api/messaging/config          Configure a messaging channel (Telegram/webhook)
GET    /api/messaging/configs         List messaging configurations
PATCH  /api/messaging/config/{id}     Update messaging configuration
DELETE /api/messaging/config/{id}     Delete messaging configuration
POST   /api/messaging/test/{id}       Send a test message to a channel
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

### Example: Goal → Decompose → Coach → Track

```bash
curl -X POST http://localhost:8000/api/goals \
  -H 'Content-Type: application/json' \
  -d '{"title": "earn $500 freelancing online", "description": "complete beginner"}'

# Decompose
curl -X POST http://localhost:8000/api/goals/1/decompose

# Get proactive suggestions
curl http://localhost:8000/api/goals/1/suggestions

# Daily check-in (returns coaching feedback)
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

# Check for stagnation nudges
curl http://localhost:8000/api/goals/1/nudge
```

### Multi-Agent Orchestration Example

```bash
# 1. Create a goal
curl -X POST http://localhost:8000/api/goals \
  -H 'Content-Type: application/json' \
  -d '{"title": "earn money online", "description": "I want to earn $500 freelancing"}'

# 2. Run multi-agent orchestration (coordinator delegates to specialists)
curl -X POST http://localhost:8000/api/goals/1/orchestrate
# Returns: strategy, tasks from all agents, handoff chain showing delegation flow

# 3. See which agents were involved and what they delegated
curl http://localhost:8000/api/goals/1/handoffs
# Returns: [{from_agent: "coordinator", to_agent: "marketing", ...}, ...]

# 4. List available agent types
curl http://localhost:8000/api/agents
# Returns: coordinator, marketing, web_dev, outreach, research, finance
```

**How orchestration works:**
1. **Coordinator** analyzes your goal and creates a high-level strategy
2. Coordinator **sends messages** to specialist agents for coordination context
3. Coordinator **delegates** to specialist agents (e.g., marketing, web_dev, outreach)
4. Each specialist reads messages from other agents, produces **concrete tasks**, and may sub-delegate
5. Example chain: `coordinator → marketing → web_dev` (marketing asks web_dev to build a landing page)
6. All handoffs and inter-agent messages are logged for full traceability

### Browser Automation Example

```bash
# Execute a task via browser automation (instead of API calls)
curl -X POST http://localhost:8000/api/tasks/1/browser
# Returns: plan (steps: navigate, click, type, etc.), actions taken, success/failure

# View browser actions for a task
curl http://localhost:8000/api/tasks/1/browser_actions
```

**How browser automation works:**
1. AI generates a step-by-step browser plan (navigate, click, type, extract, screenshot, wait)
2. If **Playwright** is installed, steps are executed in a headless browser automatically
3. If Playwright is not installed, the plan is returned as a guided walkthrough the user can follow
4. All actions are logged in `browser_actions` for traceability

### Integration Registry Example

```bash
# List all known service integrations
curl http://localhost:8000/api/integrations
# Returns: stripe, namecheap, vercel, sendgrid, github, cloudflare, twitter, linkedin, plausible, openai

# Find integrations matching a task
curl "http://localhost:8000/api/integrations/match?q=accept+payments+online"
# Returns: stripe (best match), plus others

# Get common API endpoints for a service
curl http://localhost:8000/api/integrations/stripe/endpoints
# Returns: POST /v1/customers, POST /v1/payment_intents, etc.

# Filter integrations by category
curl "http://localhost:8000/api/integrations?category=payment"
```

**Built-in integrations:** Stripe (payment), Namecheap (domain), Vercel (hosting), SendGrid (email), GitHub (development), Cloudflare (hosting), Twitter (social), LinkedIn (social), Plausible (analytics), OpenAI (AI).

### Adaptive Micro-Tasking (Drip Mode)

Instead of dumping all tasks at once, drip mode gives one task at a time:

```bash
# 1. Create a goal
curl -X POST http://localhost:8000/api/goals \
  -H 'Content-Type: application/json' \
  -d '{"title": "earn money online freelancing"}'

# 2. Answer up to 5 questions upfront (drip mode limits upfront questions)
curl http://localhost:8000/api/goals/1/drip/question
# Returns: {"done": false, "question": {"key": "technical_skills", "text": "...", "hint": "..."}}

curl -X POST http://localhost:8000/api/goals/1/drip/clarify \
  -H 'Content-Type: application/json' \
  -d '{"key": "technical_skills", "answer": "Python and web development"}'

# 3. Get your first task (created on demand)
curl http://localhost:8000/api/goals/1/drip
# Returns: {"task": {...}, "is_new": true, "adaptive_question": null, "message": "Task 1 of 6."}

# 4. Complete it, then get the next one (adapts based on your progress)
curl -X PATCH http://localhost:8000/api/tasks/1 \
  -H 'Content-Type: application/json' -d '{"status": "done"}'

curl http://localhost:8000/api/goals/1/drip
# Returns next task + possibly an adaptive follow-up question
```

After completing 2 tasks, drip mode asks "How are the tasks feeling — too easy, about right, or too challenging?" to adapt difficulty.

### Financial Execution Pipeline

Set spending budgets with daily/total limits and per-transaction approval:

```bash
# 1. Create a budget for your goal
curl -X POST http://localhost:8000/api/budgets \
  -H 'Content-Type: application/json' \
  -d '{"goal_id": 1, "daily_limit": 50, "total_limit": 500, "category": "general", "require_approval": true}'

# 2. Request to spend money on a task
curl -X POST http://localhost:8000/api/spending/request \
  -H 'Content-Type: application/json' \
  -d '{"task_id": 1, "amount": 12.99, "description": "Register domain", "service": "namecheap"}'
# Returns: {"request": {..., "status": "pending"}, "auto_approved": false}

# 3. Approve or deny the request
curl -X POST http://localhost:8000/api/spending/1/action \
  -H 'Content-Type: application/json' \
  -d '{"action": "approve"}'

# Or deny with reason:
curl -X POST http://localhost:8000/api/spending/1/action \
  -H 'Content-Type: application/json' \
  -d '{"action": "deny", "reason": "too expensive"}'
```

Budget categories: `general`, `hosting`, `domain`, `marketing`, `tools`, `services`. When `require_approval` is `false`, spending within limits is auto-approved.

### External Messaging (Telegram / Webhooks)

Get notifications via Telegram or webhooks when events happen:

```bash
# Configure Telegram notifications
curl -X POST http://localhost:8000/api/messaging/config \
  -H 'Content-Type: application/json' \
  -d '{
    "channel": "telegram",
    "config": {"bot_token": "123456:ABC-DEF", "chat_id": "987654321"},
    "notify_nudges": true,
    "notify_tasks": true,
    "notify_spending": true
  }'

# Or configure a webhook
curl -X POST http://localhost:8000/api/messaging/config \
  -H 'Content-Type: application/json' \
  -d '{"channel": "webhook", "config": {"url": "https://example.com/hooks/teb"}}'

# Test it
curl -X POST http://localhost:8000/api/messaging/test/1
```

Notifications are automatically sent for: nudges, task completions, spending approval requests, and goal completions.

---

## Configuration

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(none)_ | Enables Claude-powered AI features (preferred) |
| `TEB_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model for AI features |
| `OPENAI_API_KEY` | _(none)_ | Enables OpenAI-powered AI features |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `TEB_MODEL` | `gpt-4o-mini` | OpenAI model for AI features |
| `TEB_AI_PROVIDER` | `auto` | AI provider: `anthropic`, `openai`, or `auto` (prefers Anthropic) |
| `DATABASE_URL` | `sqlite:///teb.db` | SQLite database path |
| `MAX_TASKS_PER_GOAL` | `20` | Cap on tasks per goal (AI mode) |
| `TEB_EXECUTOR_TIMEOUT` | `30` | HTTP timeout (seconds) for API execution |
| `TEB_EXECUTOR_MAX_RETRIES` | `2` | Max retries for failed API calls |

Without an AI key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`), teb operates in **template mode** — fully offline, instant. When both keys are set, Anthropic (Claude) is preferred by default. Set `TEB_AI_PROVIDER=openai` to override.

---

## Architecture

```
teb/
├── main.py          FastAPI app + REST endpoints (goals, tasks, coaching, execution, browser, agents)
├── models.py        Goal, Task, CheckIn, OutcomeMetric, NudgeEvent, UserProfile,
│                    SuccessPath, ProactiveSuggestion, ApiCredential, ExecutionLog,
│                    AgentHandoff, AgentMessage, BrowserAction, Integration,
│                    SpendingBudget, SpendingRequest, MessagingConfig
├── storage.py       SQLite data access layer (raw sqlite3, 17 tables)
├── decomposer.py    Template-based + AI decomposition + coaching + drip mode + success paths
├── executor.py      AI-powered task execution engine (API calls via httpx)
├── browser.py       Browser automation engine (AI plan generation + Playwright execution)
├── agents.py        Multi-agent delegation system with inter-agent messaging
├── integrations.py  Pre-built integration catalog (10 services) + matching engine
├── messaging.py     External messaging (Telegram bots + webhooks) for notifications
├── ai_client.py     Unified AI client (Anthropic Claude + OpenAI)
├── config.py        Environment variable configuration
├── templates/
│   └── index.html   Single-page frontend
└── static/
    ├── app.js       Vanilla JS frontend logic
    └── style.css    CSS styling
tests/
├── test_decomposer.py           Unit tests for decomposition logic
├── test_executor.py             Unit tests for execution engine
├── test_checkin.py              Tests for coaching, nudges, outcomes, suggestions
├── test_agents.py               Tests for multi-agent delegation system
├── test_browser_integrations.py Tests for browser automation, integrations, agent messaging
├── test_new_features.py         Tests for drip mode, success paths, financial pipeline, messaging
└── test_api.py                  Integration tests for API endpoints
```

### Multi-Agent Delegation

teb uses specialized AI agents that collaborate and delegate to each other:

```
User goal: "earn money online"
    │
    ▼
┌─────────────┐
│ Coordinator │  Analyzes goal → creates strategy → sends coordination messages
└──────┬──────┘
       │ delegates to specialists (with shared context):
       ├──► Marketing Agent → positioning, content, SEO
       │    ├── 💬 messages Web Dev: "Need landing page with email capture"
       │    └──► Web Dev Agent → build landing page (reads Marketing's message)
       │    └──► Outreach Agent → run campaigns
       ├──► Research Agent → market validation, competitors
       │    └── 💬 messages Marketing: "Found untapped niche in X"
       ├──► Web Dev Agent → hosting, domain, deployment
       ├──► Outreach Agent → cold outreach, lead gen
       └──► Finance Agent → budgeting, pricing, payments
```

Each agent:
- Has a specific domain of expertise
- Produces concrete, actionable tasks
- **Sends messages** to other agents for coordination (shared context, requests, responses)
- Can delegate to other agents (up to 3 levels deep)
- Reads outputs from previously completed agents for enriched context
- Works in AI mode (Claude/OpenAI) or template mode (offline)
- All handoffs and messages are logged for full traceability

### How Execution Works

**API execution:**
1. **Register APIs**: User adds API credentials (Namecheap, Stripe, GitHub, etc.)
2. **AI Plans**: When `POST /api/tasks/{id}/execute` is called, AI analyzes the task + available APIs and produces a step-by-step execution plan
3. **Execute**: Each step (API call) is executed sequentially via httpx
4. **Log**: Every action is recorded in `execution_logs` — what was called, what happened, success/failure
5. **Status**: Task is marked `done` on success, `failed` on error

**Browser automation** (when APIs aren't enough):
1. **AI Plans**: When `POST /api/tasks/{id}/browser` is called, AI generates a browser automation plan (navigate, click, type, extract, screenshot, wait)
2. **Playwright execution**: If Playwright is installed, steps execute in a headless browser automatically
3. **Manual fallback**: Without Playwright, the plan is returned as guided steps the user can follow
4. **Log**: Every action is recorded in `browser_actions`

**Integration registry** enriches both:
- 10 pre-built service profiles (Stripe, Vercel, SendGrid, etc.) with known endpoints
- `GET /api/integrations/match?q=...` finds relevant services for any task
- Helps AI generate more accurate execution plans

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

## Persistent User Profile

teb maintains a persistent user profile that accumulates across goals:

- **Skills inventory** — tracks technical skills, soft skills, and tools you know
- **Available time** — hours per day you can realistically dedicate
- **Experience level** — evolves as you complete goals
- **Learning style preference** — video, reading, hands-on, or mixed
- **Track record** — goals completed, total tasks finished

This means teb doesn't re-ask "do you have technical skills?" every time you create a new goal. It already knows.

---

## Proactive Suggestions

teb doesn't just execute your plan — it **discovers actions you didn't think of**:

| Category | Example |
|---|---|
| **Opportunity** | "Create a portfolio before reaching out to clients — 3x more likely to get responses" |
| **Optimization** | "Automate repetitive parts of your workflow with AI tools (Zapier, Make.com)" |
| **Risk** | "You have 3 tasks in progress — focus on finishing one before starting another" |
| **Learning** | "Try teaching what you've learned to someone else (Feynman technique)" |

Suggestions are context-aware — they change based on your goal type, progress, and current task state.

---

## Knowledge Base (Success Paths)

When a goal is completed successfully, teb records the execution path:

- Which tasks were completed and in what order
- What the outcome metrics showed
- How long it took

These **success paths** are reused for similar future goals. Instead of starting from scratch, teb can say: "User A went from zero to $500/month freelancing using these 12 steps — here's a proven path."

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

### Detailed Financial Possibilities

The financial autonomy layer opens up these concrete use cases:

**Tier 1 — Low-Cost Automation ($0–$20/action)**
| Action | APIs Needed | Estimated Cost | Risk |
|---|---|---|---|
| Register a domain name | Namecheap, Cloudflare, Porkbun | $1–$12/year | Low — reversible within grace period |
| Set up cloud hosting | Render, Railway, Vercel, Fly.io | $0–$7/month | Low — free tiers available |
| Purchase a design template | Gumroad, CreativeMarket API | $5–$20 one-time | Low — digital goods |
| Run a small ad campaign test | Meta Ads API, Google Ads API | $5–$20 | Medium — budget can be set |
| Subscribe to a SaaS tool | Stripe checkout, direct API | $5–$15/month | Low — cancelable |

**Tier 2 — Medium Investment ($20–$200/action)**
| Action | APIs Needed | Estimated Cost | Risk |
|---|---|---|---|
| Commission freelance work | Upwork API, Fiverr API | $20–$200 per gig | Medium — quality varies |
| Purchase premium API access | Various | $20–$100/month | Medium — recurring cost |
| Run a marketing campaign | Mailchimp, SendGrid, Meta Ads | $50–$200 | Medium — ROI uncertain |
| Buy stock photography/assets | Shutterstock, Adobe Stock API | $30–$100 | Low — one-time purchase |

**Tier 3 — Significant Spending ($200+/action)**
| Action | APIs Needed | Estimated Cost | Risk |
|---|---|---|---|
| Launch a paid advertising funnel | Google/Meta Ads + Stripe | $200–$1000+ | High — requires monitoring |
| Hire contractors via API | Deel, Remote API | $500+ | High — commitment required |
| Purchase inventory/supplies | Shopify, wholesale APIs | Variable | High — physical goods |

**Safety Architecture:**
```
$0–$5/action   → Auto-execute (within daily cap)
$5–$50/action  → Notify user, execute after 1-hour delay unless vetoed
$50–$200       → Require explicit approval before execution
$200+          → Require approval + confirmation code
All actions    → Hard daily cap ($X), hard monthly cap ($Y), kill switch
```

**Revenue Tracking Integration:**
When the AI spends money, it should also track the return:
- Domain registered ($12) → website launched → first sale ($47) → ROI: 292%
- Ad spend ($50) → 3 leads → 1 client ($500) → ROI: 900%
- This data feeds back into the knowledge base as a proven success path

---

## Multi-Agent Architecture (Planned)

teb uses an **adaptive** multi-agent model where a meta-agent decides which specialist to invoke based on user state. Agents can request resources from each other and spawn new agents.

```
┌──────────────┐
│  Meta-Agent   │  Decides which agent to activate based on user context
│  (Conductor)  │  Routes requests, manages inter-agent communication
└──────┬───────┘
       │
  ┌────┼────────────────────────┐
  │    │    │    │              │
  ▼    ▼    ▼    ▼              ▼
┌────┐┌────┐┌────┐┌────────┐┌────────┐
│Res.││Plan││Exec││ Coach  ││Finance │
│    ││    ││    ││        ││        │
│Find││De- ││API ││Check-in││Budget  │
│info││comp││call││Nudge   ││Approve │
│    ││ose ││    ││Suggest ││Track   │
└────┘└────┘└────┘└────────┘└────────┘
```

**Agent Roles:**
| Agent | Responsibility | Can Request From |
|---|---|---|
| **Researcher** | Gather information, validate assumptions, find opportunities | External APIs, Web |
| **Planner** | Decompose goals, create task trees, schedule | Researcher, Knowledge Base |
| **Executor** | Execute API calls, automate actions | All registered APIs |
| **Coach** | Check-ins, nudges, mood detection, encouragement | Planner (for re-planning), User |
| **Finance** | Budget management, approval flows, ROI tracking | Executor (for spending), User (for approval) |

**Inter-Agent Communication:**
- Any agent can request resources from another agent
- Any agent can request the meta-agent to start a new specialized agent
- All communication is logged in the execution log for transparency
- Agents share state through the common database (goals, tasks, metrics)

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
5. ✅ Persistent user profile (cross-goal learning)
6. ✅ Proactive suggestion engine (discovers actions user didn't think of)
7. ✅ Knowledge base foundation (success path recording and reuse)
8. ✅ Task execution engine (API orchestration)
9. ✅ Financial autonomy layer (budget management, per-transaction approval, daily limits)
10. ✅ Multi-agent architecture (coordinator, marketing, web_dev, outreach, research, finance with delegation)
11. ✅ Real-time notifications (Telegram bot + webhooks)
12. ✅ Success path learning (auto-record + recommend proven paths via insights)
13. ✅ Agent-to-agent communication protocol (message passing + shared context)
14. ✅ Persistent user cache across sessions (user_profiles + user_behavior)
15. ✅ Financial API integrations (Stripe + Mercury banking)
16. 🔲 Additional payment providers (Privacy.com virtual cards, Plaid banking)
17. 🔲 SMS notifications
18. 🔲 Payment sandbox/simulation mode

---

## License

MIT
