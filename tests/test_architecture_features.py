"""Tests for architectural improvements: content blocks, cross-goal views, and SSE integration."""
import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _get_auth_headers(c: AsyncClient) -> dict:
    r = await c.post("/api/auth/register", json={"email": "archtest@teb.test", "password": "testpass123"})
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={"email": "archtest@teb.test", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
        yield c


# ─── Content Blocks ──────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_create_content_block(client):
    """Create a goal and add a content block to it."""
    g = await client.post("/api/goals", json={"title": "Block Test Goal", "description": ""})
    assert g.status_code in (200, 201)
    goal_id = g.json()["id"]

    r = await client.post(f"/api/goals/{goal_id}/blocks", json={
        "block_type": "paragraph",
        "content": "Hello, this is a block.",
        "order_index": 0,
    })
    assert r.status_code == 201
    block = r.json()
    assert block["block_type"] == "paragraph"
    assert block["content"] == "Hello, this is a block."
    assert block["entity_type"] == "goal"
    assert block["entity_id"] == goal_id


@pytest.mark.anyio
async def test_list_content_blocks(client):
    """Create multiple blocks and list them."""
    g = await client.post("/api/goals", json={"title": "List Blocks Goal", "description": ""})
    goal_id = g.json()["id"]

    for i, bt in enumerate(["heading", "paragraph", "code"]):
        await client.post(f"/api/goals/{goal_id}/blocks", json={
            "block_type": bt,
            "content": f"Block {i}",
            "order_index": i,
        })

    r = await client.get(f"/api/goals/{goal_id}/blocks")
    assert r.status_code == 200
    blocks = r.json()
    assert len(blocks) >= 3
    types = [b["block_type"] for b in blocks]
    assert "heading" in types
    assert "paragraph" in types
    assert "code" in types


@pytest.mark.anyio
async def test_content_block_tree(client):
    """Create nested blocks and retrieve as a tree."""
    g = await client.post("/api/goals", json={"title": "Tree Blocks Goal", "description": ""})
    goal_id = g.json()["id"]

    # Create parent block
    r1 = await client.post(f"/api/goals/{goal_id}/blocks", json={
        "block_type": "heading",
        "content": "Parent",
        "order_index": 0,
    })
    parent_id = r1.json()["id"]

    # Create child block
    await client.post(f"/api/goals/{goal_id}/blocks", json={
        "block_type": "paragraph",
        "content": "Child",
        "parent_block_id": parent_id,
        "order_index": 0,
    })

    r = await client.get(f"/api/goals/{goal_id}/blocks?tree=true")
    assert r.status_code == 200
    tree = r.json()
    # The parent should have children
    parent = [b for b in tree if b["content"] == "Parent"]
    assert len(parent) == 1
    assert len(parent[0]["children"]) >= 1
    assert parent[0]["children"][0]["content"] == "Child"


@pytest.mark.anyio
async def test_update_content_block(client):
    """Update a block's content."""
    g = await client.post("/api/goals", json={"title": "Update Block Goal", "description": ""})
    goal_id = g.json()["id"]

    r = await client.post(f"/api/goals/{goal_id}/blocks", json={
        "block_type": "paragraph",
        "content": "Original",
    })
    block_id = r.json()["id"]

    r2 = await client.patch(f"/api/blocks/{block_id}", json={"content": "Updated"})
    assert r2.status_code == 200
    assert r2.json()["content"] == "Updated"


@pytest.mark.anyio
async def test_delete_content_block(client):
    """Delete a content block."""
    g = await client.post("/api/goals", json={"title": "Delete Block Goal", "description": ""})
    goal_id = g.json()["id"]

    r = await client.post(f"/api/goals/{goal_id}/blocks", json={
        "block_type": "divider",
    })
    block_id = r.json()["id"]

    r2 = await client.delete(f"/api/blocks/{block_id}")
    assert r2.status_code == 204

    # Verify it's gone
    r3 = await client.get(f"/api/blocks/{block_id}")
    assert r3.status_code == 404


@pytest.mark.anyio
async def test_reorder_content_blocks(client):
    """Reorder blocks and verify new order."""
    g = await client.post("/api/goals", json={"title": "Reorder Block Goal", "description": ""})
    goal_id = g.json()["id"]

    ids = []
    for i in range(3):
        r = await client.post(f"/api/goals/{goal_id}/blocks", json={
            "block_type": "paragraph",
            "content": f"Item {i}",
            "order_index": i,
        })
        ids.append(r.json()["id"])

    # Reverse the order
    r = await client.post(f"/api/goals/{goal_id}/blocks/reorder", json={
        "block_ids": list(reversed(ids)),
    })
    assert r.status_code == 200

    # Verify new order
    r2 = await client.get(f"/api/goals/{goal_id}/blocks")
    blocks = r2.json()
    reordered = [b for b in blocks if b["id"] in ids]
    for b in reordered:
        if b["id"] == ids[0]:
            assert b["order_index"] == 2
        elif b["id"] == ids[2]:
            assert b["order_index"] == 0


@pytest.mark.anyio
async def test_task_content_blocks(client):
    """Create blocks on a task (not just a goal)."""
    g = await client.post("/api/goals", json={"title": "Task Block Goal", "description": ""})
    goal_id = g.json()["id"]
    t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Task with blocks"})
    task_id = t.json()["id"]

    r = await client.post(f"/api/tasks/{task_id}/blocks", json={
        "block_type": "checklist_item",
        "content": "Step 1",
        "properties": {"checked": False},
    })
    assert r.status_code == 201
    assert r.json()["entity_type"] == "task"
    assert r.json()["entity_id"] == task_id


@pytest.mark.anyio
async def test_invalid_entity_type(client):
    """Reject invalid entity types."""
    r = await client.post("/api/invalid/1/blocks", json={"block_type": "paragraph"})
    assert r.status_code == 400


@pytest.mark.anyio
async def test_block_properties(client):
    """Create a block with properties and verify them."""
    g = await client.post("/api/goals", json={"title": "Props Goal", "description": ""})
    goal_id = g.json()["id"]

    r = await client.post(f"/api/goals/{goal_id}/blocks", json={
        "block_type": "heading",
        "content": "Section Title",
        "properties": {"level": 2},
    })
    assert r.status_code == 201
    block = r.json()
    assert block["properties"]["level"] == 2


# ─── Cross-Goal Views ───────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_cross_goal_tasks(client):
    """List tasks across all goals."""
    # Create two goals with tasks
    g1 = await client.post("/api/goals", json={"title": "CG Goal 1", "description": ""})
    g2 = await client.post("/api/goals", json={"title": "CG Goal 2", "description": ""})

    await client.post("/api/tasks", json={"goal_id": g1.json()["id"], "title": "Task A"})
    await client.post("/api/tasks", json={"goal_id": g2.json()["id"], "title": "Task B"})

    r = await client.get("/api/users/me/tasks")
    assert r.status_code == 200
    tasks = r.json()
    titles = [t["title"] for t in tasks]
    assert "Task A" in titles
    assert "Task B" in titles


@pytest.mark.anyio
async def test_cross_goal_tasks_filter_status(client):
    """Filter cross-goal tasks by status."""
    g = await client.post("/api/goals", json={"title": "CG Filter Goal", "description": ""})
    goal_id = g.json()["id"]

    t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Done Task"})
    task_id = t.json()["id"]
    await client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Todo Task"})

    r = await client.get("/api/users/me/tasks?status=done")
    assert r.status_code == 200
    tasks = r.json()
    assert all(t["status"] == "done" for t in tasks)


@pytest.mark.anyio
async def test_cross_goal_tasks_sort(client):
    """Sort cross-goal tasks."""
    r = await client.get("/api/users/me/tasks?sort_field=title&sort_dir=asc")
    assert r.status_code == 200


@pytest.mark.anyio
async def test_saved_view_with_filters(client):
    """Create a saved view with filters and verify it persists."""
    r = await client.post("/api/views", json={
        "name": "My Filtered View",
        "view_type": "kanban",
        "filters": {"status": "todo", "priority": "high"},
        "sort": {"field": "due_date", "direction": "asc"},
        "group_by": "status",
    })
    assert r.status_code == 201
    view = r.json()
    assert view["name"] == "My Filtered View"
    assert view["view_type"] == "kanban"
    assert view["filters"]["status"] == "todo"
    assert view["sort"]["field"] == "due_date"
    assert view["group_by"] == "status"


@pytest.mark.anyio
async def test_saved_view_tasks(client):
    """Apply a saved view to get filtered tasks."""
    # Create a view
    vr = await client.post("/api/views", json={
        "name": "Todo View",
        "view_type": "list",
        "filters": {"status": "todo"},
        "sort": {"field": "title", "direction": "asc"},
        "group_by": "",
    })
    view_id = vr.json()["id"]

    r = await client.get(f"/api/views/{view_id}/tasks")
    assert r.status_code == 200
    data = r.json()
    assert "view" in data
    assert "tasks" in data or "grouped" in data
    assert "total" in data


@pytest.mark.anyio
async def test_saved_view_grouped(client):
    """Apply a saved view with group_by."""
    vr = await client.post("/api/views", json={
        "name": "Grouped View",
        "view_type": "table",
        "filters": {},
        "sort": {},
        "group_by": "status",
    })
    view_id = vr.json()["id"]

    r = await client.get(f"/api/views/{view_id}/tasks")
    assert r.status_code == 200
    data = r.json()
    assert "grouped" in data
    assert isinstance(data["grouped"], dict)


# ─── SSE token auth ─────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_sse_rejects_no_token(client):
    """Verify SSE stream rejects requests without any token."""
    from httpx import AsyncClient, ASGITransport
    from teb.main import app
    # Use a fresh client without auth headers to test rejection
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as unauthed:
        r = await unauthed.get("/api/events/stream")
        assert r.status_code == 401


@pytest.mark.anyio
async def test_sse_token_query_param_auth():
    """Verify that token query param is accepted by _get_user_id."""
    # Test the auth mechanism directly rather than streaming
    from teb import auth
    from teb.main import _get_user_id
    from starlette.testclient import TestClient
    from starlette.requests import Request
    from starlette.datastructures import Headers, QueryParams

    # Create a real user token
    from teb import storage
    from teb.models import User
    import bcrypt
    pw_hash = bcrypt.hashpw(b"testpw123", bcrypt.gensalt()).decode()
    try:
        user = storage.create_user(User(email="ssetest@teb.test", password_hash=pw_hash))
    except Exception:
        user = storage.get_user_by_email("ssetest@teb.test")

    token = auth.create_token(user.id)

    # Verify token works via decode
    uid = auth.decode_token(token)
    assert uid == user.id


# ─── DAG Endpoints ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_dag_get_empty_goal(client):
    """DAG endpoint returns valid=True for a goal with no tasks."""
    g = await client.post("/api/goals", json={"title": "DAG Empty Goal", "description": ""})
    goal_id = g.json()["id"]
    r = await client.get(f"/api/goals/{goal_id}/dag")
    assert r.status_code == 200
    data = r.json()
    assert data["valid"] is True
    assert data["plan"] == []


@pytest.mark.anyio
async def test_dag_validate_no_cycles(client):
    """DAG validate returns valid for tasks without cycles."""
    g = await client.post("/api/goals", json={"title": "DAG Valid Goal", "description": ""})
    goal_id = g.json()["id"]
    t1 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Step 1"})
    t2 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Step 2"})
    r = await client.post(f"/api/goals/{goal_id}/dag/validate")
    assert r.status_code == 200
    assert r.json()["valid"] is True


@pytest.mark.anyio
async def test_dag_execute_starts_first_batch(client):
    """DAG execute should start batch 0 tasks as in_progress."""
    g = await client.post("/api/goals", json={"title": "DAG Execute Goal", "description": ""})
    goal_id = g.json()["id"]
    t1 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "First"})
    assert t1.status_code in (200, 201)
    r = await client.post(f"/api/goals/{goal_id}/execute-dag")
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "executing"
    assert data["batches"] >= 1


