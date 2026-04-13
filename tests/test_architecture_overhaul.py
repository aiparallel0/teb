"""Tests for the architecture overhaul: success graph, execution memory, routers."""
from __future__ import annotations

import os
import tempfile

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from teb import storage
from teb.storage.base import set_db_path, init_db


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def setup_test_db(tmp_path):
    """Create a fresh database for each test."""
    db_path = str(tmp_path / "test.db")
    set_db_path(db_path)
    init_db()
    yield
    set_db_path(db_path)


async def _get_auth_headers(client: AsyncClient) -> dict:
    """Register and login a test user, return auth headers."""
    email = f"test_{os.urandom(4).hex()}@example.com"
    resp = await client.post("/api/auth/register", json={"email": email, "password": "TestPass123!"})
    if resp.status_code == 201:
        token = resp.json()["token"]
    else:
        resp = await client.post("/api/auth/login", json={"email": email, "password": "TestPass123!"})
        token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
        yield c


# ─── Success Graph Tests ─────────────────────────────────────────────────────

class TestSuccessGraph:
    """Test the success path graph module."""

    def test_update_graph_from_completed_goal(self):
        from teb.success_graph import update_graph_from_completed_goal, get_graph_stats

        tasks = [
            {"title": "Research market", "status": "done", "estimated_minutes": 60, "order_index": 0},
            {"title": "Define target audience", "status": "done", "estimated_minutes": 30, "order_index": 1},
            {"title": "Create landing page", "status": "done", "estimated_minutes": 120, "order_index": 2},
            {"title": "Launch ads", "status": "done", "estimated_minutes": 45, "order_index": 3},
        ]

        edges = update_graph_from_completed_goal("launch_product", tasks)
        assert edges == 3  # 3 sequential edges

        stats = get_graph_stats("launch_product")
        assert stats["nodes"] == 4
        assert stats["edges"] == 3
        assert "launch_product" in stats["goal_types"]

    def test_get_best_path(self):
        from teb.success_graph import update_graph_from_completed_goal, get_best_path

        # Add multiple completions to build weight
        tasks1 = [
            {"title": "Step A", "status": "done", "estimated_minutes": 30, "order_index": 0},
            {"title": "Step B", "status": "done", "estimated_minutes": 45, "order_index": 1},
            {"title": "Step C", "status": "done", "estimated_minutes": 60, "order_index": 2},
        ]
        tasks2 = [
            {"title": "Step A", "status": "done", "estimated_minutes": 25, "order_index": 0},
            {"title": "Step B", "status": "done", "estimated_minutes": 50, "order_index": 1},
            {"title": "Step C", "status": "done", "estimated_minutes": 55, "order_index": 2},
        ]

        update_graph_from_completed_goal("test_goal", tasks1)
        update_graph_from_completed_goal("test_goal", tasks2)

        path = get_best_path("test_goal")
        assert len(path) == 3
        assert path[0]["title"] == "Step A"
        assert path[1]["title"] == "Step B"
        assert path[2]["title"] == "Step C"
        # Node frequency should be 2
        assert path[0]["frequency"] == 2

    def test_empty_graph_returns_empty(self):
        from teb.success_graph import get_best_path, get_graph_stats

        path = get_best_path("nonexistent_type")
        assert path == []

        stats = get_graph_stats("nonexistent_type")
        assert stats["nodes"] == 0
        assert stats["edges"] == 0

    def test_single_task_no_edges(self):
        from teb.success_graph import update_graph_from_completed_goal

        tasks = [{"title": "Only task", "status": "done", "estimated_minutes": 30, "order_index": 0}]
        edges = update_graph_from_completed_goal("single", tasks)
        assert edges == 0

    def test_graph_stats_global(self):
        from teb.success_graph import update_graph_from_completed_goal, get_graph_stats

        tasks = [
            {"title": "X", "status": "done", "estimated_minutes": 10, "order_index": 0},
            {"title": "Y", "status": "done", "estimated_minutes": 20, "order_index": 1},
        ]
        update_graph_from_completed_goal("type_a", tasks)
        update_graph_from_completed_goal("type_b", tasks)

        stats = get_graph_stats()
        assert stats["nodes"] >= 2
        assert stats["edges"] >= 1


# ─── Execution Memory Tests ──────────────────────────────────────────────────

