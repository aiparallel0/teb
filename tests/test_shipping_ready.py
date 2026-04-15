"""Tests for shipping-ready features: goal deletion, API endpoint fixes, and UI wiring."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _get_auth_headers(c: AsyncClient) -> dict:
    r = await c.post("/api/auth/register", json={"email": "ship@teb.test", "password": "testpass123"})
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={"email": "ship@teb.test", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
        yield c


# ─── DELETE /api/goals/{goal_id} ──────────────────────────────────────────────

@pytest.mark.anyio
async def test_delete_goal_happy_path(client):
    """Create a goal with tasks, delete it, verify everything is gone."""
    # Create goal
    r = await client.post("/api/goals", json={"title": "Goal to delete"})
    assert r.status_code == 201
    goal_id = r.json()["id"]

    # Add a task
    r = await client.post("/api/tasks", json={
        "goal_id": goal_id,
        "title": "task under doomed goal",
        "description": "will be deleted",
        "estimated_minutes": 10,
    })
    assert r.status_code == 201
    task_id = r.json()["id"]

    # Delete the goal
    r = await client.delete(f"/api/goals/{goal_id}")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True
    assert data["deleted_goal_id"] == goal_id

    # Verify goal is gone
    r = await client.get(f"/api/goals/{goal_id}")
    assert r.status_code == 404

    # Verify task is gone (goal doesn't exist anymore, so /api/tasks returns 404 for that goal)
    r = await client.get(f"/api/tasks?goal_id={goal_id}")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_delete_goal_not_found(client):
    """Deleting a non-existent goal returns 404."""
    r = await client.delete("/api/goals/999999")
    assert r.status_code == 404


@pytest.mark.anyio
async def test_delete_goal_unauthorized(client):
    """Deleting without auth returns 401."""
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        r = await c.delete("/api/goals/1")
        assert r.status_code == 401


# ─── PATCH /api/tasks/{task_id} (endpoint correctness) ────────────────────────

@pytest.mark.anyio
async def test_patch_task_with_notes_field(client):
    """Verify that the PATCH endpoint accepts 'notes' for description updates."""
    r = await client.post("/api/goals", json={"title": "Notes test goal"})
    goal_id = r.json()["id"]

    r = await client.post("/api/tasks", json={
        "goal_id": goal_id,
        "title": "test notes field",
        "description": "original",
        "estimated_minutes": 15,
    })
    assert r.status_code == 201
    task_id = r.json()["id"]

    # PATCH with 'notes' field (not 'description')
    r = await client.patch(f"/api/tasks/{task_id}", json={"notes": "updated via notes field"})
    assert r.status_code == 200
    assert r.json()["description"] == "updated via notes field"


@pytest.mark.anyio
async def test_patch_task_status_and_tags(client):
    """Verify PATCH with status and tags works correctly."""
    r = await client.post("/api/goals", json={"title": "Tag test goal"})
    goal_id = r.json()["id"]

    r = await client.post("/api/tasks", json={
        "goal_id": goal_id,
        "title": "tag test task",
        "description": "",
        "estimated_minutes": 5,
    })
    task_id = r.json()["id"]

    r = await client.patch(f"/api/tasks/{task_id}", json={
        "status": "in_progress",
        "tags": "frontend, urgent",
    })
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "in_progress"
    assert "frontend" in data.get("tags", "")


@pytest.mark.anyio
async def test_patch_task_title(client):
    """Verify PATCH with title works (inline title edit)."""
    r = await client.post("/api/goals", json={"title": "Title edit test"})
    goal_id = r.json()["id"]

    r = await client.post("/api/tasks", json={
        "goal_id": goal_id,
        "title": "old title",
        "description": "",
        "estimated_minutes": 10,
    })
    task_id = r.json()["id"]

    r = await client.patch(f"/api/tasks/{task_id}", json={"title": "new shiny title"})
    assert r.status_code == 200
    assert r.json()["title"] == "new shiny title"


@pytest.mark.anyio
async def test_patch_task_due_date(client):
    """Verify PATCH with due_date works (task detail panel save)."""
    r = await client.post("/api/goals", json={"title": "Due date test"})
    goal_id = r.json()["id"]

    r = await client.post("/api/tasks", json={
        "goal_id": goal_id,
        "title": "date task",
        "description": "",
        "estimated_minutes": 10,
    })
    task_id = r.json()["id"]

    r = await client.patch(f"/api/tasks/{task_id}", json={"due_date": "2026-12-31"})
    assert r.status_code == 200
    assert r.json()["due_date"] == "2026-12-31"


# ─── POST /api/tasks (quick-add with goal_id) ─────────────────────────────────

@pytest.mark.anyio
async def test_quick_add_task_with_goal_id(client):
    """Verify POST /api/tasks with goal_id in body works (quick-add pattern)."""
    r = await client.post("/api/goals", json={"title": "Quick add test"})
    goal_id = r.json()["id"]

    r = await client.post("/api/tasks", json={
        "goal_id": goal_id,
        "title": "quick added task",
        "description": "",
        "estimated_minutes": 30,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["title"] == "quick added task"
    assert data["goal_id"] == goal_id


# ─── DELETE /api/tasks/{task_id} (batch delete pattern) ───────────────────────

@pytest.mark.anyio
async def test_batch_delete_tasks(client):
    """Simulate batch delete: delete multiple tasks one by one."""
    r = await client.post("/api/goals", json={"title": "Batch delete test"})
    goal_id = r.json()["id"]

    task_ids = []
    for i in range(3):
        r = await client.post("/api/tasks", json={
            "goal_id": goal_id,
            "title": f"batch task {i}",
            "description": "",
            "estimated_minutes": 5,
        })
        assert r.status_code == 201
        task_ids.append(r.json()["id"])

    # Delete all three
    for tid in task_ids:
        r = await client.delete(f"/api/tasks/{tid}")
        assert r.status_code == 200

    # Verify all gone
    r = await client.get(f"/api/tasks?goal_id={goal_id}")
    assert r.status_code == 200
    assert r.json() == []


# ─── Storage: delete_goal ──────────────────────────────────────────────────────

def test_storage_delete_goal():
    """Test storage.delete_goal directly."""
    from teb import storage, auth
    from teb.models import Goal, Task

    # Create a real user to satisfy FK constraint
    try:
        user = auth.register_user("storage_del_test@teb.test", "testpass123")
    except Exception:
        user = auth.login_user("storage_del_test@teb.test", "testpass123")
    uid = user["user"]["id"]

    goal = Goal(title="storage delete test", description="test desc", user_id=uid)
    goal = storage.create_goal(goal)
    task = Task(goal_id=goal.id, title="child task", description="", estimated_minutes=5)
    storage.create_task(task)

    # Verify it exists
    assert storage.get_goal(goal.id) is not None

    # Delete
    storage.delete_goal(goal.id)

    # Verify gone
    assert storage.get_goal(goal.id) is None
    tasks = storage.list_tasks(goal_id=goal.id)
    assert tasks == []


# ─── HTML template: decompose-progress-banner exists ───────────────────────────

def test_decompose_banner_in_template():
    """Verify the decompose-progress-banner element exists in the HTML template."""
    with open("teb/templates/index.html", "r") as f:
        html = f.read()
    assert 'id="decompose-progress-banner"' in html


# ─── Frontend: no wrong API endpoints ─────────────────────────────────────────

def test_no_wrong_goals_tasks_endpoint_in_frontend():
    """Verify no frontend code calls the non-existent /api/goals/{id}/tasks/{id} endpoint."""
    with open("teb/static/app.js", "r") as f:
        js = f.read()
    # This endpoint pattern should NOT exist — the correct ones are /api/tasks/{id}
    assert "/api/goals/${currentGoalId}/tasks/" not in js, \
        "Frontend still contains wrong /api/goals/{id}/tasks/ endpoint calls"


def test_task_detail_uses_notes_not_description():
    """Verify TaskDetailPanel sends 'notes' (not 'description') to match TaskPatch model."""
    with open("teb/static/app.js", "r") as f:
        js = f.read()
    # Find the TaskDetailPanel.save() area and verify it uses 'notes'
    # The save method should have 'notes:' not 'description:' in the PATCH body
    import re
    save_match = re.search(r"await api\.patch\(`/api/tasks/\$\{taskId\}`, \{[^}]+\}", js)
    assert save_match is not None, "Could not find TaskDetailPanel.save() PATCH call"
    save_body = save_match.group(0)
    assert "notes:" in save_body, "TaskDetailPanel.save() should send 'notes' field, not 'description'"


def test_task_filter_init_called():
    """Verify TaskFilter.init() is called in the init() function."""
    with open("teb/static/app.js", "r") as f:
        js = f.read()
    assert "TaskFilter.init();" in js


def test_delete_goal_button_in_template():
    """Verify the delete goal button exists in the HTML template."""
    with open("teb/templates/index.html", "r") as f:
        html = f.read()
    assert 'id="btn-delete-goal"' in html


def test_onboarding_auto_init():
    """Verify the OnboardingTour is auto-initialized in init()."""
    with open("teb/static/app.js", "r") as f:
        js = f.read()
    # Should find OnboardingTour init after Router.init()
    router_idx = js.index("Router.init();")
    onboarding_after = js[router_idx:router_idx + 200]
    assert "OnboardingTour" in onboarding_after


def test_keyboard_shortcut_help_exists():
    """Verify the ShortcutHelp object exists in app.js."""
    with open("teb/static/app.js", "r") as f:
        js = f.read()
    assert "const ShortcutHelp" in js
    assert "Keyboard Shortcuts" in js
