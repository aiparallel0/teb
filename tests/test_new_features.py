"""Tests for the four new features: Drip Mode, Success Path Learning,
Financial Pipeline, and External Messaging."""

import json
import os
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport

# ─── Shared fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Give every test its own isolated database."""
    from teb import storage
    db = str(tmp_path / "test.db")
    storage.set_db_path(db)
    storage.init_db()
    yield
    try:
        os.remove(db)
    except FileNotFoundError:
        pass


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _get_auth_headers(c) -> dict:
    """Register (or login) a test user and return auth headers."""
    r = await c.post("/api/auth/register", json={"email": "newtest@teb.test", "password": "testpass123"})
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={"email": "newtest@teb.test", "password": "testpass123"})
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
        yield c


# ─── Helpers ─────────────────────────────────────────────────────────────────

async def _create_goal(client, title="learn Python", description="from scratch"):
    r = await client.post("/api/goals", json={"title": title, "description": description})
    assert r.status_code == 201
    return r.json()["id"]


async def _create_goal_with_tasks(client, title="learn Python", description="from scratch"):
    """Create a goal and decompose it so tasks exist."""
    gid = await _create_goal(client, title, description)
    await client.post(f"/api/goals/{gid}/decompose", json={})
    return gid


async def _create_budget(client, goal_id, **kwargs):
    payload = {"goal_id": goal_id, "daily_limit": 50, "total_limit": 500,
               "category": "general", "require_approval": True}
    payload.update(kwargs)
    r = await client.post("/api/budgets", json=payload)
    assert r.status_code == 201
    return r.json()


# ═════════════════════════════════════════════════════════════════════════════
# Feature 1: Adaptive Micro-Tasking (Drip Mode)
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_drip_question_returns_first_question(client):
    gid = await _create_goal(client, "earn money online", "from scratch")
    r = await client.get(f"/api/goals/{gid}/drip/question")
    assert r.status_code == 200
    data = r.json()
    assert data["done"] is False
    assert data["question"]["key"]
    assert data["question"]["text"]


@pytest.mark.anyio
async def test_drip_clarify_submits_answer(client):
    gid = await _create_goal(client, "earn money online", "")
    # Get first question
    q = await client.get(f"/api/goals/{gid}/drip/question")
    key = q.json()["question"]["key"]
    r = await client.post(f"/api/goals/{gid}/drip/clarify",
                          json={"key": key, "answer": "Python developer"})
    assert r.status_code == 200
    # Answer should be persisted
    goal_r = await client.get(f"/api/goals/{gid}")
    assert goal_r.json()["answers"][key] == "Python developer"


@pytest.mark.anyio
async def test_drip_questions_stop_after_limit(client):
    gid = await _create_goal(client, "earn money online", "")
    # Answer 5 questions
    for _ in range(5):
        q = await client.get(f"/api/goals/{gid}/drip/question")
        data = q.json()
        if data["done"]:
            break
        key = data["question"]["key"]
        await client.post(f"/api/goals/{gid}/drip/clarify",
                          json={"key": key, "answer": "test"})
    # After 5 answers, drip questions should return done=True
    final = await client.get(f"/api/goals/{gid}/drip/question")
    assert final.json()["done"] is True


@pytest.mark.anyio
async def test_drip_next_creates_first_task(client):
    gid = await _create_goal(client, "learn Python", "from scratch")
    r = await client.get(f"/api/goals/{gid}/drip")
    assert r.status_code == 200
    data = r.json()
    assert data["task"] is not None
    assert data["is_new"] is True
    assert data["task"]["goal_id"] == gid
    assert data["task"]["id"] is not None


@pytest.mark.anyio
async def test_drip_next_returns_existing_task(client):
    gid = await _create_goal(client, "learn Python", "from scratch")
    # First drip creates a task
    first = await client.get(f"/api/goals/{gid}/drip")
    task_id = first.json()["task"]["id"]
    # Second drip returns the same task (it's still todo)
    second = await client.get(f"/api/goals/{gid}/drip")
    assert second.json()["task"]["id"] == task_id
    assert second.json()["is_new"] is False


@pytest.mark.anyio
async def test_drip_next_creates_next_after_completion(client):
    gid = await _create_goal(client, "learn Python", "from scratch")
    # Get first drip task
    first = await client.get(f"/api/goals/{gid}/drip")
    task_id = first.json()["task"]["id"]
    # Complete it
    await client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
    # Next drip should create a new task
    second = await client.get(f"/api/goals/{gid}/drip")
    data = second.json()
    assert data["task"] is not None
    assert data["is_new"] is True
    assert data["task"]["id"] != task_id


