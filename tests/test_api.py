"""API endpoint tests for teb"""
import os
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# Use an in-memory / temp DB for tests
TEST_DB = "test_teb.db"


@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    """Point storage at a separate test database."""
    from teb import storage
    storage.set_db_path(TEST_DB)
    storage.init_db()
    yield
    # Clean up the test database file
    try:
        os.remove(TEST_DB)
    except FileNotFoundError:
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ─── POST /api/goals ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_goal(client):
    r = await client.post("/api/goals", json={"title": "learn Python", "description": "from scratch"})
    assert r.status_code == 201
    data = r.json()
    assert data["id"] is not None
    assert data["title"] == "learn Python"
    assert data["status"] == "drafting"


@pytest.mark.anyio
async def test_create_goal_empty_title(client):
    r = await client.post("/api/goals", json={"title": "  ", "description": ""})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_goal_missing_title(client):
    r = await client.post("/api/goals", json={"description": "no title"})
    assert r.status_code == 422


# ─── GET /api/goals ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_goals(client):
    await client.post("/api/goals", json={"title": "list test goal"})
    r = await client.get("/api/goals")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert len(r.json()) >= 1


# ─── GET /api/goals/{id} ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_goal(client):
    create = await client.post("/api/goals", json={"title": "get goal test"})
    gid = create.json()["id"]
    r = await client.get(f"/api/goals/{gid}")
    assert r.status_code == 200
    data = r.json()
    assert data["id"] == gid
    assert data["title"] == "get goal test"
    assert "tasks" in data


@pytest.mark.anyio
async def test_get_goal_not_found(client):
    r = await client.get("/api/goals/999999")
    assert r.status_code == 404


# ─── GET /api/goals/{id}/next_question ───────────────────────────────────────

@pytest.mark.anyio
async def test_next_question(client):
    create = await client.post("/api/goals", json={"title": "earn money online"})
    gid = create.json()["id"]
    r = await client.get(f"/api/goals/{gid}/next_question")
    assert r.status_code == 200
    data = r.json()
    assert data["done"] is False
    assert data["question"]["key"]
    assert data["question"]["text"]


# ─── POST /api/goals/{id}/clarify ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_clarify_goal(client):
    create = await client.post("/api/goals", json={"title": "earn money online"})
    gid = create.json()["id"]
    # Get first question
    q_resp = await client.get(f"/api/goals/{gid}/next_question")
    first_key = q_resp.json()["question"]["key"]
    r = await client.post(f"/api/goals/{gid}/clarify", json={"key": first_key, "answer": "Python dev"})
    assert r.status_code == 200
    # Answer should be recorded
    goal = await client.get(f"/api/goals/{gid}")
    assert goal.json()["answers"][first_key] == "Python dev"


# ─── POST /api/goals/{id}/decompose ──────────────────────────────────────────

@pytest.mark.anyio
async def test_decompose_goal(client):
    create = await client.post("/api/goals", json={"title": "learn Python", "description": "complete beginner"})
    gid = create.json()["id"]
    r = await client.post(f"/api/goals/{gid}/decompose", json={})
    assert r.status_code == 200
    data = r.json()
    assert "tasks" in data
    assert len(data["tasks"]) > 0
    # Goal status should be updated
    goal = await client.get(f"/api/goals/{gid}")
    assert goal.json()["status"] == "decomposed"


@pytest.mark.anyio
async def test_decompose_returns_task_fields(client):
    create = await client.post("/api/goals", json={"title": "get fit and lose weight"})
    gid = create.json()["id"]
    r = await client.post(f"/api/goals/{gid}/decompose", json={})
    tasks = r.json()["tasks"]
    for t in tasks:
        assert "id" in t
        assert "title" in t
        assert "description" in t
        assert "estimated_minutes" in t
        assert "status" in t
        assert t["goal_id"] == gid


# ─── GET /api/tasks ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_tasks(client):
    create = await client.post("/api/goals", json={"title": "build a web app"})
    gid = create.json()["id"]
    await client.post(f"/api/goals/{gid}/decompose", json={})
    r = await client.get(f"/api/tasks?goal_id={gid}")
    assert r.status_code == 200
    tasks = r.json()
    assert len(tasks) > 0
    assert all(t["goal_id"] == gid for t in tasks)


