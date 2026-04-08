"""Tests for browser automation (browser.py), integration registry (integrations.py),
and enhanced agent collaboration (agent messages)."""

from __future__ import annotations

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from teb import agents, browser, integrations, storage
from teb.models import (
    AgentMessage, BrowserAction, Goal, Integration, Task,
)
from teb.browser import (
    BrowserPlan, BrowserStep, BrowserStepResult,
    _parse_browser_plan, generate_browser_plan, execute_browser_plan,
    is_playwright_available, _VALID_ACTION_TYPES,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Create a fresh database for each test."""
    db_path = str(tmp_path / "test.db")
    storage.set_db_path(db_path)
    storage.init_db()
    yield
    storage.set_db_path(None)


@pytest.fixture
def goal() -> Goal:
    g = Goal(title="earn money online", description="I want to earn $500 freelancing")
    return storage.create_goal(g)


@pytest.fixture
def task(goal) -> Task:
    t = Task(goal_id=goal.id, title="Create Upwork profile", description="Sign up and create a profile on Upwork")
    return storage.create_task(t)


@pytest.fixture
def search_task(goal) -> Task:
    t = Task(goal_id=goal.id, title="Research freelance platforms", description="Search for the best freelance platforms")
    return storage.create_task(t)


@pytest.fixture
def form_task(goal) -> Task:
    t = Task(goal_id=goal.id, title="Submit application form", description="Fill form and apply to a job")
    return storage.create_task(t)


# ══════════════════════════════════════════════════════════════════════════════
# BROWSER AUTOMATION TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestBrowserStep:
    def test_to_dict(self):
        step = BrowserStep("navigate", "https://example.com", "", "Go to site")
        d = step.to_dict()
        assert d["action_type"] == "navigate"
        assert d["target"] == "https://example.com"
        assert d["description"] == "Go to site"

    def test_to_dict_with_value(self):
        step = BrowserStep("type", "input#email", "test@test.com", "Enter email")
        d = step.to_dict()
        assert d["value"] == "test@test.com"


class TestBrowserPlan:
    def test_to_dict_automatable(self):
        plan = BrowserPlan(
            can_automate=True, reason="ok", steps=[
                BrowserStep("navigate", "https://x.com", "", "Go"),
            ], requires_login=False, target_url="https://x.com",
        )
        d = plan.to_dict()
        assert d["can_automate"] is True
        assert len(d["steps"]) == 1
        assert d["requires_login"] is False
        assert d["target_url"] == "https://x.com"

    def test_to_dict_not_automatable(self):
        plan = BrowserPlan(can_automate=False, reason="no", steps=[])
        d = plan.to_dict()
        assert d["can_automate"] is False
        assert d["steps"] == []


class TestParseBrowserPlan:
    def test_valid_plan(self):
        data = {
            "can_automate": True,
            "reason": "Can automate",
            "requires_login": False,
            "target_url": "https://example.com",
            "steps": [
                {"action_type": "navigate", "target": "https://example.com", "value": "", "description": "Go"},
                {"action_type": "click", "target": "#btn", "value": "", "description": "Click"},
            ],
        }
        plan = _parse_browser_plan(data)
        assert plan.can_automate is True
        assert len(plan.steps) == 2
        assert plan.steps[0].action_type == "navigate"
        assert plan.steps[1].target == "#btn"

    def test_can_automate_false(self):
        data = {"can_automate": False, "reason": "Too complex"}
        plan = _parse_browser_plan(data)
        assert plan.can_automate is False
        assert len(plan.steps) == 0

    def test_invalid_action_type_filtered(self):
        data = {
            "can_automate": True,
            "reason": "ok",
            "steps": [
                {"action_type": "hack", "target": "", "value": ""},
                {"action_type": "navigate", "target": "https://x.com", "value": ""},
            ],
        }
        plan = _parse_browser_plan(data)
        assert plan.can_automate is True
        assert len(plan.steps) == 1
        assert plan.steps[0].action_type == "navigate"

    def test_all_invalid_steps_returns_cant_automate(self):
        data = {
            "can_automate": True,
            "reason": "ok",
            "steps": [
                {"action_type": "invalid", "target": ""},
            ],
        }
        plan = _parse_browser_plan(data)
        assert plan.can_automate is False

    def test_non_dict_steps_filtered(self):
        data = {
            "can_automate": True,
            "reason": "ok",
            "steps": ["not a dict", 42],
        }
        plan = _parse_browser_plan(data)
        assert plan.can_automate is False

    def test_missing_steps_key(self):
        data = {"can_automate": True, "reason": "ok"}
        plan = _parse_browser_plan(data)
        assert plan.can_automate is False

    def test_requires_login_parsed(self):
        data = {
            "can_automate": True,
            "reason": "ok",
            "requires_login": True,
            "target_url": "https://app.example.com",
            "steps": [{"action_type": "navigate", "target": "https://app.example.com", "value": ""}],
        }
        plan = _parse_browser_plan(data)
        assert plan.requires_login is True
        assert plan.target_url == "https://app.example.com"

    def test_all_valid_action_types(self):
        steps = [{"action_type": t, "target": "t", "value": "v"} for t in _VALID_ACTION_TYPES]
        data = {"can_automate": True, "reason": "ok", "steps": steps}
        plan = _parse_browser_plan(data)
        assert len(plan.steps) == len(_VALID_ACTION_TYPES)


class TestGenerateBrowserPlanTemplate:
    """Template-based browser plan generation (no AI key)."""

    def test_signup_task(self, task):
        """Tasks about signing up / creating accounts should generate a plan."""
        plan = generate_browser_plan(task)
        assert plan.can_automate is True
        assert len(plan.steps) >= 3
        action_types = {s.action_type for s in plan.steps}
        assert "navigate" in action_types
        assert "screenshot" in action_types

    def test_search_task(self, search_task):
        plan = generate_browser_plan(search_task)
        assert plan.can_automate is True
        assert plan.steps[0].action_type == "navigate"
        action_types = {s.action_type for s in plan.steps}
        assert "extract" in action_types

    def test_form_task(self, form_task):
        plan = generate_browser_plan(form_task)
        assert plan.can_automate is True
        action_types = {s.action_type for s in plan.steps}
        assert "navigate" in action_types

    def test_unrecognized_task_cant_automate(self, goal):
        t = Task(goal_id=goal.id, title="Meditate", description="Clear your mind")
        t = storage.create_task(t)
        plan = generate_browser_plan(t)
        assert plan.can_automate is False
        assert "AI mode" in plan.reason

    def test_with_integrations(self, task):
        integ = Integration(service_name="upwork", category="freelance", base_url="https://upwork.com")
        plan = generate_browser_plan(task, [integ])
        # Should still work with integrations passed
        assert plan.can_automate is True


class TestExecuteBrowserPlan:
    def test_empty_plan_returns_empty(self):
        plan = BrowserPlan(can_automate=False, reason="no", steps=[])
        results = execute_browser_plan(plan)
        assert results == []

    def test_cant_automate_returns_empty(self):
        plan = BrowserPlan(can_automate=False, reason="no", steps=[
            BrowserStep("navigate", "https://x.com", "", "go"),
        ])
        results = execute_browser_plan(plan)
        assert results == []

    def test_manual_fallback_when_no_playwright(self):
        """Without Playwright, should return manual instructions."""
        plan = BrowserPlan(can_automate=True, reason="ok", steps=[
            BrowserStep("navigate", "https://x.com", "", "Open site"),
            BrowserStep("click", "#btn", "", "Click button"),
        ])
        with patch("teb.browser.is_playwright_available", return_value=False):
            results = execute_browser_plan(plan)
        assert len(results) == 2
        assert all(r.success for r in results)
        assert "Manual step" in results[0].extracted_text


class TestBrowserStepResult:
    def test_success_result(self):
        step = BrowserStep("navigate", "https://x.com", "", "go")
        result = BrowserStepResult(step=step, success=True)
        assert result.success is True
        assert result.error == ""

    def test_error_result(self):
        step = BrowserStep("click", "#bad", "", "click")
        result = BrowserStepResult(step=step, success=False, error="Element not found")
        assert result.success is False
        assert "not found" in result.error


# ══════════════════════════════════════════════════════════════════════════════
# BROWSER ACTIONS STORAGE TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestBrowserActionStorage:
    def test_create_and_list(self, task):
        a = BrowserAction(task_id=task.id, action_type="navigate", target="https://x.com", status="success")
        saved = storage.create_browser_action(a)
        assert saved.id is not None
        assert saved.created_at is not None

        actions = storage.list_browser_actions(task.id)
        assert len(actions) == 1
        assert actions[0].action_type == "navigate"
        assert actions[0].target == "https://x.com"

    def test_update_browser_action(self, task):
        a = BrowserAction(task_id=task.id, action_type="extract", target="#content", status="pending")
        saved = storage.create_browser_action(a)
        saved.status = "success"
        saved.value = "Extracted text here"
        updated = storage.update_browser_action(saved)
        assert updated.status == "success"
        assert updated.value == "Extracted text here"

    def test_multiple_actions_ordered(self, task):
        for i, atype in enumerate(["navigate", "click", "type", "screenshot"]):
            storage.create_browser_action(
                BrowserAction(task_id=task.id, action_type=atype, target=f"step-{i}")
            )
        actions = storage.list_browser_actions(task.id)
        assert len(actions) == 4
        assert actions[0].action_type == "navigate"
        assert actions[3].action_type == "screenshot"

    def test_to_dict(self, task):
        a = BrowserAction(
            task_id=task.id, action_type="screenshot",
            target="", value="", status="success",
            screenshot_path="/tmp/shot.png",
        )
        saved = storage.create_browser_action(a)
        d = saved.to_dict()
        assert d["action_type"] == "screenshot"
        assert d["screenshot_path"] == "/tmp/shot.png"
        assert d["id"] is not None


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION REGISTRY TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestIntegrationCatalog:
    def test_get_catalog(self):
        catalog = integrations.get_catalog()
        assert len(catalog) >= 10
        names = {c["service_name"] for c in catalog}
        assert "stripe" in names
        assert "namecheap" in names
        assert "vercel" in names
        assert "sendgrid" in names
        assert "github" in names

    def test_catalog_has_required_fields(self):
        for item in integrations.get_catalog():
            assert "service_name" in item
            assert "category" in item
            assert "base_url" in item
            assert "capabilities" in item
            assert isinstance(item["capabilities"], list)


class TestFindMatchingIntegrations:
    def test_match_payment(self):
        matches = integrations.find_matching_integrations("set up payment processing with stripe")
        assert len(matches) >= 1
        assert matches[0]["service_name"] == "stripe"

    def test_match_domain(self):
        matches = integrations.find_matching_integrations("register a domain name")
        assert len(matches) >= 1
        names = {m["service_name"] for m in matches}
        assert "namecheap" in names

    def test_match_hosting(self):
        matches = integrations.find_matching_integrations("deploy website hosting")
        assert len(matches) >= 1
        names = {m["service_name"] for m in matches}
        assert "vercel" in names or "cloudflare" in names

    def test_match_email(self):
        matches = integrations.find_matching_integrations("send marketing emails")
        assert len(matches) >= 1
        names = {m["service_name"] for m in matches}
        assert "sendgrid" in names

    def test_no_match_returns_empty(self):
        matches = integrations.find_matching_integrations("meditate and relax")
        # May or may not return results, but shouldn't crash
        assert isinstance(matches, list)

    def test_max_5_results(self):
        matches = integrations.find_matching_integrations("build deploy manage send track create")
        assert len(matches) <= 5


class TestGetEndpointsForService:
    def test_stripe_endpoints(self):
        endpoints = integrations.get_endpoints_for_service("stripe")
        assert len(endpoints) >= 5
        methods = {e["method"] for e in endpoints}
        assert "POST" in methods

    def test_unknown_service(self):
        endpoints = integrations.get_endpoints_for_service("unknown_service_xyz")
        assert endpoints == []

    def test_endpoint_structure(self):
        endpoints = integrations.get_endpoints_for_service("vercel")
        for ep in endpoints:
            assert "method" in ep
            assert "path" in ep
            assert "description" in ep


class TestSeedIntegrations:
    def test_seed_creates_entries(self):
        count = integrations.seed_integrations()
        assert count >= 10
        all_integrations = storage.list_integrations()
        assert len(all_integrations) >= 10

    def test_seed_idempotent(self):
        count1 = integrations.seed_integrations()
        count2 = integrations.seed_integrations()
        assert count1 >= 10
        assert count2 == 0  # second run should create nothing

    def test_seed_creates_correct_data(self):
        integrations.seed_integrations()
        stripe = storage.get_integration("stripe")
        assert stripe is not None
        assert stripe.category == "payment"
        assert stripe.base_url == "https://api.stripe.com"
        d = stripe.to_dict()
        assert isinstance(d["capabilities"], list)
        assert len(d["capabilities"]) >= 1

    def test_list_integrations_by_category(self):
        integrations.seed_integrations()
        payments = storage.list_integrations(category="payment")
        assert len(payments) >= 1
        assert all(p.category == "payment" for p in payments)

    def test_delete_integration(self):
        integrations.seed_integrations()
        stripe = storage.get_integration("stripe")
        assert stripe is not None
        storage.delete_integration(stripe.id)
        assert storage.get_integration("stripe") is None


class TestIntegrationModel:
    def test_to_dict(self):
        i = Integration(
            service_name="test",
            category="test",
            base_url="https://test.com",
            capabilities=json.dumps(["a", "b"]),
            common_endpoints=json.dumps([{"method": "GET", "path": "/test"}]),
        )
        d = i.to_dict()
        assert d["capabilities"] == ["a", "b"]
        assert len(d["common_endpoints"]) == 1

    def test_to_dict_empty(self):
        i = Integration(service_name="empty", category="test")
        d = i.to_dict()
        assert d["capabilities"] == []
        assert d["common_endpoints"] == []


# ══════════════════════════════════════════════════════════════════════════════
# AGENT MESSAGING TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestAgentMessageStorage:
    def test_create_and_list(self, goal):
        msg = AgentMessage(
            goal_id=goal.id, from_agent="coordinator", to_agent="web_dev",
            message_type="context", content="Marketing needs a landing page",
        )
        saved = storage.create_agent_message(msg)
        assert saved.id is not None
        assert saved.created_at is not None

        messages = storage.list_agent_messages(goal.id)
        assert len(messages) == 1
        assert messages[0].content == "Marketing needs a landing page"

    def test_filter_by_agent(self, goal):
        storage.create_agent_message(AgentMessage(
            goal_id=goal.id, from_agent="coordinator", to_agent="web_dev",
            content="msg1",
        ))
        storage.create_agent_message(AgentMessage(
            goal_id=goal.id, from_agent="coordinator", to_agent="marketing",
            content="msg2",
        ))
        storage.create_agent_message(AgentMessage(
            goal_id=goal.id, from_agent="marketing", to_agent="outreach",
            content="msg3",
        ))

        # Filter to web_dev: should see msg1 (to web_dev)
        web_msgs = storage.list_agent_messages(goal.id, agent_type="web_dev")
        assert len(web_msgs) == 1
        assert web_msgs[0].content == "msg1"

        # Filter to marketing: should see msg2 (to marketing) and msg3 (from marketing)
        mkt_msgs = storage.list_agent_messages(goal.id, agent_type="marketing")
        assert len(mkt_msgs) == 2

    def test_to_dict(self, goal):
        msg = AgentMessage(
            goal_id=goal.id, from_agent="a", to_agent="b",
            message_type="request", content="Need info",
        )
        saved = storage.create_agent_message(msg)
        d = saved.to_dict()
        assert d["from_agent"] == "a"
        assert d["to_agent"] == "b"
        assert d["message_type"] == "request"
        assert d["content"] == "Need info"


class TestAgentOutputMessages:
    """Test that AgentOutput now includes messages field."""

    def test_agent_output_default_empty_messages(self):
        output = agents.AgentOutput(tasks=[], delegations=[], summary="test")
        assert output.messages == []

    def test_agent_output_with_messages(self):
        output = agents.AgentOutput(
            tasks=[], delegations=[], summary="test",
            messages=[{"to_agent": "web_dev", "content": "Build a page"}],
        )
        assert len(output.messages) == 1


class TestOrchestrateWithMessages:
    """Test that orchestrate_goal now persists and returns messages."""

    def test_orchestrate_returns_messages_key(self, goal):
        result = agents.orchestrate_goal(goal)
        assert "messages" in result
        assert isinstance(result["messages"], list)

    def test_orchestrate_still_creates_tasks(self, goal):
        result = agents.orchestrate_goal(goal)
        assert result["total_tasks"] >= 1
        assert len(result["tasks"]) >= 1

    def test_orchestrate_still_creates_handoffs(self, goal):
        result = agents.orchestrate_goal(goal)
        assert len(result["handoffs"]) >= 1

    def test_orchestrate_agents_involved(self, goal):
        result = agents.orchestrate_goal(goal)
        assert "coordinator" in result["agents_involved"]

    def test_messages_stored_in_db(self, goal):
        # Template mode doesn't produce messages, but the infrastructure is there
        agents.orchestrate_goal(goal)
        messages = storage.list_agent_messages(goal.id)
        # Messages may be empty in template mode, but the query should work
        assert isinstance(messages, list)


# ══════════════════════════════════════════════════════════════════════════════
# API ENDPOINT TESTS
# ══════════════════════════════════════════════════════════════════════════════

class TestBrowserAPI:
    """Test browser automation API endpoints."""

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from teb.main import app
        with TestClient(app) as c:
            yield c

    def test_browser_execute_task(self, client):
        # Create goal + task
        goal = client.post("/api/goals", json={"title": "test", "description": "test"}).json()
        task = client.post("/api/tasks", json={
            "goal_id": goal["id"],
            "title": "Sign up for Upwork",
            "description": "Register and create account on Upwork",
        }).json()

        resp = client.post(f"/api/tasks/{task['id']}/browser")
        assert resp.status_code == 200
        data = resp.json()
        assert "plan" in data
        assert "playwright_available" in data

    def test_browser_execute_not_found(self, client):
        resp = client.post("/api/tasks/99999/browser")
        assert resp.status_code == 404

    def test_browser_execute_done_task(self, client):
        goal = client.post("/api/goals", json={"title": "test", "description": "test"}).json()
        task = client.post("/api/tasks", json={
            "goal_id": goal["id"], "title": "Done task", "description": "already done",
        }).json()
        client.patch(f"/api/tasks/{task['id']}", json={"status": "done"})
        resp = client.post(f"/api/tasks/{task['id']}/browser")
        assert resp.status_code == 409

    def test_browser_actions_endpoint(self, client):
        goal = client.post("/api/goals", json={"title": "test", "description": "test"}).json()
        task = client.post("/api/tasks", json={
            "goal_id": goal["id"], "title": "Sign up for site", "description": "Create account",
        }).json()
        # Execute browser to generate actions
        client.post(f"/api/tasks/{task['id']}/browser")
        resp = client.get(f"/api/tasks/{task['id']}/browser_actions")
        assert resp.status_code == 200
        data = resp.json()
        assert "actions" in data

    def test_browser_actions_not_found(self, client):
        resp = client.get("/api/tasks/99999/browser_actions")
        assert resp.status_code == 404


class TestIntegrationsAPI:
    """Test integration registry API endpoints."""

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from teb.main import app
        with TestClient(app) as c:
            yield c

    def test_list_integrations(self, client):
        resp = client.get("/api/integrations")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 10

    def test_list_integrations_by_category(self, client):
        resp = client.get("/api/integrations?category=payment")
        assert resp.status_code == 200
        data = resp.json()
        assert all(d["category"] == "payment" for d in data)

    def test_catalog(self, client):
        resp = client.get("/api/integrations/catalog")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 10

    def test_match(self, client):
        resp = client.get("/api/integrations/match?q=payment+stripe")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

    def test_service_endpoints(self, client):
        resp = client.get("/api/integrations/stripe/endpoints")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service_name"] == "stripe"
        assert len(data["endpoints"]) >= 1

    def test_service_endpoints_not_found(self, client):
        resp = client.get("/api/integrations/unknown_xyz/endpoints")
        assert resp.status_code == 404


class TestMessagesAPI:
    """Test agent messages API endpoint."""

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from teb.main import app
        with TestClient(app) as c:
            yield c

    def test_list_messages_empty(self, client):
        goal = client.post("/api/goals", json={"title": "test", "description": "test"}).json()
        resp = client.get(f"/api/goals/{goal['id']}/messages")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_messages_after_orchestrate(self, client):
        goal = client.post("/api/goals", json={
            "title": "earn money online",
            "description": "freelancing",
        }).json()
        client.post(f"/api/goals/{goal['id']}/orchestrate")
        resp = client.get(f"/api/goals/{goal['id']}/messages")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_list_messages_not_found(self, client):
        resp = client.get("/api/goals/99999/messages")
        assert resp.status_code == 404

    def test_list_messages_filter_by_agent(self, client):
        goal = client.post("/api/goals", json={"title": "test", "description": "test"}).json()
        resp = client.get(f"/api/goals/{goal['id']}/messages?agent=web_dev")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)