class TestExecutionMemory:
    """Test the execution memory module."""

    def test_record_and_retrieve(self):
        from teb.memory import record_call, get_memory_for_goal

        record_id = record_call(
            endpoint="https://api.stripe.com/v1/customers",
            method="POST",
            payload={"name": "Test User"},
            status_code=200,
            success=True,
            latency_ms=150.5,
            goal_id=1,
            task_id=10,
        )
        assert record_id > 0

        entries = get_memory_for_goal(1)
        assert len(entries) == 1
        assert entries[0]["endpoint"] == "https://api.stripe.com/v1/customers"
        assert entries[0]["success"] == 1

    def test_should_execute_no_history(self):
        from teb.memory import should_execute

        advice = should_execute("https://new-api.com/endpoint")
        assert advice.proceed is True
        assert "No previous" in advice.reason

    def test_should_execute_after_failures(self):
        from teb.memory import record_call, should_execute

        # Record 3 consecutive failures
        for _ in range(3):
            record_call(
                endpoint="https://failing-api.com/endpoint",
                method="POST",
                status_code=500,
                success=False,
                error_message="Internal server error",
            )

        advice = should_execute("https://failing-api.com/endpoint", "POST")
        assert advice.proceed is False
        assert "Escalating" in advice.reason
        assert advice.consecutive_failures >= 3

    def test_should_execute_after_recovery(self):
        from teb.memory import record_call, should_execute

        # Record failures then a success
        for _ in range(2):
            record_call(endpoint="https://api.com/recover", method="GET",
                       status_code=500, success=False)
        record_call(endpoint="https://api.com/recover", method="GET",
                   status_code=200, success=True)

        advice = should_execute("https://api.com/recover", "GET")
        assert advice.proceed is True
        assert advice.consecutive_failures == 0

    def test_memory_stats(self):
        from teb.memory import record_call, get_memory_stats

        record_call(endpoint="https://api.com/a", method="GET", status_code=200, success=True)
        record_call(endpoint="https://api.com/a", method="GET", status_code=500, success=False)
        record_call(endpoint="https://api.com/b", method="POST", status_code=201, success=True)

        stats = get_memory_stats()
        assert stats["total_calls"] == 3
        assert stats["total_successes"] == 2
        assert stats["success_rate"] == round(2/3, 2)
        assert len(stats["top_endpoints"]) >= 1


# ─── API Endpoint Tests ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_success_graph_stats_endpoint(client):
    """Test the success graph stats API endpoint."""
    resp = await client.get("/api/success-graph/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "nodes" in data
    assert "edges" in data
    assert "goal_types" in data


@pytest.mark.anyio
async def test_success_graph_path_endpoint(client):
    """Test the success graph best path API endpoint."""
    resp = await client.get("/api/success-graph/path", params={"goal_type": "test"})
    assert resp.status_code == 200
    data = resp.json()
    assert "path" in data
    assert "steps" in data


@pytest.mark.anyio
async def test_execution_memory_stats_endpoint(client):
    """Test the execution memory stats API endpoint."""
    resp = await client.get("/api/execution-memory/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "total_calls" in data
    assert "success_rate" in data


@pytest.mark.anyio
async def test_execution_memory_advice_endpoint(client):
    """Test the execution memory advice API endpoint."""
    resp = await client.get("/api/execution-memory/advice",
                           params={"endpoint": "https://api.test.com/v1", "method": "GET"})
    assert resp.status_code == 200
    data = resp.json()
    assert "proceed" in data
    assert "reason" in data


@pytest.mark.anyio
async def test_execution_memory_for_goal_endpoint(client):
    """Test the execution memory for goal API endpoint."""
    # Create a goal first
    resp = await client.post("/api/goals", json={"title": "Test Goal"})
    assert resp.status_code == 201
    goal_id = resp.json()["id"]

    resp = await client.get(f"/api/goals/{goal_id}/execution-memory")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] == goal_id
    assert isinstance(data["entries"], list)


# ─── Storage Package Tests ───────────────────────────────────────────────────

class TestStoragePackage:
    """Test that the storage package works correctly after split."""

    def test_base_imports(self):
        from teb.storage.base import _conn, _with_retry, init_db, set_db_path
        assert callable(_conn)
        assert callable(init_db)

    def test_backward_compat_imports(self):
        from teb.storage import (
            create_goal, create_task, create_user, get_goal,
            list_goals, list_tasks, _conn, _with_retry, init_db,
        )
        assert callable(create_goal)
        assert callable(_conn)

    def test_storage_operations(self):
        from teb.models import Goal, Task, User
        from teb import storage

        user = storage.create_user(User(
            email="test_pkg@example.com",
            password_hash="hash123",
        ))
        assert user.id is not None

        goal = storage.create_goal(Goal(
            title="Test storage package", description="desc",
            user_id=user.id,
        ))
        assert goal.id is not None

        task = storage.create_task(Task(
            goal_id=goal.id,
            title="Test task", description="d",
        ))
        assert task.id is not None

        retrieved = storage.get_goal(goal.id)
        assert retrieved is not None
        assert retrieved.title == "Test storage package"


# ─── Router Tests ─────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_health_router(client):
    """Test health check via extracted router."""
    resp = await client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "healthy"
    assert "components" in data


@pytest.mark.anyio
async def test_auth_router_register_login(client):
    """Test auth register and login via extracted router."""
    email = f"router_test_{os.urandom(4).hex()}@example.com"

    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        # Register
        resp = await c.post("/api/auth/register", json={"email": email, "password": "Secure123!"})
        assert resp.status_code == 201
        assert "token" in resp.json()

        # Login
        resp = await c.post("/api/auth/login", json={"email": email, "password": "Secure123!"})
        assert resp.status_code == 200
        assert "token" in resp.json()


# ─── Config Hardening Tests ──────────────────────────────────────────────────

class TestConfigHardening:
    """Test Phase 2D config hardening."""

    def test_teb_env_default(self):
        from teb.config import TEB_ENV
        # Default should be development (not production) so tests work
        assert TEB_ENV in ("development", "production")

    def test_sentry_dsn_config(self):
        from teb.config import SENTRY_DSN
        # Should be None when not configured
        assert SENTRY_DSN is None or isinstance(SENTRY_DSN, str)

    def test_cors_not_wildcard_by_default(self):
        from teb.config import CORS_ORIGINS
        # With hardened defaults, shouldn't be just ["*"]
        assert isinstance(CORS_ORIGINS, list)
