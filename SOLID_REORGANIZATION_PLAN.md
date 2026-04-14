# teb SOLID Reorganization Plan

## Executive Summary

**Current state:** 63,460 lines across 51 source files + 27 test files  
**Target:** ~15,000–18,000 lines (70–75% reduction) with **zero feature loss, zero functionality loss**  
**Strategy:** Replace boilerplate with metaclass/generic machinery, extract router domains, consolidate test infrastructure

The codebase has grown through accretive feature PRs into a monolith pattern. The three god-files — `main.py` (8,272 lines, 378 endpoints), `storage/_monolith.py` (6,410 lines, 412 functions), and `app.js` (5,953 lines) — contain massive structural repetition that can be eliminated through SOLID-driven abstractions.

---

## Codebase Audit

### By the numbers

| File | Lines | Functions/Endpoints | SOLID Violation |
|------|-------|-------------------|-----------------|
| `teb/main.py` | 8,272 | 399 funcs, 378 endpoints | **S** — God file. 117 sections, 28 Pydantic schemas, 272 thin-wrapper endpoints |
| `teb/storage/_monolith.py` | 6,410 | 412 funcs, 65 `_row_to_*` converters | **S** — Every entity's CRUD in one file. **O** — Adding entities means modifying this file |
| `teb/static/app.js` | 5,953 | 138 functions, 489 DOM ops | **S** — God file for all UI. **D** — Tightly coupled to DOM |
| `teb/models.py` | 2,083 | 79 dataclasses, 79 `to_dict()` | **O** — Every model hand-writes `to_dict()`. **D** — No base class |
| `teb/storage/base.py` | 1,360 | 84 CREATE TABLE, 72 indexes, 19 ALTER TABLE | **S** — Schema + connection + retry + encryption in one file |
| `teb/decomposer.py` | 2,682 | 53 functions | Acceptable — cohesive domain |
| `tests/*.py` | 16,512 | 833+ tests, 27 files | **D** — `setup_test_db` duplicated 9×, `_fresh_db` 11×, `_register_and_login` 5× |

### Identified waste patterns

1. **65 identical `_row_to_*` converters** — Each is 5–15 lines of `field=row["field"]` boilerplate. A generic `_row_to_model(row, ModelClass)` eliminates all 65 (~600 lines).

2. **79 hand-written `to_dict()` methods** — Each is 5–20 lines. A `@auto_dict` decorator or mixin using `dataclasses.fields()` eliminates all 79 (~800 lines).

3. **272 thin-wrapper CRUD endpoints** — Pattern: `_require_user` → `storage.get_X` → `return X.to_dict()`. A generic CRUD router factory eliminates ~200 of these (~3,000 lines).

4. **28 Pydantic request schemas in main.py** — Many are simple field bags that mirror dataclass definitions. Can be auto-generated or use a shared schema factory.

5. **84 CREATE TABLE + 72 CREATE INDEX + 19 ALTER TABLE in one function** — Schema should be declarative (table definitions derived from model dataclasses).

6. **Test boilerplate duplicated across 27 files** — `setup_test_db` (9 files), `_fresh_db` (11 files), `_register_and_login` (5 files), `_make_goal` (4 files), `_make_task` (5 files) should all live in `conftest.py`.

7. **`app.js` monolith** — 5,953 lines of vanilla JS with 489 DOM ops. Already partially split into `static/views/`. The remaining core can be reduced by extracting a fetch-wrapper, a component system, and shared DOM utilities.

---

## SOLID Principles Applied

### S — Single Responsibility

| Current | Target |
|---------|--------|
| `main.py` handles ALL 378 endpoints | 15–20 domain router modules in `teb/routers/` |
| `storage/_monolith.py` handles ALL entity CRUD | Generic CRUD base + domain-specific overrides only |
| `models.py` has 79 classes with hand-coded serialization | Base model mixin auto-generates `to_dict()` and `from_row()` |
| `app.js` handles all UI | Core module + lazy-loaded view modules |

### O — Open/Closed

| Current | Target |
|---------|--------|
| Adding an entity requires modifying 4 files (models, storage, main, tests) | Adding an entity = 1 dataclass definition + 1 router registration. CRUD auto-generated. |
| Schema changes require editing `_run_migrations()` | Schema derived from model definitions — migrations auto-generated |

### L — Liskov Substitution

| Current | Target |
|---------|--------|
| No inheritance used; every model is independent | `TebModel` base with `to_dict()`, `from_row()` — all models substitutable |
| Channel implementations (Slack/Discord/WhatsApp) already follow this ✓ | Keep as-is |

### I — Interface Segregation