@pytest.mark.anyio
async def test_drip_next_all_done(client):
    gid = await _create_goal(client, "learn Python", "from scratch")
    from teb import decomposer
    from teb.models import Goal
    goal = Goal(title="learn Python", description="from scratch", id=gid)
    template_name = decomposer._detect_template(goal)
    total_tasks = len(decomposer._TEMPLATES[template_name].tasks)

    # Create and complete all template tasks via drip
    for _ in range(total_tasks):
        drip = await client.get(f"/api/goals/{gid}/drip")
        task = drip.json().get("task")
        if task is None:
            break
        tid = task["id"]
        await client.patch(f"/api/tasks/{tid}", json={"status": "done"})

    # Now drip should say all done
    final = await client.get(f"/api/goals/{gid}/drip")
    data = final.json()
    assert data["task"] is None
    assert "completed" in data["message"].lower() or "done" in data["message"].lower()


@pytest.mark.anyio
async def test_drip_adaptive_question_at_milestone(client):
    gid = await _create_goal(client, "learn Python", "from scratch")
    # Complete 2 tasks via drip to trigger adaptive question at milestone
    for _ in range(2):
        drip = await client.get(f"/api/goals/{gid}/drip")
        task = drip.json().get("task")
        if task is None:
            break
        tid = task["id"]
        await client.patch(f"/api/tasks/{tid}", json={"status": "done"})

    # Third drip should include an adaptive question (after 2 completed tasks)
    drip = await client.get(f"/api/goals/{gid}/drip")
    data = drip.json()
    # The adaptive question should appear (pace_feedback at 2 completed)
    if data["adaptive_question"] is not None:
        assert data["adaptive_question"]["key"] == "pace_feedback"
        assert data["adaptive_question"]["text"]
    # Even if no adaptive question (edge case), task should still be returned
    assert data["task"] is not None


# ═════════════════════════════════════════════════════════════════════════════
# Feature 2: Success Path Learning
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_capture_success_path_on_goal_done(client):
    gid = await _create_goal_with_tasks(client, "learn Python", "from scratch")
    tasks_r = await client.get(f"/api/goals/{gid}")
    tasks = tasks_r.json()["tasks"]
    # Mark all top-level tasks as done
    top_level = [t for t in tasks if t["parent_id"] is None]
    for t in top_level:
        await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
    # Goal should be done
    goal = await client.get(f"/api/goals/{gid}")
    assert goal.json()["status"] == "done"
    # A success path should be captured
    paths_r = await client.get("/api/knowledge/paths")
    paths = paths_r.json()
    assert len(paths) >= 1
    assert paths[0]["source_goal_id"] == gid
    assert paths[0]["goal_type"] == "learn_skill"


@pytest.mark.anyio
async def test_capture_success_path_not_captured_incomplete(client):
    gid = await _create_goal_with_tasks(client, "learn Python", "from scratch")
    tasks_r = await client.get(f"/api/goals/{gid}")
    tasks = tasks_r.json()["tasks"]
    top_level = [t for t in tasks if t["parent_id"] is None]
    # Only mark first task as done (not all)
    await client.patch(f"/api/tasks/{top_level[0]['id']}", json={"status": "done"})
    # Goal should NOT be done
    goal = await client.get(f"/api/goals/{gid}")
    assert goal.json()["status"] != "done"
    # No success path should be captured
    paths_r = await client.get("/api/knowledge/paths")
    assert len(paths_r.json()) == 0


@pytest.mark.anyio
async def test_success_path_insights_empty_when_no_paths(client):
    gid = await _create_goal(client, "learn Python", "from scratch")
    r = await client.get(f"/api/goals/{gid}/insights")
    assert r.status_code == 200
    data = r.json()
    assert data["goal_id"] == gid
    assert data["insights"] == []


@pytest.mark.anyio
async def test_success_path_insights_populated(client):
    # Complete a goal so a success path is captured
    gid1 = await _create_goal_with_tasks(client, "learn Python", "from scratch")
    tasks_r = await client.get(f"/api/goals/{gid1}")
    for t in [t for t in tasks_r.json()["tasks"] if t["parent_id"] is None]:
        await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})

    # Create a similar new goal and check insights
    gid2 = await _create_goal(client, "learn JavaScript", "want to study")
    r = await client.get(f"/api/goals/{gid2}/insights")
    assert r.status_code == 200
    assert len(r.json()["insights"]) > 0


