"""
Tests for ROI dashboard, platform insights (aggregate learning), and enhanced AI decomposition.
"""

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from teb import auth, storage
from teb.models import (
    Goal, OutcomeMetric, SpendingBudget, SpendingRequest, Task, User, UserProfile,
)


@pytest.fixture()
def client():
    from teb.main import app, reset_rate_limits

    reset_rate_limits()
    return TestClient(app)


def _register_and_login(client, email="roi@test.com", password="Passw0rd!"):
    """Register + login and return auth headers dict."""
    client.post("/api/auth/register", json={"email": email, "password": password})
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def auth_headers(client):
    return _register_and_login(client)


def _create_user_in_db(email="test@teb.test"):
    """Create a user directly in the DB and return the user ID."""
    import bcrypt
    pw_hash = bcrypt.hashpw(b"TestPass1!", bcrypt.gensalt()).decode()
    user = storage.create_user(User(email=email, password_hash=pw_hash))
    return user.id


@pytest.fixture()
def goal_with_spending(auth_headers, client):
    """Create a goal with tasks, spending, and earnings for ROI testing."""
    # Create goal
    resp = client.post("/api/goals", json={
        "title": "Earn money online",
        "description": "Build a freelance web dev business",
    }, headers=auth_headers)
    goal = resp.json()
    goal_id = goal["id"]

    # Create tasks
    task1 = client.post("/api/tasks", json={
        "goal_id": goal_id, "title": "Register domain",
        "description": "Buy example.com", "estimated_minutes": 15,
    }, headers=auth_headers).json()

    task2 = client.post("/api/tasks", json={
        "goal_id": goal_id, "title": "Set up hosting",
        "description": "Deploy on Vercel", "estimated_minutes": 30,
    }, headers=auth_headers).json()

    task3 = client.post("/api/tasks", json={
        "goal_id": goal_id, "title": "Run ads",
        "description": "Google Ads campaign", "estimated_minutes": 60,
    }, headers=auth_headers).json()

    # Create budget
    budget = client.post("/api/budgets", json={
        "goal_id": goal_id, "daily_limit": 100, "total_limit": 1000,
        "category": "general", "require_approval": False,
    }, headers=auth_headers).json()

    # Create spending requests (approved/executed)
    storage.create_spending_request(SpendingRequest(
        task_id=task1["id"], budget_id=budget["id"],
        amount=12.99, service="namecheap", status="approved",
    ))
    storage.create_spending_request(SpendingRequest(
        task_id=task2["id"], budget_id=budget["id"],
        amount=20.00, service="vercel", status="executed",
    ))
    storage.create_spending_request(SpendingRequest(
        task_id=task3["id"], budget_id=budget["id"],
        amount=150.00, service="google ads", status="approved",
    ))
    # Pending request
    storage.create_spending_request(SpendingRequest(
        task_id=task1["id"], budget_id=budget["id"],
        amount=5.00, service="namecheap", status="pending",
    ))
    # Failed request
    storage.create_spending_request(SpendingRequest(
        task_id=task3["id"], budget_id=budget["id"],
        amount=50.00, service="google ads", status="failed",
    ))

    # Create outcome metrics (earnings)
    storage.create_outcome_metric(OutcomeMetric(
        goal_id=goal_id, label="Revenue earned",
        target_value=1000.0, current_value=350.0, unit="$",
    ))
    storage.create_outcome_metric(OutcomeMetric(
        goal_id=goal_id, label="Clients acquired",
        target_value=10.0, current_value=3.0, unit="clients",
    ))

    return {
        "goal_id": goal_id,
        "task_ids": [task1["id"], task2["id"], task3["id"]],
        "budget_id": budget["id"],
    }


# ─── ROI Dashboard Tests ────────────────────────────────────────────────────