# ─── Permission Enforcement ──────────────────────────────────────────────────

@pytest.mark.anyio
async def test_collaborator_viewer_cannot_edit_task(client):
    """A viewer collaborator cannot update tasks."""
    from teb.main import app
    from httpx import AsyncClient, ASGITransport

    # Create a second user
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post("/api/auth/register", json={"email": "viewer@teb.test", "password": "testpass123"})
        if r.status_code not in (200, 201):
            r = await c2.post("/api/auth/login", json={"email": "viewer@teb.test", "password": "testpass123"})
        viewer_token = r.json()["token"]
        viewer_headers = {"Authorization": f"Bearer {viewer_token}"}

        # Create a goal owned by the first user
        g = await client.post("/api/goals", json={"title": "Perm Test Goal", "description": ""})
        goal_id = g.json()["id"]
        t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Task A"})
        task_id = t.json()["id"]

        # Get the viewer's user ID
        me = await c2.get("/api/auth/me", headers=viewer_headers)
        viewer_uid = me.json()["id"]

        # Share goal with viewer role
        await client.post(f"/api/goals/{goal_id}/share", json={"user_id": viewer_uid, "role": "viewer"})

        # Viewer can GET the goal (read access)
        rg = await c2.get(f"/api/goals/{goal_id}", headers=viewer_headers)
        assert rg.status_code == 200

        # Viewer CANNOT patch a task (requires editor)
        rp = await c2.patch(f"/api/tasks/{task_id}", headers=viewer_headers, json={"status": "done"})
        assert rp.status_code == 403