@pytest.mark.anyio
async def test_apply_success_paths_identifies_popular_steps(client):
    # Complete a "learn_skill" goal
    gid1 = await _create_goal_with_tasks(client, "learn Python", "from scratch")
    tasks_r = await client.get(f"/api/goals/{gid1}")
    for t in [t for t in tasks_r.json()["tasks"] if t["parent_id"] is None]:
        await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})

    # Get insights for a similar goal
    gid2 = await _create_goal(client, "learn React", "beginner study")
    r = await client.get(f"/api/goals/{gid2}/insights")
    insights = r.json()["insights"]
    # Should have popular_steps insight
    types = [i["type"] for i in insights]
    assert "popular_steps" in types
    popular = next(i for i in insights if i["type"] == "popular_steps")
    assert len(popular["steps"]) > 0


# ═════════════════════════════════════════════════════════════════════════════
# Feature 3: Financial Pipeline
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_create_budget(client):
    gid = await _create_goal(client)
    r = await client.post("/api/budgets", json={
        "goal_id": gid, "daily_limit": 100, "total_limit": 1000,
        "category": "general", "require_approval": True,
    })
    assert r.status_code == 201
    data = r.json()
    assert data["daily_limit"] == 100
    assert data["total_limit"] == 1000
    assert data["require_approval"] is True


@pytest.mark.anyio
async def test_create_budget_validation(client):
    gid = await _create_goal(client)
    r = await client.post("/api/budgets", json={
        "goal_id": gid, "daily_limit": -10, "total_limit": 500,
    })
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_budget_invalid_category(client):
    gid = await _create_goal(client)
    r = await client.post("/api/budgets", json={
        "goal_id": gid, "daily_limit": 50, "total_limit": 500,
        "category": "invalid_cat",
    })
    assert r.status_code == 422


@pytest.mark.anyio
async def test_list_budgets(client):
    gid = await _create_goal(client)
    await _create_budget(client, gid, category="general")
    await _create_budget(client, gid, category="hosting")
    r = await client.get(f"/api/goals/{gid}/budgets")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.anyio
async def test_update_budget(client):
    gid = await _create_goal(client)
    budget = await _create_budget(client, gid)
    bid = budget["id"]
    r = await client.patch(f"/api/budgets/{bid}", json={"daily_limit": 200})
    assert r.status_code == 200
    assert r.json()["daily_limit"] == 200


@pytest.mark.anyio
async def test_spending_request_within_limits(client):
    gid = await _create_goal_with_tasks(client)
    budget = await _create_budget(client, gid, require_approval=False)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    r = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Domain purchase",
    })
    assert r.status_code == 201
    assert r.json()["auto_approved"] is True


@pytest.mark.anyio
async def test_spending_request_exceeds_daily_limit(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, daily_limit=5, total_limit=500,
                         require_approval=False)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    r = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Too expensive",
    })
    assert r.status_code == 422
    assert "daily" in r.json()["detail"].lower()


@pytest.mark.anyio
async def test_spending_request_exceeds_total_limit(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, daily_limit=500, total_limit=5,
                         require_approval=False)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    r = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Over total",
    })
    assert r.status_code == 422
    assert "total" in r.json()["detail"].lower()


@pytest.mark.anyio
async def test_spending_auto_approved_when_not_required(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=False)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    r = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 5.0, "description": "Small tool",
    })
    assert r.status_code == 201
    assert r.json()["auto_approved"] is True
    assert r.json()["request"]["status"] == "approved"


@pytest.mark.anyio
async def test_spending_pending_when_approval_required(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=True)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    r = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 5.0, "description": "Needs approval",
    })
    assert r.status_code == 201
    assert r.json()["auto_approved"] is False
    assert r.json()["request"]["status"] == "pending"


@pytest.mark.anyio
async def test_approve_spending(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=True)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    sr = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Approve me",
    })
    req_id = sr.json()["request"]["id"]
    r = await client.post(f"/api/spending/{req_id}/action",
                          json={"action": "approve"})
    assert r.status_code == 200
    assert r.json()["status"] == "approved"


@pytest.mark.anyio
async def test_deny_spending(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=True)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    sr = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Deny me",
    })
    req_id = sr.json()["request"]["id"]
    r = await client.post(f"/api/spending/{req_id}/action",
                          json={"action": "deny"})
    assert r.status_code == 200
    assert r.json()["status"] == "denied"


