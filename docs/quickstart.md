# TEB Quick-Start Guide

Get up and running with teb — the Task Execution Bridge — and complete the full
**Goal → Clarify → Decompose → Execute → Measure → Learn** loop in under 15 minutes.

teb is not a task manager. It takes your raw intentions and dissolves everything
beneath them into solved problems — decomposing goals into concrete tasks,
executing what it can autonomously, and tracking real outcomes (not just
checkboxes).

---

## Prerequisites

| Requirement | Minimum | Notes |
|-------------|---------|-------|
| Python      | 3.12+   | For local install |
| pip         | 22+     | For local install |
| Docker      | 24+     | For Docker install (recommended) |
| SQLite      | 3.35+   | Bundled with Python 3.12 |

You do **not** need an AI API key to start. teb runs in **template mode** by
default — all features work with heuristic fallbacks. AI keys unlock smarter
decomposition and autonomous execution but never gate functionality.

---

## Table of Contents

- [Option A: One-Liner Install](#option-a-one-liner-install)
- [Option B: Docker Install (Recommended)](#option-b-docker-install-recommended)
- [Option C: Manual Install](#option-c-manual-install)
- [End-to-End Walkthrough (Web UI)](#end-to-end-walkthrough-web-ui)
- [End-to-End Walkthrough (API / curl)](#end-to-end-walkthrough-api--curl)
- [Setting Up AI Keys](#setting-up-ai-keys)
- [Template Mode (No AI Key)](#template-mode-no-ai-key)
- [Environment Variable Reference](#environment-variable-reference)
- [What's Next](#whats-next)

---

## Option A: One-Liner Install

```bash
git clone https://github.com/aiparallel0/teb.git && cd teb && bash start.sh
```

`start.sh` does the following automatically:

1. Copies `.env.example` → `.env` if `.env` does not exist
2. Generates a cryptographically random `TEB_JWT_SECRET`
3. Generates a `TEB_SECRET_KEY` (Fernet) for credential encryption
4. Installs Python dependencies
5. Starts the server at **http://localhost:8000**

Pass `--docker` to use Docker Compose instead:

```bash
git clone https://github.com/aiparallel0/teb.git && cd teb && bash start.sh --docker
```

---

## Option B: Docker Install (Recommended)

Docker gives you teb + Redis in one command with data persistence via a named
volume. No Python install required on your host.

### 1. Clone and configure

```bash
git clone https://github.com/aiparallel0/teb.git
cd teb
cp .env.example .env
```

### 2. Set the JWT secret

Open `.env` and replace the placeholder JWT secret:

```bash
# Generate a strong secret
python3 -c "import secrets; print(secrets.token_urlsafe(64))"

# Paste the output into .env:
# TEB_JWT_SECRET=<your-generated-secret>
```

Or let `start.sh` handle it for you (see Option A).

### 3. Start the stack

```bash
docker compose up --build
```

This starts two services:

| Service | Port | Purpose |
|---------|------|---------|
| `teb`   | 8000 | FastAPI app (SQLite + Playwright) |
| `redis` | 6379 | Caching layer |

The container runs as a **non-root user** with a health check at `/health`.
SQLite data persists in the `teb-data` Docker volume.

### 4. Verify it's running

```bash
curl http://localhost:8000/health
# {"status": "healthy", ...}
```

Open **http://localhost:8000** in your browser.

### Pre-built image (skip the build)

```bash
docker pull aiparallel0/teb:latest
docker run -e TEB_JWT_SECRET=your-secret -p 8000:8000 aiparallel0/teb
```

> **Important:** AI keys in your `.env` are invisible to the Docker container
> unless they are listed under `environment:` in `docker-compose.yml`. The
> default compose file already includes `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`,
> and `TEB_AI_PROVIDER`. If you add new env vars, add them to compose too.

---

## Option C: Manual Install

```bash
git clone https://github.com/aiparallel0/teb.git
cd teb
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` — set `TEB_JWT_SECRET` at minimum (see the generated comment in the
file for how to create one). Then start the server:

```bash
uvicorn teb.main:asgi_app --reload
# Server running on http://localhost:8000
```

Override the port:

```bash
uvicorn teb.main:asgi_app --reload --port 3000
```

---

## End-to-End Walkthrough (Web UI)

This walks you through the complete **Goal → Clarify → Decompose → Execute →
Measure** loop using the browser UI.

### Step 1: Register your account

1. Open **http://localhost:8000** in your browser.
2. Click **Register** and create an account with your email and a strong
   password.
3. The **first user** is automatically granted **admin** privileges.
4. You're logged in immediately — no email verification in development mode.

### Step 2: Create a goal

1. Click **New Goal** on the dashboard.
2. Enter a concrete goal title, for example:
   - *"Earn $500 freelancing online this month"*
   - *"Launch a personal portfolio website"*
   - *"Get 100 newsletter subscribers"*
3. Add a description with relevant context — your skills, budget, timeline.
   The more context you give, the better the decomposition.
4. Press **Save**.

### Step 3: Clarify (optional but recommended)

teb can ask clarifying questions before decomposing your goal. This sharpens
the task breakdown so it fits *your* situation.

1. Inside your goal, look for the **Clarify** prompt.
2. Answer questions like: *Do you have technical skills? How many hours per
   week? Do you need income in 30 days?*
3. Each answer is stored and used to tailor the decomposition.

### Step 4: Decompose into tasks

1. Click **AI Decompose** on your goal.
2. teb breaks the goal into 4–8 concrete, ordered tasks with:
   - Time estimates (in minutes)
   - Priority levels (high / normal / low)
   - Dependencies between tasks
   - Sub-tasks where appropriate
3. In **template mode** (no AI key), teb uses a built-in library of task
   templates matched to your goal type. With an AI key, decomposition is
   tailored to your specific answers and context.

### Step 5: Work through tasks

1. Use the **Dashboard**, **Kanban**, **Calendar**, or **Timeline** view to
   see your tasks.
2. Click **Focus Mode** to get the single next task you should work on (based
   on dependencies, priority, and order).
3. Mark tasks **done** or **skipped** as you progress.
4. For tasks that can be automated, click **Execute** — teb will attempt
   autonomous execution via registered API credentials or browser automation.

### Step 6: Track outcomes

1. Inside your goal, click **Add Outcome Metric**.
2. Define what success looks like in measurable terms:
   - Label: *"Revenue earned"*, Target: *500*, Unit: *$*
   - Label: *"Subscribers acquired"*, Target: *100*, Unit: *people*
3. Update the **current value** as results come in.
4. Check the **ROI dashboard** (`/api/goals/{id}/roi`) to see money spent by
   AI vs. money earned.

### Step 7: Check in and get coaching

1. Submit a **daily check-in**: what you accomplished and what's blocking you.
2. teb analyzes your check-in and returns coaching feedback — mood detection,
   specific next-step advice, and stagnation nudges if you've been idle.

---

## End-to-End Walkthrough (API / curl)

Every action in the UI has a corresponding REST endpoint. Here's the complete
loop using `curl`. All examples use `http://localhost:8000` — if you're running
behind a reverse proxy at `/teb`, prepend that base path.

### Step 1: Register

```bash
curl -s -X POST http://localhost:8000/api/auth/register \
  -H 'Content-Type: application/json' \
  -d '{"email": "you@example.com", "password": "strongpassword123"}' | jq .
```

Response:

```json
{
  "user": {"id": 1, "email": "you@example.com", "role": "admin"},
  "token": "eyJ...",
  "refresh_token": "..."
}
```

> **Note:** The login/register response uses the key `"token"` (not
> `"access_token"`). Save it:

```bash
export TOKEN="eyJ..."
```

### Step 2: Create a goal

```bash
curl -s -X POST http://localhost:8000/api/goals \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "title": "Earn $500 freelancing online",
    "description": "Complete beginner, 10 hours/week available, need income within 30 days"
  }' | jq .
```

Response:

```json
{
  "id": 1,
  "title": "Earn $500 freelancing online",
  "description": "Complete beginner, 10 hours/week available, need income within 30 days",
  "status": "drafting",
  "tags": [],
  "version": 1
}
```

### Step 3: Clarify the goal (optional)

Get the next clarifying question:

```bash
curl -s http://localhost:8000/api/goals/1/next_question \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Submit an answer:

```bash
curl -s -X POST http://localhost:8000/api/goals/1/clarify \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"key": "technical_skills", "answer": "Basic HTML/CSS, learning Python"}' | jq .
```

Repeat until no more questions are returned.

### Step 4: Decompose into tasks

```bash
curl -s -X POST http://localhost:8000/api/goals/1/decompose \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Response (abbreviated):

```json
{
  "goal_id": 1,
  "tasks": [
    {
      "id": 1,
      "title": "Create Upwork freelancer profile",
      "description": "Sign up on Upwork, complete profile with skills...",
      "estimated_minutes": 45,
      "status": "todo",
      "priority": "high",
      "order_index": 0
    },
    {
      "id": 2,
      "title": "Write 3 portfolio samples",
      "description": "Create sample work demonstrating your skills...",
      "estimated_minutes": 120,
      "status": "todo",
      "priority": "high",
      "order_index": 1
    }
  ]
}
```

The goal status changes to `"decomposed"` automatically.

### Step 5: Get your focus task

```bash
curl -s http://localhost:8000/api/goals/1/focus \
  -H "Authorization: Bearer $TOKEN" | jq .
```

This returns the single highest-priority task you should work on next, based on
dependency order and status.

### Step 6: Work on tasks

Mark a task as in-progress:

```bash
curl -s -X PATCH http://localhost:8000/api/tasks/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "in_progress"}' | jq .
```

Mark it done:

```bash
curl -s -X PATCH http://localhost:8000/api/tasks/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"status": "done"}' | jq .
```

Valid task statuses: `todo`, `in_progress`, `done`, `skipped`, `executing`,
`failed`.

### Step 7: Execute a task autonomously (optional)

If you've registered API credentials, teb can execute tasks for you:

```bash
# Register an external API credential
curl -s -X POST http://localhost:8000/api/credentials \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "Namecheap",
    "base_url": "https://api.namecheap.com",
    "auth_header": "X-Api-Key",
    "auth_value": "your-api-key-here",
    "description": "Domain registration and DNS management"
  }' | jq .

# Execute a task — teb plans and runs the API calls
curl -s -X POST http://localhost:8000/api/tasks/2/execute \
  -H "Authorization: Bearer $TOKEN" | jq .

# View the execution log
curl -s http://localhost:8000/api/tasks/2/executions \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Credentials are **Fernet-encrypted** at rest when `TEB_SECRET_KEY` is set.

### Step 8: Track outcome metrics

Define what success looks like:

```bash
curl -s -X POST http://localhost:8000/api/goals/1/outcomes \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"label": "Revenue earned", "target_value": 500, "unit": "$"}' | jq .
```

Update progress as results come in:

```bash
curl -s -X PATCH http://localhost:8000/api/outcomes/1 \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"current_value": 150}' | jq .
```

Check ROI (money spent by AI vs. money earned):

```bash
curl -s http://localhost:8000/api/goals/1/roi \
  -H "Authorization: Bearer $TOKEN" | jq .
```

### Step 9: Daily check-in and coaching

```bash
curl -s -X POST http://localhost:8000/api/goals/1/checkin \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "done_summary": "Created Upwork profile and submitted 2 proposals",
    "blockers": "Unsure how to price my services"
  }' | jq .
```

Response includes coaching feedback:

```json
{
  "checkin": {
    "id": 1,
    "goal_id": 1,
    "done_summary": "Created Upwork profile and submitted 2 proposals",
    "blockers": "Unsure how to price my services",
    "mood": "motivated",
    "feedback": "Great progress! For pricing, start at $15-25/hr..."
  },
  "coaching": {
    "mood_detected": "motivated",
    "feedback": "Great progress! For pricing, start at $15-25/hr..."
  }
}
```

### Step 10: Check goal progress

```bash
curl -s http://localhost:8000/api/goals/1/progress \
  -H "Authorization: Bearer $TOKEN" | jq .
```

This returns completion percentage, estimated time remaining, and task status
breakdown.

---

## Drip Mode (Adaptive Micro-Tasking)

Drip mode is an alternative to bulk decomposition. Instead of generating all
tasks at once, teb gives you **one task at a time** and adapts based on your
completions — asking follow-up questions at milestones.

```bash
# Get a drip-mode clarifying question
curl -s http://localhost:8000/api/goals/1/drip/question \
  -H "Authorization: Bearer $TOKEN" | jq .

# Answer it
curl -s -X POST http://localhost:8000/api/goals/1/drip/clarify \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"key": "technical_skills", "answer": "Python and web development"}' | jq .

# Get your next single task
curl -s http://localhost:8000/api/goals/1/drip \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Drip mode is ideal for users who feel overwhelmed by a full task list. It
surfaces one actionable step, then adapts the next step based on what you did.

---

## Multi-Agent Orchestration

teb has six specialist AI agents that collaborate on complex goals:

| Agent | Role |
|-------|------|
| **coordinator** | Analyzes your goal, creates strategy, delegates to specialists |
| **marketing** | Positioning, content strategy, SEO |
| **web_dev** | Technical setup, deployment |
| **outreach** | Cold outreach, campaigns |
| **research** | Competitive analysis, market validation |
| **finance** | Budgeting, pricing, payments |

```bash
# Run multi-agent orchestration on a goal
curl -s -X POST http://localhost:8000/api/goals/1/orchestrate \
  -H "Authorization: Bearer $TOKEN" | jq .

# View the agent delegation chain
curl -s http://localhost:8000/api/goals/1/handoffs \
  -H "Authorization: Bearer $TOKEN" | jq .

# View inter-agent messages (collaboration log)
curl -s http://localhost:8000/api/goals/1/messages \
  -H "Authorization: Bearer $TOKEN" | jq .
```

Orchestration runs in parallel via ThreadPoolExecutor. The coordinator delegates
to specialists, who may sub-delegate (e.g., marketing → web_dev to build a
landing page). All handoffs and messages are logged for full traceability.

---

## Setting Up AI Keys

AI keys are **optional**. Without them, teb uses template mode (see next
section). With them, you get smarter decomposition, tailored coaching, and
autonomous execution planning.

### Anthropic (Claude) — recommended

```bash
# In your .env file:
ANTHROPIC_API_KEY=sk-ant-your-key-here
TEB_AI_PROVIDER=auto
```

With `TEB_AI_PROVIDER=auto` (the default), teb prefers Anthropic when the key
is set. The default model is `claude-sonnet-4-20250514`.

### OpenAI

```bash
# In your .env file:
OPENAI_API_KEY=sk-your-key-here
TEB_AI_PROVIDER=auto
```

The default model is `gpt-4o-mini`. Override with `TEB_MODEL=gpt-4o` or any
OpenAI-compatible model.

### OpenAI-compatible providers (local LLMs, etc.)

```bash
OPENAI_API_KEY=your-key
OPENAI_BASE_URL=http://localhost:11434/v1   # e.g., Ollama
TEB_MODEL=llama3
TEB_AI_PROVIDER=openai
```

### Both providers

Set both keys. With `TEB_AI_PROVIDER=auto`, Anthropic is used. You can force a
specific provider:

```bash
TEB_AI_PROVIDER=anthropic   # always use Claude
TEB_AI_PROVIDER=openai      # always use OpenAI
```

### Docker users: verify your keys reach the container

AI keys in `.env` are only visible inside the Docker container if they are
listed under `environment:` in `docker-compose.yml`. The default compose file
already includes:

```yaml
environment:
  - OPENAI_API_KEY=${OPENAI_API_KEY:-}
  - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY:-}
  - TEB_AI_PROVIDER=${TEB_AI_PROVIDER:-auto}
```

If you add new env vars (e.g., `OPENAI_BASE_URL`), add them to
`docker-compose.yml` too. The template fallback silently masks missing keys —
seeing $0 AI spend is not proof your keys are configured correctly.

---

## Template Mode (No AI Key)

teb is designed to work **without any AI API key**. This is called template
mode, and it is a first-class experience — not a degraded fallback.

In template mode:

- **Decomposition** uses a built-in library of task templates matched to your
  goal type (freelancing, SaaS, portfolio, etc.)
- **Clarifying questions** are drawn from a fixed question bank
- **Coaching feedback** uses rule-based heuristics and mood keyword detection
- **Proactive suggestions** use pattern-matching rules
- **Outcome metric suggestions** come from a template library

What requires an AI key:

- Tailored decomposition that references your specific answers and context
- Multi-agent orchestration with agent-to-agent reasoning
- Autonomous execution planning (the AI decides which API calls to make)
- AI-powered service discovery

The pattern throughout the codebase: `try: return ai_result() except: return
template_result()`. If the AI call fails for any reason (missing key, rate
limit, network error), the template fallback activates automatically.

---

## Environment Variable Reference

All configuration is via environment variables, set in `.env` or passed to
Docker. Only `TEB_JWT_SECRET` is required in production.

### Required

| Variable | Description | Default |
|----------|-------------|---------|
| `TEB_JWT_SECRET` | JWT signing secret. Auto-generated in dev; **required** in production. Generate with: `python -c "import secrets; print(secrets.token_urlsafe(64))"` | Random per-process in dev |

### AI Providers (optional)

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key | *(unset — template mode)* |
| `OPENAI_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com/v1` |
| `TEB_MODEL` | OpenAI model name | `gpt-4o-mini` |
| `ANTHROPIC_API_KEY` | Anthropic API key | *(unset — template mode)* |
| `TEB_ANTHROPIC_MODEL` | Anthropic model name | `claude-sonnet-4-20250514` |
| `TEB_AI_PROVIDER` | `auto`, `anthropic`, or `openai` | `auto` |

### Security

| Variable | Description | Default |
|----------|-------------|---------|
| `TEB_SECRET_KEY` | Fernet key for encrypting stored API credentials. Generate with: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` | *(unset — credentials stored unencrypted)* |
| `TEB_JWT_EXPIRE_HOURS` | JWT token lifetime | `168` (7 days) |
| `TEB_CORS_ORIGINS` | Comma-separated allowed CORS origins | `https://portearchive.com` + localhost in dev |

### Database

| Variable | Description | Default |
|----------|-------------|---------|
| `DATABASE_URL` | SQLite connection string | `sqlite:///teb.db` |
| `REDIS_URL` | Redis URL for caching | *(unset — in-memory LRU cache)* |

### Execution

| Variable | Description | Default |
|----------|-------------|---------|
| `TEB_EXECUTOR_TIMEOUT` | HTTP timeout for outbound calls (seconds) | `30` |
| `TEB_EXECUTOR_MAX_RETRIES` | Max retries for failed API calls | `2` |
| `MAX_TASKS_PER_GOAL` | Maximum tasks per goal | `20` |
| `TEB_AUTONOMOUS_EXECUTION` | Enable background execution loop | `true` |
| `TEB_AUTONOMOUS_EXECUTION_INTERVAL` | Seconds between execution loop checks | `30` |
| `TEB_AUTOPILOT_DEFAULT_THRESHOLD` | Max $ auto-approved per transaction | `50.0` |

### Deployment

| Variable | Description | Default |
|----------|-------------|---------|
| `TEB_BASE_PATH` | URL prefix for reverse proxy (e.g., `/teb`) | *(empty — app at `/`)* |
| `TEB_ENV` | `development` or `production` | `development` |
| `TEB_LOG_LEVEL` | Logging level | `INFO` |
| `TEB_LOG_FORMAT` | `text` or `json` (structured logging) | `text` |
| `TEB_SENTRY_DSN` | Sentry DSN for error tracking | *(unset)* |

### Payments (optional)

| Variable | Description | Default |
|----------|-------------|---------|
| `TEB_MERCURY_API_KEY` | Mercury banking API key | *(unset)* |
| `TEB_STRIPE_API_KEY` | Stripe API key | *(unset)* |

### Channel Adapters (optional)

| Variable | Description | Default |
|----------|-------------|---------|
| `TEB_SLACK_BOT_TOKEN` | Slack bot token | *(unset)* |
| `TEB_SLACK_SIGNING_SECRET` | Slack signing secret | *(unset)* |
| `TEB_DISCORD_WEBHOOK_URL` | Discord webhook URL | *(unset)* |
| `TEB_WHATSAPP_TOKEN` | WhatsApp Cloud API token | *(unset)* |
| `TEB_WHATSAPP_PHONE_ID` | WhatsApp phone number ID | *(unset)* |

---

## Troubleshooting

### "401 Unauthorized" on every request

All API endpoints except `/health`, `/api/auth/register`, and `/api/auth/login`
require a JWT bearer token. Make sure you're passing `Authorization: Bearer
$TOKEN` in the header.

### AI decomposition returns generic/template tasks despite keys being set

1. Check that your `.env` has the keys uncommented (no `#` prefix).
2. If using Docker, verify the keys are listed under `environment:` in
   `docker-compose.yml`.
3. Test directly: `curl http://localhost:8000/health` — the response may
   include AI provider status.

### "TEB_JWT_SECRET must be set in production"

In production mode (`TEB_ENV=production`), `TEB_JWT_SECRET` is required and
will not be auto-generated. Set it explicitly in your `.env`.

### Tasks created with `POST /api/goals/{id}/tasks` return 404

This endpoint does not exist. The correct endpoint is:

```bash
POST /api/tasks
# with goal_id in the request body:
{"goal_id": 1, "title": "My task", "description": "..."}
```

### Static assets return 404 in production

If you're running behind a reverse proxy at `/teb`, set `TEB_BASE_PATH=/teb` in
your `.env`. All static asset URLs and API fetch calls use this prefix.

---

## What's Next

You've completed the core loop. Here's where to go deeper:

| Guide | What you'll learn |
|-------|-------------------|
| [User Guide](user-guide.md) | Complete feature reference — coaching, drip mode, budgets, gamification |
| [Tutorials](tutorials.md) | Step-by-step walkthroughs for specific goal types (freelancing, SaaS, etc.) |
| [API Clients](api-clients.md) | Python, JavaScript, and httpie client examples for every endpoint |
| [Plugin Guide](plugin-guide.md) | Build and register custom plugins — manifest format, execution hooks |
| [Webhooks](webhooks.md) | Set up outbound webhooks with HMAC-SHA256 signing for external integrations |
| [Architecture](architecture.md) | Deep dive into the multi-agent system, financial pipeline, DAG execution, and storage layer |
| [FAQ](faq.md) | Common questions and answers |