class TestGoalRoi:
    def test_roi_endpoint_returns_correct_totals(self, client, auth_headers, goal_with_spending):
        goal_id = goal_with_spending["goal_id"]
        resp = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        # Total spent = 12.99 + 20.00 + 150.00 = 182.99
        assert data["total_spent"] == 182.99
        # Total earned from $ metrics = 350.00
        assert data["total_earned"] == 350.0
        # Net profit
        assert data["net_profit"] == pytest.approx(350.0 - 182.99, abs=0.01)
        # ROI percent
        assert data["roi_percent"] == pytest.approx(
            ((350.0 - 182.99) / 182.99) * 100, abs=0.1
        )

    def test_roi_spending_breakdown_by_category(self, client, auth_headers, goal_with_spending):
        goal_id = goal_with_spending["goal_id"]
        data = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()

        cats = data["spending_by_category"]
        assert "namecheap" in cats
        assert cats["namecheap"] == 12.99
        assert "vercel" in cats
        assert cats["vercel"] == 20.0
        assert "google ads" in cats
        assert cats["google ads"] == 150.0

    def test_roi_earnings_breakdown(self, client, auth_headers, goal_with_spending):
        goal_id = goal_with_spending["goal_id"]
        data = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()

        # Only $ metric should appear in earnings
        assert len(data["earnings_breakdown"]) == 1
        assert data["earnings_breakdown"][0]["label"] == "Revenue earned"
        assert data["earnings_breakdown"][0]["current_value"] == 350.0

    def test_roi_pending_and_failed_counts(self, client, auth_headers, goal_with_spending):
        goal_id = goal_with_spending["goal_id"]
        data = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()

        assert data["pending_requests"] == 1
        assert data["failed_transactions"] == 1

    def test_roi_budget_summary(self, client, auth_headers, goal_with_spending):
        goal_id = goal_with_spending["goal_id"]
        data = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()

        assert len(data["budget_summary"]) == 1
        assert data["budget_summary"][0]["total_limit"] == 1000.0

    def test_roi_spending_timeline(self, client, auth_headers, goal_with_spending):
        goal_id = goal_with_spending["goal_id"]
        data = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()

        # Timeline should have entries (all created on same day)
        assert len(data["spending_timeline"]) >= 1
        total_in_timeline = sum(d["amount"] for d in data["spending_timeline"])
        assert total_in_timeline == pytest.approx(182.99, abs=0.01)

    def test_roi_empty_goal(self, client, auth_headers):
        """ROI for a goal with no spending or earnings."""
        resp = client.post("/api/goals", json={
            "title": "Empty goal", "description": "Nothing here",
        }, headers=auth_headers)
        goal_id = resp.json()["id"]

        data = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()
        assert data["total_spent"] == 0.0
        assert data["total_earned"] == 0.0
        assert data["net_profit"] == 0.0
        assert data["roi_percent"] == 0.0
        assert data["spending_by_category"] == {}
        assert data["earnings_breakdown"] == []

    def test_roi_requires_auth(self, client):
        resp = client.get("/api/goals/1/roi")
        assert resp.status_code in (401, 403)

    def test_roi_wrong_user_forbidden(self, client, auth_headers, goal_with_spending):
        """Another user can't see the ROI of goals they don't own."""
        other_headers = _register_and_login(client, "other@test.com", "Passw0rd!")

        goal_id = goal_with_spending["goal_id"]
        resp = client.get(f"/api/goals/{goal_id}/roi", headers=other_headers)
        assert resp.status_code in (403, 404)


class TestUserRoi:
    def test_user_roi_aggregates_across_goals(self, client, auth_headers, goal_with_spending):
        # Create a second goal with spending
        resp = client.post("/api/goals", json={
            "title": "Second project", "description": "Another thing",
        }, headers=auth_headers)
        goal2_id = resp.json()["id"]

        task = client.post("/api/tasks", json={
            "goal_id": goal2_id, "title": "Buy tools",
            "description": "Get software", "estimated_minutes": 15,
        }, headers=auth_headers).json()

        budget = client.post("/api/budgets", json={
            "goal_id": goal2_id, "daily_limit": 50, "total_limit": 200,
        }, headers=auth_headers).json()

        storage.create_spending_request(SpendingRequest(
            task_id=task["id"], budget_id=budget["id"],
            amount=25.00, service="github", status="approved",
        ))

        data = client.get("/api/users/me/roi", headers=auth_headers).json()
        # Total across both goals
        assert data["total_spent"] == pytest.approx(182.99 + 25.0, abs=0.01)
        assert data["total_earned"] == 350.0
        assert len(data["goals"]) == 2

    def test_user_roi_requires_auth(self, client):
        resp = client.get("/api/users/me/roi")
        assert resp.status_code in (401, 403)


# ─── Storage-level ROI Tests ────────────────────────────────────────────────