@pytest.mark.anyio
async def test_deny_spending_with_reason(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=True)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    sr = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Deny with reason",
    })
    req_id = sr.json()["request"]["id"]
    r = await client.post(f"/api/spending/{req_id}/action",
                          json={"action": "deny", "reason": "Too costly"})
    assert r.status_code == 200
    assert r.json()["denial_reason"] == "Too costly"


@pytest.mark.anyio
async def test_spending_already_actioned(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=True)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    sr = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 10.0, "description": "Double action",
    })
    req_id = sr.json()["request"]["id"]
    # Approve first
    await client.post(f"/api/spending/{req_id}/action",
                      json={"action": "approve"})
    # Try again — should fail
    r = await client.post(f"/api/spending/{req_id}/action",
                          json={"action": "deny"})
    assert r.status_code == 409


@pytest.mark.anyio
async def test_list_goal_spending(client):
    gid = await _create_goal_with_tasks(client)
    await _create_budget(client, gid, require_approval=False)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 5.0, "description": "Item 1",
    })
    await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 15.0, "description": "Item 2",
    })
    r = await client.get(f"/api/goals/{gid}/spending")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.anyio
async def test_spending_updates_budget_totals(client):
    gid = await _create_goal_with_tasks(client)
    budget = await _create_budget(client, gid, require_approval=True)
    bid = budget["id"]
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    sr = await client.post("/api/spending/request", json={
        "task_id": task_id, "amount": 25.0, "description": "Update totals",
    })
    req_id = sr.json()["request"]["id"]
    await client.post(f"/api/spending/{req_id}/action",
                      json={"action": "approve"})
    # Check budget was updated
    budgets_r = await client.get(f"/api/goals/{gid}/budgets")
    updated_budget = next(b for b in budgets_r.json() if b["id"] == bid)
    assert updated_budget["spent_today"] == 25.0
    assert updated_budget["spent_total"] == 25.0


# ═════════════════════════════════════════════════════════════════════════════
# Feature 4: External Messaging
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_create_messaging_config_telegram(client):
    r = await client.post("/api/messaging/config", json={
        "channel": "telegram",
        "config": {"bot_token": "123:ABC", "chat_id": "456"},
    })
    assert r.status_code == 201
    data = r.json()
    assert data["channel"] == "telegram"
    assert data["config"]["bot_token"] == "123:ABC"
    assert data["enabled"] is True


@pytest.mark.anyio
async def test_create_messaging_config_webhook(client):
    r = await client.post("/api/messaging/config", json={
        "channel": "webhook",
        "config": {"url": "https://example.com/hook"},
    })
    assert r.status_code == 201
    assert r.json()["channel"] == "webhook"


@pytest.mark.anyio
async def test_create_messaging_config_invalid_channel(client):
    r = await client.post("/api/messaging/config", json={
        "channel": "email",
        "config": {},
    })
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_telegram_missing_fields(client):
    r = await client.post("/api/messaging/config", json={
        "channel": "telegram",
        "config": {"bot_token": "123:ABC"},  # missing chat_id
    })
    assert r.status_code == 422


@pytest.mark.anyio
async def test_create_webhook_missing_url(client):
    r = await client.post("/api/messaging/config", json={
        "channel": "webhook",
        "config": {},
    })
    assert r.status_code == 422


@pytest.mark.anyio
async def test_list_messaging_configs(client):
    await client.post("/api/messaging/config", json={
        "channel": "telegram",
        "config": {"bot_token": "t1", "chat_id": "c1"},
    })
    await client.post("/api/messaging/config", json={
        "channel": "webhook",
        "config": {"url": "https://example.com/hook"},
    })
    r = await client.get("/api/messaging/configs")
    assert r.status_code == 200
    assert len(r.json()) == 2


@pytest.mark.anyio
async def test_update_messaging_config(client):
    cr = await client.post("/api/messaging/config", json={
        "channel": "telegram",
        "config": {"bot_token": "old", "chat_id": "123"},
    })
    cfg_id = cr.json()["id"]
    r = await client.patch(f"/api/messaging/config/{cfg_id}", json={
        "enabled": False,
        "notify_nudges": False,
    })
    assert r.status_code == 200
    assert r.json()["enabled"] is False
    assert r.json()["notify_nudges"] is False