@pytest.mark.anyio
async def test_list_tasks_filter_status(client):
    create = await client.post("/api/goals", json={"title": "build a website"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    first_task_id = decomp.json()["tasks"][0]["id"]
    await client.patch(f"/api/tasks/{first_task_id}", json={"status": "done"})
    r = await client.get(f"/api/tasks?goal_id={gid}&status=done")
    assert r.status_code == 200
    tasks = r.json()
    assert all(t["status"] == "done" for t in tasks)


# ─── PATCH /api/tasks/{id} ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_patch_task_status(client):
    create = await client.post("/api/goals", json={"title": "learn cooking"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]
    r = await client.patch(f"/api/tasks/{tid}", json={"status": "in_progress"})
    assert r.status_code == 200
    assert r.json()["status"] == "in_progress"


@pytest.mark.anyio
async def test_patch_task_invalid_status(client):
    create = await client.post("/api/goals", json={"title": "learn cooking 2"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]
    r = await client.patch(f"/api/tasks/{tid}", json={"status": "flying"})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_patch_task_not_found(client):
    r = await client.patch("/api/tasks/999999", json={"status": "done"})
    assert r.status_code == 404


@pytest.mark.anyio
async def test_patch_task_notes(client):
    create = await client.post("/api/goals", json={"title": "learn cooking 3"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]
    r = await client.patch(f"/api/tasks/{tid}", json={"notes": "my custom notes"})
    assert r.status_code == 200
    assert r.json()["description"] == "my custom notes"


# ─── Frontend ─────────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_serve_frontend(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "teb" in r.text.lower()


# ─── Full workflow ─────────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_full_workflow(client):
    """create goal → clarify → decompose → complete all top-level tasks → goal done"""

    # 1. Create goal
    r = await client.post("/api/goals", json={
        "title": "earn money online",
        "description": "I want to earn passive income on the internet",
    })
    assert r.status_code == 201
    gid = r.json()["id"]

    # 2. Answer clarifying questions
    answered = 0
    for _ in range(10):  # safety cap
        q_resp = await client.get(f"/api/goals/{gid}/next_question")
        q_data = q_resp.json()
        if q_data["done"]:
            break
        key = q_data["question"]["key"]
        await client.post(f"/api/goals/{gid}/clarify", json={"key": key, "answer": "test answer"})
        answered += 1
    assert answered > 0

    # 3. Decompose
    r = await client.post(f"/api/goals/{gid}/decompose", json={})
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    assert len(tasks) > 0

    # 4. Mark all top-level tasks done
    r = await client.get(f"/api/tasks?goal_id={gid}")
    top_level = [t for t in r.json() if t["parent_id"] is None]
    for t in top_level:
        await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})

    # 5. Goal should now be "done"
    goal = await client.get(f"/api/goals/{gid}")
    assert goal.json()["status"] == "done"


# ─── POST /api/tasks/{id}/decompose ──────────────────────────────────────────

@pytest.mark.anyio
async def test_decompose_task(client):
    create = await client.post("/api/goals", json={"title": "learn cooking 4"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    # Find a top-level task (no children yet)
    tasks = decomp.json()["tasks"]
    top_level = [t for t in tasks if t["parent_id"] is None]
    tid = top_level[0]["id"]

    r = await client.post(f"/api/tasks/{tid}/decompose", json={})
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == tid
    assert len(data["subtasks"]) >= 2
    for sub in data["subtasks"]:
        assert sub["parent_id"] == tid
        assert sub["estimated_minutes"] <= 25


@pytest.mark.anyio
async def test_decompose_task_not_found(client):
    r = await client.post("/api/tasks/999999/decompose", json={})
    assert r.status_code == 404


@pytest.mark.anyio
async def test_decompose_task_already_has_children(client):
    create = await client.post("/api/goals", json={"title": "learn cooking 5"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tasks = decomp.json()["tasks"]
    top_level = [t for t in tasks if t["parent_id"] is None]
    tid = top_level[0]["id"]

    # First decompose works
    r1 = await client.post(f"/api/tasks/{tid}/decompose", json={})
    assert r1.status_code == 200

    # Second decompose fails with 409
    r2 = await client.post(f"/api/tasks/{tid}/decompose", json={})
    assert r2.status_code == 409


# ─── GET /api/goals/{id}/focus ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_focus_returns_task(client):
    create = await client.post("/api/goals", json={"title": "learn cooking 6"})
    gid = create.json()["id"]
    await client.post(f"/api/goals/{gid}/decompose", json={})

    r = await client.get(f"/api/goals/{gid}/focus")
    assert r.status_code == 200
    data = r.json()
    assert data["focus_task"] is not None
    assert "title" in data["focus_task"]
    assert "estimated_minutes" in data["focus_task"]


@pytest.mark.anyio
async def test_focus_returns_none_when_all_done(client):
    create = await client.post("/api/goals", json={"title": "quick goal"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tasks = decomp.json()["tasks"]
    # Mark all tasks done
    for t in tasks:
        await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})

    r = await client.get(f"/api/goals/{gid}/focus")
    assert r.status_code == 200
    assert r.json()["focus_task"] is None


@pytest.mark.anyio
async def test_focus_not_found_goal(client):
    r = await client.get("/api/goals/999999/focus")
    assert r.status_code == 404


# ─── GET /api/goals/{id}/progress ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_progress_endpoint(client):
    create = await client.post("/api/goals", json={"title": "learn cooking 7"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tasks = decomp.json()["tasks"]

    r = await client.get(f"/api/goals/{gid}/progress")
    assert r.status_code == 200
    data = r.json()
    assert data["goal_id"] == gid
    assert data["total_tasks"] > 0
    assert data["completion_pct"] == 0
    assert data["estimated_remaining_minutes"] > 0

    # Mark one top-level task done
    top_level = [t for t in tasks if t["parent_id"] is None]
    await client.patch(f"/api/tasks/{top_level[0]['id']}", json={"status": "done"})

    r2 = await client.get(f"/api/goals/{gid}/progress")
    data2 = r2.json()
    assert data2["done"] >= 1
    assert data2["completion_pct"] > 0


@pytest.mark.anyio
async def test_progress_not_found_goal(client):
    r = await client.get("/api/goals/999999/progress")
    assert r.status_code == 404
