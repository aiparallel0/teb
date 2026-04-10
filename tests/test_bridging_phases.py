"""
Comprehensive tests for the bridging plan: Phases 1-4.

Tests cover:
- Phase 1: Task due dates, dependencies, tags, comments, artifacts
- Phase 2: DAG planner, execution replay, webhooks
- Phase 3: Trello/Asana import, adaptive pacing, outcome attribution
- Phase 4: Trello/Asana export (sync adapters)
"""

import json
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

from teb import storage
from teb.dag import (
    DAGValidation,
    ExecutionBatch,
    build_execution_plan,
    get_critical_path,
    validate_dag,
)
from teb.models import (
    Goal,
    Task,
    TaskArtifact,
    TaskComment,
    WebhookConfig,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    db = str(tmp_path / "test_bridging_phases.db")
    storage.set_db_path(db)
    storage.init_db()
    yield
    storage.set_db_path(None)


@pytest.fixture()
def client():
    from teb.main import app
    return TestClient(app, raise_server_exceptions=False)


def _register_and_login(client):
    """Helper: register and login a user, return auth headers."""
    from teb.main import reset_rate_limits
    reset_rate_limits()
    resp = client.post("/api/auth/register", json={"email": "test@example.com", "password": "TestPass123!"})
    assert resp.status_code == 201
    resp = client.post("/api/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_goal_with_tasks(headers, client, n=3, tags=None):
    """Helper: create a goal with n tasks."""
    body = {"title": "Test Goal", "description": "A test goal"}
    if tags:
        body["tags"] = tags
    resp = client.post("/api/goals", json=body, headers=headers)
    assert resp.status_code == 201
    goal = resp.json()
    tasks = []
    for i in range(n):
        resp = client.post("/api/tasks", json={
            "goal_id": goal["id"],
            "title": f"Task {i+1}",
            "description": f"Description {i+1}",
        }, headers=headers)
        assert resp.status_code == 201
        tasks.append(resp.json())
    return goal, tasks


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: Usability Foundations
# ═══════════════════════════════════════════════════════════════════════════════


class TestTaskDueDatesAndDependencies:
    """Phase 1, Step 1: Due dates and dependencies on tasks."""

    def test_task_model_has_due_date(self):
        t = Task(goal_id=1, title="Test", description="", due_date="2025-06-15")
        d = t.to_dict()
        assert d["due_date"] == "2025-06-15"

    def test_task_model_has_depends_on(self):
        t = Task(goal_id=1, title="Test", description="", depends_on="[1, 2]")
        d = t.to_dict()
        assert d["depends_on"] == [1, 2]

    def test_task_model_empty_depends_on(self):
        t = Task(goal_id=1, title="Test", description="")
        d = t.to_dict()
        assert d["depends_on"] == []

    def test_create_task_with_due_date(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "G"}, headers=headers)
        goal = resp.json()
        resp = client.post("/api/tasks", json={
            "goal_id": goal["id"],
            "title": "Task with due date",
            "due_date": "2025-12-31",
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.json()["due_date"] == "2025-12-31"

    def test_create_task_with_dependencies(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "G"}, headers=headers)
        goal = resp.json()

        # Create two tasks
        r1 = client.post("/api/tasks", json={"goal_id": goal["id"], "title": "T1"}, headers=headers)
        r2 = client.post("/api/tasks", json={"goal_id": goal["id"], "title": "T2"}, headers=headers)
        t1 = r1.json()

        # Create task depending on t1
        r3 = client.post("/api/tasks", json={
            "goal_id": goal["id"],
            "title": "T3 depends on T1",
            "depends_on": [t1["id"]],
        }, headers=headers)
        assert r3.status_code == 201
        assert r3.json()["depends_on"] == [t1["id"]]

    def test_update_task_due_date(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.patch(f"/api/tasks/{tasks[0]['id']}", json={
            "due_date": "2025-07-01",
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["due_date"] == "2025-07-01"

    def test_update_task_dependencies(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        resp = client.patch(f"/api/tasks/{tasks[1]['id']}", json={
            "depends_on": [tasks[0]["id"]],
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["depends_on"] == [tasks[0]["id"]]

    def test_storage_task_due_date_persists(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t = Task(goal_id=g.id, title="T", description="", due_date="2025-09-01")
        t = storage.create_task(t)
        loaded = storage.get_task(t.id)
        assert loaded.due_date == "2025-09-01"

    def test_storage_task_depends_on_persists(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="T1", description=""))
        t2 = Task(goal_id=g.id, title="T2", description="", depends_on=json.dumps([t1.id]))
        t2 = storage.create_task(t2)
        loaded = storage.get_task(t2.id)
        assert json.loads(loaded.depends_on) == [t1.id]


class TestLabelsAndTags:
    """Phase 1, Step 2: Labels/tags on goals and tasks."""

    def test_goal_model_has_tags(self):
        g = Goal(title="G", description="", tags="marketing,technical")
        d = g.to_dict()
        assert d["tags"] == ["marketing", "technical"]

    def test_goal_empty_tags(self):
        g = Goal(title="G", description="")
        d = g.to_dict()
        assert d["tags"] == []

    def test_task_model_has_tags(self):
        t = Task(goal_id=1, title="T", description="", tags="web,design")
        d = t.to_dict()
        assert d["tags"] == ["web", "design"]

    def test_create_goal_with_tags(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={
            "title": "Tagged Goal",
            "tags": "marketing,growth",
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.json()["tags"] == ["marketing", "growth"]

    def test_create_task_with_tags(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "G"}, headers=headers)
        goal = resp.json()
        resp = client.post("/api/tasks", json={
            "goal_id": goal["id"],
            "title": "Tagged Task",
            "tags": "frontend,react",
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.json()["tags"] == ["frontend", "react"]

    def test_update_task_tags(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.patch(f"/api/tasks/{tasks[0]['id']}", json={
            "tags": "updated,new_tag",
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["tags"] == ["updated", "new_tag"]

    def test_storage_goal_tags_persist(self):
        g = Goal(title="G", description="", tags="a,b,c")
        g = storage.create_goal(g)
        loaded = storage.get_goal(g.id)
        assert loaded.tags == "a,b,c"

    def test_search_tasks_by_tags(self):
        g = storage.create_goal(Goal(title="G", description=""))
        storage.create_task(Task(goal_id=g.id, title="Marketing task", description="", tags="marketing"))
        storage.create_task(Task(goal_id=g.id, title="Dev task", description="", tags="development"))
        results = storage.search_tasks(goal_id=g.id, tags="marketing")
        assert len(results) == 1
        assert results[0].title == "Marketing task"


class TestTaskComments:
    """Phase 1, Step 3: Task-level comments."""

    def test_task_comment_model(self):
        c = TaskComment(task_id=1, content="Test comment", author_type="agent", author_id="coordinator")
        d = c.to_dict()
        assert d["content"] == "Test comment"
        assert d["author_type"] == "agent"

    def test_create_and_list_comments(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="T", description=""))
        c = storage.create_task_comment(TaskComment(
            task_id=t.id, content="Agent did X", author_type="agent", author_id="coordinator",
        ))
        assert c.id is not None
        comments = storage.list_task_comments(t.id)
        assert len(comments) == 1
        assert comments[0].content == "Agent did X"

    def test_delete_comment(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="T", description=""))
        c = storage.create_task_comment(TaskComment(task_id=t.id, content="temp"))
        storage.delete_task_comment(c.id)
        assert len(storage.list_task_comments(t.id)) == 0

    def test_create_comment_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.post(f"/api/tasks/{tasks[0]['id']}/comments", json={
            "content": "Human feedback",
            "author_type": "human",
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.json()["content"] == "Human feedback"
        assert resp.json()["author_type"] == "human"

    def test_list_comments_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        tid = tasks[0]["id"]
        client.post(f"/api/tasks/{tid}/comments", json={"content": "C1"}, headers=headers)
        client.post(f"/api/tasks/{tid}/comments", json={"content": "C2"}, headers=headers)
        resp = client.get(f"/api/tasks/{tid}/comments", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 2

    def test_create_comment_empty_content_rejected(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.post(f"/api/tasks/{tasks[0]['id']}/comments", json={
            "content": "",
        }, headers=headers)
        assert resp.status_code == 422

    def test_create_comment_invalid_author_type(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.post(f"/api/tasks/{tasks[0]['id']}/comments", json={
            "content": "test",
            "author_type": "alien",
        }, headers=headers)
        assert resp.status_code == 422


class TestTaskArtifacts:
    """Phase 1, Step 4: Task artifacts."""

    def test_task_artifact_model(self):
        a = TaskArtifact(
            task_id=1, artifact_type="screenshot", title="Result",
            content_url="/screenshots/abc.png", metadata_json='{"size": 1024}',
        )
        d = a.to_dict()
        assert d["artifact_type"] == "screenshot"
        assert d["metadata"] == {"size": 1024}

    def test_create_and_list_artifacts(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="T", description=""))
        a = storage.create_task_artifact(TaskArtifact(
            task_id=t.id, artifact_type="code", title="Output",
            content_url="https://example.com/output.py",
        ))
        assert a.id is not None
        artifacts = storage.list_task_artifacts(t.id)
        assert len(artifacts) == 1
        assert artifacts[0].artifact_type == "code"

    def test_create_artifact_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.post(f"/api/tasks/{tasks[0]['id']}/artifacts", json={
            "artifact_type": "url",
            "title": "Deployed site",
            "content_url": "https://example.com",
            "metadata": {"status": "live"},
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.json()["artifact_type"] == "url"
        assert resp.json()["metadata"] == {"status": "live"}

    def test_list_artifacts_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        tid = tasks[0]["id"]
        client.post(f"/api/tasks/{tid}/artifacts", json={
            "artifact_type": "file", "title": "f1",
        }, headers=headers)
        resp = client.get(f"/api/tasks/{tid}/artifacts", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_invalid_artifact_type_rejected(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.post(f"/api/tasks/{tasks[0]['id']}/artifacts", json={
            "artifact_type": "invalid",
        }, headers=headers)
        assert resp.status_code == 422


class TestTaskSearch:
    """Phase 1: Search and filter tasks."""

    def test_search_tasks_by_query(self):
        g = storage.create_goal(Goal(title="G", description=""))
        storage.create_task(Task(goal_id=g.id, title="Buy domain", description=""))
        storage.create_task(Task(goal_id=g.id, title="Write code", description=""))
        results = storage.search_tasks(goal_id=g.id, query="domain")
        assert len(results) == 1
        assert results[0].title == "Buy domain"

    def test_search_tasks_by_status(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="Done task", description="", status="done"))
        storage.create_task(Task(goal_id=g.id, title="Todo task", description=""))
        results = storage.search_tasks(goal_id=g.id, status="done")
        assert len(results) == 1

    def test_search_tasks_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        resp = client.get(f"/api/tasks/search?q=Task+1&goal_id={goal['id']}", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) >= 1


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Strengthen Autonomous Execution
# ═══════════════════════════════════════════════════════════════════════════════


class TestDAGPlanner:
    """Phase 2, Step 5: DAG execution planner."""

    def _make_tasks(self, goal_id, specs):
        """Create tasks from specs: [(title, depends_on_indices)]"""
        tasks = []
        for title, _ in specs:
            t = storage.create_task(Task(goal_id=goal_id, title=title, description=""))
            tasks.append(t)
        # Set dependencies
        for i, (_, dep_indices) in enumerate(specs):
            if dep_indices:
                deps = [tasks[j].id for j in dep_indices]
                tasks[i].depends_on = json.dumps(deps)
                storage.update_task(tasks[i])
        return tasks

    def test_validate_dag_valid(self):
        g = storage.create_goal(Goal(title="G", description=""))
        tasks = self._make_tasks(g.id, [
            ("A", []),
            ("B", [0]),  # B depends on A
            ("C", [0]),  # C depends on A
            ("D", [1, 2]),  # D depends on B and C
        ])
        result = validate_dag(tasks)
        assert result.is_valid
        assert len(result.errors) == 0

    def test_validate_dag_cycle(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t2 = storage.create_task(Task(goal_id=g.id, title="B", description=""))
        # A depends on B, B depends on A = cycle
        t1.depends_on = json.dumps([t2.id])
        storage.update_task(t1)
        t2.depends_on = json.dumps([t1.id])
        storage.update_task(t2)
        tasks = [storage.get_task(t1.id), storage.get_task(t2.id)]
        result = validate_dag(tasks)
        assert not result.is_valid
        assert any("cycle" in e.lower() for e in result.errors)

    def test_validate_dag_self_reference(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t.depends_on = json.dumps([t.id])
        storage.update_task(t)
        tasks = [storage.get_task(t.id)]
        result = validate_dag(tasks)
        assert not result.is_valid
        assert any("itself" in e.lower() for e in result.errors)

    def test_build_execution_plan_linear(self):
        g = storage.create_goal(Goal(title="G", description=""))
        tasks = self._make_tasks(g.id, [
            ("A", []),
            ("B", [0]),
            ("C", [1]),
        ])
        batches = build_execution_plan(tasks)
        assert len(batches) == 3
        assert tasks[0].id in batches[0].task_ids
        assert tasks[1].id in batches[1].task_ids
        assert tasks[2].id in batches[2].task_ids

    def test_build_execution_plan_parallel(self):
        g = storage.create_goal(Goal(title="G", description=""))
        tasks = self._make_tasks(g.id, [
            ("A", []),
            ("B", []),
            ("C", []),
        ])
        batches = build_execution_plan(tasks)
        assert len(batches) == 1
        assert len(batches[0].task_ids) == 3

    def test_build_execution_plan_diamond(self):
        g = storage.create_goal(Goal(title="G", description=""))
        tasks = self._make_tasks(g.id, [
            ("A", []),
            ("B", [0]),
            ("C", [0]),
            ("D", [1, 2]),
        ])
        batches = build_execution_plan(tasks)
        assert len(batches) == 3
        assert tasks[0].id in batches[0].task_ids
        assert set(batches[1].task_ids) == {tasks[1].id, tasks[2].id}
        assert tasks[3].id in batches[2].task_ids

    def test_build_execution_plan_skips_done_tasks(self):
        g = storage.create_goal(Goal(title="G", description=""))
        tasks = self._make_tasks(g.id, [("A", []), ("B", [0])])
        tasks[0].status = "done"
        storage.update_task(tasks[0])
        tasks = [storage.get_task(t.id) for t in tasks]
        batches = build_execution_plan(tasks)
        assert len(batches) == 1
        assert tasks[1].id in batches[0].task_ids

    def test_get_critical_path(self):
        g = storage.create_goal(Goal(title="G", description=""))
        tasks = self._make_tasks(g.id, [
            ("A", []),      # 0
            ("B", [0]),     # 1
            ("C", [1]),     # 2
            ("D", [0]),     # 3 - shorter path
        ])
        path = get_critical_path(tasks)
        assert len(path) == 3  # A -> B -> C
        assert tasks[0].id in path
        assert tasks[1].id in path
        assert tasks[2].id in path

    def test_dependency_graph_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        client.patch(f"/api/tasks/{tasks[1]['id']}", json={
            "depends_on": [tasks[0]["id"]],
        }, headers=headers)
        resp = client.get(f"/api/goals/{goal['id']}/dependency-graph", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["nodes"]) == 2
        assert len(data["edges"]) == 1
        assert not data["has_cycles"]

    def test_ready_tasks_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        client.patch(f"/api/tasks/{tasks[1]['id']}", json={
            "depends_on": [tasks[0]["id"]],
        }, headers=headers)
        resp = client.get(f"/api/goals/{goal['id']}/ready-tasks", headers=headers)
        assert resp.status_code == 200
        ready = resp.json()
        ready_ids = [t["id"] for t in ready]
        assert tasks[0]["id"] in ready_ids
        assert tasks[1]["id"] not in ready_ids


class TestGetReadyTasks:
    """Phase 2, Step 5: Ready tasks from dependency graph."""

    def test_get_ready_tasks_all_independent(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t2 = storage.create_task(Task(goal_id=g.id, title="B", description=""))
        ready = storage.get_ready_tasks(g.id)
        assert len(ready) == 2

    def test_get_ready_tasks_with_deps(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t2 = Task(goal_id=g.id, title="B", description="", depends_on=json.dumps([t1.id]))
        t2 = storage.create_task(t2)
        ready = storage.get_ready_tasks(g.id)
        assert len(ready) == 1
        assert ready[0].id == t1.id

    def test_get_ready_tasks_after_dep_done(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t2 = Task(goal_id=g.id, title="B", description="", depends_on=json.dumps([t1.id]))
        t2 = storage.create_task(t2)
        t1.status = "done"
        storage.update_task(t1)
        ready = storage.get_ready_tasks(g.id)
        assert len(ready) == 1
        assert ready[0].id == t2.id


class TestExecutionReplay:
    """Phase 2, Step 6: Replay failed executions."""

    def test_replay_failed_task(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        tid = tasks[0]["id"]
        # Mark as failed
        client.patch(f"/api/tasks/{tid}", json={"status": "failed"}, headers=headers)
        # Replay
        resp = client.post(f"/api/tasks/{tid}/replay", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["status"] == "todo"

    def test_replay_non_failed_task_rejected(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        resp = client.post(f"/api/tasks/{tasks[0]['id']}/replay", headers=headers)
        assert resp.status_code == 422

    def test_replay_creates_comment(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        tid = tasks[0]["id"]
        client.patch(f"/api/tasks/{tid}", json={"status": "failed"}, headers=headers)
        client.post(f"/api/tasks/{tid}/replay", headers=headers)
        resp = client.get(f"/api/tasks/{tid}/comments", headers=headers)
        comments = resp.json()
        assert len(comments) >= 1
        assert any("replay" in c["content"].lower() for c in comments)


class TestWebhooks:
    """Phase 2, Step 7: Webhook configuration."""

    def test_webhook_model(self):
        wh = WebhookConfig(
            user_id=1, url="https://example.com/hook",
            events='["task_completed","goal_updated"]',
            secret="test_secret",
        )
        d = wh.to_dict()
        assert d["events"] == ["task_completed", "goal_updated"]
        assert d["secret_set"] is True

    def test_create_and_list_webhooks(self):
        from teb.models import User
        u = storage.create_user(User(email="hook@test.com", password_hash="x"))
        wh = WebhookConfig(
            user_id=u.id, url="https://example.com/hook",
            events='["task_completed"]',
        )
        wh = storage.create_webhook_config(wh)
        assert wh.id is not None
        hooks = storage.list_webhook_configs(u.id)
        assert len(hooks) == 1

    def test_create_webhook_endpoint(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/webhooks", json={
            "url": "https://example.com/webhook",
            "events": ["task_completed", "goal_updated"],
            "secret": "my_secret",
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["url"] == "https://example.com/webhook"
        assert data["events"] == ["task_completed", "goal_updated"]
        assert data["secret_set"] is True

    def test_list_webhooks_endpoint(self, client):
        headers = _register_and_login(client)
        client.post("/api/webhooks", json={
            "url": "https://example.com/hook1",
            "events": [],
        }, headers=headers)
        resp = client.get("/api/webhooks", headers=headers)
        assert resp.status_code == 200
        assert len(resp.json()) == 1

    def test_delete_webhook_endpoint(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/webhooks", json={
            "url": "https://example.com/hook1",
        }, headers=headers)
        wh_id = resp.json()["id"]
        resp = client.delete(f"/api/webhooks/{wh_id}", headers=headers)
        assert resp.status_code == 200
        resp = client.get("/api/webhooks", headers=headers)
        assert len(resp.json()) == 0

    def test_update_webhook_endpoint(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/webhooks", json={
            "url": "https://example.com/hook1",
        }, headers=headers)
        wh_id = resp.json()["id"]
        resp = client.patch(f"/api/webhooks/{wh_id}", json={
            "enabled": False,
        }, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["enabled"] is False

    def test_webhook_url_empty_rejected(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/webhooks", json={"url": ""}, headers=headers)
        assert resp.status_code == 422

    def test_list_webhooks_for_event(self):
        from teb.models import User
        u = storage.create_user(User(email="evthook@test.com", password_hash="x"))
        wh1 = storage.create_webhook_config(WebhookConfig(
            user_id=u.id, url="https://a.com", events='["task_completed"]',
        ))
        wh2 = storage.create_webhook_config(WebhookConfig(
            user_id=u.id, url="https://b.com", events='["goal_updated"]',
        ))
        # task_completed hooks
        hooks = storage.list_webhooks_for_event(u.id, "task_completed")
        assert len(hooks) == 1
        assert hooks[0].url == "https://a.com"

    def test_webhook_delivery_module(self):
        from teb.webhooks import _sign_payload
        sig = _sign_payload('{"test": 1}', "secret")
        assert isinstance(sig, str)
        assert len(sig) == 64  # SHA-256 hex digest


class TestExecutionStepRollback:
    """Phase 2, Step 6: ExecutionStep reversible + rollback_plan."""

    def test_execution_step_has_rollback_fields(self):
        from teb.executor import ExecutionStep
        step = ExecutionStep(
            credential_id=1, method="POST", path="/api/create",
            headers={}, body=None, description="Create resource",
            reversible=True, rollback_plan='{"method": "DELETE", "path": "/api/delete/1"}',
        )
        d = step.to_dict()
        assert d["reversible"] is True
        assert d["rollback_plan"] != ""


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Deepen AI Coaching
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrelloImport:
    """Phase 3, Step 9: Trello board import."""

    def _trello_board(self):
        return {
            "name": "My Trello Board",
            "desc": "Board description",
            "lists": [
                {"id": "list1", "name": "To Do", "closed": False},
                {"id": "list2", "name": "In Progress", "closed": False},
                {"id": "list3", "name": "Done", "closed": False},
            ],
            "cards": [
                {"name": "Card 1", "desc": "Description 1", "idList": "list1", "closed": False},
                {"name": "Card 2", "desc": "Description 2", "idList": "list2", "closed": False, "due": "2025-12-31T00:00:00.000Z"},
                {"name": "Card 3", "desc": "Done card", "idList": "list3", "closed": False},
                {"name": "Closed", "desc": "Should not import", "idList": "list1", "closed": True},
            ],
        }

    def test_import_trello_endpoint(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/trello", json={
            "board": self._trello_board(),
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["goal"]["title"] == "My Trello Board"
        assert data["tasks_imported"] == 3  # 4 cards, 1 closed = 3

    def test_trello_import_status_mapping(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/trello", json={
            "board": self._trello_board(),
        }, headers=headers)
        goal_id = resp.json()["goal"]["id"]
        tasks_resp = client.get(f"/api/tasks?goal_id={goal_id}", headers=headers)
        tasks = tasks_resp.json()
        statuses = {t["title"]: t["status"] for t in tasks}
        assert statuses["Card 1"] == "todo"
        assert statuses["Card 2"] == "in_progress"
        assert statuses["Card 3"] == "done"

    def test_trello_import_due_date(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/trello", json={
            "board": self._trello_board(),
        }, headers=headers)
        goal_id = resp.json()["goal"]["id"]
        tasks_resp = client.get(f"/api/tasks?goal_id={goal_id}", headers=headers)
        tasks = tasks_resp.json()
        card2 = next(t for t in tasks if t["title"] == "Card 2")
        assert card2["due_date"] == "2025-12-31"

    def test_trello_import_tags(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/trello", json={
            "board": self._trello_board(),
        }, headers=headers)
        data = resp.json()
        assert "trello" in data["goal"]["tags"]

    def test_trello_import_empty_board(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/trello", json={
            "board": {"name": "Empty", "lists": [], "cards": []},
        }, headers=headers)
        assert resp.status_code == 201
        assert resp.json()["tasks_imported"] == 0


class TestAsanaImport:
    """Phase 3, Step 9: Asana project import."""

    def _asana_project(self):
        return {
            "name": "My Asana Project",
            "notes": "Project description",
            "tasks": [
                {"name": "Task A", "notes": "Notes A", "completed": False, "due_on": "2025-06-15", "subtasks": [
                    {"name": "Sub A1", "notes": "", "completed": True},
                    {"name": "Sub A2", "notes": "", "completed": False},
                ]},
                {"name": "Task B", "notes": "Notes B", "completed": True, "subtasks": []},
            ],
        }

    def test_import_asana_endpoint(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/asana", json={
            "project": self._asana_project(),
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["goal"]["title"] == "My Asana Project"
        assert data["tasks_imported"] == 4  # 2 tasks + 2 subtasks

    def test_asana_import_subtasks(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/asana", json={
            "project": self._asana_project(),
        }, headers=headers)
        goal_id = resp.json()["goal"]["id"]
        tasks_resp = client.get(f"/api/tasks?goal_id={goal_id}", headers=headers)
        tasks = tasks_resp.json()
        subtasks = [t for t in tasks if t["parent_id"] is not None]
        assert len(subtasks) == 2

    def test_asana_import_status_mapping(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/import/asana", json={
            "project": self._asana_project(),
        }, headers=headers)
        goal_id = resp.json()["goal"]["id"]
        tasks_resp = client.get(f"/api/tasks?goal_id={goal_id}", headers=headers)
        tasks = tasks_resp.json()
        task_b = next(t for t in tasks if t["title"] == "Task B")
        assert task_b["status"] == "done"


class TestAdaptivePacing:
    """Phase 3, Step 10: Adaptive pacing."""

    def test_pacing_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=5)
        resp = client.get(f"/api/goals/{goal['id']}/pacing", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] == 5
        assert data["done"] == 0
        assert "recommendation" in data

    def test_pacing_on_track(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        resp = client.get(f"/api/goals/{goal['id']}/pacing", headers=headers)
        assert resp.json()["recommendation"] == "on_track"

    def test_pacing_break_down_after_failures(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        # Fail most tasks
        for t in tasks[:2]:
            client.patch(f"/api/tasks/{t['id']}", json={"status": "failed"}, headers=headers)
        resp = client.get(f"/api/goals/{goal['id']}/pacing", headers=headers)
        # High failure rate should suggest break_down
        data = resp.json()
        assert data["failed"] == 2


class TestOutcomeAttribution:
    """Phase 3, Step 11: Impact / outcome attribution."""

    def test_impact_endpoint(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        resp = client.get(f"/api/goals/{goal['id']}/impact", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "metrics" in data
        assert "agent_contributions" in data
        assert "task_impact" in data

    def test_impact_with_done_tasks(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        client.patch(f"/api/tasks/{tasks[0]['id']}", json={"status": "done"}, headers=headers)
        resp = client.get(f"/api/goals/{goal['id']}/impact", headers=headers)
        data = resp.json()
        done_titles = [t["title"] for t in data["task_impact"]]
        assert tasks[0]["title"] in done_titles


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: Strategic Positioning
# ═══════════════════════════════════════════════════════════════════════════════


class TestTrelloExport:
    """Phase 4, Step 13: Trello export (sync adapter)."""

    def test_export_to_trello_format(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        # Mark one task done
        client.patch(f"/api/tasks/{tasks[0]['id']}", json={"status": "done"}, headers=headers)
        resp = client.post("/api/sync/trello/export", json={
            "goal_id": goal["id"],
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Goal"
        assert len(data["lists"]) == 3
        assert len(data["cards"]) == 3
        # Verify done task is in "done" list
        done_cards = [c for c in data["cards"] if c["idList"] == "done"]
        assert len(done_cards) == 1

    def test_export_trello_missing_goal_id(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/sync/trello/export", json={}, headers=headers)
        assert resp.status_code == 422


class TestAsanaExport:
    """Phase 4, Step 13: Asana export (sync adapter)."""

    def test_export_to_asana_format(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        client.patch(f"/api/tasks/{tasks[1]['id']}", json={"status": "done"}, headers=headers)
        resp = client.post("/api/sync/asana/export", json={
            "goal_id": goal["id"],
        }, headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "Test Goal"
        assert len(data["tasks"]) == 2
        completed = [t for t in data["tasks"] if t["completed"]]
        assert len(completed) == 1

    def test_export_asana_missing_goal_id(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/sync/asana/export", json={}, headers=headers)
        assert resp.status_code == 422


class TestCycleValidation:
    """Phase 2: Storage-level cycle validation."""

    def test_validate_no_cycles_clean(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t2 = Task(goal_id=g.id, title="B", description="", depends_on=json.dumps([t1.id]))
        storage.create_task(t2)
        result = storage.validate_no_cycles(g.id)
        assert result is None

    def test_validate_no_cycles_with_cycle(self):
        g = storage.create_goal(Goal(title="G", description=""))
        t1 = storage.create_task(Task(goal_id=g.id, title="A", description=""))
        t2 = storage.create_task(Task(goal_id=g.id, title="B", description=""))
        t1.depends_on = json.dumps([t2.id])
        storage.update_task(t1)
        t2.depends_on = json.dumps([t1.id])
        storage.update_task(t2)
        result = storage.validate_no_cycles(g.id)
        assert result is not None
        assert "cycle" in result.lower()


class TestImporterModules:
    """Phase 3: Importer module direct tests."""

    def test_trello_importer_module(self):
        from teb.importers import import_trello_board
        from teb.models import User
        u = storage.create_user(User(email="trello@test.com", password_hash="x"))
        board = {
            "name": "Test Board",
            "lists": [{"id": "l1", "name": "Backlog", "closed": False}],
            "cards": [
                {"name": "Card A", "idList": "l1", "closed": False, "desc": ""},
            ],
        }
        goal, tasks = import_trello_board(u.id, board)
        assert goal.title == "Test Board"
        assert len(tasks) == 1

    def test_asana_importer_module(self):
        from teb.importers import import_asana_project
        from teb.models import User
        u = storage.create_user(User(email="asana@test.com", password_hash="x"))
        project = {
            "name": "Test Project",
            "tasks": [
                {"name": "Do thing", "notes": "", "completed": False, "subtasks": []},
            ],
        }
        goal, tasks = import_asana_project(u.id, project)
        assert goal.title == "Test Project"
        assert len(tasks) == 1


class TestDAGModule:
    """Phase 2: DAG module unit tests."""

    def test_execution_batch_to_dict(self):
        b = ExecutionBatch(batch_index=0, task_ids=[1, 2, 3])
        d = b.to_dict()
        assert d["batch_index"] == 0
        assert d["task_ids"] == [1, 2, 3]

    def test_dag_validation_to_dict(self):
        v = DAGValidation(is_valid=True, errors=[], warnings=["warn"])
        d = v.to_dict()
        assert d["is_valid"] is True
        assert len(d["warnings"]) == 1

    def test_validate_dag_empty(self):
        result = validate_dag([])
        assert result.is_valid

    def test_build_plan_empty(self):
        batches = build_execution_plan([])
        assert batches == []
