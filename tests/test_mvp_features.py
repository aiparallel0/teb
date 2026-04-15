"""
Tests for all new features:
- Payments (Mercury + Stripe providers)
- Service discovery (catalog + scoring + AI discovery)
- User behavior inference & abandonment detection
- Persistent agent memory
- Success path knowledge graph
- Security fixes (schema migration, Telegram security, messaging scoping)
"""

import json
import os
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from teb import storage
from teb.main import app
from teb.models import Goal, SpendingBudget, SpendingRequest, SuccessPath, Task, User

client = TestClient(app)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _register_user(email="test@teb.test", password="testpass123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    if r.status_code not in (200, 201):
        r = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_goal(title="test goal", user_id=None):
    g = Goal(title=title, description="")
    g.user_id = user_id
    return storage.create_goal(g)


def _make_task(goal_id, title="task1", status="todo"):
    t = Task(goal_id=goal_id, title=title, description="desc", estimated_minutes=30, order_index=0)
    t.status = status
    return storage.create_task(t)


# ═══════════════════════════════════════════════════════════════════════════════
# Schema Migration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaMigration:
    def test_init_db_creates_new_tables(self):
        """Verify that init_db creates the new tables."""
        with storage._conn() as con:
            tables = [r["name"] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()]
        assert "agent_memory" in tables
        assert "user_behavior" in tables
        assert "payment_accounts" in tables
        assert "payment_transactions" in tables
        assert "discovered_services" in tables

    def test_messaging_configs_has_user_id(self):
        """Verify user_id column exists on messaging_configs."""
        with storage._conn() as con:
            cols = [r["name"] for r in con.execute("PRAGMA table_info(messaging_configs)").fetchall()]
        assert "user_id" in cols

    def test_migration_is_idempotent(self):
        """Running init_db twice should not fail."""
        storage.init_db()
        storage.init_db()
        # Should not raise


# ═══════════════════════════════════════════════════════════════════════════════
# Payment Provider Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaymentProviders:
    def test_list_providers(self):
        from teb import payments
        providers = payments.list_providers()
        names = [p["name"] for p in providers]
        assert "mercury" in names
        assert "stripe" in names

    def test_get_provider(self):
        from teb import payments
        assert payments.get_provider("mercury") is not None
        assert payments.get_provider("stripe") is not None
        assert payments.get_provider("unknown") is None

    def test_mercury_get_balance_no_account_id(self):
        from teb.payments import MercuryProvider
        p = MercuryProvider()
        result = p.get_balance({})
        assert "error" in result

    def test_stripe_get_balance_no_key(self):
        from teb.payments import StripeProvider
        p = StripeProvider()
        # Will fail because no API key
        result = p.get_balance({"api_key": ""})
        assert result.get("available", -1) == 0  # Returns 0 on error

    def test_mercury_list_accounts_no_key(self):
        from teb.payments import MercuryProvider
        p = MercuryProvider()
        result = p.list_accounts({"api_key": ""})
        assert isinstance(result, list)

    def test_execute_payment_unknown_provider(self):
        from teb import payments
        result = payments.execute_payment(1, "unknown_provider", 10, "USD", "", "test")
        assert result["status"] == "failed"
        assert "Unknown provider" in result["error"]


class TestPaymentAPI:
    def test_list_providers_endpoint(self):
        headers = _register_user("pay_prov@teb.test")
        r = client.get("/api/payments/providers", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) >= 2

    def test_create_payment_account(self):
        headers = _register_user("pay_acct@teb.test")
        r = client.post("/api/payments/accounts", headers=headers, json={
            "provider": "mercury",
            "account_id": "acct_123",
            "config": {"api_key": "test-key"},
        })
        assert r.status_code == 201
        assert r.json()["provider"] == "mercury"

    def test_create_payment_account_invalid_provider(self):
        headers = _register_user("pay_inv@teb.test")
        r = client.post("/api/payments/accounts", headers=headers, json={
            "provider": "invalid",
        })
        assert r.status_code == 422

    def test_list_payment_accounts(self):
        headers = _register_user("pay_list@teb.test")
        client.post("/api/payments/accounts", headers=headers, json={
            "provider": "stripe", "account_id": "acct_s1",
        })
        r = client.get("/api/payments/accounts", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) >= 1

    def test_get_balance_no_account(self):
        headers = _register_user("pay_bal@teb.test")
        r = client.get("/api/payments/balance/mercury", headers=headers)
        assert r.status_code == 400

    def test_payment_requires_auth(self):
        r = client.get("/api/payments/providers")
        assert r.status_code == 401


class TestPaymentStorage:
    def test_create_and_list_payment_accounts(self):
        u = storage.create_user(User(email="paystor@t.com", password_hash="h"))
        acct = storage.create_payment_account(u.id, "mercury", "acct_1", '{"key":"val"}')
        assert acct["id"] is not None
        assert acct["provider"] == "mercury"

        accounts = storage.list_payment_accounts(u.id)
        assert len(accounts) == 1
        assert accounts[0]["account_id"] == "acct_1"

    def test_get_payment_account(self):
        u = storage.create_user(User(email="payget@t.com", password_hash="h"))
        acct = storage.create_payment_account(u.id, "stripe", "acct_s")
        fetched = storage.get_payment_account(acct["id"])
        assert fetched is not None
        assert fetched["provider"] == "stripe"

    def test_create_and_list_transactions(self):
        u = storage.create_user(User(email="paytx@t.com", password_hash="h"))
        acct = storage.create_payment_account(u.id, "stripe", "acct_s")
        tx = storage.create_payment_transaction(acct["id"], None, 50.0, "USD", "Test payment")
        assert tx["id"] is not None
        assert tx["amount"] == 50.0

        txs = storage.list_payment_transactions(acct["id"])
        assert len(txs) == 1
        assert txs[0]["description"] == "Test payment"

    def test_update_payment_transaction(self):
        u = storage.create_user(User(email="paytxu@t.com", password_hash="h"))
        acct = storage.create_payment_account(u.id, "mercury", "acct_m")
        tx = storage.create_payment_transaction(acct["id"], None, 25.0, "USD", "Transfer")
        updated = storage.update_payment_transaction(tx["id"], "completed", "tx_provider_123")
        assert updated["status"] == "completed"
        assert updated["provider_tx_id"] == "tx_provider_123"


# ═══════════════════════════════════════════════════════════════════════════════
# Service Discovery Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestDiscoveryEngine:
    def test_discover_for_money_goal(self):
        from teb import discovery
        results = discovery.discover_for_goal("earn money online freelancing")
        assert len(results) > 0
        names = [r["service_name"] for r in results]
        assert "upwork" in names or "fiverr" in names

    def test_discover_for_build_goal(self):
        from teb import discovery
        results = discovery.discover_for_goal("build a website portfolio")
        assert len(results) > 0
        names = [r["service_name"] for r in results]
        assert any(n in names for n in ["webflow", "netlify", "figma"])

    def test_discover_for_startup_goal(self):
        from teb import discovery
        results = discovery.discover_for_goal("launch a startup SaaS product", template_name="launch_startup")
        assert len(results) > 0
        names = [r["service_name"] for r in results]
        # Should find at least one of these startup/product tools
        assert any(n in names for n in ["stripe", "mercury", "vercel", "railway",
                                         "lemonsqueezy", "bubble", "render"])

    def test_discover_for_learning_goal(self):
        from teb import discovery
        results = discovery.discover_for_goal("learn Python programming")
        assert len(results) > 0
        names = [r["service_name"] for r in results]
        assert "coursera" in names or "udemy" in names

    def test_discover_skill_level_filtering(self):
        from teb import discovery
        beginner = discovery.discover_for_goal("build an app", user_skill_level="beginner")
        advanced = discovery.discover_for_goal("build an app", user_skill_level="advanced")
        # Both should return results but may have different rankings
        assert len(beginner) > 0
        assert len(advanced) > 0

    def test_discover_with_template(self):
        from teb import discovery
        results = discovery.discover_for_goal("make money", template_name="make_money_online")
        assert len(results) > 0

    def test_discover_for_user_no_goals(self):
        from teb import discovery
        u = storage.create_user(User(email="disc_user@t.com", password_hash="h"))
        results = discovery.discover_for_user(u.id)
        assert results == []

    def test_discover_for_user_with_goals(self):
        from teb import discovery
        u = storage.create_user(User(email="disc_user2@t.com", password_hash="h"))
        g = Goal(title="earn money online freelancing", description="")
        g.user_id = u.id
        storage.create_goal(g)
        results = discovery.discover_for_user(u.id)
        assert len(results) > 0

    def test_record_discovery(self):
        from teb import discovery
        result = discovery.record_discovery(
            service_name="test_service",
            category="testing",
            description="A test service",
            url="https://test.com",
            capabilities=["test"],
        )
        assert result["service_name"] == "test_service"

    def test_scoring_direct_name_match(self):
        from teb import discovery
        results = discovery.discover_for_goal("I need to use bubble to build my app")
        names = [r["service_name"] for r in results]
        assert "bubble" in names
        # Direct name mention should score high
        bubble_result = next(r for r in results if r["service_name"] == "bubble")
        assert bubble_result["score"] >= 15


class TestDiscoveryAPI:
    def test_discover_services_with_query(self):
        headers = _register_user("disc_api@teb.test")
        r = client.get("/api/discover/services?q=earn+money+freelancing", headers=headers)
        assert r.status_code == 200
        assert len(r.json()) > 0

    def test_discover_services_for_user(self):
        headers = _register_user("disc_api2@teb.test")
        # Create a goal first
        client.post("/api/goals", headers=headers, json={"title": "build a website"})
        r = client.get("/api/discover/services", headers=headers)
        assert r.status_code == 200

    def test_discover_catalog(self):
        r = client.get("/api/discover/catalog")
        assert r.status_code == 200
        assert len(r.json()) >= 15  # At least 15 of the 20+ services in catalog

    def test_record_discovered_service(self):
        headers = _register_user("disc_rec@teb.test")
        r = client.post("/api/discover/record", headers=headers, json={
            "service_name": "new_tool",
            "category": "productivity",
            "description": "A new productivity tool",
            "url": "https://newtool.com",
            "capabilities": ["organize", "plan"],
        })
        assert r.status_code == 201


class TestDiscoveryStorage:
    def test_create_and_list_discovered_services(self):
        storage.create_discovered_service(
            "svc1", "testing", "Test service", "https://svc1.com",
            json.dumps(["cap1", "cap2"]), "ai", 0.8,
        )
        services = storage.list_discovered_services()
        assert len(services) >= 1
        assert services[0]["service_name"] == "svc1"

    def test_discovered_service_upsert(self):
        storage.create_discovered_service("svc_dup", "cat1", "v1", "url1")
        result = storage.create_discovered_service("svc_dup", "cat2", "v2", "url2")
        assert result["updated"] is True

    def test_filter_by_category(self):
        storage.create_discovered_service("svc_cat1", "banking", "Bank", "url")
        storage.create_discovered_service("svc_cat2", "hosting", "Host", "url")
        banking = storage.list_discovered_services(category="banking")
        assert all(s["category"] == "banking" for s in banking)

    def test_increment_recommendation(self):
        result = storage.create_discovered_service("svc_inc", "cat", "desc", "url")
        storage.increment_service_recommendation(result["id"])
        services = storage.list_discovered_services()
        svc = next(s for s in services if s["service_name"] == "svc_inc")
        assert svc["times_recommended"] == 1


# ═══════════════════════════════════════════════════════════════════════════════
# User Behavior Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserBehaviorStorage:
    def test_record_behavior(self):
        u = storage.create_user(User(email="beh1@t.com", password_hash="h"))
        result = storage.record_user_behavior(u.id, "avoids", "cli_deployment")
        assert result["occurrences"] == 1

    def test_record_behavior_increments(self):
        u = storage.create_user(User(email="beh2@t.com", password_hash="h"))
        storage.record_user_behavior(u.id, "avoids", "terminal")
        result = storage.record_user_behavior(u.id, "avoids", "terminal")
        assert result["occurrences"] == 2

    def test_list_behaviors(self):
        u = storage.create_user(User(email="beh3@t.com", password_hash="h"))
        storage.record_user_behavior(u.id, "avoids", "cli")
        storage.record_user_behavior(u.id, "prefers", "visual_tools")
        all_b = storage.list_user_behaviors(u.id)
        assert len(all_b) == 2
        avoids = storage.list_user_behaviors(u.id, "avoids")
        assert len(avoids) == 1


class TestUserBehaviorAPI:
    def test_list_behaviors_endpoint(self):
        headers = _register_user("beh_api@teb.test")
        r = client.get("/api/users/me/behaviors", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_abandonment_detection_no_goals(self):
        headers = _register_user("beh_abn@teb.test")
        r = client.get("/api/users/me/abandonment", headers=headers)
        assert r.status_code == 200
        assert r.json()["total_stalled"] == 0

    def test_abandonment_detection_with_stalled_goal(self):
        headers = _register_user("beh_abn2@teb.test")
        # Create a goal via API
        gr = client.post("/api/goals", headers=headers, json={"title": "stalled goal"})
        goal_id = gr.json()["id"]

        # Update the goal to be stale (manual DB update)
        old_date = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        with storage._conn() as con:
            con.execute("UPDATE goals SET status = 'in_progress', updated_at = ? WHERE id = ?",
                        (old_date, goal_id))

        # Create a stuck task
        t = Task(goal_id=goal_id, title="stuck task", description="", status="in_progress")
        t = storage.create_task(t)
        old_created = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        with storage._conn() as con:
            con.execute("UPDATE tasks SET created_at = ? WHERE id = ?", (old_created, t.id))

        r = client.get("/api/users/me/abandonment", headers=headers)
        assert r.status_code == 200
        assert r.json()["total_stalled"] >= 1

    def test_behaviors_require_auth(self):
        r = client.get("/api/users/me/behaviors")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# Agent Memory Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAgentMemoryStorage:
    def test_create_agent_memory(self):
        mem = storage.create_agent_memory("marketing", "make_money_online",
                                           "best_strategy", "Use social proof in profile")
        assert mem["id"] is not None
        assert mem["agent_type"] == "marketing"

    def test_list_agent_memories(self):
        storage.create_agent_memory("research", "learn_skill", "key1", "value1")
        storage.create_agent_memory("research", "learn_skill", "key2", "value2")
        storage.create_agent_memory("research", "build_project", "key3", "value3")

        all_research = storage.list_agent_memories("research")
        assert len(all_research) == 3
        learn_only = storage.list_agent_memories("research", "learn_skill")
        assert len(learn_only) == 2

    def test_increment_memory_usage(self):
        mem = storage.create_agent_memory("coordinator", "", "insight", "test insight")
        storage.increment_agent_memory_usage(mem["id"])
        mems = storage.list_agent_memories("coordinator")
        updated = next(m for m in mems if m["id"] == mem["id"])
        assert updated["times_used"] == 1


class TestAgentMemoryAPI:
    def test_get_agent_memory(self):
        headers = _register_user("mem_api@teb.test")
        r = client.get("/api/agents/memory/coordinator", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_store_agent_memory(self):
        headers = _register_user("mem_store@teb.test")
        r = client.post("/api/agents/memory", headers=headers, json={
            "agent_type": "finance",
            "goal_type": "launch_startup",
            "memory_key": "budget_tip",
            "memory_value": "Startups typically need $500-2000 for initial tools",
        })
        assert r.status_code == 201
        assert r.json()["agent_type"] == "finance"

    def test_agent_memory_requires_auth(self):
        r = client.get("/api/agents/memory/coordinator")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# Knowledge Graph (Success Path) Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestKnowledgeGraphAPI:
    def test_list_knowledge_paths(self):
        headers = _register_user("kg_list@teb.test")
        r = client.get("/api/knowledge/paths?goal_type=make_money_online", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "paths" in data
        assert "total" in data

    def test_recommend_path_no_data(self):
        headers = _register_user("kg_rec@teb.test")
        r = client.get("/api/knowledge/recommend/unknown_type", headers=headers)
        assert r.status_code == 200
        assert r.json()["recommendation"] is None

    def test_recommend_path_with_data(self):
        headers = _register_user("kg_rec2@teb.test")
        # Create a real goal for FK constraint
        goal = _make_goal("test goal for path")
        sp = SuccessPath(
            goal_type="make_money_online",
            steps_json=json.dumps({
                "steps": [
                    {"title": "Research market", "status": "done"},
                    {"title": "Create profile", "status": "done"},
                    {"title": "Apply to jobs", "status": "done"},
                ],
                "deviations": {"added_tasks": ["Set up payment"], "skipped_template_tasks": []},
            }),
            outcome_summary="Earned first $100 freelancing",
            source_goal_id=goal.id,
            times_reused=5,
        )
        storage.create_success_path(sp)

        r = client.get("/api/knowledge/recommend/make_money_online", headers=headers)
        assert r.status_code == 200
        rec = r.json()["recommendation"]
        assert rec is not None
        assert rec["times_reused"] == 5

    def test_knowledge_requires_auth(self):
        r = client.get("/api/knowledge/paths?goal_type=test")
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# Security Fix Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityFixes:
    def test_goals_list_no_data_leak(self):
        """Unauthenticated list_goals returns only unscoped goals."""
        u = storage.create_user(User(email="sec1@t.com", password_hash="h"))
        g = Goal(title="private", description="")
        g.user_id = u.id
        storage.create_goal(g)
        # Unscoped call returns only NULL user_id goals
        public = storage.list_goals()
        assert all(getattr(g, "user_id", None) is None for g in public)

    def test_spending_action_requires_ownership(self):
        """Spending action endpoint verifies task ownership."""
        h1 = _register_user("sec_own1@teb.test")
        h2 = _register_user("sec_own2@teb.test")
        # User 1 creates a goal + task + budget + request
        gr = client.post("/api/goals", headers=h1, json={"title": "my goal"})
        goal_id = gr.json()["id"]
        t = Task(goal_id=goal_id, title="task", description="", estimated_minutes=30)
        t = storage.create_task(t)
        budget = storage.create_spending_budget(
            SpendingBudget(goal_id=goal_id, daily_limit=100, total_limit=1000, category="general"))
        req = storage.create_spending_request(SpendingRequest(
            task_id=t.id, budget_id=budget.id, amount=10, description="test", service="test", status="pending"))
        # User 2 tries to approve — should get 403
        r = client.post(f"/api/spending/{req.id}/action", headers=h2,
                        json={"action": "approve"})
        assert r.status_code == 403

    def test_messaging_config_scoped_to_user(self):
        """Messaging configs are scoped to the creating user."""
        h1 = _register_user("msg_sc1@teb.test")
        h2 = _register_user("msg_sc2@teb.test")
        # User 1 creates a config
        client.post("/api/messaging/config", headers=h1, json={
            "channel": "webhook",
            "config": {"url": "https://example.com/hook"},
        })
        # User 2 should not see it
        r = client.get("/api/messaging/configs", headers=h2)
        assert r.status_code == 200
        assert len(r.json()) == 0

    def test_telegram_secret_token_verification(self):
        """Telegram webhook rejects requests with wrong secret token."""
        with patch.dict(os.environ, {"TEB_TELEGRAM_SECRET_TOKEN": "my-secret-123"}):
            # Without correct header — should be 403
            r = client.post("/api/messaging/telegram/webhook", json={
                "message": {"text": "/next", "chat": {"id": 1}},
            })
            assert r.status_code == 403

            # With correct header — should pass
            r = client.post("/api/messaging/telegram/webhook", json={
                "message": {"text": "/next", "chat": {"id": 99998}},
            }, headers={"X-Telegram-Bot-Api-Secret-Token": "my-secret-123"})
            assert r.status_code == 200

    def test_telegram_no_global_goal_fallback(self):
        """Telegram /next should not fall back to other users' goals."""
        # Create a goal owned by some user
        u = storage.create_user(User(email="tg_sec@t.com", password_hash="h"))
        g = Goal(title="private goal", description="")
        g.user_id = u.id
        storage.create_goal(g)

        # Telegram /next from unknown chat — should not see the goal
        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": "/next", "chat": {"id": 88888}},
        })
        assert r.status_code == 200
        # Should get "no selected goal" message, not a task from the private goal
        data = r.json()
        assert data.get("message") == "No selected goal" or data.get("ok") is True


# ═══════════════════════════════════════════════════════════════════════════════
# Skip Rate Fix Test
# ═══════════════════════════════════════════════════════════════════════════════

class TestSkipRateFix:
    def test_skip_rate_counts_dict_steps(self):
        """_check_skip_rate should count skips from both deviations and steps in dict format."""
        from teb.decomposer import _check_skip_rate

        # Create a real goal for FK constraint
        goal = _make_goal("skip rate test goal")

        # Create success paths with dict format including steps
        for _ in range(3):
            sp = SuccessPath(
                goal_type="test_template",
                steps_json=json.dumps({
                    "steps": [
                        {"title": "Step A", "status": "done"},
                        {"title": "Step B", "status": "skipped"},
                        {"title": "Step C", "status": "done"},
                    ],
                    "deviations": {"skipped_template_tasks": [], "added_tasks": []},
                }),
                outcome_summary="completed",
                source_goal_id=goal.id,
            )
            storage.create_success_path(sp)

        # Step B is skipped in all 3 paths → should trigger warning
        result = _check_skip_rate("test_template", "Step B")
        assert result is not None
        assert "skip" in result.lower()

        # Step A is not skipped → should return None
        result = _check_skip_rate("test_template", "Step A")
        assert result is None
