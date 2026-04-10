"""Tests for all 8 bridging plan features:
- Step 1: Execution Plugin System
- Step 2: Persistent Agent Goal Memory
- Step 3: Goal Hierarchy (Sub-goals & Milestones)
- Step 4: Real-time Event Streaming (SSE)
- Step 5: Goal Template Marketplace
- Step 6: Structured Execution Audit Trail
- Step 7: MCP Server Exposure
- Step 8: Execution Sandbox Isolation
"""

from __future__ import annotations

import asyncio
import json
import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from teb import storage
from teb.models import (
    AgentGoalMemory, AuditEvent, Goal, GoalTemplate, Milestone,
    PluginManifest, Task,
)

TEST_DB = "test_bridging_plan.db"


# ─── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    """Point storage at a separate test database."""
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


async def _get_auth_headers(c: AsyncClient, email: str = "bridgeplan@teb.test") -> dict:
    r = await c.post("/api/auth/register", json={
        "email": email, "password": "testpass123"
    })
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={
            "email": email, "password": "testpass123"
        })
    return {"Authorization": f"Bearer {r.json()['token']}"}


async def _get_admin_headers(c: AsyncClient) -> dict:
    headers = await _get_auth_headers(c, email="admin_bridge@teb.test")
    # Promote to admin
    from teb.auth import decode_token
    token = headers["Authorization"].split(" ")[1]
    uid = decode_token(token)
    user = storage.get_user(uid)
    if user:
        user.role = "admin"
        storage.update_user(user)
    return headers


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
        yield c


@pytest_asyncio.fixture
async def admin_client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_admin_headers(c)
        c.headers.update(headers)
        yield c


async def _create_goal(client: AsyncClient, title: str, desc: str = "") -> int:
    resp = await client.post("/api/goals", json={"title": title, "description": desc})
    assert resp.status_code == 201
    return resp.json()["id"]


async def _create_task(client: AsyncClient, goal_id: int, title: str) -> int:
    resp = await client.post("/api/tasks", json={
        "goal_id": goal_id, "title": title, "description": "test task"
    })
    assert resp.status_code == 201
    return resp.json()["id"]


# ═══════════════════════════════════════════════════════════════════════════════
# Step 2: Persistent Agent Goal Memory
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_agent_goal_memory_crud(client):
    """Test creating, retrieving, and updating agent goal memory."""
    goal_id = await _create_goal(client, "Memory test goal")

    # Get/create memory (should auto-create)
    resp = await client.get(f"/api/goals/{goal_id}/agent-memory/coordinator")
    assert resp.status_code == 200
    mem = resp.json()
    assert mem["agent_type"] == "coordinator"
    assert mem["goal_id"] == goal_id
    assert mem["invocation_count"] == 0

    # List all agent memories for goal
    resp = await client.get(f"/api/goals/{goal_id}/agent-memory")
    assert resp.status_code == 200
    assert len(resp.json()["memories"]) >= 1


@pytest.mark.anyio
async def test_agent_goal_memory_storage_update():
    """Test storage-level memory update with invocation count."""
    goal = storage.create_goal(Goal(title="Memory storage test", description=""))
    mem = storage.get_or_create_agent_goal_memory("marketing", goal.id)
    assert mem.invocation_count == 0

    mem.context_json = json.dumps({"learned": "SEO is important"})
    mem.summary = "Learned about SEO importance"
    mem = storage.update_agent_goal_memory(mem)
    assert mem.invocation_count == 1

    # Retrieve again
    mem2 = storage.get_or_create_agent_goal_memory("marketing", goal.id)
    assert mem2.invocation_count == 1
    assert "SEO" in mem2.context_json