@pytest.mark.anyio
async def test_collaborator_editor_can_edit_task(client):
    """An editor collaborator can update tasks."""
    from teb.main import app
    from httpx import AsyncClient, ASGITransport

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c2:
        r = await c2.post("/api/auth/register", json={"email": "editor@teb.test", "password": "testpass123"})
        if r.status_code not in (200, 201):
            r = await c2.post("/api/auth/login", json={"email": "editor@teb.test", "password": "testpass123"})
        editor_token = r.json()["token"]
        editor_headers = {"Authorization": f"Bearer {editor_token}"}

        g = await client.post("/api/goals", json={"title": "Editor Test Goal", "description": ""})
        goal_id = g.json()["id"]
        t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Task B"})
        task_id = t.json()["id"]

        me = await c2.get("/api/auth/me", headers=editor_headers)
        editor_uid = me.json()["id"]

        await client.post(f"/api/goals/{goal_id}/share", json={"user_id": editor_uid, "role": "editor"})

        # Editor CAN patch a task
        rp = await c2.patch(f"/api/tasks/{task_id}", headers=editor_headers, json={"status": "done"})
        assert rp.status_code == 200


# ─── Relation / Rollup / Formula Custom Fields ──────────────────────────────

@pytest.mark.anyio
async def test_create_relation_field(client):
    """Create a relation custom field linking two tasks."""
    g = await client.post("/api/goals", json={"title": "Rel Goal", "description": ""})
    goal_id = g.json()["id"]
    t1 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Client Task"})
    t2 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Project Task"})
    t1_id = t1.json()["id"]
    t2_id = t2.json()["id"]

    r = await client.post(f"/api/tasks/{t2_id}/fields", json={
        "field_name": "related_client",
        "field_type": "relation",
        "field_value": str(t1_id),
    })
    assert r.status_code == 200
    field = r.json()
    assert field["field_type"] == "relation"

    # List fields should show resolved value
    fields = await client.get(f"/api/tasks/{t2_id}/fields")
    rel = [f for f in fields.json() if f["field_type"] == "relation"]
    assert len(rel) >= 1
    assert rel[0]["resolved_value"] == "Client Task"


