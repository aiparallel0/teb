# Changelog

All notable changes to teb are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Planned
- Privacy.com virtual card integration
- SMS notifications (Twilio)
- Payment sandbox/simulation mode

## [0.1.0] — 2026-04-09

### Added

#### Core
- Goal management with status tracking (drafting → clarifying → decomposed → in_progress → done)
- Task decomposition — AI-powered (Claude/OpenAI) with template fallback (10 templates)
- Clarifying question flow — structured questioning before decomposition
- Focus mode — surface the single next actionable task
- Progress tracking with completion stats and time estimates
- Sub-task decomposition (up to 3 levels deep)

#### Execution
- API-based task execution — AI plans and executes REST API calls via registered credentials
- Browser automation — AI generates Playwright-based browser plans (navigate, click, type, extract)
- Manual fallback for browser plans when Playwright is not installed
- Pre-built integration catalog (10 services: Stripe, Namecheap, Vercel, SendGrid, GitHub, Cloudflare, Twitter, LinkedIn, Plausible, OpenAI)
- Execution logging for full traceability

#### AI & Agents
- Unified AI client supporting Anthropic Claude and OpenAI with automatic provider selection
- Multi-agent delegation system with 6 specialist agents (coordinator, marketing, web_dev, outreach, research, finance)
- Inter-agent message passing for context sharing and coordination
- Agent memory and knowledge persistence
- Retry with exponential backoff and provider fallback

#### Coaching & Engagement
- Daily check-in system with mood detection and tailored coaching feedback
- Stagnation detection with configurable nudge triggers
- Outcome metrics tracking (revenue, clients, skills — not just task completion)
- Proactive suggestion engine (opportunity, optimization, risk, learning categories)
- Adaptive micro-tasking (drip mode) — one task at a time with milestone questions

#### Financial
- Spending budget management with daily/total limits and category controls
- Per-transaction approval workflow (auto-approve, notify, require approval tiers)
- Mercury banking API integration (balances, transfers)
- Stripe payment processing integration (payment intents, customers, balance)

#### Personalization
- Persistent user profiles (skills, availability, experience, learning style)
- Success path learning — auto-captures proven paths and recommends to new users
- User behavior analytics and abandonment analysis
- Tool/service discovery engine with 50+ curated services and AI-powered matching

#### Notifications
- Telegram bot integration for real-time notifications
- Webhook integration for custom notification endpoints
- Configurable event filtering (nudges, tasks, spending, check-ins)

#### Authentication & Security
- JWT authentication with role-based access control (user/admin)
- Refresh token support
- Password hashing with bcrypt
- API credential encryption with Fernet
- Rate limiting on auth endpoints (20 req/min per IP)
- Account locking after failed login attempts
- CORS middleware with configurable origins

#### Infrastructure
- FastAPI REST API (80+ endpoints)
- SQLite database with 24 tables and WAL mode
- Docker support with health checks and non-root user
- docker-compose with persistent volume
- Structured logging with configurable levels
- Single-page web frontend (HTML + vanilla JS + CSS)
- 481 automated tests across 9 test files