@pytest.mark.anyio
async def test_agent_goal_memory_prune(client):
    """Test pruning overly long agent memories produces valid JSON."""
    goal_id = await _create_goal(client, "Prune test goal")
    mem = storage.get_or_create_agent_goal_memory("web_dev", goal_id)
    # Create a large dict context
    large_ctx = {f"key_{i}": f"value_{i}" * 100 for i in range(100)}
    mem.context_json = json.dumps(large_ctx)
    storage.update_agent_goal_memory(mem)

    resp = await client.post(f"/api/goals/{goal_id}/agent-memory/prune")
    assert resp.status_code == 200
    assert resp.json()["pruned"] is True

    # Check it was trimmed and is still valid JSON
    mem2 = storage.get_or_create_agent_goal_memory("web_dev", goal_id)
    assert len(mem2.context_json) <= 8000
    parsed = json.loads(mem2.context_json)  # Should not raise
    assert isinstance(parsed, dict)


@pytest.mark.anyio
async def test_agent_goal_memory_multiple_agents():
    """Test that different agents have separate memories for the same goal."""
    goal = storage.create_goal(Goal(title="Multi-agent memory", description=""))
    mem_coord = storage.get_or_create_agent_goal_memory("coordinator", goal.id)
    mem_mktg = storage.get_or_create_agent_goal_memory("marketing", goal.id)

    mem_coord.context_json = json.dumps({"strategy": "content marketing"})
    storage.update_agent_goal_memory(mem_coord)

    mem_mktg.context_json = json.dumps({"campaigns": ["blog", "social"]})
    storage.update_agent_goal_memory(mem_mktg)

    all_mems = storage.list_agent_goal_memories(goal.id)
    assert len(all_mems) == 2
    types = {m.agent_type for m in all_mems}
    assert "coordinator" in types
    assert "marketing" in types


# ═══════════════════════════════════════════════════════════════════════════════
# Step 3: Goal Hierarchy (Sub-goals & Milestones)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_create_sub_goal(client):
    """Test creating sub-goals under a parent goal."""
    parent_id = await _create_goal(client, "Parent goal: build a business")
    resp = await client.post(f"/api/goals/{parent_id}/sub-goals", json={
        "title": "Sub-goal: build website",
        "description": "Create the company website",
    })
    assert resp.status_code == 201
    sub = resp.json()
    assert sub["parent_goal_id"] == parent_id
    assert sub["title"] == "Sub-goal: build website"


@pytest.mark.anyio
async def test_list_sub_goals(client):
    """Test listing sub-goals for a parent."""
    parent_id = await _create_goal(client, "Parent for listing subs")
    await client.post(f"/api/goals/{parent_id}/sub-goals", json={"title": "Sub 1"})
    await client.post(f"/api/goals/{parent_id}/sub-goals", json={"title": "Sub 2"})

    resp = await client.get(f"/api/goals/{parent_id}/sub-goals")
    assert resp.status_code == 200
    subs = resp.json()["sub_goals"]
    assert len(subs) >= 2


@pytest.mark.anyio
async def test_goal_hierarchy(client):
    """Test full goal hierarchy endpoint."""
    parent_id = await _create_goal(client, "Hierarchy parent")
    await client.post(f"/api/goals/{parent_id}/sub-goals", json={"title": "Sub A"})
    await client.post(f"/api/goals/{parent_id}/milestones", json={
        "title": "First revenue", "target_metric": "revenue", "target_value": 100
    })

    resp = await client.get(f"/api/goals/{parent_id}/hierarchy")
    assert resp.status_code == 200
    h = resp.json()
    assert h["goal"]["id"] == parent_id
    assert len(h["sub_goals"]) >= 1
    assert len(h["milestones"]) >= 1