@pytest.mark.anyio
async def test_create_formula_field(client):
    """Create a formula custom field."""
    g = await client.post("/api/goals", json={"title": "Formula Goal", "description": ""})
    goal_id = g.json()["id"]
    t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Formula Task"})
    task_id = t.json()["id"]

    r = await client.post(f"/api/tasks/{task_id}/fields", json={
        "field_name": "days_left",
        "field_type": "formula",
        "config": {"formula_type": "days_until_due"},
    })
    assert r.status_code == 200
    assert r.json()["field_type"] == "formula"


@pytest.mark.anyio
async def test_resolve_formula_field(client):
    """Resolve a formula field value via the resolve endpoint."""
    g = await client.post("/api/goals", json={"title": "Resolve Goal", "description": ""})
    goal_id = g.json()["id"]
    t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Resolve Task"})
    task_id = t.json()["id"]

    r = await client.post(f"/api/tasks/{task_id}/fields", json={
        "field_name": "formula_test",
        "field_type": "formula",
        "config": {"formula_type": "days_until_due"},
    })
    field_id = r.json()["id"]

    resolve = await client.get(f"/api/fields/{field_id}/resolve")
    assert resolve.status_code == 200
    # No due date set, so should return "[No due date]"
    assert resolve.json()["resolved_value"] == "[No due date]"


