"""Tests for MEGA Enhancement Work Packages WP-07 through WP-20."""
import os
import json
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

TEST_DB = "test_mega_wp07_20.db"


@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    from teb import storage
    storage.set_db_path(TEST_DB)
    storage.init_db()
    yield
    try:
        os.remove(TEST_DB)
    except FileNotFoundError:
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _auth(c: AsyncClient, email: str = "mega@teb.test") -> dict:
    r = await c.post("/api/auth/register", json={"email": email, "password": "testpass123"})
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={"email": email, "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers.update(await _auth(c))
        yield c


@pytest_asyncio.fixture
async def client2():
    """Second user for collaboration tests."""
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c.headers.update(await _auth(c, "mega2@teb.test"))
        yield c


async def _make_goal(client, title="Test Goal"):
    r = await client.post("/api/goals", json={"title": title, "description": "desc"})
    assert r.status_code == 201
    return r.json()["id"]


async def _make_task(client, goal_id, title="Test Task", minutes=30):
    r = await client.post("/api/tasks",
                          json={"goal_id": goal_id, "title": title, "description": "d", "estimated_minutes": minutes})
    assert r.status_code == 201
    return r.json()["id"]


# ─── WP-07: Goal Cloning ─────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_clone_goal(client):
    gid = await _make_goal(client, "Original Goal")
    await _make_task(client, gid, "Task A")
    await _make_task(client, gid, "Task B")
    r = await client.post(f"/api/goals/{gid}/clone", json={"title": "Cloned Goal"})
    assert r.status_code == 200
    data = r.json()
    assert data["goal"]["title"] == "Cloned Goal"
    assert data["tasks_cloned"] == 2


@pytest.mark.anyio
async def test_clone_goal_default_title(client):
    gid = await _make_goal(client, "My Goal")
    r = await client.post(f"/api/goals/{gid}/clone",
                          content="{}", headers={"content-type": "application/json"})
    assert r.status_code == 200
    assert "(Copy)" in r.json()["goal"]["title"]


@pytest.mark.anyio
async def test_clone_goal_not_found(client):
    r = await client.post("/api/goals/99999/clone", json={})
    assert r.status_code == 404


# ─── WP-08: Time Tracking ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_log_time_entry(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/time",
                          json={"duration_minutes": 45, "note": "worked on it"})
    assert r.status_code == 200
    assert r.json()["duration_minutes"] == 45


