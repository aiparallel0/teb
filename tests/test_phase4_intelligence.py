"""Phase 4 Intelligence tests — scheduler functions and API endpoints."""

import json
from datetime import datetime, timedelta, timezone

import pytest
from starlette.testclient import TestClient

from teb.main import app, reset_rate_limits
from teb.models import Task
from teb.scheduler import (
    auto_schedule_tasks,
    detect_duplicates,
    detect_risks,
    estimate_completion,
    smart_prioritize,
    suggest_focus_blocks,
)


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _register(client, email="intel@test.com", password="TestPass123!"):
    reset_rate_limits()
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    return resp.json()["token"]


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


def _create_goal(client, token, title="Test Goal"):
    resp = client.post("/api/goals", json={"title": title, "description": "desc"}, headers=_auth(token))
    assert resp.status_code == 201
    return resp.json()["id"]


def _create_task(client, token, goal_id, title="Task", **kwargs):
    body = {"goal_id": goal_id, "title": title, "description": kwargs.get("description", "do it")}
    body.update(kwargs)
    resp = client.post("/api/tasks", json=body, headers=_auth(token))
    assert resp.status_code == 201
    return resp.json()["id"]


def _make_task(id_=1, title="Task", goal_id=1, estimated_minutes=30,
               status="todo", due_date="", depends_on="[]", tags="",
               created_at=None, **kwargs):
    """Build a Task dataclass for unit tests."""
    return Task(
        id=id_,
        goal_id=goal_id,
        title=title,
        description=kwargs.get("description", "desc"),
        estimated_minutes=estimated_minutes,
        status=status,
        due_date=due_date,
        depends_on=depends_on,
        tags=tags,
        created_at=created_at or datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Unit tests for scheduler.py
# ═══════════════════════════════════════════════════════════════════════════════


class TestAutoSchedule:
    def test_empty_list(self):
        assert auto_schedule_tasks([]) == []

    def test_single_task(self):
        tasks = [_make_task(id_=1, estimated_minutes=60)]
        result = auto_schedule_tasks(tasks)
        assert len(result) == 1
        assert result[0]["task_id"] == 1
        assert result[0]["day_slot"] == 1

    def test_multiple_tasks_fit_one_day(self):
        tasks = [
            _make_task(id_=1, estimated_minutes=120),
            _make_task(id_=2, title="Task 2", estimated_minutes=120),
        ]
        result = auto_schedule_tasks(tasks, work_hours_per_day=8)
        assert len(result) == 2
        assert all(r["day_slot"] == 1 for r in result)

    def test_tasks_overflow_to_next_day(self):
        tasks = [
            _make_task(id_=1, estimated_minutes=300),
            _make_task(id_=2, title="Task 2", estimated_minutes=300),
        ]
        result = auto_schedule_tasks(tasks, work_hours_per_day=8)
        assert result[0]["day_slot"] == 1
        assert result[1]["day_slot"] == 2

    def test_done_tasks_skipped(self):
        tasks = [
            _make_task(id_=1, status="done"),
            _make_task(id_=2, title="Task 2", status="todo"),
        ]
        result = auto_schedule_tasks(tasks)
        assert len(result) == 1
        assert result[0]["task_id"] == 2

    def test_respects_dependencies(self):
        tasks = [
            _make_task(id_=2, title="Depends on 1", depends_on=json.dumps([1])),
            _make_task(id_=1, title="First"),
        ]
        result = auto_schedule_tasks(tasks)
        ids = [r["task_id"] for r in result]
        assert ids.index(1) < ids.index(2)

    def test_custom_start_date(self):
        start = datetime(2025, 1, 1, tzinfo=timezone.utc)
        tasks = [_make_task(id_=1)]
        result = auto_schedule_tasks(tasks, start_date=start)
        assert "2025-01-01" in result[0]["scheduled_start"]


class TestSmartPrioritize:
    def test_empty_list(self):
        assert smart_prioritize([]) == []

    def test_overdue_ranks_highest(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        next_month = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
        tasks = [
            _make_task(id_=1, title="Far away", due_date=next_month),
            _make_task(id_=2, title="Overdue", due_date=yesterday),
        ]
        result = smart_prioritize(tasks)
        assert result[0]["task_id"] == 2
        assert "overdue" in result[0]["explanation"]

    def test_done_tasks_excluded(self):
        tasks = [
            _make_task(id_=1, status="done"),
            _make_task(id_=2, status="todo"),
        ]
        result = smart_prioritize(tasks)
        assert len(result) == 1
        assert result[0]["task_id"] == 2

    def test_blocking_task_gets_higher_priority(self):
        tasks = [
            _make_task(id_=1, title="Blocker"),
            _make_task(id_=2, title="Depends", depends_on=json.dumps([1])),
        ]
        result = smart_prioritize(tasks)
        # Task 1 blocks task 2, so it should rank higher
        scores = {r["task_id"]: r["priority_score"] for r in result}
        assert scores[1] >= scores[2]

    def test_no_due_date_still_works(self):
        tasks = [_make_task(id_=1, due_date="")]
        result = smart_prioritize(tasks)
        assert len(result) == 1
        assert "no deadline" in result[0]["explanation"]

    def test_score_fields_present(self):
        tasks = [_make_task(id_=1)]
        result = smart_prioritize(tasks)
        entry = result[0]
        assert "priority_score" in entry
        assert "deadline_urgency" in entry
        assert "dependency_impact" in entry
        assert "effort_efficiency" in entry
        assert "staleness" in entry


class TestEstimateCompletion:
    def test_all_done(self):
        tasks = [_make_task(id_=1, status="done"), _make_task(id_=2, status="skipped")]
        result = estimate_completion(tasks)
        assert result["remaining_tasks"] == 0
        assert result["confidence"] == 1.0
        assert result["percent_complete"] == 100.0

    def test_none_done(self):
        tasks = [_make_task(id_=1), _make_task(id_=2, title="T2")]
        result = estimate_completion(tasks, velocity_tasks_per_day=1.0)
        assert result["remaining_tasks"] == 2
        assert result["remaining_hours"] == 1.0  # 2 * 30min = 60min = 1h
        assert result["percent_complete"] == 0.0

    def test_partial_progress(self):
        tasks = [
            _make_task(id_=1, status="done"),
            _make_task(id_=2, title="T2"),
        ]
        result = estimate_completion(tasks)
        assert result["remaining_tasks"] == 1
        assert result["percent_complete"] == 50.0

    def test_empty_tasks(self):
        result = estimate_completion([])
        assert result["remaining_tasks"] == 0


class TestDetectRisks:
    def test_empty_list(self):
        assert detect_risks([]) == []

    def test_overdue_detection(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        tasks = [_make_task(id_=1, due_date=yesterday)]
        risks = detect_risks(tasks)
        overdue = [r for r in risks if r["risk_type"] == "overdue"]
        assert len(overdue) == 1
        assert overdue[0]["severity"] == "critical"

    def test_blocked_detection(self):
        tasks = [
            _make_task(id_=1, title="Blocker", status="todo"),
            _make_task(id_=2, title="Blocked", depends_on=json.dumps([1])),
        ]
        risks = detect_risks(tasks)
        blocked = [r for r in risks if r["risk_type"] == "blocked"]
        assert len(blocked) == 1
        assert blocked[0]["task_id"] == 2

    def test_stagnant_detection(self):
        old_date = datetime.now(timezone.utc) - timedelta(days=10)
        tasks = [_make_task(id_=1, created_at=old_date)]
        risks = detect_risks(tasks)
        stagnant = [r for r in risks if r["risk_type"] == "stagnant"]
        assert len(stagnant) == 1

    def test_overloaded_detection(self):
        tasks = [_make_task(id_=1, estimated_minutes=500)]
        risks = detect_risks(tasks)
        overloaded = [r for r in risks if r["risk_type"] == "overloaded"]
        assert len(overloaded) == 1

    def test_done_tasks_excluded(self):
        yesterday = (datetime.now(timezone.utc) - timedelta(days=5)).isoformat()
        tasks = [_make_task(id_=1, status="done", due_date=yesterday)]
        assert detect_risks(tasks) == []


class TestSuggestFocusBlocks:
    def test_empty_list(self):
        assert suggest_focus_blocks([]) == []

    def test_quick_wins_block(self):
        tasks = [
            _make_task(id_=1, title="Quick 1", estimated_minutes=15),
            _make_task(id_=2, title="Quick 2", estimated_minutes=20),
        ]
        blocks = suggest_focus_blocks(tasks)
        assert any(b["block_name"] == "Quick Wins" for b in blocks)

    def test_tag_grouping(self):
        tasks = [
            _make_task(id_=1, title="Backend 1", tags="backend", estimated_minutes=60),
            _make_task(id_=2, title="Backend 2", tags="backend", estimated_minutes=60),
            _make_task(id_=3, title="Frontend 1", tags="frontend", estimated_minutes=60),
        ]
        blocks = suggest_focus_blocks(tasks)
        names = [b["block_name"] for b in blocks]
        assert any("backend" in n.lower() for n in names)

    def test_all_done_returns_empty(self):
        tasks = [_make_task(id_=1, status="done")]
        assert suggest_focus_blocks(tasks) == []

    def test_respects_available_hours(self):
        tasks = [
            _make_task(id_=i, title=f"T{i}", estimated_minutes=120)
            for i in range(1, 6)
        ]
        blocks = suggest_focus_blocks(tasks, available_hours=2)
        for block in blocks:
            assert block["total_minutes"] <= 120


class TestDetectDuplicates:
    def test_empty_list(self):
        assert detect_duplicates([]) == []

    def test_single_task(self):
        assert detect_duplicates([_make_task()]) == []

    def test_identical_tasks_detected(self):
        tasks = [
            _make_task(id_=1, title="Setup database schema", description="Create tables"),
            _make_task(id_=2, title="Setup database schema", description="Create tables"),
        ]
        dups = detect_duplicates(tasks)
        assert len(dups) == 1
        assert dups[0]["similarity"] == 1.0

    def test_similar_tasks_detected(self):
        tasks = [
            _make_task(id_=1, title="Setup the database schema", description="Create the tables"),
            _make_task(id_=2, title="Setup database schema tables", description="Create tables"),
        ]
        dups = detect_duplicates(tasks, threshold=0.5)
        assert len(dups) >= 1

    def test_different_tasks_not_flagged(self):
        tasks = [
            _make_task(id_=1, title="Fix login bug", description="Auth issue"),
            _make_task(id_=2, title="Deploy to production", description="Run CI/CD pipeline"),
        ]
        dups = detect_duplicates(tasks)
        assert len(dups) == 0

    def test_custom_threshold(self):
        tasks = [
            _make_task(id_=1, title="Create user profile page", description="Frontend"),
            _make_task(id_=2, title="Create user settings page", description="Frontend"),
        ]
        strict = detect_duplicates(tasks, threshold=0.9)
        lenient = detect_duplicates(tasks, threshold=0.3)
        assert len(lenient) >= len(strict)


class TestTopologicalSortEdgeCases:
    def test_circular_dependencies(self):
        """Circular deps should not crash; cyclic tasks appended at end."""
        tasks = [
            _make_task(id_=1, depends_on=json.dumps([2])),
            _make_task(id_=2, title="T2", depends_on=json.dumps([1])),
        ]
        result = auto_schedule_tasks(tasks)
        assert len(result) == 2

    def test_missing_dependency_ids(self):
        """Deps referencing non-existent tasks should be ignored."""
        tasks = [_make_task(id_=1, depends_on=json.dumps([999]))]
        result = auto_schedule_tasks(tasks)
        assert len(result) == 1

    def test_tasks_with_no_ids(self):
        """Tasks without IDs should still be handled."""
        tasks = [_make_task(id_=None, title="No ID")]
        result = auto_schedule_tasks(tasks)
        assert len(result) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# API integration tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestScheduleEndpoint:
    def test_get_schedule(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Task A", estimated_minutes=60)
        _create_task(client, token, gid, "Task B", estimated_minutes=120)
        resp = client.get(f"/api/goals/{gid}/schedule", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert "scheduled_start" in data[0]

    def test_schedule_empty_goal(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        resp = client.get(f"/api/goals/{gid}/schedule", headers=_auth(token))
        assert resp.status_code == 200
        assert resp.json() == []

    def test_schedule_requires_auth(self, client):
        resp = client.get("/api/goals/1/schedule")
        assert resp.status_code == 401

    def test_schedule_wrong_owner(self, client):
        token1 = _register(client, email="u1@test.com")
        token2 = _register(client, email="u2@test.com")
        gid = _create_goal(client, token1)
        resp = client.get(f"/api/goals/{gid}/schedule", headers=_auth(token2))
        assert resp.status_code == 403


class TestSmartPriorityEndpoint:
    def test_get_priority(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Important task")
        resp = client.get(f"/api/goals/{gid}/smart-priority", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert "priority_score" in data[0]

    def test_priority_requires_auth(self, client):
        resp = client.get("/api/goals/1/smart-priority")
        assert resp.status_code == 401


class TestCompletionEstimateEndpoint:
    def test_get_estimate(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Task 1")
        resp = client.get(f"/api/goals/{gid}/completion-estimate", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "estimated_completion_date" in data
        assert "remaining_tasks" in data
        assert "confidence" in data

    def test_estimate_requires_auth(self, client):
        resp = client.get("/api/goals/1/completion-estimate")
        assert resp.status_code == 401


class TestRisksEndpoint:
    def test_get_risks(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        yesterday = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d")
        _create_task(client, token, gid, "Overdue task", due_date=yesterday)
        resp = client.get(f"/api/goals/{gid}/risks", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["risk_type"] == "overdue"

    def test_risks_requires_auth(self, client):
        resp = client.get("/api/goals/1/risks")
        assert resp.status_code == 401


class TestFocusBlocksEndpoint:
    def test_get_focus_blocks(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Quick task", estimated_minutes=15)
        resp = client.get(f"/api/goals/{gid}/focus-blocks", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)

    def test_focus_blocks_with_hours_param(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Task 1", estimated_minutes=60)
        resp = client.get(f"/api/goals/{gid}/focus-blocks?available_hours=2", headers=_auth(token))
        assert resp.status_code == 200

    def test_focus_blocks_requires_auth(self, client):
        resp = client.get("/api/goals/1/focus-blocks")
        assert resp.status_code == 401


class TestDuplicatesEndpoint:
    def test_detect_duplicates(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Setup database schema", description="Create tables")
        _create_task(client, token, gid, "Setup database schema", description="Create tables")
        resp = client.get(f"/api/goals/{gid}/duplicates", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["similarity"] >= 0.7

    def test_duplicates_requires_auth(self, client):
        resp = client.get("/api/goals/1/duplicates")
        assert resp.status_code == 401


class TestAutoPrioritizeEndpoint:
    def test_auto_prioritize(self, client):
        token = _register(client)
        gid = _create_goal(client, token)
        _create_task(client, token, gid, "Task A")
        _create_task(client, token, gid, "Task B")
        resp = client.post(f"/api/goals/{gid}/auto-prioritize", headers=_auth(token))
        assert resp.status_code == 200
        data = resp.json()
        assert "updated" in data
        assert "ranking" in data

    def test_auto_prioritize_requires_auth(self, client):
        resp = client.post("/api/goals/1/auto-prioritize")
        assert resp.status_code == 401

    def test_auto_prioritize_nonexistent_goal(self, client):
        token = _register(client)
        resp = client.post("/api/goals/99999/auto-prioritize", headers=_auth(token))
        assert resp.status_code == 404


class TestOpenAPIMetadata:
    def test_openapi_title(self, client):
        resp = client.get("/openapi.json")
        assert resp.status_code == 200
        data = resp.json()
        assert data["info"]["title"] == "teb API"
        assert data["info"]["version"] == "2.0.0"

    def test_docs_available(self, client):
        resp = client.get("/docs")
        assert resp.status_code == 200

    def test_redoc_available(self, client):
        resp = client.get("/redoc")
        assert resp.status_code == 200

    def test_tags_metadata_present(self, client):
        resp = client.get("/openapi.json")
        data = resp.json()
        tag_names = [t["name"] for t in data.get("tags", [])]
        assert "intelligence" in tag_names