@pytest.mark.anyio
async def test_milestone_crud(client):
    """Test creating, listing, and updating milestones."""
    goal_id = await _create_goal(client, "Milestone test goal")

    # Create
    resp = await client.post(f"/api/goals/{goal_id}/milestones", json={
        "title": "Launch MVP",
        "target_metric": "launch",
        "target_value": 1,
        "deadline": "2026-06-01",
    })
    assert resp.status_code == 201
    ms = resp.json()
    ms_id = ms["id"]
    assert ms["status"] == "pending"

    # List
    resp = await client.get(f"/api/goals/{goal_id}/milestones")
    assert resp.status_code == 200
    assert len(resp.json()["milestones"]) >= 1

    # Update — not yet achieved
    resp = await client.patch(f"/api/milestones/{ms_id}", json={
        "current_value": 0.5,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "pending"

    # Update — achieved!
    resp = await client.patch(f"/api/milestones/{ms_id}", json={
        "current_value": 1.0,
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "achieved"


@pytest.mark.anyio
async def test_milestone_not_found(client):
    """Test updating a non-existent milestone returns 404."""
    resp = await client.patch("/api/milestones/99999", json={"title": "nope"})
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_parent_goal_id_in_goal_model():
    """Test that Goal model properly stores parent_goal_id."""
    parent = storage.create_goal(Goal(title="Parent", description=""))
    child = Goal(title="Child", description="", parent_goal_id=parent.id)
    child = storage.create_goal(child)
    assert child.parent_goal_id == parent.id

    retrieved = storage.get_goal(child.id)
    assert retrieved.parent_goal_id == parent.id


# ═══════════════════════════════════════════════════════════════════════════════
# Step 4: Real-time Event Streaming (SSE)
# ═══════════════════════════════════════════════════════════════════════════════


def test_event_bus_publish_subscribe():
    """Test the event bus pub/sub mechanics."""
    from teb.events import EventBus

    bus = EventBus()
    queue = bus.subscribe(user_id=1)

    bus.publish(1, "test_event", {"hello": "world"})

    assert not queue.empty()
    event = queue.get_nowait()
    assert event.event_type == "test_event"
    assert event.data["hello"] == "world"
    assert event.id == "1"


def test_event_bus_backlog():
    """Test that events are stored in backlog for reconnection."""
    from teb.events import EventBus

    bus = EventBus()
    e1 = bus.publish(1, "a", {})
    e2 = bus.publish(1, "b", {})
    e3 = bus.publish(1, "c", {})

    backlog = bus.get_backlog_since(e1.id)
    assert len(backlog) == 2
    assert backlog[0].event_type == "b"
    assert backlog[1].event_type == "c"


def test_event_bus_broadcast():
    """Test broadcast to all subscribers."""
    from teb.events import EventBus

    bus = EventBus()
    q1 = bus.subscribe(1)
    q2 = bus.subscribe(2)

    bus.publish_broadcast("global_event", {"msg": "hi"})

    assert not q1.empty()
    assert not q2.empty()


def test_event_bus_unsubscribe():
    """Test unsubscribing removes the queue."""
    from teb.events import EventBus

    bus = EventBus()
    q = bus.subscribe(1)
    assert bus.subscriber_count == 1
    bus.unsubscribe(1, q)
    assert bus.subscriber_count == 0


def test_sse_event_serialize():
    """Test SSE event serialization format."""
    from teb.events import SSEEvent

    event = SSEEvent(event_type="task_completed", data={"task_id": 42}, id="5")
    serialized = event.serialize()
    assert "id: 5" in serialized
    assert "event: task_completed" in serialized
    assert '"task_id": 42' in serialized


def test_convenience_emitters():
    """Test that convenience emitters publish to the bus."""
    from teb.events import event_bus, emit_task_completed

    q = event_bus.subscribe(999)
    emit_task_completed(999, task_id=1, task_title="Test", goal_id=1)
    assert not q.empty()
    event = q.get_nowait()
    assert event.event_type == "task_completed"
    event_bus.unsubscribe(999, q)


@pytest.mark.anyio
async def test_sse_endpoint_requires_auth():
    """Test that SSE endpoint requires authentication."""
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        resp = await c.get("/api/events/stream")
        assert resp.status_code == 401


@pytest.mark.anyio
async def test_sse_status_endpoint(client):
    """Test SSE status endpoint."""
    resp = await client.get("/api/events/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "subscribers" in data
    assert "backlog_size" in data


# ═══════════════════════════════════════════════════════════════════════════════
# Step 5: Goal Template Marketplace
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_export_goal_as_template(client):
    """Test exporting a goal as a reusable template."""
    goal_id = await _create_goal(client, "Template source goal: earn money freelancing")
    await _create_task(client, goal_id, "Set up portfolio")
    await _create_task(client, goal_id, "Find clients on Upwork")

    resp = await client.post(f"/api/templates/export/{goal_id}", json={
        "category": "freelancing",
        "skill_level": "beginner",
    })
    assert resp.status_code == 201
    tpl = resp.json()
    assert tpl["source_goal_id"] == goal_id
    assert tpl["category"] == "freelancing"
    tasks = json.loads(tpl["tasks_json"])
    assert len(tasks) >= 2


@pytest.mark.anyio
async def test_import_template_creates_goal(client):
    """Test importing a template creates a new goal with tasks."""
    # First export
    goal_id = await _create_goal(client, "Import source goal")
    await _create_task(client, goal_id, "Task A")
    await _create_task(client, goal_id, "Task B")
    resp = await client.post(f"/api/templates/export/{goal_id}", json={})
    tpl_id = resp.json()["id"]

    # Then import
    resp = await client.post(f"/api/templates/import/{tpl_id}")
    assert resp.status_code == 201
    data = resp.json()
    new_goal_id = data["goal"]["id"]
    assert new_goal_id != goal_id
    assert data["template_id"] == tpl_id

    # Verify tasks were created
    resp = await client.get(f"/api/tasks?goal_id={new_goal_id}")
    assert resp.status_code == 200
    tasks = resp.json()
    assert len(tasks) >= 2


@pytest.mark.anyio
async def test_list_templates(client):
    """Test browsing the template marketplace."""
    resp = await client.get("/api/templates")
    assert resp.status_code == 200
    data = resp.json()
    assert "templates" in data
    assert "total" in data


@pytest.mark.anyio
async def test_get_template_detail(client):
    """Test getting details of a specific template."""
    goal_id = await _create_goal(client, "Detail template source")
    resp = await client.post(f"/api/templates/export/{goal_id}", json={})
    tpl_id = resp.json()["id"]

    resp = await client.get(f"/api/templates/{tpl_id}")
    assert resp.status_code == 200
    assert resp.json()["id"] == tpl_id


@pytest.mark.anyio
async def test_rate_template(client):
    """Test rating a template."""
    goal_id = await _create_goal(client, "Rate template source")
    resp = await client.post(f"/api/templates/export/{goal_id}", json={})
    tpl_id = resp.json()["id"]

    resp = await client.post(f"/api/templates/{tpl_id}/rate", json={"rating": 5})
    assert resp.status_code == 200
    assert resp.json()["rating_count"] == 1
    assert resp.json()["rating"] > 0

    # Rate again
    resp = await client.post(f"/api/templates/{tpl_id}/rate", json={"rating": 3})
    assert resp.status_code == 200
    assert resp.json()["rating_count"] == 2


@pytest.mark.anyio
async def test_rate_template_invalid(client):
    """Test rating with invalid value."""
    goal_id = await _create_goal(client, "Rate invalid template")
    resp = await client.post(f"/api/templates/export/{goal_id}", json={})
    tpl_id = resp.json()["id"]

    resp = await client.post(f"/api/templates/{tpl_id}/rate", json={"rating": 0})
    assert resp.status_code == 400


@pytest.mark.anyio
async def test_import_nonexistent_template(client):
    """Test importing a template that doesn't exist."""
    resp = await client.post("/api/templates/import/99999")
    assert resp.status_code == 404


@pytest.mark.anyio
async def test_template_with_milestones(client):
    """Test that milestones are included in exported templates."""
    goal_id = await _create_goal(client, "Template with milestones")
    await client.post(f"/api/goals/{goal_id}/milestones", json={
        "title": "First sale", "target_metric": "sales", "target_value": 1,
    })

    resp = await client.post(f"/api/templates/export/{goal_id}", json={})
    assert resp.status_code == 201
    ms = json.loads(resp.json()["milestones_json"])
    assert len(ms) >= 1
    assert ms[0]["title"] == "First sale"


# ═══════════════════════════════════════════════════════════════════════════════
# Step 6: Structured Execution Audit Trail
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_audit_events_created_on_actions(client):
    """Test that audit events are automatically created."""
    goal_id = await _create_goal(client, "Audit trail test goal")

    # Create a sub-goal → should create audit event
    await client.post(f"/api/goals/{goal_id}/sub-goals", json={"title": "Audited sub-goal"})

    resp = await client.get(f"/api/goals/{goal_id}/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert any(e["event_type"] == "sub_goal_created" for e in events)


@pytest.mark.anyio
async def test_audit_events_for_milestones(client):
    """Test audit events for milestone creation."""
    goal_id = await _create_goal(client, "Milestone audit test")
    await client.post(f"/api/goals/{goal_id}/milestones", json={
        "title": "Revenue milestone", "target_metric": "revenue", "target_value": 1000,
    })

    resp = await client.get(f"/api/goals/{goal_id}/audit")
    assert resp.status_code == 200
    events = resp.json()["events"]
    types = [e["event_type"] for e in events]
    assert "milestone_created" in types


@pytest.mark.anyio
async def test_audit_events_filtered_by_type(client):
    """Test filtering audit events by event_type."""
    goal_id = await _create_goal(client, "Filtered audit test")
    await client.post(f"/api/goals/{goal_id}/sub-goals", json={"title": "Sub for filter"})
    await client.post(f"/api/goals/{goal_id}/milestones", json={"title": "MS for filter"})

    resp = await client.get(f"/api/goals/{goal_id}/audit?event_type=sub_goal_created")
    assert resp.status_code == 200
    events = resp.json()["events"]
    assert all(e["event_type"] == "sub_goal_created" for e in events)


@pytest.mark.anyio
async def test_audit_event_immutability():
    """Test that audit events are append-only at the storage level."""
    goal = storage.create_goal(Goal(title="Immutability test", description=""))
    event = storage.create_audit_event(AuditEvent(
        goal_id=goal.id, event_type="test_event",
        actor_type="system", actor_id="test",
        context_json=json.dumps({"key": "value"}),
    ))
    assert event.id is not None
    # No update function exists — that's the design (append-only)
    events = storage.list_audit_events(goal_id=goal.id)
    assert len(events) >= 1
    assert events[0].context_json == json.dumps({"key": "value"})


@pytest.mark.anyio
async def test_admin_audit_events(admin_client):
    """Test admin can list all audit events."""
    resp = await admin_client.get("/api/audit/events")
    assert resp.status_code == 200
    assert "events" in resp.json()


@pytest.mark.anyio
async def test_audit_events_for_template_export(client):
    """Test that template export creates an audit event."""
    goal_id = await _create_goal(client, "Audit export test")
    await client.post(f"/api/templates/export/{goal_id}", json={})

    resp = await client.get(f"/api/goals/{goal_id}/audit")
    events = resp.json()["events"]
    assert any(e["event_type"] == "template_exported" for e in events)


# ═══════════════════════════════════════════════════════════════════════════════
# Step 7: MCP Server Exposure
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_mcp_server_info(client):
    """Test MCP server info endpoint."""
    resp = await client.get("/api/mcp/info")
    assert resp.status_code == 200
    info = resp.json()
    assert info["name"] == "teb"
    assert len(info["tools"]) >= 5


@pytest.mark.anyio
async def test_mcp_list_tools(client):
    """Test listing MCP tools."""
    resp = await client.get("/api/mcp/tools")
    assert resp.status_code == 200
    tools = resp.json()["tools"]
    names = [t["name"] for t in tools]
    assert "create_goal" in names
    assert "list_goals" in names
    assert "get_goal_status" in names
    assert "complete_task" in names


@pytest.mark.anyio
async def test_mcp_create_goal(client):
    """Test MCP create_goal tool."""
    resp = await client.post("/api/mcp/tools/call", json={
        "name": "create_goal",
        "arguments": {"title": "MCP-created goal", "description": "via AI assistant"},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] is not None
    assert "MCP-created goal" in data["title"]


@pytest.mark.anyio
async def test_mcp_list_goals(client):
    """Test MCP list_goals tool."""
    # Create a goal first
    await client.post("/api/mcp/tools/call", json={
        "name": "create_goal",
        "arguments": {"title": "MCP list test"},
    })

    resp = await client.post("/api/mcp/tools/call", json={
        "name": "list_goals",
        "arguments": {},
    })
    assert resp.status_code == 200
    assert resp.json()["total"] > 0


@pytest.mark.anyio
async def test_mcp_get_goal_status(client):
    """Test MCP get_goal_status tool."""
    goal_id = await _create_goal(client, "MCP status test")
    await _create_task(client, goal_id, "Some task")

    resp = await client.post("/api/mcp/tools/call", json={
        "name": "get_goal_status",
        "arguments": {"goal_id": goal_id},
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["tasks"]["total"] >= 1


@pytest.mark.anyio
async def test_mcp_complete_task(client):
    """Test MCP complete_task tool."""
    goal_id = await _create_goal(client, "MCP complete test")
    task_id = await _create_task(client, goal_id, "Task to complete via MCP")

    resp = await client.post("/api/mcp/tools/call", json={
        "name": "complete_task",
        "arguments": {"task_id": task_id},
    })
    assert resp.status_code == 200
    assert resp.json()["status"] == "done"


@pytest.mark.anyio
async def test_mcp_list_milestones(client):
    """Test MCP list_milestones tool."""
    goal_id = await _create_goal(client, "MCP milestones test")
    await client.post(f"/api/goals/{goal_id}/milestones", json={"title": "MCP MS"})

    resp = await client.post("/api/mcp/tools/call", json={
        "name": "list_milestones",
        "arguments": {"goal_id": goal_id},
    })
    assert resp.status_code == 200
    assert len(resp.json()["milestones"]) >= 1


@pytest.mark.anyio
async def test_mcp_unknown_tool(client):
    """Test MCP with unknown tool returns error."""
    resp = await client.post("/api/mcp/tools/call", json={
        "name": "nonexistent_tool",
        "arguments": {},
    })
    assert resp.status_code == 200  # MCP returns errors in body, not HTTP status
    assert "error" in resp.json()


# ═══════════════════════════════════════════════════════════════════════════════
# Step 8: Execution Sandbox Isolation
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_get_execution_sandbox(client):
    """Test getting/creating an execution sandbox for a goal."""
    goal_id = await _create_goal(client, "Sandbox test goal")

    resp = await client.get(f"/api/goals/{goal_id}/sandbox")
    assert resp.status_code == 200
    ctx = resp.json()
    assert ctx["goal_id"] == goal_id
    assert ctx["status"] == "active"
    assert ctx["browser_profile_dir"] != ""
    assert ctx["temp_dir"] != ""


@pytest.mark.anyio
async def test_update_execution_sandbox(client):
    """Test updating sandbox credential scope."""
    goal_id = await _create_goal(client, "Sandbox update test")

    # First create
    await client.get(f"/api/goals/{goal_id}/sandbox")

    # Then update
    resp = await client.patch(f"/api/goals/{goal_id}/sandbox", json={
        "credential_scope": [1, 2, 3],
    })
    assert resp.status_code == 200
    assert resp.json()["credential_scope"] == "[1, 2, 3]"


@pytest.mark.anyio
async def test_cleanup_execution_sandbox(client):
    """Test cleaning up sandbox."""
    goal_id = await _create_goal(client, "Sandbox cleanup test")
    await client.get(f"/api/goals/{goal_id}/sandbox")

    resp = await client.post(f"/api/goals/{goal_id}/sandbox/cleanup")
    assert resp.status_code == 200
    assert resp.json()["cleaned_up"] is True


@pytest.mark.anyio
async def test_sandbox_isolation_between_goals(client):
    """Test that different goals get different sandbox directories."""
    goal1_id = await _create_goal(client, "Sandbox goal 1")
    goal2_id = await _create_goal(client, "Sandbox goal 2")

    resp1 = await client.get(f"/api/goals/{goal1_id}/sandbox")
    resp2 = await client.get(f"/api/goals/{goal2_id}/sandbox")

    assert resp1.json()["browser_profile_dir"] != resp2.json()["browser_profile_dir"]
    assert resp1.json()["temp_dir"] != resp2.json()["temp_dir"]


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Execution Plugin System
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_list_plugins(client):
    """Test listing plugins (empty initially)."""
    resp = await client.get("/api/plugins")
    assert resp.status_code == 200
    assert "plugins" in resp.json()


@pytest.mark.anyio
async def test_register_plugin(admin_client):
    """Test registering a new plugin (admin only)."""
    resp = await admin_client.post("/api/plugins", json={
        "name": "test-email-plugin",
        "version": "1.0.0",
        "description": "Sends emails via SMTP",
        "task_types": ["email_send", "email_campaign"],
        "required_credentials": ["smtp"],
    })
    assert resp.status_code == 201
    plugin = resp.json()
    assert plugin["name"] == "test-email-plugin"
    assert plugin["enabled"] is True


@pytest.mark.anyio
async def test_register_duplicate_plugin(admin_client):
    """Test that duplicate plugin names are rejected."""
    await admin_client.post("/api/plugins", json={
        "name": "duplicate-plugin",
        "task_types": ["test"],
    })
    resp = await admin_client.post("/api/plugins", json={
        "name": "duplicate-plugin",
        "task_types": ["test"],
    })
    assert resp.status_code == 409


@pytest.mark.anyio
async def test_delete_plugin(admin_client):
    """Test deleting a plugin."""
    await admin_client.post("/api/plugins", json={
        "name": "to-delete-plugin",
        "task_types": ["cleanup"],
    })
    resp = await admin_client.delete("/api/plugins/to-delete-plugin")
    assert resp.status_code == 200
    assert resp.json()["deleted"] == "to-delete-plugin"

    # Verify it's gone
    existing = storage.get_plugin("to-delete-plugin")
    assert existing is None


@pytest.mark.anyio
async def test_plugin_match_for_task(client, admin_client):
    """Test finding plugins that match a task type."""
    await admin_client.post("/api/plugins", json={
        "name": "matchable-plugin",
        "task_types": ["dns_setup", "domain_register"],
    })

    resp = await client.get("/api/plugins/match?task_type=dns_setup")
    assert resp.status_code == 200
    plugins = resp.json()["plugins"]
    assert any(p["name"] == "matchable-plugin" for p in plugins)


def test_plugin_execute_no_executor():
    """Test executing a plugin with no loaded executor returns error."""
    from teb.plugins import execute_plugin, PluginResult
    result = execute_plugin("nonexistent", {}, {})
    assert not result.success
    assert "not loaded" in result.error


def test_plugin_register_and_execute():
    """Test registering an in-memory executor and running it."""
    from teb.plugins import register_executor, execute_plugin, unregister_executor

    def my_executor(context, creds):
        return {"success": True, "output": f"Processed: {context.get('task', 'none')}"}

    register_executor("test-inline-plugin", my_executor)
    result = execute_plugin("test-inline-plugin", {"task": "hello"}, {})
    assert result.success
    assert "hello" in result.output
    unregister_executor("test-inline-plugin")


def test_plugin_manifest_model():
    """Test PluginManifest model serialization."""
    p = PluginManifest(
        name="test-plugin", version="2.0.0",
        description="A test", task_types='["a", "b"]',
        required_credentials='["cred1"]', module_path="/path/to/plugin.py",
    )
    d = p.to_dict()
    assert d["name"] == "test-plugin"
    assert d["version"] == "2.0.0"
    assert d["enabled"] is True


# ═══════════════════════════════════════════════════════════════════════════════
# Integration: Cross-cutting tests
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.anyio
async def test_full_workflow_with_hierarchy_and_audit(client):
    """Test a full workflow: create goal → sub-goals → milestones → audit trail."""
    # Create parent goal
    parent_id = await _create_goal(client, "Full workflow: start freelancing business")

    # Add sub-goals
    resp = await client.post(f"/api/goals/{parent_id}/sub-goals", json={
        "title": "Build portfolio website"
    })
    sub_id = resp.json()["id"]

    # Add milestones to parent
    await client.post(f"/api/goals/{parent_id}/milestones", json={
        "title": "First client", "target_metric": "clients", "target_value": 1,
    })

    # Add tasks to sub-goal
    await _create_task(client, sub_id, "Design landing page")
    await _create_task(client, sub_id, "Deploy on Vercel")

    # Check hierarchy
    resp = await client.get(f"/api/goals/{parent_id}/hierarchy")
    h = resp.json()
    assert len(h["sub_goals"]) >= 1
    assert h["sub_goals"][0]["task_count"] >= 2
    assert len(h["milestones"]) >= 1

    # Check audit trail
    resp = await client.get(f"/api/goals/{parent_id}/audit")
    events = resp.json()["events"]
    types = {e["event_type"] for e in events}
    assert "sub_goal_created" in types
    assert "milestone_created" in types

    # Export as template
    resp = await client.post(f"/api/templates/export/{parent_id}", json={
        "category": "freelancing",
    })
    assert resp.status_code == 201
    tpl_id = resp.json()["id"]

    # Check audit includes template export
    resp = await client.get(f"/api/goals/{parent_id}/audit")
    events = resp.json()["events"]
    assert any(e["event_type"] == "template_exported" for e in events)

    # Import template as a new goal
    resp = await client.post(f"/api/templates/import/{tpl_id}")
    assert resp.status_code == 201
    new_goal_id = resp.json()["goal"]["id"]
    assert new_goal_id != parent_id


@pytest.mark.anyio
async def test_mcp_with_milestones_and_sandbox(client):
    """Test MCP tools work with milestones and sandbox."""
    # Create goal via MCP
    resp = await client.post("/api/mcp/tools/call", json={
        "name": "create_goal",
        "arguments": {"title": "MCP + sandbox test"},
    })
    goal_id = resp.json()["goal_id"]

    # Add milestone
    await client.post(f"/api/goals/{goal_id}/milestones", json={
        "title": "First revenue", "target_value": 100,
    })

    # Check milestone via MCP
    resp = await client.post("/api/mcp/tools/call", json={
        "name": "list_milestones",
        "arguments": {"goal_id": goal_id},
    })
    assert len(resp.json()["milestones"]) >= 1

    # Get sandbox
    resp = await client.get(f"/api/goals/{goal_id}/sandbox")
    assert resp.status_code == 200
    assert resp.json()["status"] == "active"


@pytest.mark.anyio
async def test_unauthenticated_access_blocked():
    """Test that all new endpoints require authentication."""
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        endpoints = [
            ("GET", "/api/goals/1/agent-memory"),
            ("GET", "/api/goals/1/sub-goals"),
            ("GET", "/api/goals/1/hierarchy"),
            ("GET", "/api/goals/1/milestones"),
            ("GET", "/api/goals/1/audit"),
            ("GET", "/api/events/status"),
            ("GET", "/api/templates"),
            ("GET", "/api/mcp/info"),
            ("GET", "/api/mcp/tools"),
            ("GET", "/api/goals/1/sandbox"),
            ("GET", "/api/plugins"),
        ]
        for method, url in endpoints:
            if method == "GET":
                resp = await c.get(url)
            else:
                resp = await c.post(url)
            assert resp.status_code == 401, f"{method} {url} should require auth, got {resp.status_code}"