| Current | Target |
|---------|--------|
| Every endpoint handler mixes auth, validation, business logic, serialization | Separate middleware chain: auth → validate → execute → serialize |
| Storage functions mix DB access with business rules | Pure storage layer (CRUD) + service layer (business rules) |

### D — Dependency Inversion

| Current | Target |
|---------|--------|
| Endpoints directly import and call `storage.*` | Service layer abstractions; endpoints depend on interfaces |
| Tests directly set up DB paths and create users | Shared fixtures via conftest.py |

---

## Implementation Phases

### Phase 1: Foundation Layer (eliminate ~3,000 lines)

**Goal:** Create the meta-machinery that makes subsequent phases trivial.

#### 1.1 — `TebModel` base mixin for models.py

```
# What it does:
# - Auto-generates to_dict() from dataclass fields
# - Handles datetime → isoformat conversion
# - Handles JSON string fields → parsed objects
# - Provides from_row(sqlite3.Row) class method
# - Provides field_names() class method for SQL generation

# Result: 79 hand-written to_dict() methods → 0
# Result: 65 _row_to_* functions → 0 (replaced by ModelClass.from_row(row))
# Net deletion: ~1,400 lines from models.py + storage
```

#### 1.2 — Generic CRUD storage engine

```
# What it does:
# - CrudTable(model_class, table_name, writable_fields, id_field)
# - Auto-generates: create(), get(), list_by(), update(), delete()
# - Uses _with_retry decorator on writes
# - Handles timestamp auto-population
# - Domain-specific overrides only for non-standard queries

# Result: ~300 boilerplate CRUD functions → ~30 registrations + ~50 custom overrides
# Net deletion: ~3,000 lines from storage/_monolith.py
```

#### 1.3 — Schema declaration from models

```
# What it does:
# - Derives CREATE TABLE from dataclass field definitions
# - Type mapping: int → INTEGER, str → TEXT, bool → INTEGER, datetime → TEXT
# - Optional[int] → allows NULL
# - Auto-generates indexes for foreign key fields
# - Migration: compare declared schema vs existing → auto ALTER TABLE

# Result: 84 hand-written CREATE TABLE → ~10 lines of config
# Net deletion: ~600 lines from storage/base.py
```

### Phase 2: Router Extraction (eliminate ~4,000 lines)

**Goal:** Break `main.py` into domain routers using FastAPI's `APIRouter`.

#### 2.1 — Generic CRUD router factory

```
# What it does:
# - crud_router(prefix, model_class, storage_table, auth_level, ...)
# - Auto-generates: POST (create), GET /{id} (read), GET (list), PATCH /{id} (update), DELETE /{id}
# - Handles _require_user / _require_admin injection
# - Handles pagination via shared helper
# - Handles error responses via shared patterns

# Result: 272 thin-wrapper endpoints → ~40 router registrations
# Net deletion: ~3,000 lines from main.py
```

#### 2.2 — Domain router modules

Extract the remaining non-trivial endpoints into cohesive routers:

| Router | Endpoints | Current sections |
|--------|-----------|-----------------|
| `routers/goals.py` | ~20 | Goals, decompose, focus, progress, cloning |
| `routers/tasks.py` | ~15 | Tasks, task execution, recurrence, blockers |
| `routers/agents.py` | ~10 | Multi-agent delegation, agent flows/schedules |
| `routers/financial.py` | ~15 | Budgets, spending, payments, ROI |
| `routers/collaboration.py` | ~15 | Workspaces, collaborators, chat, DMs |
| `routers/integrations.py` | ~15 | Integration marketplace, OAuth, webhooks, Zapier |
| `routers/plugins.py` | ~10 | Plugin system, plugin views, themes |
| `routers/enterprise.py` | ~15 | SSO, IP allowlist, org management, compliance |
| `routers/analytics.py` | ~10 | Dashboard, reports, metrics, insights |
| `routers/community.py` | ~10 | Blog, roadmap, feature votes, content blocks |
| `routers/gamification.py` | ~8 | XP, streaks, leaderboard, challenges |
| `routers/channels.py` | (keep existing) | Slack, Discord, WhatsApp webhooks |

**Note:** 4 routers already extracted (`health`, `auth`, `settings`, `notifications`). The remaining ~106 non-trivial endpoints get domain routers. The ~272 thin-wrappers are eliminated by the CRUD factory.

#### 2.3 — Shared dependencies module

