"""
Tests for MVP features:
- Autonomous execution loop
- Financial autopilot (SpendingBudget autopilot fields)
- Auto-provisioning (service signup)
- Infrastructure lifecycle (deploy/monitor/fix)
- Auto-execute goal flag
"""

import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from teb import config, deployer, provisioning, storage
from teb.main import app
from teb.models import (
    ApiCredential, ExecutionLog, Goal, SpendingBudget,
    SpendingRequest, Task,
)

client = TestClient(app)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _register_user(email="auto@teb.test", password="testpass123"):
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    if r.status_code not in (200, 201):
        r = client.post("/api/auth/login", json={"email": email, "password": password})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _make_goal(title="test goal", user_id=None, auto_execute=False):
    g = Goal(title=title, description="")
    g.user_id = user_id
    g.auto_execute = auto_execute
    return storage.create_goal(g)


def _make_task(goal_id, title="task1", status="todo", order_index=0):
    t = Task(goal_id=goal_id, title=title, description="desc",
             estimated_minutes=30, order_index=order_index)
    t.status = status
    return storage.create_task(t)


# ═══════════════════════════════════════════════════════════════════════════════
# Goal auto_execute flag
# ═══════════════════════════════════════════════════════════════════════════════

class TestGoalAutoExecute:
    def test_goal_default_auto_execute_false(self):
        g = _make_goal("test")
        assert g.auto_execute is False

    def test_goal_create_with_auto_execute(self):
        g = _make_goal("test", auto_execute=True)
        assert g.auto_execute is True
        # Verify persisted
        loaded = storage.get_goal(g.id)
        assert loaded.auto_execute is True

    def test_goal_update_auto_execute(self):
        g = _make_goal("test")
        assert g.auto_execute is False
        g.auto_execute = True
        storage.update_goal(g)
        loaded = storage.get_goal(g.id)
        assert loaded.auto_execute is True

    def test_goal_to_dict_includes_auto_execute(self):
        g = _make_goal("test", auto_execute=True)
        d = g.to_dict()
        assert "auto_execute" in d
        assert d["auto_execute"] is True

    def test_enable_auto_execute_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]

        r = client.post(f"/api/goals/{goal_id}/auto-execute", headers=headers)
        assert r.status_code == 200
        assert r.json()["auto_execute"] is True

        # Verify persisted
        loaded = storage.get_goal(goal_id)
        assert loaded.auto_execute is True

    def test_disable_auto_execute_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]

        client.post(f"/api/goals/{goal_id}/auto-execute", headers=headers)
        r = client.delete(f"/api/goals/{goal_id}/auto-execute", headers=headers)
        assert r.status_code == 200
        assert r.json()["auto_execute"] is False

    def test_auto_execute_requires_auth(self):
        r = client.post("/api/goals/1/auto-execute")
        assert r.status_code in (401, 403)

    def test_auto_execute_status_endpoint(self):
        headers = _register_user()
        r = client.get("/api/auto-execute/status", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "loop_enabled" in data
        assert "loop_running" in data
        assert "pending_tasks" in data
        assert "interval_seconds" in data


# ═══════════════════════════════════════════════════════════════════════════════
# list_auto_execute_tasks
# ═══════════════════════════════════════════════════════════════════════════════

class TestListAutoExecuteTasks:
    def test_no_auto_execute_goals(self):
        g = _make_goal("test")
        _make_task(g.id)
        tasks = storage.list_auto_execute_tasks()
        assert len(tasks) == 0

    def test_auto_execute_goal_with_pending_task(self):
        g = _make_goal("test", auto_execute=True)
        g.status = "decomposed"
        storage.update_goal(g)
        _make_task(g.id, "task1", "todo")
        tasks = storage.list_auto_execute_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "task1"

    def test_only_todo_tasks_returned(self):
        g = _make_goal("test", auto_execute=True)
        g.status = "in_progress"
        storage.update_goal(g)
        _make_task(g.id, "done_task", "done")
        _make_task(g.id, "pending_task", "todo", order_index=1)
        tasks = storage.list_auto_execute_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "pending_task"

    def test_one_task_per_goal(self):
        g = _make_goal("test", auto_execute=True)
        g.status = "decomposed"
        storage.update_goal(g)
        _make_task(g.id, "task1", "todo", order_index=0)
        _make_task(g.id, "task2", "todo", order_index=1)
        tasks = storage.list_auto_execute_tasks()
        assert len(tasks) == 1
        assert tasks[0].title == "task1"

    def test_respects_goal_status(self):
        g = _make_goal("test", auto_execute=True)
        g.status = "drafting"  # not decomposed yet
        storage.update_goal(g)
        _make_task(g.id, "task1", "todo")
        tasks = storage.list_auto_execute_tasks()
        assert len(tasks) == 0

    def test_multiple_goals_with_auto_execute(self):
        g1 = _make_goal("goal1", auto_execute=True)
        g1.status = "decomposed"
        storage.update_goal(g1)
        _make_task(g1.id, "g1_task", "todo")

        g2 = _make_goal("goal2", auto_execute=True)
        g2.status = "in_progress"
        storage.update_goal(g2)
        _make_task(g2.id, "g2_task", "todo")

        tasks = storage.list_auto_execute_tasks()
        assert len(tasks) == 2
        titles = {t.title for t in tasks}
        assert "g1_task" in titles
        assert "g2_task" in titles


# ═══════════════════════════════════════════════════════════════════════════════
# SpendingBudget autopilot
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpendingBudgetAutopilot:
    def test_budget_default_autopilot_off(self):
        g = _make_goal("test")
        b = SpendingBudget(goal_id=g.id, daily_limit=100, total_limit=500)
        b = storage.create_spending_budget(b)
        assert b.autopilot_enabled is False
        assert b.autopilot_threshold == 50.0

    def test_budget_create_with_autopilot(self):
        g = _make_goal("test")
        b = SpendingBudget(
            goal_id=g.id, daily_limit=100, total_limit=500,
            autopilot_enabled=True, autopilot_threshold=25.0,
        )
        b = storage.create_spending_budget(b)
        assert b.autopilot_enabled is True
        assert b.autopilot_threshold == 25.0
        # Verify persisted
        loaded = storage.get_spending_budget(b.id)
        assert loaded.autopilot_enabled is True
        assert loaded.autopilot_threshold == 25.0

    def test_budget_update_autopilot(self):
        g = _make_goal("test")
        b = SpendingBudget(goal_id=g.id, daily_limit=100, total_limit=500)
        b = storage.create_spending_budget(b)
        b.autopilot_enabled = True
        b.autopilot_threshold = 10.0
        storage.update_spending_budget(b)
        loaded = storage.get_spending_budget(b.id)
        assert loaded.autopilot_enabled is True
        assert loaded.autopilot_threshold == 10.0

    def test_budget_to_dict_includes_autopilot(self):
        g = _make_goal("test")
        b = SpendingBudget(
            goal_id=g.id, daily_limit=100, total_limit=500,
            autopilot_enabled=True, autopilot_threshold=30.0,
        )
        b = storage.create_spending_budget(b)
        d = b.to_dict()
        assert "autopilot_enabled" in d
        assert d["autopilot_enabled"] is True
        assert d["autopilot_threshold"] == 30.0

    def test_budget_create_endpoint_with_autopilot(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        r = client.post("/api/budgets", json={
            "goal_id": goal_id,
            "daily_limit": 100,
            "total_limit": 1000,
            "autopilot_enabled": True,
            "autopilot_threshold": 25.0,
        }, headers=headers)
        assert r.status_code == 201
        data = r.json()
        assert data["autopilot_enabled"] is True
        assert data["autopilot_threshold"] == 25.0

    def test_budget_update_endpoint_autopilot(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        r = client.post("/api/budgets", json={
            "goal_id": goal_id, "daily_limit": 100, "total_limit": 1000,
        }, headers=headers)
        budget_id = r.json()["id"]

        r = client.patch(f"/api/budgets/{budget_id}", json={
            "autopilot_enabled": True,
            "autopilot_threshold": 15.0,
        }, headers=headers)
        assert r.status_code == 200
        assert r.json()["autopilot_enabled"] is True
        assert r.json()["autopilot_threshold"] == 15.0

    def test_budget_update_negative_threshold_rejected(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        r = client.post("/api/budgets", json={
            "goal_id": goal_id, "daily_limit": 100, "total_limit": 1000,
        }, headers=headers)
        budget_id = r.json()["id"]

        r = client.patch(f"/api/budgets/{budget_id}", json={
            "autopilot_threshold": -10.0,
        }, headers=headers)
        assert r.status_code == 422

    def test_execute_with_autopilot_skips_approval(self):
        """When autopilot is enabled, execution should not pause for budget approval."""
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]

        # Create a task
        g = storage.get_goal(goal_id)
        t = _make_task(goal_id, "exec task", "todo")

        # Create budget with require_approval=True but autopilot=True
        b = SpendingBudget(
            goal_id=goal_id, daily_limit=100, total_limit=1000,
            require_approval=True, autopilot_enabled=True, autopilot_threshold=50.0,
        )
        storage.create_spending_budget(b)

        # Execute — should NOT pause for approval
        r = client.post(f"/api/tasks/{t.id}/execute", headers=headers)
        data = r.json()
        # Autopilot is enabled, so the spending_request_id should NOT be in the response
        assert "spending_request_id" not in data


# ═══════════════════════════════════════════════════════════════════════════════
# Deployer module
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeployer:
    def test_detect_service_vercel(self):
        assert deployer._detect_service("deploy my next.js app to vercel") == "vercel"

    def test_detect_service_railway(self):
        assert deployer._detect_service("deploy python backend to railway") == "railway"

    def test_detect_service_render(self):
        assert deployer._detect_service("deploy docker app to render") == "render"

    def test_detect_service_default(self):
        assert deployer._detect_service("deploy something") == "railway"

    def test_extract_repo_url(self):
        url = deployer._extract_repo_url("deploy github.com/user/myapp to vercel")
        assert url == "github.com/user/myapp"

    def test_extract_repo_url_with_https(self):
        url = deployer._extract_repo_url("deploy https://github.com/user/myapp")
        assert url == "https://github.com/user/myapp"

    def test_extract_repo_url_none(self):
        url = deployer._extract_repo_url("deploy my app")
        assert url == ""

    def test_extract_project_name_from_repo(self):
        name = deployer._extract_project_name("deploy", "https://github.com/user/myapp")
        assert name == "myapp"

    def test_extract_project_name_from_text(self):
        name = deployer._extract_project_name("deploy mysite", "")
        assert name == "mysite"

    def test_generate_deployment_plan_no_creds(self):
        t = Task(goal_id=1, title="deploy to vercel", description="github.com/u/r")
        plan = deployer.generate_deployment_plan(t, [])
        assert plan.can_deploy is False
        assert "credentials" in plan.reason.lower()

    def test_generate_deployment_plan_no_repo(self):
        cred = ApiCredential(name="vercel", base_url="https://api.vercel.com",
                             auth_value="tok123")
        t = Task(goal_id=1, title="deploy to vercel", description="my app")
        plan = deployer.generate_deployment_plan(t, [cred])
        assert plan.can_deploy is False
        assert "repository" in plan.reason.lower()

    def test_generate_deployment_plan_success(self):
        cred = ApiCredential(name="vercel", base_url="https://api.vercel.com",
                             auth_value="tok123")
        t = Task(goal_id=1, title="deploy to vercel",
                 description="deploy github.com/user/myapp")
        plan = deployer.generate_deployment_plan(t, [cred])
        assert plan.can_deploy is True
        assert plan.service == "vercel"
        assert plan.project_name == "myapp"

    def test_deployment_plan_to_dict(self):
        plan = deployer.DeploymentPlan(
            service="vercel", project_name="myapp",
            repository_url="github.com/user/myapp",
            can_deploy=True, reason="ready",
        )
        d = plan.to_dict()
        assert d["service"] == "vercel"
        assert d["can_deploy"] is True

    def test_check_health_no_url(self):
        result = deployer.check_health("")
        assert result["status"] == "unknown"

    def test_deploy_endpoint_no_credentials(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        t = _make_task(goal_id, "deploy to vercel", "todo")

        r = client.post(f"/api/tasks/{t.id}/deploy", headers=headers)
        assert r.status_code == 200
        assert r.json()["deployed"] is False

    def test_list_deployments_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        r = client.get(f"/api/goals/{goal_id}/deployments", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ═══════════════════════════════════════════════════════════════════════════════
# Deployment storage
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeploymentStorage:
    def test_create_deployment(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        d = storage.create_deployment(
            task_id=t.id, goal_id=g.id, service="vercel",
            project_name="myapp", repository_url="github.com/u/r",
        )
        assert d["id"] is not None
        assert d["service"] == "vercel"
        assert d["status"] == "pending"

    def test_update_deployment(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        d = storage.create_deployment(task_id=t.id, goal_id=g.id, service="vercel")
        updated = storage.update_deployment(
            d["id"], status="deployed", deploy_url="https://myapp.vercel.app",
        )
        assert updated["status"] == "deployed"
        assert updated["deploy_url"] == "https://myapp.vercel.app"

    def test_get_deployment(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        d = storage.create_deployment(task_id=t.id, goal_id=g.id, service="railway")
        loaded = storage.get_deployment(d["id"])
        assert loaded is not None
        assert loaded["service"] == "railway"

    def test_list_deployments(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        storage.create_deployment(task_id=t.id, goal_id=g.id, service="vercel")
        storage.create_deployment(task_id=t.id, goal_id=g.id, service="railway")
        deployments = storage.list_deployments(g.id)
        assert len(deployments) == 2

    def test_list_deployments_all(self):
        g1 = _make_goal("test1")
        g2 = _make_goal("test2")
        t1 = _make_task(g1.id)
        t2 = _make_task(g2.id)
        storage.create_deployment(task_id=t1.id, goal_id=g1.id, service="vercel")
        storage.create_deployment(task_id=t2.id, goal_id=g2.id, service="railway")
        deployments = storage.list_deployments()
        assert len(deployments) == 2

    def test_update_deployment_health(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        d = storage.create_deployment(task_id=t.id, goal_id=g.id, service="vercel")
        storage.update_deployment(d["id"], health_status="healthy")
        loaded = storage.get_deployment(d["id"])
        assert loaded["health_status"] == "healthy"
        assert loaded["last_health_check"] is not None

    def test_get_nonexistent_deployment(self):
        result = storage.get_deployment(99999)
        assert result is None

    def test_update_nonexistent_deployment(self):
        result = storage.update_deployment(99999, status="deployed")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# Provisioning module
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvisioning:
    def test_detect_service_vercel(self):
        assert provisioning._detect_service("sign up for vercel") == "vercel"

    def test_detect_service_stripe(self):
        assert provisioning._detect_service("create stripe account") == "stripe"

    def test_detect_service_sendgrid(self):
        assert provisioning._detect_service("setup sendgrid") == "sendgrid"

    def test_detect_service_github(self):
        assert provisioning._detect_service("create github account") == "github"

    def test_detect_service_cloudflare(self):
        assert provisioning._detect_service("setup cloudflare cdn") == "cloudflare"

    def test_detect_service_namecheap(self):
        assert provisioning._detect_service("register namecheap domain") == "namecheap"

    def test_detect_service_by_alias(self):
        assert provisioning._detect_service("setup email service") == "sendgrid"
        assert provisioning._detect_service("setup payment processing") == "stripe"
        assert provisioning._detect_service("buy a domain name") == "namecheap"
        assert provisioning._detect_service("setup dns records") == "cloudflare"

    def test_detect_service_unknown(self):
        assert provisioning._detect_service("do something random") is None

    def test_generate_provisioning_plan_known_service(self):
        t = Task(goal_id=1, title="sign up for vercel", description="")
        plan = provisioning.generate_provisioning_plan(t)
        assert plan.can_provision is True
        assert plan.service_name == "vercel"
        assert len(plan.steps) > 0
        assert plan.signup_url != ""

    def test_generate_provisioning_plan_unknown_service(self):
        t = Task(goal_id=1, title="do something random", description="")
        plan = provisioning.generate_provisioning_plan(t)
        assert plan.can_provision is False
        assert plan.service_name == "unknown"

    def test_provisioning_plan_to_dict(self):
        t = Task(goal_id=1, title="sign up for stripe", description="")
        plan = provisioning.generate_provisioning_plan(t)
        d = plan.to_dict()
        assert d["service_name"] == "stripe"
        assert d["can_provision"] is True
        assert len(d["steps"]) > 0

    def test_provision_service_no_playwright(self):
        g = _make_goal("test")
        t = _make_task(g.id, "sign up for vercel")
        result = provisioning.provision_service(t)
        # Without Playwright, should return manual instructions
        assert result["service"] == "vercel"
        if not result.get("success"):
            assert "manual" in result.get("mode", "") or "plan" in result

    def test_provision_service_unknown(self):
        g = _make_goal("test")
        t = _make_task(g.id, "do random thing")
        result = provisioning.provision_service(t)
        assert result["success"] is False

    def test_list_provisionable_services(self):
        services = provisioning.list_provisionable_services()
        assert len(services) >= 6
        names = {s["service_name"] for s in services}
        assert "vercel" in names
        assert "stripe" in names
        assert "sendgrid" in names

    def test_provision_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        t = _make_task(goal_id, "sign up for stripe")

        r = client.post(f"/api/tasks/{t.id}/provision", headers=headers)
        assert r.status_code == 200
        assert "service" in r.json()

    def test_list_provisionable_services_endpoint(self):
        headers = _register_user()
        r = client.get("/api/provision/services", headers=headers)
        assert r.status_code == 200
        services = r.json()
        assert len(services) >= 6

    def test_provisioning_logs_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        t = _make_task(goal_id, "sign up for vercel")

        # Provision first
        client.post(f"/api/tasks/{t.id}/provision", headers=headers)

        # Check logs
        r = client.get(f"/api/tasks/{t.id}/provisioning-logs", headers=headers)
        assert r.status_code == 200
        logs = r.json()
        assert len(logs) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# Provisioning storage
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvisioningStorage:
    def test_create_provisioning_log(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        log = storage.create_provisioning_log(
            task_id=t.id, service_name="vercel",
            action="signup", status="success",
        )
        assert log["id"] is not None
        assert log["service_name"] == "vercel"

    def test_list_provisioning_logs(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        storage.create_provisioning_log(task_id=t.id, service_name="vercel")
        storage.create_provisioning_log(task_id=t.id, service_name="stripe")
        logs = storage.list_provisioning_logs(t.id)
        assert len(logs) == 2

    def test_provisioning_log_with_error(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        log = storage.create_provisioning_log(
            task_id=t.id, service_name="vercel",
            status="failed", error="No browser available",
        )
        assert log["status"] == "failed"


# ═══════════════════════════════════════════════════════════════════════════════
# Schema migrations for new columns
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewMigrations:
    def test_goals_auto_execute_column(self):
        """The auto_execute column should exist after init_db."""
        g = _make_goal("test")
        assert hasattr(g, "auto_execute")
        assert g.auto_execute is False

    def test_spending_budgets_autopilot_columns(self):
        """Autopilot columns should exist after init_db."""
        g = _make_goal("test")
        b = SpendingBudget(goal_id=g.id, daily_limit=10, total_limit=100)
        b = storage.create_spending_budget(b)
        loaded = storage.get_spending_budget(b.id)
        assert hasattr(loaded, "autopilot_enabled")
        assert hasattr(loaded, "autopilot_threshold")

    def test_deployments_table_exists(self):
        """Deployments table should be created by migrations."""
        g = _make_goal("test")
        t = _make_task(g.id)
        d = storage.create_deployment(task_id=t.id, goal_id=g.id, service="test")
        assert d["id"] is not None

    def test_provisioning_logs_table_exists(self):
        """Provisioning logs table should be created by migrations."""
        g = _make_goal("test")
        t = _make_task(g.id)
        log = storage.create_provisioning_log(task_id=t.id, service_name="test")
        assert log["id"] is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Deployer health monitoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeployerMonitoring:
    def test_monitor_deployment_not_found(self):
        result = deployer.monitor_deployment(99999)
        assert "error" in result

    def test_monitor_all_empty(self):
        g = _make_goal("test")
        results = deployer.monitor_all_deployments(g.id)
        assert results == []

    def test_monitor_all_skips_non_deployed(self):
        g = _make_goal("test")
        t = _make_task(g.id)
        storage.create_deployment(task_id=t.id, goal_id=g.id, service="vercel")
        # Status is "pending", not "deployed", so it should be skipped
        results = deployer.monitor_all_deployments(g.id)
        assert results == []

    def test_health_check_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        t = _make_task(goal_id)
        d = storage.create_deployment(task_id=t.id, goal_id=goal_id, service="vercel")

        r = client.get(f"/api/deployments/{d['id']}/health", headers=headers)
        assert r.status_code == 200

    def test_health_check_all_endpoint(self):
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]

        r = client.get(f"/api/goals/{goal_id}/deployments/health", headers=headers)
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Autonomous execution loop logic
# ═══════════════════════════════════════════════════════════════════════════════

class TestAutonomousLoopLogic:
    def test_config_defaults(self):
        assert config.AUTONOMOUS_EXECUTION_ENABLED is True
        assert config.AUTONOMOUS_EXECUTION_INTERVAL == 30
        assert config.AUTOPILOT_DEFAULT_THRESHOLD == 50.0

    def test_auto_execute_budget_autopilot_bypass(self):
        """When autopilot is enabled on a budget, the auto-execute loop should
        not create pending spending requests (approval is bypassed)."""
        headers = _register_user()
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        goal_id = r.json()["id"]
        t = _make_task(goal_id, "test task")

        # Budget with require_approval=True BUT autopilot_enabled=True
        b = SpendingBudget(
            goal_id=goal_id, daily_limit=100, total_limit=1000,
            require_approval=True, autopilot_enabled=True,
        )
        storage.create_spending_budget(b)

        # The execute endpoint should NOT pause for approval
        r = client.post(f"/api/tasks/{t.id}/execute", headers=headers)
        data = r.json()
        # It should not have a spending_request_id (meaning approval was bypassed)
        assert "spending_request_id" not in data


# ═══════════════════════════════════════════════════════════════════════════════
# Config variables
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewConfig:
    def test_autonomous_execution_enabled_default(self):
        assert hasattr(config, "AUTONOMOUS_EXECUTION_ENABLED")
        assert isinstance(config.AUTONOMOUS_EXECUTION_ENABLED, bool)

    def test_autonomous_execution_interval_default(self):
        assert hasattr(config, "AUTONOMOUS_EXECUTION_INTERVAL")
        assert config.AUTONOMOUS_EXECUTION_INTERVAL > 0

    def test_autopilot_default_threshold(self):
        assert hasattr(config, "AUTOPILOT_DEFAULT_THRESHOLD")
        assert config.AUTOPILOT_DEFAULT_THRESHOLD > 0