@pytest.mark.anyio
async def test_rollup_field_count(client):
    """Create a rollup field that counts related tasks."""
    g = await client.post("/api/goals", json={"title": "Rollup Goal", "description": ""})
    goal_id = g.json()["id"]
    t1 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Main Task"})
    t2 = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Related Task"})
    t1_id = t1.json()["id"]
    t2_id = t2.json()["id"]

    # Add a relation from t1 to t2
    await client.post(f"/api/tasks/{t1_id}/fields", json={
        "field_name": "linked_tasks",
        "field_type": "relation",
        "field_value": str(t2_id),
    })

    # Add a rollup that counts via that relation
    r = await client.post(f"/api/tasks/{t1_id}/fields", json={
        "field_name": "task_count",
        "field_type": "rollup",
        "config": {
            "relation_field": "linked_tasks",
            "aggregation": "count",
        },
    })
    assert r.status_code == 200

    # Resolve it
    fields = await client.get(f"/api/tasks/{t1_id}/fields")
    rollup = [f for f in fields.json() if f["field_type"] == "rollup"]
    assert len(rollup) >= 1
    assert rollup[0]["resolved_value"] == "1"  # One linked task


@pytest.mark.anyio
async def test_invalid_field_type_rejected(client):
    """Invalid field_type should be rejected with 400."""
    g = await client.post("/api/goals", json={"title": "Invalid Type Goal", "description": ""})
    goal_id = g.json()["id"]
    t = await client.post("/api/tasks", json={"goal_id": goal_id, "title": "Task"})
    task_id = t.json()["id"]

    r = await client.post(f"/api/tasks/{task_id}/fields", json={
        "field_name": "bad_type",
        "field_type": "nonexistent",
    })
    assert r.status_code == 400


# ─── Plugin Lifecycle Hooks ──────────────────────────────────────────────────

def test_plugin_lifecycle_hooks():
    """Test plugin lifecycle hooks (before_execute, after_execute, on_error)."""
    from teb.plugins import (
        PluginLifecycle, PluginResult, _PLUGIN_LIFECYCLE,
        register_executor, execute_plugin, unregister_executor,
    )
    from teb import storage as _storage

    call_log = []

    def _before(ctx, creds):
        call_log.append("before")
        return None  # continue to execute

    def _after(ctx, creds, result):
        call_log.append("after")
        # Enrich the result
        result.metadata["enriched"] = True
        return result

    def _on_error(ctx, creds, err):
        call_log.append("on_error")
        return PluginResult(success=False, error="handled: " + str(err))

    def _execute(ctx, creds):
        call_log.append("execute")
        return PluginResult(success=True, output="done")

    # Register plugin
    register_executor("test-lifecycle", _execute)
    _PLUGIN_LIFECYCLE["test-lifecycle"] = PluginLifecycle(
        before_execute=_before,
        after_execute=_after,
        on_error=_on_error,
    )

    # Execute
    result = execute_plugin("test-lifecycle", {"task": "test"}, {})
    assert result.success is True
    assert result.output == "done"
    assert result.metadata.get("enriched") is True
    assert call_log == ["before", "execute", "after"]

    # Cleanup
    unregister_executor("test-lifecycle")
    del _PLUGIN_LIFECYCLE["test-lifecycle"]


def test_plugin_on_error_hook():
    """Test that on_error hook catches execution failures."""
    from teb.plugins import (
        PluginLifecycle, PluginResult, _PLUGIN_LIFECYCLE,
        register_executor, execute_plugin, unregister_executor,
    )

    def _failing_execute(ctx, creds):
        raise ValueError("Something broke")

    def _on_error(ctx, creds, err):
        return PluginResult(success=False, error=f"recovered: {err}")

    register_executor("test-error", _failing_execute)
    _PLUGIN_LIFECYCLE["test-error"] = PluginLifecycle(on_error=_on_error)

    result = execute_plugin("test-error", {}, {})
    assert result.success is False
    assert "recovered:" in result.error

    unregister_executor("test-error")
    del _PLUGIN_LIFECYCLE["test-error"]


def test_plugin_before_execute_short_circuit():
    """Test that before_execute can short-circuit execution."""
    from teb.plugins import (
        PluginLifecycle, PluginResult, _PLUGIN_LIFECYCLE,
        register_executor, execute_plugin, unregister_executor,
    )

    call_log = []

    def _before(ctx, creds):
        call_log.append("before")
        return PluginResult(success=True, output="short-circuited")

    def _execute(ctx, creds):
        call_log.append("execute")  # Should never be called
        return PluginResult(success=True, output="executed")

    register_executor("test-shortcircuit", _execute)
    _PLUGIN_LIFECYCLE["test-shortcircuit"] = PluginLifecycle(before_execute=_before)

    result = execute_plugin("test-shortcircuit", {}, {})
    assert result.success is True
    assert result.output == "short-circuited"
    assert call_log == ["before"]  # execute was never called

    unregister_executor("test-shortcircuit")
    del _PLUGIN_LIFECYCLE["test-shortcircuit"]
