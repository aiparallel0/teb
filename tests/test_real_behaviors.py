"""
Tests for real (non-mocked) behavior:
- Browser automation execution with Playwright (mocking only the browser, not our code)
- Parallel agent orchestration
- Payment provider idempotency
- SQLite concurrency (busy_timeout, _with_retry)
- Rate limiting enforcement
"""

import hashlib
import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch, PropertyMock

import pytest
from fastapi.testclient import TestClient

from teb import agents, payments, storage
from teb.browser import (
    BrowserPlan,
    BrowserStep,
    BrowserStepResult,
    execute_browser_plan,
    _execute_manual_fallback,
)
from teb.main import app, reset_rate_limits
from teb.models import Goal, Task

client = TestClient(app)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _auth_headers():
    r = client.post("/api/auth/register", json={"email": "real@teb.test", "password": "testpass123"})
    if r.status_code not in (200, 201):
        r = client.post("/api/auth/login", json={"email": "real@teb.test", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_goal(title="Test goal") -> Goal:
    return storage.create_goal(Goal(title=title, description="test", user_id=1))


def _make_task(goal_id, title="Test task") -> Task:
    return storage.create_task(Task(goal_id=goal_id, title=title, description="do stuff"))


# ═══════════════════════════════════════════════════════════════════════════════
# 1. BROWSER AUTOMATION — Playwright execution path
# ═══════════════════════════════════════════════════════════════════════════════

class TestBrowserExecution:
    """Test that the browser execution engine handles all action types."""

    def test_manual_fallback_returns_all_steps(self):
        """Manual fallback should return a BrowserStepResult for each step."""
        plan = BrowserPlan(
            can_automate=True,
            reason="test",
            steps=[
                BrowserStep("navigate", "https://example.com", "", "Go to site"),
                BrowserStep("click", "#btn", "", "Click button"),
                BrowserStep("type", "#input", "hello", "Type text"),
            ],
        )
        results = _execute_manual_fallback(plan)
        assert len(results) == 3
        assert all(r.success for r in results)
        assert "Manual step" in results[0].extracted_text

    def test_execute_empty_plan_returns_empty(self):
        """A plan that can't automate should return no results."""
        plan = BrowserPlan(can_automate=False, reason="nope", steps=[])
        assert execute_browser_plan(plan) == []

    def test_execute_plan_with_no_steps(self):
        plan = BrowserPlan(can_automate=True, reason="ok", steps=[])
        assert execute_browser_plan(plan) == []

    def test_playwright_execution_navigates_and_clicks(self):
        """Test full Playwright execution with mocked browser objects."""
        from teb.browser import _execute_single_step

        mock_page = MagicMock()
        mock_page.text_content.return_value = "Extracted text"
        mock_page.screenshot.return_value = None

        # Test each action type individually through _execute_single_step
        nav = _execute_single_step(mock_page, BrowserStep("navigate", "https://example.com", "", "Go"))
        assert nav.success
        mock_page.goto.assert_called_once_with("https://example.com", timeout=30000)

        click = _execute_single_step(mock_page, BrowserStep("click", "#submit", "", "Click"))
        assert click.success
        mock_page.click.assert_called_once_with("#submit", timeout=10000)

        typ = _execute_single_step(mock_page, BrowserStep("type", "#name", "John", "Type"))
        assert typ.success
        mock_page.fill.assert_called_once_with("#name", "John", timeout=10000)

        ext = _execute_single_step(mock_page, BrowserStep("extract", "#result", "", "Extract"))
        assert ext.success
        assert ext.extracted_text == "Extracted text"

        wait = _execute_single_step(mock_page, BrowserStep("wait", "", "1", "Wait"))
        assert wait.success
        mock_page.wait_for_timeout.assert_called_once_with(1000)

        scroll = _execute_single_step(mock_page, BrowserStep("scroll", "", "down", "Scroll"))
        assert scroll.success
        mock_page.evaluate.assert_called_with("window.scrollBy(0, 500)")

        hover = _execute_single_step(mock_page, BrowserStep("hover", "#menu", "", "Hover"))
        assert hover.success
        mock_page.hover.assert_called_once_with("#menu", timeout=10000)

        select = _execute_single_step(mock_page, BrowserStep("select", "#dropdown", "Option A", "Select"))
        assert select.success
        mock_page.select_option.assert_called_once_with("#dropdown", label="Option A", timeout=10000)

        upload = _execute_single_step(mock_page, BrowserStep("upload", "#file", "/tmp/test.txt", "Upload"))
        assert upload.success
        mock_page.set_input_files.assert_called_once()

        dialog = _execute_single_step(mock_page, BrowserStep("accept_dialog", "", "", "Accept"))
        assert dialog.success

    def test_playwright_step_failure_returns_error(self):
        """If a step throws, it should return success=False with error message."""
        from teb.browser import _execute_single_step

        mock_page = MagicMock()
        mock_page.goto.side_effect = Exception("Connection refused")

        result = _execute_single_step(mock_page, BrowserStep("navigate", "https://example.com", "", "Go"))
        assert not result.success
        assert "Connection refused" in result.error

    def test_playwright_blocks_private_urls(self):
        """Navigation to private IPs should be blocked by SSRF protection."""
        from teb.browser import _execute_single_step

        mock_page = MagicMock()
        result = _execute_single_step(mock_page, BrowserStep("navigate", "http://169.254.169.254/metadata", "", "Go"))
        assert not result.success
        assert "Blocked" in result.error or "private" in result.error.lower() or "disallowed" in result.error.lower()
        mock_page.goto.assert_not_called()

    def test_playwright_caps_wait_at_30s(self):
        """Wait steps should be capped at 30 seconds max."""
        from teb.browser import _execute_single_step

        mock_page = MagicMock()
        _execute_single_step(mock_page, BrowserStep("wait", "", "999", "Long wait"))
        mock_page.wait_for_timeout.assert_called_once_with(30000)  # 30s cap


# ═══════════════════════════════════════════════════════════════════════════════
# 2. PARALLEL AGENT ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════

class TestParallelAgentOrchestration:
    """Test that agent orchestration runs specialists in parallel."""

    def test_orchestration_produces_tasks_and_handoffs(self):
        """Basic orchestration should produce tasks from coordinator + specialists."""
        headers = _auth_headers()
        goal = _make_goal("earn money online freelancing")
        result = agents.orchestrate_goal(goal)

        assert result["total_tasks"] > 0
        assert len(result["handoffs"]) > 0
        assert "coordinator" in result["agents_involved"]
        # Verify all handoffs completed
        for h in result["handoffs"]:
            assert h["status"] == "completed"

    def test_orchestration_with_multiple_specialists(self):
        """Coordinator should delegate to multiple specialists concurrently."""
        headers = _auth_headers()
        goal = _make_goal("build and sell an online course")
        result = agents.orchestrate_goal(goal)

        # Should involve multiple specialists
        assert len(result["agents_involved"]) >= 2
        # Should produce tasks from specialists
        assert result["total_tasks"] >= 3

    def test_parallel_execution_thread_safety(self):
        """Multiple orchestrations on different goals should not corrupt shared state."""
        headers = _auth_headers()
        goal1 = _make_goal("earn money online")
        goal2 = _make_goal("learn Python programming")

        results = [None, None]
        errors = [None, None]

        def run_orchestration(idx, goal):
            try:
                results[idx] = agents.orchestrate_goal(goal)
            except Exception as e:
                errors[idx] = e

        t1 = threading.Thread(target=run_orchestration, args=(0, goal1))
        t2 = threading.Thread(target=run_orchestration, args=(1, goal2))
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        assert errors[0] is None, f"Goal 1 orchestration failed: {errors[0]}"
        assert errors[1] is None, f"Goal 2 orchestration failed: {errors[1]}"
        assert results[0]["total_tasks"] > 0
        assert results[1]["total_tasks"] > 0
        # Tasks should be assigned to the correct goals
        for t in results[0]["tasks"]:
            assert t["goal_id"] == goal1.id
        for t in results[1]["tasks"]:
            assert t["goal_id"] == goal2.id


# ═══════════════════════════════════════════════════════════════════════════════
# 3. PAYMENT IDEMPOTENCY
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaymentIdempotency:
    """Test that payment providers use proper idempotency keys."""

    def test_stripe_idempotency_key_sent(self):
        """Stripe create_transfer should include Idempotency-Key header."""
        provider = payments.StripeProvider()

        with patch("teb.payments.httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"id": "pi_123", "status": "succeeded"}
            mock_post.return_value = mock_resp

            result = provider.create_transfer(
                config={"api_key": "sk_test_123"},
                amount=25.00,
                currency="usd",
                recipient="cust_123",
                description="Test payment",
            )

            assert result["tx_id"] == "pi_123"
            # Verify Idempotency-Key header was sent
            call_kwargs = mock_post.call_args
            headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
            assert "Idempotency-Key" in headers
            assert headers["Idempotency-Key"].startswith("teb-")

    def test_stripe_idempotency_key_deterministic(self):
        """Same inputs should produce the same idempotency key."""
        provider = payments.StripeProvider()
        keys = []

        for _ in range(2):
            with patch("teb.payments.httpx.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.status_code = 200
                mock_resp.json.return_value = {"id": "pi_123", "status": "succeeded"}
                mock_post.return_value = mock_resp

                provider.create_transfer(
                    config={"api_key": "sk_test_123"},
                    amount=25.00, currency="usd",
                    recipient="cust_123", description="Test",
                )
                call_kwargs = mock_post.call_args
                headers = call_kwargs.kwargs.get("headers", call_kwargs[1].get("headers", {}))
                keys.append(headers["Idempotency-Key"])

        assert keys[0] == keys[1], "Idempotency key should be deterministic for same inputs"

    def test_mercury_idempotency_key_in_payload(self):
        """Mercury create_transfer should include idempotencyKey in payload."""
        provider = payments.MercuryProvider()

        with patch("teb.payments.httpx.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 201
            mock_resp.json.return_value = {"id": "tx_123", "status": "pending"}
            mock_post.return_value = mock_resp

            provider.create_transfer(
                config={"api_key": "test_key", "account_id": "acct_123"},
                amount=50.00, currency="usd",
                recipient='{"name": "Bob", "account_number": "123", "routing_number": "456"}',
                description="Wire transfer",
            )

            call_kwargs = mock_post.call_args
            payload = call_kwargs.kwargs.get("json", call_kwargs[1].get("json", {}))
            assert "idempotencyKey" in payload
            assert payload["idempotencyKey"].startswith("teb-")


# ═══════════════════════════════════════════════════════════════════════════════
# 4. SQLITE CONCURRENCY
# ═══════════════════════════════════════════════════════════════════════════════

class TestSQLiteConcurrency:
    """Test SQLite concurrency improvements."""

    def test_busy_timeout_is_set(self):
        """Connection should set busy_timeout pragma."""
        with storage._conn() as con:
            result = con.execute("PRAGMA busy_timeout").fetchone()
            assert result[0] == storage._BUSY_TIMEOUT_MS

    def test_wal_mode_enabled(self):
        """WAL journal mode should be active."""
        with storage._conn() as con:
            mode = con.execute("PRAGMA journal_mode").fetchone()
            assert mode[0].lower() == "wal"

    def test_with_retry_retries_on_busy(self):
        """_with_retry should retry on OperationalError with 'locked'."""
        call_count = 0

        @storage._with_retry
        def flaky_fn():
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise sqlite3.OperationalError("database is locked")
            return "success"

        result = flaky_fn()
        assert result == "success"
        assert call_count == 3

    def test_with_retry_raises_after_max_retries(self):
        """_with_retry should raise after max retries exhausted."""
        @storage._with_retry
        def always_locked():
            raise sqlite3.OperationalError("database is locked")

        with pytest.raises(sqlite3.OperationalError, match="locked"):
            always_locked()

    def test_with_retry_does_not_catch_other_errors(self):
        """_with_retry should not catch non-lock errors."""
        @storage._with_retry
        def syntax_error():
            raise sqlite3.OperationalError("no such table: foo")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            syntax_error()

    def test_concurrent_task_creation(self):
        """Multiple threads creating tasks should not fail."""
        _auth_headers()  # ensure user_id=1 exists
        goal = _make_goal("concurrent test")
        errors = []

        def create_task_thread(i):
            try:
                storage.create_task(Task(
                    goal_id=goal.id, title=f"Task {i}", description=f"Thread {i}",
                ))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_task_thread, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        assert len(errors) == 0, f"Concurrent task creation failed: {errors}"
        tasks = storage.list_tasks(goal_id=goal.id)
        assert len(tasks) == 10


# ═══════════════════════════════════════════════════════════════════════════════
# 5. RATE LIMITING
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    """Test that rate limiting actually blocks excessive requests."""

    def test_rate_limit_triggers_429(self):
        """Exceeding rate limit on auth endpoints should return 429."""
        reset_rate_limits()
        # Burn through the 20-request limit
        for i in range(20):
            client.post("/api/auth/login", json={
                "email": f"ratelimit{i}@test.com", "password": "wrong"
            })

        # The 21st request should be rate-limited
        r = client.post("/api/auth/login", json={
            "email": "ratelimit_final@test.com", "password": "wrong"
        })
        assert r.status_code == 429

    def test_rate_limit_per_endpoint(self):
        """Non-auth endpoints should not be rate-limited."""
        reset_rate_limits()
        # Auth endpoint exhausted
        for i in range(21):
            client.post("/api/auth/login", json={"email": f"x{i}@t.c", "password": "w"})

        # Health endpoint should still work
        r = client.get("/health")
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# 6. FULL API WORKFLOW — End-to-end without mocking AI
# ═══════════════════════════════════════════════════════════════════════════════

class TestEndToEndWorkflow:
    """Test the full user workflow using template mode (no AI keys)."""

    def test_full_lifecycle(self):
        """Register → create goal → decompose → work tasks → track outcomes."""
        # Register
        r = client.post("/api/auth/register", json={
            "email": "e2e@teb.test", "password": "strongpass123"
        })
        assert r.status_code == 201
        h = {"Authorization": f"Bearer {r.json()['token']}"}

        # Create goal
        r = client.post("/api/goals", json={
            "title": "earn money freelancing online",
            "description": "beginner, 2 hours/day"
        }, headers=h)
        assert r.status_code == 201
        goal_id = r.json()["id"]

        # Decompose
        r = client.post(f"/api/goals/{goal_id}/decompose", headers=h)
        assert r.status_code == 200
        tasks = r.json()["tasks"]
        assert len(tasks) >= 3, "Template decomposition should produce at least 3 tasks"

        # Get focus task
        r = client.get(f"/api/goals/{goal_id}/focus", headers=h)
        assert r.status_code == 200
        focus = r.json()["focus_task"]
        assert focus is not None
        # Focus should be one of the goal's tasks
        task_ids = {t["id"] for t in tasks}
        assert focus["id"] in task_ids

        # Complete a task
        task_id = tasks[0]["id"]
        r = client.patch(f"/api/tasks/{task_id}", json={"status": "done"}, headers=h)
        assert r.status_code == 200

        # Check progress
        r = client.get(f"/api/goals/{goal_id}/progress", headers=h)
        assert r.status_code == 200
        progress = r.json()
        assert progress["done"] >= 1

        # Add outcome metric
        r = client.post(f"/api/goals/{goal_id}/outcomes", json={
            "label": "Revenue", "target_value": 500, "unit": "$"
        }, headers=h)
        assert r.status_code == 201
        metric_id = r.json()["id"]

        # Update outcome
        r = client.patch(f"/api/outcomes/{metric_id}", json={"current_value": 150}, headers=h)
        assert r.status_code == 200
        assert r.json()["current_value"] == 150
        assert r.json()["achievement_pct"] == 30

        # Daily check-in
        r = client.post(f"/api/goals/{goal_id}/checkin", json={
            "done_summary": "Completed first task", "blockers": ""
        }, headers=h)
        assert r.status_code == 201
        assert "coaching" in r.json() or "feedback" in r.json()

        # Multi-agent orchestration
        goal2_r = client.post("/api/goals", json={
            "title": "build a SaaS product", "description": "developer"
        }, headers=h)
        goal2_id = goal2_r.json()["id"]
        r = client.post(f"/api/goals/{goal2_id}/orchestrate", headers=h)
        assert r.status_code == 200
        orch = r.json()
        assert orch["total_tasks"] > 0
        assert len(orch["agents_involved"]) >= 2

    def test_budget_and_spending_workflow(self):
        """Create budget → request spending → approve → verify."""
        r = client.post("/api/auth/register", json={
            "email": "budget@teb.test", "password": "strongpass123"
        })
        h = {"Authorization": f"Bearer {r.json()['token']}"}

        # Create goal and task
        r = client.post("/api/goals", json={"title": "launch startup"}, headers=h)
        goal_id = r.json()["id"]
        r = client.post("/api/tasks", json={
            "goal_id": goal_id, "title": "Register domain"
        }, headers=h)
        task_id = r.json()["id"]

        # Create budget
        r = client.post("/api/budgets", json={
            "goal_id": goal_id, "daily_limit": 50, "total_limit": 200,
            "category": "domain", "require_approval": True
        }, headers=h)
        assert r.status_code == 201

        # Request spending
        r = client.post("/api/spending/request", json={
            "task_id": task_id, "amount": 12.99,
            "description": "Register example.com", "service": "namecheap"
        }, headers=h)
        assert r.status_code == 201
        req_id = r.json()["request"]["id"]

        # Approve spending
        r = client.post(f"/api/spending/{req_id}/action", json={
            "action": "approve"
        }, headers=h)
        assert r.status_code == 200
        assert r.json()["status"] == "approved"

        # Verify spending list
        r = client.get(f"/api/goals/{goal_id}/spending", headers=h)
        assert r.status_code == 200
        assert len(r.json()) == 1
