# Changelog

All notable changes to teb are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Planned
- Privacy.com virtual card integration
- SMS notifications (Twilio)
- Payment sandbox/simulation mode

## [0.5.0] — 2026-04-12

### Added

#### Phase 2: Collaboration (10 items)
- Goal sharing with other users
- @mentions with notification extraction
- Task assignment (`assigned_to` field)
- SSE live updates via EventBus
- Presence indicators with heartbeat
- Collaborative editing with optimistic concurrency (version field, 409 on conflict)
- In-app direct messaging
- Goal-scoped chat rooms
- Email notification preferences
- Push subscription management

#### Phase 3: Views & Analytics (11 items)
- Mind map view (SVG-based `mindmap.js`)
- Kanban enhancements: swimlanes, WIP limits, card aging
- Custom saved views with filter/sort persistence
- View switching toolbar
- Dashboard builder with drag-and-drop widgets
- SVG chart widgets (bar, line, pie via `charts.js`)
- Goal progress timeline view
- CSV and JSON export endpoints
- Scheduled reports with cron-style configuration
- Burndown chart data API
- Time tracking reports with aggregation

#### Phase 4: Intelligence (11 items)
- Auto-rescheduling heuristics (`teb/intelligence.py`)
- Focus recommendations based on urgency/importance scoring
- AI writing assistant (expand, summarize, improve)
- Template generation from completed goals
- Meeting notes → task extraction
- Automated status report generation
- Smart tagging based on content analysis
- Workflow suggestions from task patterns
- Cross-goal insights and dependency detection
- Skill gap analysis from task history
- Stagnation prevention with proactive nudges

#### Phase 5: Ecosystem (17 items)
- Integration directory with categories
- OAuth flow support for service connections
- Integration templates (pre-built workflow mappings)
- Webhook builder with event/filter/target configuration
- Zapier/Make trigger and action registry
- API rate limit dashboard
- Plugin marketplace with install/browse
- Custom field type definitions
- Plugin-registered custom views
- Theming system with CSS variable overrides
- Plugin SDK documentation endpoint
- Monday.com, Jira, ClickUp importers
- CSV import for bulk task creation
- Full project export (goal + tasks + comments + artifacts)
- API export schema documentation

#### Phase 6: Enterprise (16 items)
- SSO/SAML configuration
- IP allowlisting with CIDR range support
- Data encryption at rest (Fernet)
- Audit log viewer with search/filter
- Organization management (multi-tenant)
- Usage analytics dashboard
- SCIM user provisioning endpoints
- Custom branding configuration
- Compliance report generation
- PostgreSQL migration planner
- Redis caching layer (in-memory default)
- CDN configuration for static assets
- Horizontal scaling recommendations
- Kubernetes deployment manifests (deploy/k8s/)
- Terraform infrastructure modules (deploy/terraform/)
- Multi-region configuration

#### Phase 7: Documentation & Community (16 items)
- Comprehensive user guide (docs/user-guide.md)
- Tutorial outlines (docs/tutorials.md)
- Quick-start guide (docs/quickstart.md)
- FAQ & troubleshooting (docs/faq.md)
- Architecture documentation (docs/architecture.md)
- Plugin development guide (docs/plugin-guide.md)
- API client examples for Python/JS/Go (docs/api-clients.md)
- Webhook documentation (docs/webhooks.md)
- Template gallery with community contributions
- Blog post management
- Public roadmap with feature voting
- Community links (Discord, GitHub Discussions)
- Changelog API endpoint

#### Phase 8: Polish & Differentiation (18 items)
- Level-up animation with XP tracking
- Sound effects (Web Audio API — complete/notification/levelup)
- Contextual first-time tooltips
- High contrast mode (WCAG AA)
- Smooth page transitions (fadeSlideIn/Out)
- Card hover micro-interactions
- Loading skeleton animations
- Confetti on goal completion
- Streak visualization
- ARIA live regions for screen readers
- Virtual scrolling for large task lists
- Lazy view loading (deferred script loading)
- Enhanced service worker with API response caching
- Performance preload hints
- Responsive image optimization
- Reduced motion media query support

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
- Pre-built integration catalog (25 services: Stripe, Namecheap, Vercel, SendGrid, GitHub, Cloudflare, Twitter, LinkedIn, Plausible, OpenAI, DigitalOcean, AWS S3, Twilio, HubSpot, Airtable, Notion, Slack, Discord, Shopify, Mailgun, Resend, Supabase, Anthropic, Google Maps, Zapier)
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
- SSRF protection for outbound HTTP calls (private IP/metadata blocking)
- Timing-safe token comparison for JWT validation
- Path traversal protection in file-serving routes

#### Admin Panel
- Admin web UI with platform stats, user management, and integration management
- 8 admin REST API endpoints (role-gated to admin users)
- User search, role editing, account unlocking, and user deletion
- Integration CRUD from the admin panel

#### UI
- Dark mode with system-preference detection and toggle
- Toast notification system for user feedback
- CSS animations and transitions
- Design tokens for consistent theming
- Accessibility improvements (ARIA labels, keyboard navigation, focus management)

#### Infrastructure
- FastAPI REST API (97 endpoints)
- SQLite database with 27 tables and WAL mode
- Docker support with health checks, non-root user, and Playwright pre-installed
- docker-compose with persistent volume
- Docker entrypoint auto-generates `TEB_SECRET_KEY` if not set
- Structured logging with configurable levels
- Single-page web frontend (HTML + vanilla JS + CSS)
- One-liner start script (`start.sh`) with secret auto-generation
- `pip install teb` support with CLI entry point
- Database backup script (`deploy/backup.sh`) with systemd timer
- SQL migration system (`migrations/migrate.py`)
- CI/CD workflows: tests, Docker build, PyPI publish, Docker Hub publish, deploy
- Systemd service file with full installation instructions
- nginx reverse-proxy config for `/teb` path
- HTTPS/TLS setup documentation (Certbot + Caddy)
- 601 automated tests across 12 test files