@pytest.mark.anyio
async def test_list_time_entries(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    await client.post(f"/api/tasks/{tid}/time", json={"duration_minutes": 30})
    await client.post(f"/api/tasks/{tid}/time", json={"duration_minutes": 15})
    r = await client.get(f"/api/tasks/{tid}/time")
    assert r.status_code == 200
    data = r.json()
    assert data["total_minutes"] == 45
    assert len(data["entries"]) == 2


@pytest.mark.anyio
async def test_log_time_negative_duration(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/time", json={"duration_minutes": -5})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_log_time_task_not_found(client):
    r = await client.post("/api/tasks/99999/time", json={"duration_minutes": 10})
    assert r.status_code == 404


# ─── WP-09: Goal Activity Feed ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_goal_activity_feed(client):
    gid = await _make_goal(client, "Activity Goal")
    r = await client.get(f"/api/goals/{gid}/activity")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.anyio
async def test_goal_activity_not_found(client):
    r = await client.get("/api/goals/99999/activity")
    assert r.status_code == 404


# ─── WP-10: Task Recurrence ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_set_recurrence(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/recurrence",
                          json={"frequency": "daily", "interval": 1, "next_due": "2026-05-01"})
    assert r.status_code == 200
    assert r.json()["frequency"] == "daily"


@pytest.mark.anyio
async def test_get_recurrence(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    await client.post(f"/api/tasks/{tid}/recurrence", json={"frequency": "weekly"})
    r = await client.get(f"/api/tasks/{tid}/recurrence")
    assert r.status_code == 200
    assert r.json()["frequency"] == "weekly"


@pytest.mark.anyio
async def test_delete_recurrence(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    await client.post(f"/api/tasks/{tid}/recurrence", json={"frequency": "monthly"})
    r = await client.delete(f"/api/tasks/{tid}/recurrence")
    assert r.status_code == 200
    r2 = await client.get(f"/api/tasks/{tid}/recurrence")
    assert r2.json()["recurrence"] is None


@pytest.mark.anyio
async def test_recurrence_invalid_frequency(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/recurrence", json={"frequency": "yearly"})
    assert r.status_code == 400


# ─── WP-11: Goal Collaboration ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_add_collaborator(client, client2):
    gid = await _make_goal(client, "Collab Goal")
    # Get user2's ID
    r2 = await client2.get("/api/users/me/profile")
    user2_id = r2.json().get("user_id") or r2.json().get("id")
    if not user2_id:
        # Fallback: query by registration
        user2_id = 2  # Second registered user
    r = await client.post(f"/api/goals/{gid}/collaborators",
                          json={"user_id": user2_id, "role": "editor"})
    assert r.status_code == 200
    assert r.json()["role"] == "editor"


@pytest.mark.anyio
async def test_list_collaborators(client):
    gid = await _make_goal(client, "Collab List Goal")
    r = await client.get(f"/api/goals/{gid}/collaborators")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.anyio
async def test_add_self_as_collaborator_fails(client):
    gid = await _make_goal(client)
    r = await client.get("/api/users/me/profile")
    # Try to get user ID
    me = r.json()
    my_id = me.get("user_id") or me.get("id") or 1
    r = await client.post(f"/api/goals/{gid}/collaborators",
                          json={"user_id": my_id, "role": "viewer"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_remove_collaborator(client, client2):
    gid = await _make_goal(client, "Remove Collab")
    # Use a real user's ID (client2)
    r2 = await client2.get("/api/users/me/profile")
    user2_data = r2.json()
    user2_id = user2_data.get("user_id") or user2_data.get("id") or 2
    await client.post(f"/api/goals/{gid}/collaborators",
                      json={"user_id": user2_id, "role": "viewer"})
    r = await client.delete(f"/api/goals/{gid}/collaborators/{user2_id}")
    assert r.status_code == 200


# ─── WP-12: Custom Fields ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_add_custom_field(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/fields",
                          json={"field_name": "priority_score", "field_value": "9.5", "field_type": "number"})
    assert r.status_code == 200
    assert r.json()["field_name"] == "priority_score"


@pytest.mark.anyio
async def test_list_custom_fields(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    await client.post(f"/api/tasks/{tid}/fields", json={"field_name": "color", "field_value": "blue"})
    await client.post(f"/api/tasks/{tid}/fields", json={"field_name": "size", "field_value": "large"})
    r = await client.get(f"/api/tasks/{tid}/fields")
    assert r.status_code == 200
    assert len(r.json()) >= 2


@pytest.mark.anyio
async def test_delete_custom_field(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/fields",
                          json={"field_name": "temp", "field_value": "x"})
    fid = r.json()["id"]
    r2 = await client.delete(f"/api/fields/{fid}")
    assert r2.status_code == 200


@pytest.mark.anyio
async def test_custom_field_empty_name(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/fields",
                          json={"field_name": "", "field_value": "x"})
    assert r.status_code == 400


# ─── WP-13: Bulk Task Operations ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_bulk_update_status(client):
    gid = await _make_goal(client, "Bulk Goal")
    t1 = await _make_task(client, gid, "Bulk 1")
    t2 = await _make_task(client, gid, "Bulk 2")
    r = await client.post(f"/api/goals/{gid}/tasks/bulk",
                          json={"task_ids": [t1, t2], "operation": "update_status", "status": "done"})
    assert r.status_code == 200
    assert r.json()["affected"] == 2


@pytest.mark.anyio
async def test_bulk_delete(client):
    gid = await _make_goal(client, "Bulk Del")
    t1 = await _make_task(client, gid, "Del 1")
    t2 = await _make_task(client, gid, "Del 2")
    r = await client.post(f"/api/goals/{gid}/tasks/bulk",
                          json={"task_ids": [t1, t2], "operation": "delete"})
    assert r.status_code == 200
    assert r.json()["affected"] == 2


@pytest.mark.anyio
async def test_bulk_move(client):
    gid1 = await _make_goal(client, "Source")
    gid2 = await _make_goal(client, "Target")
    tid = await _make_task(client, gid1, "Move Me")
    r = await client.post(f"/api/goals/{gid1}/tasks/bulk",
                          json={"task_ids": [tid], "operation": "move", "target_goal_id": gid2})
    assert r.status_code == 200
    assert r.json()["affected"] == 1


@pytest.mark.anyio
async def test_bulk_invalid_operation(client):
    gid = await _make_goal(client)
    r = await client.post(f"/api/goals/{gid}/tasks/bulk",
                          json={"task_ids": [1], "operation": "fly"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_bulk_empty_task_ids(client):
    gid = await _make_goal(client)
    r = await client.post(f"/api/goals/{gid}/tasks/bulk",
                          json={"task_ids": [], "operation": "delete"})
    assert r.status_code == 400


# ─── WP-14: Goal Progress Snapshots ──────────────────────────────────────────

@pytest.mark.anyio
async def test_capture_snapshot(client):
    gid = await _make_goal(client, "Snap Goal")
    await _make_task(client, gid, "A")
    await _make_task(client, gid, "B")
    r = await client.post(f"/api/goals/{gid}/snapshots")
    assert r.status_code == 200
    data = r.json()
    assert data["total_tasks"] == 2
    assert data["completed_tasks"] == 0
    assert data["percentage"] == 0.0


@pytest.mark.anyio
async def test_list_snapshots(client):
    gid = await _make_goal(client, "Snap List")
    await _make_task(client, gid)
    await client.post(f"/api/goals/{gid}/snapshots")
    await client.post(f"/api/goals/{gid}/snapshots")
    r = await client.get(f"/api/goals/{gid}/snapshots")
    assert r.status_code == 200
    assert len(r.json()) >= 2


@pytest.mark.anyio
async def test_snapshot_not_found(client):
    r = await client.post("/api/goals/99999/snapshots")
    assert r.status_code == 404


# ─── WP-15: Task Priority Levels ─────────────────────────────────────────────

@pytest.mark.anyio
async def test_set_priority(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.put(f"/api/tasks/{tid}/priority", json={"priority": "high"})
    assert r.status_code == 200
    assert "high" in r.json()["tags"]


@pytest.mark.anyio
async def test_set_invalid_priority(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.put(f"/api/tasks/{tid}/priority", json={"priority": "extreme"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_change_priority(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    await client.put(f"/api/tasks/{tid}/priority", json={"priority": "high"})
    r = await client.put(f"/api/tasks/{tid}/priority", json={"priority": "low"})
    tags = r.json()["tags"]
    assert "low" in tags
    assert "high" not in tags


@pytest.mark.anyio
async def test_tasks_by_priority(client):
    gid = await _make_goal(client, "Priority Goal")
    t1 = await _make_task(client, gid, "Critical Task")
    t2 = await _make_task(client, gid, "Low Task")
    await client.put(f"/api/tasks/{t1}/priority", json={"priority": "critical"})
    await client.put(f"/api/tasks/{t2}/priority", json={"priority": "low"})
    r = await client.get(f"/api/goals/{gid}/tasks/by-priority")
    assert r.status_code == 200
    data = r.json()
    assert len(data["critical"]) >= 1
    assert len(data["low"]) >= 1


# ─── WP-16: Notification Preferences ─────────────────────────────────────────

@pytest.mark.anyio
async def test_set_notification_preference(client):
    r = await client.put("/api/users/me/notifications/preferences",
                         json={"channel": "email", "event_type": "task_completed", "enabled": True})
    assert r.status_code == 200
    assert r.json()["channel"] == "email"


@pytest.mark.anyio
async def test_list_notification_preferences(client):
    await client.put("/api/users/me/notifications/preferences",
                     json={"channel": "slack", "event_type": "all", "enabled": False})
    r = await client.get("/api/users/me/notifications/preferences")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


@pytest.mark.anyio
async def test_update_notification_preference(client):
    await client.put("/api/users/me/notifications/preferences",
                     json={"channel": "telegram", "event_type": "nudge", "enabled": True})
    r = await client.put("/api/users/me/notifications/preferences",
                         json={"channel": "telegram", "event_type": "nudge", "enabled": False})
    assert r.status_code == 200
    assert r.json()["enabled"] is False


# ─── WP-17: API Key Management ───────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_api_key(client):
    r = await client.post("/api/users/me/api-keys", json={"name": "my-key"})
    assert r.status_code == 200
    data = r.json()
    assert "key" in data
    assert data["key"].startswith("teb_")
    assert data["name"] == "my-key"


@pytest.mark.anyio
async def test_list_api_keys(client):
    await client.post("/api/users/me/api-keys", json={"name": "list-key"})
    r = await client.get("/api/users/me/api-keys")
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.anyio
async def test_delete_api_key(client):
    r = await client.post("/api/users/me/api-keys", json={"name": "del-key"})
    kid = r.json()["id"]
    r2 = await client.delete(f"/api/users/me/api-keys/{kid}")
    assert r2.status_code == 200


@pytest.mark.anyio
async def test_create_api_key_no_name(client):
    r = await client.post("/api/users/me/api-keys", json={"name": ""})
    assert r.status_code == 400


# ─── WP-18: Goal Export ──────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_export_markdown(client):
    gid = await _make_goal(client, "Export Goal")
    await _make_task(client, gid, "Export Task")
    r = await client.get(f"/api/goals/{gid}/export?format=markdown")
    assert r.status_code == 200
    data = r.json()
    assert data["format"] == "markdown"
    assert "Export Goal" in data["content"]
    assert "Export Task" in data["content"]


@pytest.mark.anyio
async def test_export_json(client):
    gid = await _make_goal(client, "JSON Goal")
    await _make_task(client, gid, "JSON Task")
    r = await client.get(f"/api/goals/{gid}/export?format=json")
    assert r.status_code == 200
    data = r.json()
    assert data["goal"]["title"] == "JSON Goal"
    assert len(data["tasks"]) >= 1


@pytest.mark.anyio
async def test_export_not_found(client):
    r = await client.get("/api/goals/99999/export")
    assert r.status_code == 404


# ─── WP-19: Task Blockers ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_add_blocker(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/blockers",
                          json={"description": "Waiting for API access", "blocker_type": "external"})
    assert r.status_code == 200
    assert r.json()["status"] == "open"
    assert r.json()["blocker_type"] == "external"


@pytest.mark.anyio
async def test_list_blockers(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    await client.post(f"/api/tasks/{tid}/blockers", json={"description": "Blocker 1"})
    await client.post(f"/api/tasks/{tid}/blockers", json={"description": "Blocker 2"})
    r = await client.get(f"/api/tasks/{tid}/blockers")
    assert r.status_code == 200
    assert len(r.json()) >= 2


@pytest.mark.anyio
async def test_resolve_blocker(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/blockers", json={"description": "Fix me"})
    bid = r.json()["id"]
    r2 = await client.post(f"/api/blockers/{bid}/resolve")
    assert r2.status_code == 200
    assert r2.json()["status"] == "resolved"
    assert r2.json()["resolved_at"] is not None


@pytest.mark.anyio
async def test_list_open_blockers(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r1 = await client.post(f"/api/tasks/{tid}/blockers", json={"description": "Open"})
    r2 = await client.post(f"/api/tasks/{tid}/blockers", json={"description": "Resolved"})
    await client.post(f"/api/blockers/{r2.json()['id']}/resolve")
    r = await client.get(f"/api/tasks/{tid}/blockers?status=open")
    assert r.status_code == 200
    assert all(b["status"] == "open" for b in r.json())


@pytest.mark.anyio
async def test_add_blocker_empty_description(client):
    gid = await _make_goal(client)
    tid = await _make_task(client, gid)
    r = await client.post(f"/api/tasks/{tid}/blockers", json={"description": ""})
    assert r.status_code == 400


# ─── WP-20: Dashboard Widgets ────────────────────────────────────────────────

@pytest.mark.anyio
async def test_get_default_dashboard(client):
    r = await client.get("/api/users/me/dashboard")
    assert r.status_code == 200
    data = r.json()
    assert "widgets" in data


@pytest.mark.anyio
async def test_add_widget(client):
    r = await client.post("/api/users/me/dashboard/widgets",
                          json={"widget_type": "calendar", "position": 0})
    assert r.status_code == 200
    assert r.json()["widget_type"] == "calendar"


@pytest.mark.anyio
async def test_update_widget(client):
    r = await client.post("/api/users/me/dashboard/widgets",
                          json={"widget_type": "streak", "position": 1})
    wid = r.json()["id"]
    r2 = await client.put(f"/api/users/me/dashboard/widgets/{wid}",
                          json={"position": 5, "enabled": False})
    assert r2.status_code == 200
    assert r2.json()["position"] == 5


@pytest.mark.anyio
async def test_delete_widget(client):
    r = await client.post("/api/users/me/dashboard/widgets",
                          json={"widget_type": "xp_bar", "position": 2})
    wid = r.json()["id"]
    r2 = await client.delete(f"/api/users/me/dashboard/widgets/{wid}")
    assert r2.status_code == 200


@pytest.mark.anyio
async def test_add_invalid_widget_type(client):
    r = await client.post("/api/users/me/dashboard/widgets",
                          json={"widget_type": "flying_unicorn"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_dashboard_with_custom_widgets(client):
    await client.post("/api/users/me/dashboard/widgets",
                      json={"widget_type": "activity_feed", "position": 0})
    await client.post("/api/users/me/dashboard/widgets",
                      json={"widget_type": "blockers", "position": 1})
    r = await client.get("/api/users/me/dashboard")
    assert r.status_code == 200
    assert r.json()["is_default"] is False


# ─── Storage-level tests ─────────────────────────────────────────────────────

def test_time_entry_storage():
    from teb import storage
    from teb.models import TimeEntry
    storage.init_db()
    entry = TimeEntry(task_id=1, user_id=1, duration_minutes=60, note="test")
    entry = storage.create_time_entry(entry)
    assert entry.id is not None
    total = storage.get_task_total_time(1)
    assert total >= 60


def test_recurrence_rule_storage():
    from teb import storage
    from teb.models import RecurrenceRule
    storage.init_db()
    # Need a real task for FK constraint
    from teb.models import Goal, Task
    g = storage.create_goal(Goal(title="RecGoal", description="d", user_id=1))
    t = storage.create_task(Task(goal_id=g.id, title="RecTask", description="d"))
    rule = RecurrenceRule(task_id=t.id, frequency="daily", next_due="2026-06-01")
    rule = storage.create_recurrence_rule(rule)
    assert rule.id is not None
    fetched = storage.get_recurrence_rule(t.id)
    assert fetched is not None
    assert fetched.frequency == "daily"
    storage.delete_recurrence_rule(t.id)
    assert storage.get_recurrence_rule(t.id) is None


def test_collaborator_storage():
    from teb import storage
    from teb.models import GoalCollaborator, Goal, User
    storage.init_db()
    # Ensure we have a second user for FK constraint
    try:
        from teb.auth import hash_password
        u = User(email="collab_storage_test@teb.test", password_hash=hash_password("pass"))
        u = storage.create_user(u)
        collab_uid = u.id
    except Exception:
        collab_uid = 2  # fallback
    g = storage.create_goal(Goal(title="CollabGoal", description="d", user_id=1))
    collab = GoalCollaborator(goal_id=g.id, user_id=collab_uid, role="editor")
    collab = storage.add_collaborator(collab)
    assert collab.id is not None
    collabs = storage.list_collaborators(g.id)
    found = [c for c in collabs if c.user_id == collab_uid]
    assert len(found) >= 1
    storage.remove_collaborator(g.id, collab_uid)


def test_custom_field_storage():
    from teb import storage
    from teb.models import CustomField
    storage.init_db()
    cf = CustomField(task_id=1, field_name="test_field", field_value="v1")
    cf = storage.create_custom_field(cf)
    assert cf.id is not None
    fields = storage.list_custom_fields(1)
    assert any(f.field_name == "test_field" for f in fields)
    storage.delete_custom_field(cf.id)


def test_progress_snapshot_storage():
    from teb import storage
    from teb.models import Goal
    storage.init_db()
    g = storage.create_goal(Goal(title="SnapGoal", description="d", user_id=1))
    snap = storage.capture_progress_snapshot(g.id)
    assert snap.id is not None
    snaps = storage.list_progress_snapshots(g.id)
    assert len(snaps) >= 1


def test_notification_preference_storage():
    from teb import storage
    from teb.models import NotificationPreference
    storage.init_db()
    pref = NotificationPreference(user_id=1, channel="email", event_type="all", enabled=True)
    pref = storage.set_notification_preference(pref)
    assert pref.id is not None
    # Update same pref
    pref2 = NotificationPreference(user_id=1, channel="email", event_type="all", enabled=False)
    pref2 = storage.set_notification_preference(pref2)
    assert pref2.id == pref.id


def test_api_key_storage():
    from teb import storage
    from teb.models import PersonalApiKey
    import hashlib
    storage.init_db()
    key_hash = hashlib.sha256(b"test_key_12345").hexdigest()
    key = PersonalApiKey(user_id=1, name="test", key_hash=key_hash, key_prefix="test_key")
    key = storage.create_personal_api_key(key)
    assert key.id is not None
    found = storage.get_api_key_by_hash(key_hash)
    assert found is not None
    keys = storage.list_personal_api_keys(1)
    assert len(keys) >= 1
    storage.delete_personal_api_key(key.id, 1)


def test_blocker_storage():
    from teb import storage
    from teb.models import TaskBlocker
    storage.init_db()
    b = TaskBlocker(task_id=1, description="Test blocker", blocker_type="internal")
    b = storage.create_task_blocker(b)
    assert b.id is not None
    assert b.status == "open"
    resolved = storage.resolve_task_blocker(b.id)
    assert resolved.status == "resolved"


def test_widget_storage():
    from teb import storage
    from teb.models import DashboardWidget
    storage.init_db()
    w = DashboardWidget(user_id=1, widget_type="streak", position=0)
    w = storage.create_dashboard_widget(w)
    assert w.id is not None
    widgets = storage.list_dashboard_widgets(1)
    assert len(widgets) >= 1
    updated = storage.update_dashboard_widget(w.id, 1, position=5)
    assert updated.position == 5
    storage.delete_dashboard_widget(w.id, 1)


# ─── Model to_dict() tests ───────────────────────────────────────────────────

def test_model_to_dict_coverage():
    from teb.models import (TimeEntry, RecurrenceRule, GoalCollaborator,
                            CustomField, ProgressSnapshot, NotificationPreference,
                            PersonalApiKey, TaskBlocker, DashboardWidget)
    # Verify all to_dict methods work without error
    assert "task_id" in TimeEntry(task_id=1, user_id=1).to_dict()
    assert "frequency" in RecurrenceRule(task_id=1).to_dict()
    assert "role" in GoalCollaborator(goal_id=1, user_id=1).to_dict()
    assert "field_name" in CustomField(task_id=1, field_name="x").to_dict()
    assert "percentage" in ProgressSnapshot(goal_id=1).to_dict()
    assert "channel" in NotificationPreference(user_id=1).to_dict()
    assert "name" in PersonalApiKey(user_id=1, name="k").to_dict()
    assert "blocker_type" in TaskBlocker(task_id=1, description="x").to_dict()
    assert "widget_type" in DashboardWidget(user_id=1, widget_type="streak").to_dict()