```
# What it does:
# - Consolidates _require_user, _require_admin, _get_goal_for_user, _get_task_for_user
# - Pagination helper
# - Rate limiting middleware (replaces per-endpoint checks)
# - Error response factory
# - Move Pydantic schemas to per-router modules or auto-generate from models

# Result: main.py shrinks from 8,272 to ~300 lines (app creation, middleware, router inclusion)
```

### Phase 3: Storage Consolidation (eliminate ~2,500 lines)

**Goal:** Replace `_monolith.py` with the generic engine + domain-specific overrides.

#### 3.1 — Register all simple entities with CrudTable

~45 of the 65 entity types follow the exact same pattern:
- `_row_to_X` → eliminated by `TebModel.from_row()`
- `create_X` → `CrudTable.create()`
- `list_X` → `CrudTable.list_by()`
- `get_X` → `CrudTable.get()`
- `update_X` → `CrudTable.update()`
- `delete_X` → `CrudTable.delete()`

Each registration is ~3 lines vs the current ~50–80 lines per entity.

#### 3.2 — Domain storage modules for complex queries

Only ~20 entity types have non-trivial queries (joins, aggregations, custom logic):

| Module | Entities | Why non-trivial |
|--------|----------|-----------------|
| `storage/goals.py` | Goal, Milestone, GoalCollaborator | Hierarchy queries, sub-goal listing, ROI computation |
| `storage/tasks.py` | Task, TaskBlocker, TaskComment, TaskArtifact | Dependency graph, ready-task detection, search |
| `storage/financial.py` | SpendingBudget, SpendingRequest, PaymentTransaction | Budget checks, reconciliation, failed-tx recovery |
| `storage/auth.py` | User, RefreshToken, UserSession, TwoFactorConfig | Password handling, token rotation, session management |
| `storage/analytics.py` | OutcomeMetric, ProgressSnapshot, TimeEntry | ROI aggregation, burndown computation |
| `storage/enterprise.py` | Organization, SSOConfig, IPAllowlist | Multi-tenant queries |

#### 3.3 — Eliminate redundant _monolith.py

Once all functions are either:
- Handled by CrudTable registrations, or
- Moved to domain storage modules

...the `_monolith.py` file is deleted entirely. The `__init__.py` re-exports from domain modules.

### Phase 4: Model Layer Cleanup (eliminate ~800 lines)

#### 4.1 — Apply `TebModel` mixin to all 79 dataclasses

- Remove all hand-written `to_dict()` methods
- Add field annotations for special serialization (JSON fields, datetime fields)
- Keep only models that have genuinely custom `to_dict()` logic (Goal with tags parsing, etc.) as overrides

#### 4.2 — Consolidate near-duplicate models

Several models are structurally identical or near-identical:
- `AgentSchedule` / `RecurrenceRule` — both are schedule definitions
- `IntegrationListing` / `PluginListing` — both are marketplace entries
- `IntegrationTemplate` / `GoalTemplate` — both are template structures
- `WebhookConfig` / `WebhookRule` — overlapping webhook definitions

Evaluate merging into parameterized models where the only difference is a `type` field.

### Phase 5: Test Infrastructure (eliminate ~3,000 lines)

#### 5.1 — Unified conftest.py

Move all shared fixtures to `tests/conftest.py`:

```
# Fixtures to centralize:
# - fresh_db (currently _fresh_db in 11 files)
# - setup_test_db (currently in 9 files)
# - registered_user (encapsulates _register_user / _register_and_login, 5+ files)
# - auth_header (creates user and returns Authorization header)
# - make_goal (creates a goal for a user)
# - make_task (creates a task under a goal)
# - api_client (httpx AsyncClient with app mounted)
```

#### 5.2 — Test consolidation

Many test files test overlapping functionality from different PRs:
- `test_bridging_plan.py`, `test_bridging_phases.py`, `test_bridging_features.py`, `test_grade_bridging.py` — 4 files (3,158 lines) that all test "bridging" features
- `test_phases5_to_8.py`, `test_phase2_collab.py`, `test_phase4_intelligence.py`, `test_phase6_enterprise.py` — phase-based tests (2,400 lines)

These should be reorganized by **domain** (matching the router structure), not by PR/phase:
- `tests/test_goals.py` — all goal CRUD and decomposition tests
- `tests/test_tasks.py` — all task CRUD, search, blockers
- `tests/test_financial.py` — budgets, spending, payments, ROI
- `tests/test_collaboration.py` — workspaces, DMs, chat
- `tests/test_agents.py` — multi-agent system (keep)
- `tests/test_enterprise.py` — SSO, org, compliance
- `tests/test_integrations.py` — webhooks, OAuth, Zapier
- `tests/test_gamification.py` — XP, streaks, challenges
- `tests/test_analytics.py` — dashboards, reports, burndown

