# teb — Task Execution Bridge

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.12](https://img.shields.io/badge/Python-3.12-3776AB.svg)](https://python.org)
[![Tests: 578 passing](https://img.shields.io/badge/Tests-578_passing-brightgreen.svg)](#running-tests)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115-009688.svg)](https://fastapi.tiangolo.com)

> *Humans are will without infinite execution; AI is infinite execution without will — teb sits at that seam, taking your raw intentions and dissolving everything beneath them into solved problems. You stop managing tasks and start governing outcomes.*

---

## Philosophy

Every AI chatbot, course platform, and productivity app fails at the same point: they hand you advice and walk away. They generate generic answers even when they know your skills, budget, and timeline. The real bottleneck is not information — it is the will and knowledge to narrow an infinite possibility space into focused, actionable chunks, then *execute* them.

teb closes the loop. It combines AI's ability to decompose, research, and automate with structured micro-tasking that adapts to *your* context. It does not just tell you what to do — it registers the domain, sends the outreach emails, manages the money, and coaches you through the parts only a human can handle.

---

## The Problem

"I want to earn money online" → AI returns 500-word fluff.
teb asks: *Do you have technical skills? How many hours/week? Do you need income in 30 days?*
Then produces 6 concrete, ordered tasks with realistic time estimates.
Then **executes** what it can via APIs and browser automation.
Then tracks whether you actually earned any money.

**What makes teb different:**

| Feature | Traditional AI | teb |
|---|---|---|
| Planning | Generic advice | Structured decomposition adapted to your skills, budget, timeline |
| Execution | "Here's what to do" | Autonomous API calls, browser automation, financial pipeline |
| Follow-up | Nothing | Daily check-ins, stagnation nudges, mood-aware coaching |
| Measurement | Task checkboxes | Outcome metrics (revenue, clients, conversions) |
| Learning | Forgets everything | Persistent user profile + knowledge base of proven paths |
| Collaboration | Single agent | 6 specialist AI agents that delegate and message each other |

---

## Key Features

- **Adaptive micro-tasking** — drip-feed mode gives one task at a time, adapting based on completions with follow-up questions at milestones
- **Autonomous execution** — AI plans and executes API calls via registered credentials (httpx)
- **Browser automation** — Playwright-powered headless browser plans (navigate, click, type, extract, screenshot)
- **Multi-agent delegation** — 6 specialist agents (coordinator, marketing, web_dev, outreach, research, finance) collaborate via message passing
- **Financial execution pipeline** — real payment integration (Mercury banking + Stripe) with budget controls, daily limits, and per-transaction approval
- **Service discovery** — 50+ curated tools/services matched to your goal and skill level, plus AI-powered discovery
- **Active coaching** — daily check-ins, mood detection, stagnation nudges, coaching feedback
- **Outcome tracking** — vertical-specific metrics (revenue earned, clients acquired), not just task checkboxes
- **Proactive suggestions** — rule-based and AI-powered engine surfaces opportunities, optimizations, and risks
- **Persistent user profile** — skills, pace, style, and track record accumulate across goals
- **Knowledge base** — success paths auto-captured and recommended to new users
- **Pre-built integration catalog** — 25 popular services (Stripe, Namecheap, Vercel, SendGrid, GitHub, Cloudflare, Twitter, LinkedIn, Plausible, OpenAI, DigitalOcean, AWS S3, Twilio, HubSpot, Airtable, Notion, Slack, Discord, Shopify, Mailgun, Resend, Supabase, Anthropic, Google Maps, Zapier) with API endpoint metadata
- **Admin panel** — web UI and REST API for user management, account unlocking, platform stats, and integration management (role-gated to admin users)
- **External messaging** — Telegram bot and webhook notifications for nudges, completions, spending approvals
- **Credential vault** — Fernet-encrypted storage for API keys

---

## Quick Start

### One-liner

```bash
git clone https://github.com/aiparallel0/teb.git && cd teb && bash start.sh
```

`start.sh` auto-generates `TEB_JWT_SECRET` and `TEB_SECRET_KEY`, copies `.env.example` to `.env` if absent, installs dependencies, and starts the server. Pass `--docker` for Docker mode.

### pip install

```bash
pip install teb
cp .env.example .env   # set TEB_JWT_SECRET (TEB_SECRET_KEY is auto-generated)
teb                     # starts the server at http://localhost:8000
```

### Manual

```bash
pip install -r requirements.txt
cp .env.example .env          # edit .env — set TEB_JWT_SECRET at minimum
uvicorn teb.main:app --reload
# Open http://localhost:8000
```

### Docker

```bash
cp .env.example .env  # edit TEB_JWT_SECRET and any API keys
docker compose up --build
# Open http://localhost:8000
```

The container runs as a non-root user with a health check at `/health`. Data persists via the `teb-data` Docker volume.

### Pre-built Docker image

```bash
docker pull aiparallel0/teb:latest
docker run -e TEB_JWT_SECRET=your-secret -p 8000:8000 aiparallel0/teb
```

---

## Authentication

All API endpoints (except `/health`, `/api/auth/register`, and `/api/auth/login`) require a JWT bearer token.

```bash
# 1. Register
curl -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "strongpassword"}'

# 2. Login — returns access_token + refresh_token
curl -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "strongpassword"}'

# 3. Use the token in subsequent requests
export TOKEN="eyJ..."
curl http://localhost:8000/api/auth/me \
  -H "Authorization: Bearer $TOKEN"

# 4. Refresh when expired
curl -X POST http://localhost:8000/api/auth/refresh \
  -H 'Content-Type: application/json' \
  -d '{"refresh_token": "..."}'
```

Features: bcrypt password hashing, RBAC (user/admin roles), account locking after failed logins, refresh token rotation, rate limiting (20 req/min per IP on auth endpoints).

---

## REST API Reference

All endpoints require `Authorization: Bearer <token>` unless marked *(no auth)*.

### Health & Auth

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/health` | Health check *(no auth)* |
| `POST` | `/api/auth/register` | Create account *(no auth)* |
| `POST` | `/api/auth/login` | Login, receive JWT *(no auth)* |
| `GET` | `/api/auth/me` | Get current user |
| `POST` | `/api/auth/refresh` | Refresh access token |
| `POST` | `/api/auth/logout` | Invalidate refresh token |

### Goals

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/goals` | Create a new goal |
| `GET` | `/api/goals` | List all goals |
| `GET` | `/api/goals/{id}` | Get goal + tasks |
| `POST` | `/api/goals/{id}/decompose` | Decompose goal into tasks |
| `GET` | `/api/goals/{id}/next_question` | Get next clarifying question |
| `POST` | `/api/goals/{id}/clarify` | Submit answer to a clarifying question |
| `GET` | `/api/goals/{id}/focus` | Get the single next task to work on |
| `GET` | `/api/goals/{id}/progress` | Completion stats and estimated time remaining |

### Tasks

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/tasks` | List tasks (filterable by goal_id, status) |
| `POST` | `/api/tasks` | Create a custom task manually |
| `PATCH` | `/api/tasks/{id}` | Update task status/notes/title/order |
| `DELETE` | `/api/tasks/{id}` | Delete a task and its children |
| `POST` | `/api/tasks/{id}/decompose` | Break a task into sub-tasks (max depth 3) |

### Execution

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/tasks/{id}/execute` | Execute a task autonomously via registered APIs |
| `GET` | `/api/tasks/{id}/executions` | View execution log for a task |
| `POST` | `/api/tasks/{id}/browser` | Execute a task via browser automation |
| `GET` | `/api/tasks/{id}/browser_actions` | View browser automation actions for a task |

### API Credentials

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/credentials` | Register an external API credential (Fernet-encrypted) |
| `GET` | `/api/credentials` | List all registered API credentials |
| `DELETE` | `/api/credentials/{id}` | Remove an API credential |

### Coaching & Check-ins

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/goals/{id}/checkin` | Submit a daily check-in (coaching feedback returned) |
| `GET` | `/api/goals/{id}/checkins` | View check-in history |
| `GET` | `/api/goals/{id}/nudge` | Get stagnation nudge (if needed) |
| `POST` | `/api/nudges/{id}/acknowledge` | Acknowledge a nudge |

### Outcome Metrics

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/goals/{id}/outcomes` | Create an outcome metric |
| `GET` | `/api/goals/{id}/outcomes` | List outcome metrics |
| `PATCH` | `/api/outcomes/{id}` | Update outcome metric progress |
| `GET` | `/api/goals/{id}/outcome_suggestions` | Get suggested metrics for this goal |

### Proactive Suggestions

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/goals/{id}/suggestions` | Get proactive action suggestions |
| `POST` | `/api/suggestions/{id}` | Accept or dismiss a suggestion |

### User Profile

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/profile` | Get persistent user profile |
| `PATCH` | `/api/profile` | Update user profile |

### Multi-Agent System

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/agents` | List all agent types and capabilities |
| `POST` | `/api/agents/register` | Register a custom agent endpoint |
| `POST` | `/api/goals/{id}/orchestrate` | Run multi-agent delegation on a goal |
| `GET` | `/api/goals/{id}/handoffs` | View agent delegation chain |
| `GET` | `/api/goals/{id}/messages` | View inter-agent messages (collaboration log) |
| `GET` | `/api/agents/memory/{agent_type}` | Get persistent memory for an agent |
| `POST` | `/api/agents/memory` | Store memory for an agent |

### Integration Registry

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/integrations` | List known integrations (filterable by category) |
| `GET` | `/api/integrations/catalog` | Get the built-in integration catalog |
| `GET` | `/api/integrations/match?q=` | Find integrations matching a task description |
| `GET` | `/api/integrations/{name}/endpoints` | Get common API endpoints for a service |

### Drip Mode (Adaptive Micro-Tasking)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/goals/{id}/drip` | Get next adaptive drip task (one at a time) |
| `GET` | `/api/goals/{id}/drip/question` | Get next drip-mode clarifying question |
| `POST` | `/api/goals/{id}/drip/clarify` | Submit answer to drip-mode question |

### Knowledge Base & Insights

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/goals/{id}/insights` | Get success path insights for similar goals |
| `GET` | `/api/knowledge/paths` | List successful execution paths |
| `GET` | `/api/knowledge/recommend/{goal_type}` | Get recommended paths for a goal type |

### Budgets & Spending

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/budgets` | Create a spending budget for a goal |
| `GET` | `/api/goals/{id}/budgets` | List spending budgets for a goal |
| `PATCH` | `/api/budgets/{id}` | Update budget limits |
| `POST` | `/api/spending/request` | Request to spend money on a task |
| `POST` | `/api/spending/{id}/action` | Approve or deny a spending request |
| `GET` | `/api/goals/{id}/spending` | List spending requests for a goal |

### Payments (Mercury + Stripe)

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/payments/providers` | List available payment providers |
| `POST` | `/api/payments/accounts` | Register a payment account |
| `GET` | `/api/payments/accounts` | List registered payment accounts |
| `GET` | `/api/payments/balance/{provider}` | Get account balance for a provider |
| `POST` | `/api/payments/execute` | Execute a payment/transfer |
| `GET` | `/api/payments/transactions/{account_id}` | Get transaction history |

### Service Discovery

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/discover/services` | Discover relevant services for a goal |
| `GET` | `/api/discover/services/ai` | AI-powered service discovery |
| `GET` | `/api/discover/catalog` | Get the curated service catalog (50+ services) |
| `POST` | `/api/discover/record` | Record a discovered service |

### Autonomous Execution (Autopilot)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/goals/{id}/auto-execute` | Enable autonomous execution for a goal |
| `DELETE` | `/api/goals/{id}/auto-execute` | Disable autonomous execution |
| `GET` | `/api/auto-execute/status` | Get status of autonomous execution system |

### Deployment (Vercel / Railway / Render)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/tasks/{id}/deploy` | Deploy an application for a task |
| `GET` | `/api/goals/{id}/deployments` | List deployments for a goal |
| `GET` | `/api/deployments/{id}/health` | Check health of a specific deployment |
| `GET` | `/api/goals/{id}/deployments/health` | Check health of all deployments for a goal |

### Service Provisioning (Automated Signup)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/tasks/{id}/provision` | Auto-provision a service for a task |
| `GET` | `/api/provision/services` | List provisionable services |
| `GET` | `/api/tasks/{id}/provisioning-logs` | View provisioning logs for a task |

### Messaging (Telegram + Webhooks)

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/messaging/config` | Configure a messaging channel |
| `GET` | `/api/messaging/configs` | List messaging configurations |
| `PATCH` | `/api/messaging/config/{id}` | Update messaging configuration |
| `DELETE` | `/api/messaging/config/{id}` | Delete messaging configuration |
| `POST` | `/api/messaging/test/{id}` | Send a test message to a channel |
| `POST` | `/api/messaging/telegram/webhook` | Telegram incoming webhook handler |

### User Behavior & Analytics

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/users/me/behaviors` | Get user behavior patterns |
| `GET` | `/api/users/me/abandonment` | Get abandonment risk analysis |

### Admin Panel *(admin role required)*

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/admin/users` | List all users with goal and task counts |
| `GET` | `/api/admin/users/{id}` | Get user detail plus their goals |
| `PATCH` | `/api/admin/users/{id}` | Update user role or unlock account |
| `DELETE` | `/api/admin/users/{id}` | Delete a user and all their data |
| `GET` | `/api/admin/stats` | Aggregate platform statistics |
| `GET` | `/api/admin/integrations` | List all integrations with full detail |
| `POST` | `/api/admin/integrations` | Create a new integration entry |
| `DELETE` | `/api/admin/integrations/{name}` | Delete an integration by name |

---

## Usage Examples

> All examples below require `Authorization: Bearer $TOKEN`. See [Authentication](#authentication).

### Goal → Decompose → Coach → Track

```bash
# Create a goal
curl -X POST http://localhost:8000/api/goals \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title": "earn $500 freelancing online", "description": "complete beginner"}'

# Decompose
curl -X POST http://localhost:8000/api/goals/1/decompose \
  -H "Authorization: Bearer $TOKEN"

# Get proactive suggestions
curl http://localhost:8000/api/goals/1/suggestions \
  -H "Authorization: Bearer $TOKEN"

# Daily check-in (returns coaching feedback)
curl -X POST http://localhost:8000/api/goals/1/checkin \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"done_summary": "Created Upwork profile", "blockers": ""}'

# Track outcome
curl -X POST http://localhost:8000/api/goals/1/outcomes \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"label": "Revenue earned", "target_value": 500, "unit": "$"}'

curl -X PATCH http://localhost:8000/api/outcomes/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_value": 150}'
```

### Autonomous Task Execution

```bash
# 1. Register an external API
curl -X POST http://localhost:8000/api/credentials \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Namecheap",
    "base_url": "https://api.namecheap.com",
    "auth_header": "X-Api-Key",
    "auth_value": "your-api-key-here",
    "description": "Domain registration and DNS management API"
  }'

# 2. Execute a task autonomously (AI plans the API calls)
curl -X POST http://localhost:8000/api/tasks/1/execute \
  -H "Authorization: Bearer $TOKEN"

# 3. View the execution log
curl http://localhost:8000/api/tasks/1/executions \
  -H "Authorization: Bearer $TOKEN"
```

### Multi-Agent Orchestration

```bash
curl -X POST http://localhost:8000/api/goals/1/orchestrate \
  -H "Authorization: Bearer $TOKEN"

curl http://localhost:8000/api/goals/1/handoffs \
  -H "Authorization: Bearer $TOKEN"

curl http://localhost:8000/api/goals/1/messages \
  -H "Authorization: Bearer $TOKEN"
```

**How orchestration works:**
1. **Coordinator** analyzes your goal and creates a high-level strategy
2. Coordinator **sends messages** to specialist agents for coordination
3. Coordinator **delegates** to specialists (marketing, web_dev, outreach, etc.)
4. Each specialist reads messages from other agents, produces **concrete tasks**, and may sub-delegate
5. Example chain: `coordinator → marketing → web_dev` (marketing asks web_dev to build a landing page)
6. All handoffs and messages are logged for full traceability

### Browser Automation

```bash
curl -X POST http://localhost:8000/api/tasks/1/browser \
  -H "Authorization: Bearer $TOKEN"

curl http://localhost:8000/api/tasks/1/browser_actions \
  -H "Authorization: Bearer $TOKEN"
```

### Adaptive Micro-Tasking (Drip Mode)

```bash
curl http://localhost:8000/api/goals/1/drip/question \
  -H "Authorization: Bearer $TOKEN"

curl -X POST http://localhost:8000/api/goals/1/drip/clarify \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"key": "technical_skills", "answer": "Python and web development"}'

curl http://localhost:8000/api/goals/1/drip \
  -H "Authorization: Bearer $TOKEN"
```

### Financial Execution Pipeline

```bash
curl -X POST http://localhost:8000/api/budgets \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"goal_id": 1, "daily_limit": 50, "total_limit": 500, "category": "general", "require_approval": true}'

curl -X POST http://localhost:8000/api/spending/request \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"task_id": 1, "amount": 12.99, "description": "Register domain", "service": "namecheap"}'

curl -X POST http://localhost:8000/api/spending/1/action \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action": "approve"}'
```

### Service Discovery

```bash
curl "http://localhost:8000/api/discover/services?goal_title=build+a+SaaS&skill_level=intermediate" \
  -H "Authorization: Bearer $TOKEN"

curl http://localhost:8000/api/discover/catalog \
  -H "Authorization: Bearer $TOKEN"
```

### Payment Integration

```bash
curl http://localhost:8000/api/payments/providers \
  -H "Authorization: Bearer $TOKEN"

curl -X POST http://localhost:8000/api/payments/accounts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"provider": "stripe", "config": {"api_key": "sk_live_..."}}'
```

### Autonomous Execution (Autopilot)

```bash
# Enable autopilot on a goal — tasks are auto-executed in the background
curl -X POST http://localhost:8000/api/goals/1/auto-execute \
  -H "Authorization: Bearer $TOKEN"

# Check autopilot status
curl http://localhost:8000/api/auto-execute/status \
  -H "Authorization: Bearer $TOKEN"

# Disable autopilot
curl -X DELETE http://localhost:8000/api/goals/1/auto-execute \
  -H "Authorization: Bearer $TOKEN"
```

### Deploy & Provision

```bash
# Deploy an application (Vercel, Railway, or Render — detected from task)
curl -X POST http://localhost:8000/api/tasks/1/deploy \
  -H "Authorization: Bearer $TOKEN"

# Check deployment health
curl http://localhost:8000/api/goals/1/deployments/health \
  -H "Authorization: Bearer $TOKEN"

# Auto-provision a service (e.g. Stripe, Heroku, GitHub — browser automation)
curl -X POST http://localhost:8000/api/tasks/1/provision \
  -H "Authorization: Bearer $TOKEN"

# List provisionable services
curl http://localhost:8000/api/provision/services \
  -H "Authorization: Bearer $TOKEN"
```

---

## User Manual — Complete First-Use Walkthrough

This section walks you through using teb end-to-end, from first install to autonomous execution. Follow it step by step.

### Step 1: Install & Start

**Option A — Local (Python 3.12+)**

```bash
git clone https://github.com/aiparallel0/teb.git
cd teb
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — set **at minimum**:
```
TEB_JWT_SECRET=<generate with: python -c "import secrets; print(secrets.token_urlsafe(64))">
```

Optionally set an AI key for AI-powered features (without one, teb uses built-in templates):
```
ANTHROPIC_API_KEY=sk-ant-...
# or
OPENAI_API_KEY=sk-...
```

Start the server:
```bash
uvicorn teb.main:app --reload
# Open http://localhost:8000
```

**Option B — Docker**

```bash
git clone https://github.com/aiparallel0/teb.git
cd teb
cp .env.example .env    # edit TEB_JWT_SECRET
docker compose up --build
# Open http://localhost:8000
```

### Step 2: Create an Account

Open http://localhost:8000 in your browser. The web UI will prompt you to register.

Or via API:
```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "strongpassword"}'
```

Then log in:
```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "strongpassword"}'
```

Save the `token` from the response:
```bash
export TOKEN="eyJ..."
```

### Step 3: Set Your First Goal

**Web UI:** Type your goal in the text box (e.g. "earn $500 freelancing online") and click Create.

**API:**
```bash
curl -X POST http://localhost:8000/api/goals \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title": "earn $500 freelancing online", "description": "complete beginner, have a laptop, can dedicate 2 hours/day"}'
```

### Step 4: Answer Clarifying Questions

teb will ask 3–5 targeted questions to personalize the plan.

```bash
# See the next question
curl http://localhost:8000/api/goals/1/next_question \
  -H "Authorization: Bearer $TOKEN"

# Answer it
curl -X POST http://localhost:8000/api/goals/1/clarify \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"key": "technical_skills", "answer": "Basic Python, some HTML/CSS"}'
```

Repeat until no more questions are returned.

### Step 5: Decompose into Tasks

```bash
curl -X POST http://localhost:8000/api/goals/1/decompose \
  -H "Authorization: Bearer $TOKEN"
```

This creates 6–15 ordered, concrete tasks with time estimates. View them:
```bash
curl http://localhost:8000/api/goals/1 \
  -H "Authorization: Bearer $TOKEN"
```

### Step 6: Work Through Tasks

**See your next focus task:**
```bash
curl http://localhost:8000/api/goals/1/focus \
  -H "Authorization: Bearer $TOKEN"
```

**Mark a task done:**
```bash
curl -X PATCH http://localhost:8000/api/tasks/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "done"}'
```

**Or use Drip Mode** — get one task at a time with adaptive questions:
```bash
curl http://localhost:8000/api/goals/1/drip \
  -H "Authorization: Bearer $TOKEN"
```

### Step 7: Let teb Execute Tasks Automatically

Register an API credential so teb can act on your behalf:
```bash
curl -X POST http://localhost:8000/api/credentials \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Namecheap",
    "base_url": "https://api.namecheap.com",
    "auth_header": "X-Api-Key",
    "auth_value": "your-api-key",
    "description": "Domain registration"
  }'
```

Execute a specific task:
```bash
curl -X POST http://localhost:8000/api/tasks/1/execute \
  -H "Authorization: Bearer $TOKEN"
```

Or enable full autopilot on a goal:
```bash
curl -X POST http://localhost:8000/api/goals/1/auto-execute \
  -H "Authorization: Bearer $TOKEN"
```

### Step 8: Set Up Budget Controls

Before teb spends money, set limits:
```bash
curl -X POST http://localhost:8000/api/budgets \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"goal_id": 1, "daily_limit": 25, "total_limit": 200, "category": "general", "require_approval": true}'
```

Approve or deny spending requests:
```bash
curl -X POST http://localhost:8000/api/spending/1/action \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"action": "approve"}'
```

### Step 9: Daily Check-ins & Coaching

Submit a 2-minute check-in (teb responds with coaching):
```bash
curl -X POST http://localhost:8000/api/goals/1/checkin \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"done_summary": "Created Upwork profile and sent 3 proposals", "blockers": "Not sure how to price my services"}'
```

If you go quiet for 48+ hours, teb nudges you:
```bash
curl http://localhost:8000/api/goals/1/nudge \
  -H "Authorization: Bearer $TOKEN"
```

### Step 10: Track Real Outcomes

Don't just check boxes — measure results:
```bash
# Create a metric
curl -X POST http://localhost:8000/api/goals/1/outcomes \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"label": "Revenue earned", "target_value": 500, "unit": "$"}'

# Update progress
curl -X PATCH http://localhost:8000/api/outcomes/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_value": 150}'
```

### Step 11: Optional — Notifications

Get notified via Telegram or webhooks:
```bash
curl -X POST http://localhost:8000/api/messaging/config \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"channel": "telegram", "config": {"bot_token": "123:ABC...", "chat_id": "your-chat-id"}, "events": ["nudge", "task_done", "spending_request"]}'
```

### Step 12: Optional — Payments

Connect real payment providers for financial execution:
```bash
# Stripe
curl -X POST http://localhost:8000/api/payments/accounts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"provider": "stripe", "config": {"api_key": "sk_live_..."}}'

# Mercury banking
curl -X POST http://localhost:8000/api/payments/accounts \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"provider": "mercury", "config": {"api_key": "..."}}'
```

### What Works Without Any API Keys

teb is fully functional in **template mode** — no AI keys needed:
- ✅ Goal creation and clarifying questions
- ✅ Task decomposition (10 built-in templates)
- ✅ Task management (create, update, reorder, delete)
- ✅ Daily check-ins and coaching
- ✅ Outcome tracking
- ✅ Budget management and spending approval
- ✅ User profiles and knowledge base
- ✅ Service discovery (50+ curated services)
- ✅ Integration catalog (25 services)
- ✅ Multi-agent delegation (template mode)
- ✅ Proactive suggestions (rule-based)
- ✅ Drip mode micro-tasking
- ✅ Web UI

### What Requires API Keys

| Feature | Key Required |
|---|---|
| AI-enhanced decomposition | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| AI coaching feedback | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| AI-powered service discovery | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` |
| Autonomous API execution | User-registered API credentials |
| Browser automation | Playwright installed (`pip install playwright && playwright install`) |
| Telegram notifications | Bot token from [@BotFather](https://t.me/BotFather) |
| Stripe payments | `TEB_STRIPE_API_KEY` |
| Mercury banking | `TEB_MERCURY_API_KEY` |

### Production Checklist

Before deploying to production:

- [ ] Set `TEB_JWT_SECRET` to a strong random value
- [ ] Set `TEB_SECRET_KEY` for credential encryption — ⚠️ **without this, API credentials are stored UNENCRYPTED**
- [ ] Set `TEB_CORS_ORIGINS` to your specific domain (not `*`)
- [ ] Review `TEB_AUTOPILOT_DEFAULT_THRESHOLD` (default: $50 per auto-approved transaction)
- [ ] Set up a reverse proxy (nginx/Caddy) with HTTPS
- [ ] Use Docker with `docker compose up -d` for persistence
- [ ] Back up the SQLite database file regularly

---

## Configuration

All configuration is via environment variables. Copy `.env.example` to `.env`.

### AI Providers

| Variable | Default | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | _(none)_ | Enables Claude-powered AI features (preferred) |
| `TEB_ANTHROPIC_MODEL` | `claude-sonnet-4-20250514` | Anthropic model for AI features |
| `OPENAI_API_KEY` | _(none)_ | Enables OpenAI-powered AI features |
| `OPENAI_BASE_URL` | `https://api.openai.com/v1` | OpenAI-compatible API base URL |
| `TEB_MODEL` | `gpt-4o-mini` | OpenAI model for AI features |
| `TEB_AI_PROVIDER` | `auto` | AI provider: `anthropic`, `openai`, or `auto` (prefers Anthropic) |

### Security

| Variable | Default | Description |
|---|---|---|
| `TEB_JWT_SECRET` | ⚠️ `change-me-...` | **REQUIRED in production.** JWT signing secret |
| `TEB_JWT_EXPIRE_HOURS` | `168` | JWT token lifetime (7 days) |
| `TEB_SECRET_KEY` | _(auto-generated)_ | Fernet key for encrypting stored API credentials |

### Database & Execution

| Variable | Default | Description |
|---|---|---|
| `DATABASE_URL` | `sqlite:///teb.db` | SQLite database path |
| `MAX_TASKS_PER_GOAL` | `20` | Cap on tasks per goal (AI mode) |
| `TEB_EXECUTOR_TIMEOUT` | `30` | HTTP timeout (seconds) for API execution |
| `TEB_EXECUTOR_MAX_RETRIES` | `2` | Max retries for failed API calls |

### Payment Providers

| Variable | Default | Description |
|---|---|---|
| `TEB_MERCURY_API_KEY` | _(none)_ | Mercury banking API key |
| `TEB_MERCURY_BASE_URL` | `https://api.mercury.com/api/v1` | Mercury API base URL |
| `TEB_STRIPE_API_KEY` | _(none)_ | Stripe payment processing API key |
| `TEB_STRIPE_BASE_URL` | `https://api.stripe.com/v1` | Stripe API base URL |

### Autonomous Execution

| Variable | Default | Description |
|---|---|---|
| `TEB_AUTONOMOUS_EXECUTION` | `true` | Enable/disable background autopilot loop |
| `TEB_AUTONOMOUS_EXECUTION_INTERVAL` | `30` | How often (seconds) the loop checks for pending tasks |
| `TEB_AUTOPILOT_DEFAULT_THRESHOLD` | `50.0` | Max $ per auto-approved transaction |

### Application

| Variable | Default | Description |
|---|---|---|
| `TEB_CORS_ORIGINS` | `*` | Comma-separated allowed origins (restrict in production) |
| `TEB_LOG_LEVEL` | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `TEB_BASE_PATH` | _(empty)_ | URL path prefix for reverse-proxy mounting (e.g. `/teb`) |

Without an AI key, teb operates in **template mode** — fully offline, instant. When both keys are set, Anthropic (Claude) is preferred by default.

---

## Architecture

```
teb/
├── main.py            FastAPI app + 97 REST endpoints
├── models.py          18 dataclass models (Goal, Task, User, etc.)
├── storage.py         SQLite data access layer (27 tables)
├── decomposer.py      Template-based + AI decomposition, coaching, drip mode, success paths
├── executor.py        AI-powered task execution engine (API calls via httpx)
├── browser.py         Browser automation engine (AI plan generation + Playwright)
├── agents.py          Multi-agent delegation system with inter-agent messaging
├── integrations.py    Pre-built integration catalog (25 services) + matching engine
├── payments.py        Real payment integration (Mercury banking + Stripe processing)
├── discovery.py       Tool/service discovery engine (50+ curated services + AI discovery)
├── deployer.py        Deployment engine (Vercel, Railway, Render) + health monitoring
├── provisioning.py    Service auto-signup via browser automation (6 service templates)
├── messaging.py       External messaging (Telegram bots + webhooks)
├── ai_client.py       Unified AI client (Anthropic Claude + OpenAI, retry + fallback)
├── auth.py            JWT authentication, bcrypt hashing, RBAC, account locking
├── security.py        SSRF-safe URL validation for outbound HTTP calls
├── config.py          Environment variable configuration (23 variables)
├── templates/
│   └── index.html     Single-page frontend
└── static/
    ├── app.js         Vanilla JS frontend logic
    └── style.css      CSS styling
tests/
├── conftest.py                  Test fixtures (rate-limit reset, DB setup)
├── test_api.py                  Integration tests for API endpoints
├── test_decomposer.py           Unit tests for decomposition logic
├── test_executor.py             Unit tests for execution engine
├── test_checkin.py              Tests for coaching, nudges, outcomes, suggestions
├── test_agents.py               Tests for multi-agent delegation system
├── test_browser_integrations.py Tests for browser automation, integrations, agent messaging
├── test_new_features.py         Tests for drip mode, success paths, financial pipeline, messaging
├── test_plan_features.py        Tests for new templates, spending resets, user storage
├── test_mvp_features.py         Tests for payments, discovery, behavior, agent memory
├── test_autopilot_features.py   Tests for autonomous execution, deployer, provisioning
└── test_security_fixes.py       Tests for credential scoping, ownership, payment config
deploy/
├── backup.sh                    Database backup script (SQLite .backup)
├── docker-entrypoint.sh         Docker entrypoint (auto-generates TEB_SECRET_KEY)
└── systemd/
    ├── teb.service              Systemd unit file
    ├── teb-backup.service       Backup service (triggered by timer)
    └── teb-backup.timer         Daily backup timer
migrations/
├── migrate.py                   SQL migration runner
└── versions/                    Numbered .sql migration files
```

### Database (27 tables)

| Table | Purpose |
|---|---|
| `users` | Accounts with email, hashed password, role, lockout tracking |
| `refresh_tokens` | JWT refresh token storage |
| `goals` | User goals with status lifecycle and clarifying answers |
| `tasks` | Hierarchical tasks (parent/child, max depth 3) |
| `api_credentials` | Fernet-encrypted API keys |
| `execution_logs` | API execution history |
| `check_ins` | Daily check-ins with mood tracking |
| `outcome_metrics` | Vertical-specific success metrics |
| `nudge_events` | Coaching nudges and acknowledgments |
| `user_profiles` | Persistent cross-goal user context |
| `success_paths` | Recorded goal completion patterns |
| `proactive_suggestions` | AI-discovered action suggestions |
| `agent_handoffs` | Agent delegation chain |
| `agent_messages` | Inter-agent communication |
| `agent_memory` | Persistent agent context |
| `browser_actions` | Browser automation step log |
| `integrations` | User-registered service integrations |
| `spending_budgets` | Budget rules and limits |
| `spending_requests` | Pending spending approvals |
| `messaging_configs` | Notification channel configurations |
| `user_behavior` | Behavioral analytics data |
| `payment_accounts` | Registered payment accounts |
| `payment_transactions` | Payment transaction history |
| `discovered_services` | AI-discovered service records |
| `deployments` | Application deployment records (Vercel/Railway/Render) |
| `provisioning_logs` | Service provisioning attempt log |
| `telegram_sessions` | Telegram bot session state |

### Execution Flow

```
User Goal
    │
    ▼
┌─────────────────────────────────────────────┐
│  Clarification Engine                       │
│  (adaptive questions → user context)        │
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  Decomposer                                 │
│  10 templates + AI enhancement              │
│  → Sequenced subtasks with time estimates   │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────┼────────┐
          ▼        ▼        ▼
      ┌───────┐ ┌──────┐ ┌────────┐
      │ API   │ │Browse│ │ Human  │
      │Execut.│ │ Auto │ │ Tasks  │
      └───┬───┘ └──┬───┘ └───┬────┘
          │        │         │
          ▼        ▼         ▼
┌─────────────────────────────────────────────┐
│  Outcome Tracking & Coaching                │
│  Metrics → Check-ins → Nudges → Insights    │
└─────────────────────────────────────────────┘
```

### Multi-Agent Delegation

```
User goal: "earn money online"
    │
    ▼
┌─────────────┐
│ Coordinator │  Analyzes goal → strategy → coordination messages
└──────┬──────┘
       │ delegates to specialists (with shared context):
       ├──▶ Marketing Agent → positioning, content, SEO
       │    ├── messages Web Dev: "Need landing page with email capture"
       │    └──▶ Web Dev Agent → builds it (reads Marketing's message)
       ├──▶ Research Agent → market validation, competitors
       │    └── messages Marketing: "Found untapped niche in X"
       ├──▶ Web Dev Agent → hosting, domain, deployment
       ├──▶ Outreach Agent → cold outreach, lead gen
       └──▶ Finance Agent → budgeting, pricing, payments
```

Each agent has a specific domain, produces concrete tasks, sends messages to other agents, can delegate (up to 3 levels deep), reads outputs from previously completed agents, works in AI mode or template mode (offline). All handoffs and messages are logged.

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

| Template | Slug | Trigger Keywords |
|---|---|---|
| Make Money Online | `make_money_online` | money/income/earn + online/internet |
| Learn a Skill | `learn_skill` | learn/study/master/understand |
| Get Fit | `get_fit` | fit/workout/exercise/gym/weight |
| Build a Project | `build_project` | build/create/develop + app/website/tool |
| Write a Book | `write_book` | book/write/novel/manuscript/author |
| Launch a Startup | `launch_startup` | startup/company/found/venture/business |
| Find a Job | `find_job` | job/career/hire/resume/interview |
| Improve Health | `improve_health` | health/sleep/nutrition/stress/wellness |
| Side Project | `side_project` | side project/hobby/passion project |
| Generic | `generic` | everything else |

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

This means teb does not re-ask "do you have technical skills?" every time you create a new goal. It already knows.

---

## Proactive Suggestions

teb does not just execute your plan — it **discovers actions you didn't think of**:

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

## Financial Autonomy

The financial layer lets AI spend money on your behalf with safety controls:

**Safety Architecture:**
```
$0–$5/action   → Auto-execute (within daily cap)
$5–$50/action  → Notify user, execute after 1-hour delay unless vetoed
$50–$200       → Require explicit approval before execution
$200+          → Require approval + confirmation code
All actions    → Hard daily cap ($X), hard monthly cap ($Y), kill switch
```

**Payment Providers:**
- **Mercury** — Business banking API (account balances, wire transfers, ACH payments)
- **Stripe** — Payment processing (charges, customers, invoicing, subscriptions)

**Budget categories:** `general`, `hosting`, `domain`, `marketing`, `tools`, `services`.

---

## Running Tests

```bash
pip install -r requirements.txt
pytest tests/ -v
```

578 tests across 11 test files. Tests use an in-memory SQLite database and mock all external services.

---

## Deployment

### Systemd (bare-metal)

1. Deploy the repo to `/opt/teb` and create a virtualenv:
   ```bash
   sudo useradd -r -s /usr/sbin/nologin appuser
   git clone https://github.com/aiparallel0/teb.git /opt/teb
   cd /opt/teb
   python3 -m venv venv && venv/bin/pip install -r requirements.txt
   cp .env.example .env   # edit .env — set TEB_JWT_SECRET and TEB_SECRET_KEY
   sudo chown -R appuser:appuser /opt/teb
   ```
2. Install the systemd unit:
   ```bash
   sudo cp deploy/systemd/teb.service /etc/systemd/system/teb.service
   sudo systemctl daemon-reload
   sudo systemctl enable --now teb
   ```

### Docker Compose (production)

```bash
cp .env.example .env  # edit TEB_JWT_SECRET
docker compose up -d --build
```

The Docker entrypoint auto-generates `TEB_SECRET_KEY` (Fernet encryption key) if not already set.

---

## HTTPS / TLS

The existing `nginx/teb.conf` handles the `/teb` reverse-proxy path but does not include TLS configuration. Choose one of the options below.

### Option A — Certbot with nginx

```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d portearchive.com
```

Certbot will automatically configure TLS in the nginx server block and set up auto-renewal.

### Option B — Caddy (auto-HTTPS)

Create a `Caddyfile`:

```
portearchive.com {
    handle_path /teb/* {
        reverse_proxy localhost:8000
    }
}
```

Run with:
```bash
caddy run
```

Caddy automatically provisions and renews TLS certificates via Let's Encrypt.

---

## Database Backups

A backup script is provided at `deploy/backup.sh`. It uses SQLite's `.backup` command for a safe, consistent copy and prunes backups older than 30 days.

### Manual backup

```bash
bash deploy/backup.sh /path/to/teb.db
```

### Cron (daily at 02:00)

```bash
0 2 * * * /opt/teb/deploy/backup.sh /opt/teb/data/teb.db >> /var/log/teb-backup.log 2>&1
```

### Systemd timer (recommended)

```bash
sudo cp deploy/systemd/teb-backup.service /etc/systemd/system/
sudo cp deploy/systemd/teb-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now teb-backup.timer
```

Environment variables:
- `BACKUP_DIR` — backup destination (default: `/opt/teb/backups`)
- `KEEP_DAYS` — number of days to retain backups (default: `30`)

---

## Database Migrations

teb includes a lightweight SQL migration system in `migrations/`.

### Apply pending migrations

```bash
python -m migrations.migrate
# or with a specific database path:
python -m migrations.migrate --db /opt/teb/data/teb.db
```

### Create a new migration

```bash
python -m migrations.migrate --new "add_foobar_column"
```

This creates a numbered `.sql` file in `migrations/versions/`. Write your `ALTER TABLE` / `CREATE TABLE` statements there. Migrations run inside a transaction and are tracked in the `schema_migrations` table.

---

## Browser Automation (Playwright)

Browser automation requires Playwright to be installed:

```bash
pip install playwright
playwright install --with-deps chromium
```

**start.sh:** Set `ENABLE_BROWSER=true` before running `start.sh` to auto-install Playwright:

```bash
ENABLE_BROWSER=true bash start.sh
```

**Docker:** The Dockerfile installs Playwright and Chromium automatically. No extra steps needed.

---

## Roadmap

1. ✅ Template-based goal decomposition with clarifying questions (10 templates)
2. ✅ AI-powered decomposition (OpenAI + Anthropic Claude)
3. ✅ Active coaching (daily check-in + stagnation detection + nudges)
4. ✅ Outcome tracking with vertical-specific metrics
5. ✅ Persistent user profile (cross-goal learning)
6. ✅ Proactive suggestion engine (discovers actions user didn't think of)
7. ✅ Knowledge base (success path recording, reuse, and recommendation)
8. ✅ Task execution engine (API orchestration via httpx)
9. ✅ Browser automation (Playwright plan generation + execution)
10. ✅ Financial autonomy layer (budget management, per-transaction approval, daily limits)
11. ✅ Multi-agent architecture (coordinator, marketing, web_dev, outreach, research, finance)
12. ✅ Agent-to-agent communication protocol (message passing + shared context)
13. ✅ Real-time notifications (Telegram bot + webhooks)
14. ✅ Financial API integrations (Stripe + Mercury banking)
15. ✅ Service discovery engine (50+ curated services + AI-powered matching)
16. ✅ User behavior analytics and abandonment risk analysis
17. ✅ Persistent agent memory
18. ✅ JWT authentication with RBAC, refresh tokens, and account locking
19. ✅ Autonomous execution loop (background autopilot for tasks)
20. ✅ Deployment engine (Vercel, Railway, Render — deploy + health monitoring)
21. ✅ Service provisioning (automated signup via browser automation)
22. ✅ Credential scoping (per-user API credential isolation)
23. ✅ PyPI package (`pip install teb`)
24. ✅ Docker Hub image (`docker pull aiparallel0/teb`)
25. ✅ CI/CD pipeline (test → publish → deploy)
26. ✅ Database backup system (script + systemd timer)
27. ✅ Database migration system (SQL-based)
28. ✅ HTTPS/TLS documentation
29. 🔲 Additional payment providers (Privacy.com virtual cards, Plaid banking)
30. 🔲 SMS notifications
31. 🔲 Payment sandbox/simulation mode

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup, code style, testing, and pull request guidelines.

## Security

See [SECURITY.md](SECURITY.md) for vulnerability reporting and production hardening.

## License

[MIT](LICENSE)