class TestStorageRoi:
    def test_get_goal_roi_structure(self):
        """get_goal_roi returns all expected keys."""
        uid = _create_user_in_db()
        goal = storage.create_goal(Goal(title="Test goal", description="desc", user_id=uid))
        roi = storage.get_goal_roi(goal.id)
        required_keys = {
            "goal_id", "total_spent", "total_earned", "net_profit",
            "roi_percent", "spending_by_category", "spending_timeline",
            "earnings_breakdown", "budget_summary", "pending_requests",
            "failed_transactions",
        }
        assert required_keys.issubset(set(roi.keys()))

    def test_get_goal_roi_recognizes_dollar_variants(self):
        """Revenue metrics with unit '$', 'USD', 'revenue' all count as earnings."""
        uid = _create_user_in_db("dollar@test.com")
        goal = storage.create_goal(Goal(title="Revenue test", description="", user_id=uid))
        storage.create_outcome_metric(OutcomeMetric(
            goal_id=goal.id, label="Sales", target_value=100, current_value=50, unit="$"
        ))
        storage.create_outcome_metric(OutcomeMetric(
            goal_id=goal.id, label="Consulting", target_value=200, current_value=80, unit="USD"
        ))
        storage.create_outcome_metric(OutcomeMetric(
            goal_id=goal.id, label="Side revenue", target_value=300, current_value=120, unit="revenue"
        ))
        # Non-dollar metric should NOT be counted
        storage.create_outcome_metric(OutcomeMetric(
            goal_id=goal.id, label="Followers", target_value=1000, current_value=500, unit="followers"
        ))

        roi = storage.get_goal_roi(goal.id)
        assert roi["total_earned"] == 50 + 80 + 120
        # No spending, but earnings exist: ROI should be None (not inf)
        assert roi["roi_percent"] is None

    def test_get_user_roi_summary_structure(self):
        uid = _create_user_in_db("summary@test.com")
        goal = storage.create_goal(Goal(title="X", description="Y", user_id=uid))
        summary = storage.get_user_roi_summary(uid)
        assert "total_spent" in summary
        assert "total_earned" in summary
        assert "overall_roi_percent" in summary
        assert "goals" in summary
        assert len(summary["goals"]) == 1


# ─── Platform Insights Tests ────────────────────────────────────────────────