#### 5.3 — Eliminate redundant test patterns

With the generic CRUD engine, many CRUD tests become redundant (they test the framework, not domain logic). Keep:
- One comprehensive CRUD test per entity type (happy path + error)
- All domain-specific behavior tests
- All security/auth tests

### Phase 6: Frontend Consolidation (eliminate ~2,000 lines)

#### 6.1 — Extract fetch wrapper

```javascript
// Currently: 4 raw fetch() calls + 489 DOM ops scattered
// Target: fetchApi(path, options) with automatic BASE_PATH, auth header, error handling
// Eliminates ~200 lines of repeated fetch boilerplate
```

#### 6.2 — DOM utility layer

```javascript
// Currently: 489 raw document.getElementById / querySelector calls
// Target: $(id), $$(selector), el.html(), el.text() micro-helpers
// Eliminates ~150 lines of null-guard boilerplate
```

#### 6.3 — Component extraction

The already-started `static/views/` pattern should absorb more from `app.js`:
- Move goal-list rendering to `views/goals.js`
- Move task-list rendering to `views/tasks.js`
- Move settings/admin panels to `views/admin.js`
- Move notification/chat UI to `views/comms.js`

### Phase 7: Cross-Cutting Cleanup

#### 7.1 — Eliminate dead code

- Check for unused imports across all files
- Remove functions that are defined but never called
- Remove commented-out code blocks
- Remove excessive section separator comments (`# ─── ...`)

#### 7.2 — Consolidate duplicate utilities

- `teb/logging_config.py` vs JSON formatter in `main.py` — pick one
- Rate limiting logic in `main.py` vs in `routers/deps.py` — centralize
- Error response helpers — one pattern, used everywhere

#### 7.3 — Fix known issues along the way

From the PR history bug patterns:
- Verify all `parseInt()` calls in `app.js` handle UUID IDs
- Verify all AI features have template fallbacks
- Verify all Docker env vars are in `docker-compose.yml`
- Verify drip-mode completion check has `tasks.length > 0` guard
- Verify all fetch calls use `BASE_PATH` prefix

---

## Execution Order and Dependencies

```
Phase 1.1 (TebModel mixin)
    ↓
Phase 1.2 (Generic CRUD storage) ← depends on 1.1 for from_row()
    ↓
Phase 1.3 (Schema from models) ← depends on 1.1 for field introspection
    ↓
Phase 3 (Storage consolidation) ← depends on 1.2
    ↓
Phase 4 (Model cleanup) ← depends on 1.1
    ↓
Phase 2 (Router extraction) ← depends on 1.2 for CRUD router factory
    ↓
Phase 5 (Test consolidation) ← depends on 2 + 3 (new import paths)
    ↓
Phase 6 (Frontend) ← independent, can parallel with 5
    ↓
Phase 7 (Cleanup) ← final pass
```

---

## Risk Mitigation

1. **Zero feature loss guarantee:** Every phase starts by running the full test suite (833+ tests). Every phase ends by running it again. Tests are migrated, never deleted without replacement.

2. **Incremental commits:** Each sub-phase (1.1, 1.2, etc.) is a self-contained commit. If anything breaks, rollback is granular.

3. **Backward-compatible imports:** The `storage/__init__.py` re-export pattern is maintained throughout. Old import paths continue to work until all consumers are migrated.

4. **Template mode preservation:** Every AI feature must still work without API keys. The generic engine does not change this invariant.

5. **Base path invariant:** All new routers use FastAPI's `prefix` parameter, maintaining the `/teb` mount path.

---

## Expected Outcome

| Metric | Before | After | Reduction |
|--------|--------|-------|-----------|
| Total source lines | 46,948 | ~12,000–15,000 | 68–74% |
| Total test lines | 16,512 | ~6,000–8,000 | 52–64% |
| Grand total | 63,460 | ~18,000–23,000 | **64–72%** |
| `main.py` | 8,272 | ~300 | 96% |
| `storage/_monolith.py` | 6,410 | 0 (deleted) | 100% |
| `models.py` | 2,083 | ~700 | 66% |
| `app.js` | 5,953 | ~2,500 | 58% |
| Files with >1000 lines | 5 | 0 | 100% |
| `_row_to_*` converters | 65 | 0 | 100% |
| Hand-written `to_dict()` | 79 | ~5 (custom only) | 94% |
| Duplicate test helpers | 15+ across files | 0 (in conftest) | 100% |
| Endpoints | 378 | 378 (same) | 0% (no loss) |
| Test count | 833+ | 833+ (same or more) | 0% (no loss) |