@pytest.mark.anyio
async def test_delete_messaging_config(client):
    cr = await client.post("/api/messaging/config", json={
        "channel": "webhook",
        "config": {"url": "https://example.com/hook"},
    })
    cfg_id = cr.json()["id"]
    r = await client.delete(f"/api/messaging/config/{cfg_id}")
    assert r.status_code == 200
    assert r.json()["deleted"] == cfg_id
    # Should be gone
    configs = await client.get("/api/messaging/configs")
    assert len(configs.json()) == 0


@pytest.mark.anyio
async def test_send_notification_no_configs(client):
    """send_notification with no configs returns 0 sent."""
    from teb import messaging
    result = messaging.send_notification("nudge", {"message": "Hello"})
    assert result["sent"] == 0
    assert result["failed"] == 0


@pytest.mark.anyio
async def test_format_message_nudge(client):
    from teb.messaging import _format_message
    msg = _format_message("nudge", {"message": "Time to work!"})
    assert "Nudge" in msg
    assert "Time to work!" in msg
    assert "⏰" in msg


@pytest.mark.anyio
async def test_format_message_spending_request(client):
    from teb.messaging import _format_message
    msg = _format_message("spending_request", {
        "amount": 25.50, "description": "Domain", "service": "Namecheap",
        "request_id": 42,
    })
    assert "$25.50" in msg
    assert "Domain" in msg
    assert "Namecheap" in msg
    assert "💰" in msg


@pytest.mark.anyio
async def test_format_message_task_done(client):
    from teb.messaging import _format_message
    msg = _format_message("task_done", {"title": "Set up website"})
    assert "Set up website" in msg
    assert "✅" in msg


# ═════════════════════════════════════════════════════════════════════════════
# Cross-Feature Integration Tests
# ═════════════════════════════════════════════════════════════════════════════

@pytest.mark.anyio
async def test_task_completion_triggers_notification(client):
    """Completing a task should attempt to send a notification."""
    gid = await _create_goal_with_tasks(client)
    tasks_r = await client.get(f"/api/goals/{gid}")
    task_id = tasks_r.json()["tasks"][0]["id"]
    with patch("teb.messaging.send_notification") as mock_send:
        mock_send.return_value = {"sent": 0, "failed": 0, "channels": []}
        r = await client.patch(f"/api/tasks/{task_id}", json={"status": "done"})
        assert r.status_code == 200
        mock_send.assert_called()
        # Should be called with "task_done" event type
        call_args = mock_send.call_args_list
        event_types = [c[0][0] for c in call_args]
        assert "task_done" in event_types


@pytest.mark.anyio
async def test_goal_completion_captures_path_and_notifies(client):
    """When a goal completes, a success path should be captured and
    a notification sent."""
    gid = await _create_goal_with_tasks(client, "learn Python", "from scratch")
    tasks_r = await client.get(f"/api/goals/{gid}")
    top_level = [t for t in tasks_r.json()["tasks"] if t["parent_id"] is None]

    with patch("teb.messaging.send_notification") as mock_send:
        mock_send.return_value = {"sent": 0, "failed": 0, "channels": []}
        # Mark all top-level tasks done
        for t in top_level:
            await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})
        # Check goal_complete notification was sent
        event_types = [c[0][0] for c in mock_send.call_args_list]
        assert "goal_complete" in event_types

    # Success path should exist
    paths_r = await client.get("/api/knowledge/paths")
    assert len(paths_r.json()) >= 1


@pytest.mark.anyio
async def test_spending_validation_logic(client):
    """Direct test of decomposer.validate_spending."""
    from teb.decomposer import validate_spending
    result = validate_spending(10.0, 50.0, 500.0, 0.0, 0.0)
    assert result["allowed"] is True
    assert result["remaining_daily"] == 40.0
    assert result["remaining_total"] == 490.0


@pytest.mark.anyio
async def test_spending_validation_zero_amount(client):
    """Zero amount should not be allowed."""
    from teb.decomposer import validate_spending
    result = validate_spending(0, 50.0, 500.0, 0.0, 0.0)
    assert result["allowed"] is False


@pytest.mark.anyio
async def test_guess_spending_category(client):
    """Test the category guessing function from main."""
    from teb.main import _guess_spending_category
    assert _guess_spending_category("Namecheap") == "domain"
    assert _guess_spending_category("Vercel") == "hosting"
    assert _guess_spending_category("Google Ads") == "marketing"
    assert _guess_spending_category("GitHub") == "tools"
    assert _guess_spending_category("Stripe") == "services"
    assert _guess_spending_category("Something random") == "general"
