"""API endpoint tests for teb"""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _get_auth_headers(c: AsyncClient) -> dict:
    """Register (or login if already registered) a test user and return auth headers."""
    r = await c.post("/api/auth/register", json={"email": "apitest@teb.test", "password": "testpass123"})
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={"email": "apitest@teb.test", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
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


# ─── DELETE /api/tasks/{id} ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_delete_task(client):
    create = await client.post("/api/goals", json={"title": "delete test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tasks = decomp.json()["tasks"]
    tid = tasks[0]["id"]

    r = await client.delete(f"/api/tasks/{tid}")
    assert r.status_code == 200
    assert r.json()["deleted"] == tid

    # Task should be gone
    all_tasks = await client.get(f"/api/tasks?goal_id={gid}")
    assert tid not in [t["id"] for t in all_tasks.json()]


@pytest.mark.anyio
async def test_delete_task_not_found(client):
    r = await client.delete("/api/tasks/999999")
    assert r.status_code == 404


# ─── PATCH /api/tasks/{id} — title editing ───────────────────────────────────

@pytest.mark.anyio
async def test_patch_task_title(client):
    create = await client.post("/api/goals", json={"title": "title edit test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.patch(f"/api/tasks/{tid}", json={"title": "My custom title"})
    assert r.status_code == 200
    assert r.json()["title"] == "My custom title"


@pytest.mark.anyio
async def test_patch_task_empty_title_rejected(client):
    create = await client.post("/api/goals", json={"title": "empty title test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.patch(f"/api/tasks/{tid}", json={"title": "  "})
    assert r.status_code == 422


# ─── Decompose depth limit ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_decompose_depth_limit(client):
    """Decomposing at max depth should return 422."""
    create = await client.post("/api/goals", json={"title": "depth limit test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tasks = decomp.json()["tasks"]
    # Find a top-level task without existing children
    top_level = [t for t in tasks if t["parent_id"] is None]
    tid_depth0 = top_level[0]["id"]

    # Depth 0 → 1: should work
    r1 = await client.post(f"/api/tasks/{tid_depth0}/decompose", json={})
    assert r1.status_code == 200
    sub1 = r1.json()["subtasks"][0]["id"]

    # Depth 1 → 2: should work
    r2 = await client.post(f"/api/tasks/{sub1}/decompose", json={})
    assert r2.status_code == 200
    sub2 = r2.json()["subtasks"][0]["id"]

    # Depth 2 → 3: should work
    r3 = await client.post(f"/api/tasks/{sub2}/decompose", json={})
    assert r3.status_code == 200
    sub3 = r3.json()["subtasks"][0]["id"]

    # Depth 3 → 4: should be rejected
    r4 = await client.post(f"/api/tasks/{sub3}/decompose", json={})
    assert r4.status_code == 422
    assert "depth" in r4.json()["detail"].lower()


# ─── Answer-aware decomposition (API level) ──────────────────────────────────

@pytest.mark.anyio
async def test_decompose_with_answers_adapts_tasks(client):
    """Goals with answers should produce adapted task descriptions."""
    create = await client.post("/api/goals", json={"title": "earn money online"})
    gid = create.json()["id"]

    # Answer all questions
    answers_map = {
        "technical_skills": "none",
        "income_urgency": "need money this month",
        "skill_level": "complete beginner",
        "time_per_day": "30 minutes",
        "timeline": "2 weeks",
    }
    # Get first question
    q_resp = await client.get(f"/api/goals/{gid}/next_question")
    while not q_resp.json().get("done", False):
        q_data = q_resp.json()
        # GET returns {"question": {...}}, POST returns {"next_question": {...}}
        question = q_data.get("question") or q_data.get("next_question")
        if question is None:
            break
        key = question["key"]
        answer = answers_map.get(key, "test answer")
        q_resp = await client.post(f"/api/goals/{gid}/clarify", json={"key": key, "answer": answer})

    # Decompose
    r = await client.post(f"/api/goals/{gid}/decompose", json={})
    assert r.status_code == 200
    tasks = r.json()["tasks"]
    top_level = [t for t in tasks if t["parent_id"] is None]

    # Descriptions should contain context-specific adaptations
    all_descs = " ".join(t["description"] for t in top_level)
    assert "starting out" in all_descs.lower() or "30 min" in all_descs or "tight" in all_descs.lower()


# ─── POST /api/tasks (manual creation) ───────────────────────────────────────

@pytest.mark.anyio
async def test_create_manual_task(client):
    create = await client.post("/api/goals", json={"title": "manual task test"})
    gid = create.json()["id"]
    await client.post(f"/api/goals/{gid}/decompose", json={})

    r = await client.post("/api/tasks", json={
        "goal_id": gid,
        "title": "My custom task",
        "description": "Something the decomposer missed",
        "estimated_minutes": 15,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "My custom task"
    assert data["goal_id"] == gid
    assert data["parent_id"] is None
    assert data["estimated_minutes"] == 15

    # Should appear in task list
    tasks = await client.get(f"/api/tasks?goal_id={gid}")
    titles = [t["title"] for t in tasks.json()]
    assert "My custom task" in titles


@pytest.mark.anyio
async def test_create_manual_subtask(client):
    """Can create a custom sub-task under an existing task."""
    create = await client.post("/api/goals", json={"title": "subtask creation test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    parent_id = decomp.json()["tasks"][0]["id"]

    r = await client.post("/api/tasks", json={
        "goal_id": gid,
        "title": "My custom sub-task",
        "parent_id": parent_id,
    })
    assert r.status_code == 201
    assert r.json()["parent_id"] == parent_id


@pytest.mark.anyio
async def test_create_manual_task_empty_title(client):
    create = await client.post("/api/goals", json={"title": "empty title manual"})
    gid = create.json()["id"]
    r = await client.post("/api/tasks", json={"goal_id": gid, "title": "  "})
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_manual_task_goal_not_found(client):
    r = await client.post("/api/tasks", json={"goal_id": 999999, "title": "orphan"})
    assert r.status_code == 404


@pytest.mark.anyio
async def test_create_manual_task_parent_not_found(client):
    create = await client.post("/api/goals", json={"title": "bad parent test"})
    gid = create.json()["id"]
    r = await client.post("/api/tasks", json={
        "goal_id": gid,
        "title": "child",
        "parent_id": 999999,
    })
    assert r.status_code == 404


# ─── POST /api/credentials ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_credential(client):
    r = await client.post("/api/credentials", json={
        "name": "Test API",
        "base_url": "https://api.example.com",
        "description": "A test API for testing",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["id"] is not None
    assert data["name"] == "Test API"
    assert data["base_url"] == "https://api.example.com"
    assert data["auth_value_set"] is False  # no auth_value provided


@pytest.mark.anyio
async def test_create_credential_with_auth(client):
    r = await client.post("/api/credentials", json={
        "name": "Authed API",
        "base_url": "https://api.secure.com",
        "auth_header": "X-Api-Key",
        "auth_value": "secret-key-123",
        "description": "API with auth",
    })
    assert r.status_code == 201
    data = r.json()
    assert data["auth_value_set"] is True  # has auth
    assert "secret" not in str(data)  # raw secret not exposed


@pytest.mark.anyio
async def test_create_credential_empty_name(client):
    r = await client.post("/api/credentials", json={
        "name": "  ",
        "base_url": "https://api.example.com",
    })
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_credential_empty_url(client):
    r = await client.post("/api/credentials", json={
        "name": "Test",
        "base_url": "",
    })
    assert r.status_code == 422


# ─── GET /api/credentials ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_list_credentials(client):
    await client.post("/api/credentials", json={
        "name": "List Test API",
        "base_url": "https://api.listtest.com",
    })
    r = await client.get("/api/credentials")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
    assert any(c["name"] == "List Test API" for c in r.json())


# ─── DELETE /api/credentials/{id} ────────────────────────────────────────────

@pytest.mark.anyio
async def test_delete_credential(client):
    create = await client.post("/api/credentials", json={
        "name": "Delete Me",
        "base_url": "https://api.deleteme.com",
    })
    cid = create.json()["id"]
    r = await client.delete(f"/api/credentials/{cid}")
    assert r.status_code == 200
    assert r.json()["deleted"] == cid

    # Should be gone from list
    listing = await client.get("/api/credentials")
    assert cid not in [c["id"] for c in listing.json()]


@pytest.mark.anyio
async def test_delete_credential_not_found(client):
    r = await client.delete("/api/credentials/999999")
    assert r.status_code == 404


# ─── POST /api/tasks/{id}/execute ────────────────────────────────────────────

@pytest.mark.anyio
async def test_execute_task_no_credentials(client):
    """Without AI key, execution should report can't execute."""
    # Create a fresh goal and decompose
    create = await client.post("/api/goals", json={"title": "exec test goal"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.post(f"/api/tasks/{tid}/execute")
    assert r.status_code == 200
    data = r.json()
    assert data["executed"] is False
    # Without OPENAI_API_KEY, either "No API credentials" or "AI mode is required"
    assert "No API credentials" in data["reason"] or "AI mode" in data["reason"]


@pytest.mark.anyio
async def test_execute_task_not_found(client):
    r = await client.post("/api/tasks/999999/execute")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_execute_task_already_done(client):
    create = await client.post("/api/goals", json={"title": "done exec test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]
    await client.patch(f"/api/tasks/{tid}", json={"status": "done"})

    r = await client.post(f"/api/tasks/{tid}/execute")
    assert r.status_code == 409


@pytest.mark.anyio
async def test_execute_task_with_credentials_no_ai(client):
    """With credentials but no AI key, should report need for AI."""
    create = await client.post("/api/goals", json={"title": "exec with cred"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    # Create a credential
    await client.post("/api/credentials", json={
        "name": "Exec Test API",
        "base_url": "https://api.exectest.com",
    })

    r = await client.post(f"/api/tasks/{tid}/execute")
    assert r.status_code == 200
    data = r.json()
    assert data["executed"] is False
    assert "AI mode" in data["reason"] or "OPENAI_API_KEY" in data["reason"]


# ─── GET /api/tasks/{id}/executions ──────────────────────────────────────────

@pytest.mark.anyio
async def test_get_task_executions_empty(client):
    create = await client.post("/api/goals", json={"title": "exec log test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.get(f"/api/tasks/{tid}/executions")
    assert r.status_code == 200
    data = r.json()
    assert data["task_id"] == tid
    assert data["logs"] == []


@pytest.mark.anyio
async def test_get_task_executions_not_found(client):
    r = await client.get("/api/tasks/999999/executions")
    assert r.status_code == 404


# ─── PATCH with new statuses ─────────────────────────────────────────────────

@pytest.mark.anyio
async def test_patch_task_executing_status(client):
    create = await client.post("/api/goals", json={"title": "status test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.patch(f"/api/tasks/{tid}", json={"status": "executing"})
    assert r.status_code == 200
    assert r.json()["status"] == "executing"


@pytest.mark.anyio
async def test_patch_task_failed_status(client):
    create = await client.post("/api/goals", json={"title": "fail status test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.patch(f"/api/tasks/{tid}", json={"status": "failed"})
    assert r.status_code == 200
    assert r.json()["status"] == "failed"


@pytest.mark.asyncio
async def test_patch_task_priority_happy(client):
    """Happy path: create a task, PATCH with priority 'high', GET it, assert priority."""
    create = await client.post("/api/goals", json={"title": "priority test"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.patch(f"/api/tasks/{tid}", json={"priority": "high"})
    assert r.status_code == 200
    assert r.json()["priority"] == "high"

    # GET the task to verify persistence
    tasks_resp = await client.get(f"/api/tasks?goal_id={gid}")
    assert tasks_resp.status_code == 200
    task_list = tasks_resp.json()
    task = next(t for t in task_list if str(t["id"]) == str(tid))
    assert task["priority"] == "high"


@pytest.mark.asyncio
async def test_patch_task_priority_invalid(client):
    """Error case: PATCH with invalid priority returns 422."""
    create = await client.post("/api/goals", json={"title": "priority invalid"})
    gid = create.json()["id"]
    decomp = await client.post(f"/api/goals/{gid}/decompose", json={})
    tid = decomp.json()["tasks"][0]["id"]

    r = await client.patch(f"/api/tasks/{tid}", json={"priority": "invalid"})
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_xp_endpoint_happy(client):
    """Happy path: GET /api/users/me/xp returns xp and level."""
    r = await client.get("/api/users/me/xp")
    assert r.status_code == 200
    data = r.json()
    assert "total_xp" in data
    assert "level" in data


@pytest.mark.asyncio
async def test_xp_endpoint_requires_auth():
    """Auth test: GET without token returns 401."""
    from httpx import AsyncClient, ASGITransport
    from teb.main import app
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/api/users/me/xp")
        assert r.status_code == 401


# ─── PATCH /api/goals/{goal_id} Tests ──────────────────────────────────────

@pytest.mark.asyncio
async def test_patch_goal_title_happy(client):
    """Happy path: PATCH goal title updates it."""
    create = await client.post("/api/goals", json={"title": "original title"})
    gid = create.json()["id"]

    r = await client.patch(f"/api/goals/{gid}", json={"title": "updated title"})
    assert r.status_code == 200
    assert r.json()["title"] == "updated title"


@pytest.mark.asyncio
async def test_patch_goal_empty_title_rejected(client):
    """Error case: PATCH with empty title returns 422."""
    create = await client.post("/api/goals", json={"title": "will edit"})
    gid = create.json()["id"]

    r = await client.patch(f"/api/goals/{gid}", json={"title": "   "})
    assert r.status_code == 422