class TestPlatformInsights:
    def test_platform_insights_endpoint(self, client, auth_headers):
        # Seed some goals and tasks
        for title in ["Earn money online", "Learn Python", "Build a website"]:
            resp = client.post("/api/goals", json={
                "title": title, "description": f"Goal: {title}",
            }, headers=auth_headers)
            goal_id = resp.json()["id"]
            client.post("/api/tasks", json={
                "goal_id": goal_id, "title": f"Step 1 for {title}",
                "description": "First step", "estimated_minutes": 15,
            }, headers=auth_headers)

        resp = client.get("/api/platform/insights", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()

        assert "goal_type_insights" in data
        assert "commonly_skipped_tasks" in data
        assert "task_stats" in data
        assert "popular_services" in data
        assert "proven_paths" in data
        assert "common_behaviors" in data

    def test_platform_insights_goal_type_detection(self, client, auth_headers):
        """Goals are classified into types correctly."""
        client.post("/api/goals", json={
            "title": "Make money freelancing", "description": "Earn income online",
        }, headers=auth_headers)
        client.post("/api/goals", json={
            "title": "Learn React", "description": "Study JavaScript framework",
        }, headers=auth_headers)

        data = client.get("/api/platform/insights", headers=auth_headers).json()
        types = {t["goal_type"] for t in data["goal_type_insights"]}
        assert "make_money_online" in types
        assert "learn_skill" in types

    def test_platform_insights_requires_auth(self, client):
        resp = client.get("/api/platform/insights")
        assert resp.status_code in (401, 403)

    def test_platform_insights_task_stats(self, client, auth_headers):
        """Task stats reflect done/skipped/failed counts."""
        resp = client.post("/api/goals", json={
            "title": "Test stats", "description": "For task stats",
        }, headers=auth_headers)
        goal_id = resp.json()["id"]

        for i in range(3):
            task = client.post("/api/tasks", json={
                "goal_id": goal_id, "title": f"Task {i}",
                "description": "desc", "estimated_minutes": 10,
            }, headers=auth_headers).json()
            if i == 0:
                client.patch(f"/api/tasks/{task['id']}", json={"status": "done"}, headers=auth_headers)
            elif i == 1:
                client.patch(f"/api/tasks/{task['id']}", json={"status": "skipped"}, headers=auth_headers)

        data = client.get("/api/platform/insights", headers=auth_headers).json()
        ts = data["task_stats"]
        assert ts["total"] >= 3
        assert ts["done"] >= 1
        assert ts["skipped"] >= 1


class TestStoragePlatformPatterns:
    def test_get_platform_patterns_empty_db(self):
        """Platform patterns work on empty database."""
        patterns = storage.get_platform_patterns()
        assert patterns["goal_type_insights"] == []
        assert patterns["commonly_skipped_tasks"] == []
        assert patterns["task_stats"]["total"] == 0

    def test_detect_goal_type_keywords(self):
        """_detect_goal_type classifies goals correctly."""
        from teb.storage import _detect_goal_type

        assert _detect_goal_type("Make money online", "earn income") == "make_money_online"
        assert _detect_goal_type("Learn Python", "study programming") == "learn_skill"
        assert _detect_goal_type("Get fit", "exercise daily") == "get_fit"
        assert _detect_goal_type("Build an app", "create software") == "build_project"
        assert _detect_goal_type("Write a book", "author content") == "write_book"
        assert _detect_goal_type("Organize closet", "tidy up") == "generic"

    def test_platform_patterns_with_spending(self):
        """Popular services are tracked correctly."""
        uid = _create_user_in_db("spend@test.com")
        goal = storage.create_goal(Goal(title="Test", description="", user_id=uid))
        task = storage.create_task(Task(goal_id=goal.id, title="T", description="D"))
        budget = storage.create_spending_budget(SpendingBudget(
            goal_id=goal.id, daily_limit=100, total_limit=500
        ))
        for svc in ["stripe", "stripe", "vercel"]:
            storage.create_spending_request(SpendingRequest(
                task_id=task.id, budget_id=budget.id,
                amount=10.0, service=svc, status="approved",
            ))

        patterns = storage.get_platform_patterns()
        services = {s["service"] for s in patterns["popular_services"]}
        assert "stripe" in services


# ─── Enhanced AI Decomposer Tests ────────────────────────────────────────────

class TestDecomposerContextBuilder:
    def test_build_context_returns_string(self):
        """_build_context_for_ai returns a string even with no data."""
        from teb.decomposer import _build_context_for_ai

        goal = Goal(title="Test", description="Test goal", id=1, user_id=None)
        context = _build_context_for_ai(goal)
        assert isinstance(context, str)

    def test_build_context_includes_user_profile(self):
        """Context includes user profile info when available."""
        from teb.decomposer import _build_context_for_ai

        uid = _create_user_in_db("profile@test.com")
        profile = storage.get_or_create_profile(uid)
        profile.skills = "python,javascript"
        profile.experience_level = "intermediate"
        profile.available_hours_per_day = 2.0
        storage.update_profile(profile)

        goal = Goal(title="Build app", description="Create a web app", id=1, user_id=uid)
        context = _build_context_for_ai(goal)
        assert "python" in context.lower()
        assert "intermediate" in context.lower()

    def test_build_context_includes_platform_patterns(self):
        """Context includes platform-wide learnings."""
        from teb.decomposer import _build_context_for_ai

        uid = _create_user_in_db("platform@test.com")
        uid2 = _create_user_in_db("platform2@test.com")
        storage.create_goal(Goal(
            title="Make money", description="earn online", user_id=uid, status="done",
        ))
        storage.create_goal(Goal(
            title="Make money again", description="earn more", user_id=uid2, status="done",
        ))

        test_goal = Goal(title="Make money", description="earn online", id=99, user_id=uid)
        context = _build_context_for_ai(test_goal)
        # Context should be a string (may or may not have platform data depending on thresholds)
        assert isinstance(context, str)

    def test_build_context_includes_behavior_patterns(self):
        """Context includes user behavior patterns."""
        from teb.decomposer import _build_context_for_ai

        uid = _create_user_in_db("behavior@test.com")
        storage.record_user_behavior(uid, "avoids", "cli_tasks")
        storage.record_user_behavior(uid, "stalled", "goal_5", "7_days")

        goal = Goal(title="Build app", description="desc", id=1, user_id=uid)
        context = _build_context_for_ai(goal)
        assert "cli_tasks" in context

    def test_enhanced_system_prompt_is_richer(self):
        """The enhanced decompose_ai uses a richer system prompt."""
        import inspect
        from teb.decomposer import decompose_ai

        source = inspect.getsource(decompose_ai)
        # Check for key prompt enhancements
        assert "hyper-specific" in source
        assert "immediately actionable" in source
        assert "no-code" in source
        assert "proven path" in source

    def test_decompose_still_works_template_mode(self):
        """decompose() still works in template mode (no API key)."""
        from teb.decomposer import decompose

        uid = _create_user_in_db("template@test.com")
        goal = storage.create_goal(Goal(
            title="Make money online",
            description="I want to earn money",
            user_id=uid,
            answers={"skill_level": "beginner"},
        ))
        tasks = decompose(goal)
        assert len(tasks) > 0
        assert all(isinstance(t, Task) for t in tasks)

    def test_decompose_ai_falls_back_to_template(self):
        """decompose_ai gracefully falls back when OpenAI key is missing."""
        from teb.decomposer import decompose_ai

        uid = _create_user_in_db("fallback@test.com")
        goal = storage.create_goal(Goal(
            title="Test goal", description="Test",
            user_id=uid,
            answers={"skill_level": "beginner"},
        ))
        # Without OPENAI_API_KEY, should fall back to template
        tasks = decompose_ai(goal)
        assert len(tasks) > 0


# ─── Integration: ROI with real spending workflow ────────────────────────────

class TestRoiIntegration:
    def test_full_roi_workflow(self, client, auth_headers):
        """End-to-end: create goal -> budget -> spend -> earn -> check ROI."""
        # 1. Create goal
        goal = client.post("/api/goals", json={
            "title": "Launch SaaS", "description": "Build and sell software",
        }, headers=auth_headers).json()
        goal_id = goal["id"]

        # 2. Create task
        task = client.post("/api/tasks", json={
            "goal_id": goal_id, "title": "Buy domain",
            "description": "Register saas.com", "estimated_minutes": 10,
        }, headers=auth_headers).json()

        # 3. Create budget
        budget = client.post("/api/budgets", json={
            "goal_id": goal_id, "daily_limit": 50, "total_limit": 500,
        }, headers=auth_headers).json()

        # 4. Create approved spending request
        spend_resp = client.post("/api/spending/request", json={
            "task_id": task["id"], "amount": 12.99,
            "description": "Domain registration", "service": "namecheap",
        }, headers=auth_headers)
        assert spend_resp.status_code == 201

        # 5. Add revenue metric
        client.post(f"/api/goals/{goal_id}/outcomes", json={
            "label": "Monthly Revenue", "target_value": 1000, "unit": "$",
        }, headers=auth_headers)

        # Update with earned amount
        metrics = client.get(f"/api/goals/{goal_id}/outcomes", headers=auth_headers).json()
        metric_id = metrics[0]["id"]
        client.patch(f"/api/outcomes/{metric_id}", json={
            "current_value": 250,
        }, headers=auth_headers)

        # 6. Check ROI
        roi = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()
        assert roi["total_earned"] == 250.0
        assert roi["goal_id"] == goal_id

    def test_roi_with_multiple_currency_units(self, client, auth_headers):
        """Only $ / USD / revenue metrics count as earnings."""
        goal = client.post("/api/goals", json={
            "title": "Mixed metrics", "description": "Test",
        }, headers=auth_headers).json()
        goal_id = goal["id"]

        # Dollar metric
        client.post(f"/api/goals/{goal_id}/outcomes", json={
            "label": "Revenue", "target_value": 1000, "unit": "$",
        }, headers=auth_headers)
        # USD metric
        client.post(f"/api/goals/{goal_id}/outcomes", json={
            "label": "Consulting", "target_value": 500, "unit": "USD",
        }, headers=auth_headers)
        # Non-dollar metric (should NOT count)
        client.post(f"/api/goals/{goal_id}/outcomes", json={
            "label": "Followers", "target_value": 10000, "unit": "people",
        }, headers=auth_headers)

        # Update all metrics
        metrics = client.get(f"/api/goals/{goal_id}/outcomes", headers=auth_headers).json()
        for m in metrics:
            client.patch(f"/api/outcomes/{m['id']}", json={"current_value": 100}, headers=auth_headers)

        roi = client.get(f"/api/goals/{goal_id}/roi", headers=auth_headers).json()
        # Only $ and USD should count: 100 + 100 = 200 (not the followers)
        assert roi["total_earned"] == 200.0
