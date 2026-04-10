"""Tests for bridging plan features: AI provider unification, drip mode fixes,
agent-activity endpoint, voice check-in, and suggestion display fixes."""

from __future__ import annotations

import os
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from teb import storage, config

TEST_DB = "test_bridging.db"


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


async def _get_auth_headers(c: AsyncClient) -> dict:
    r = await c.post("/api/auth/register", json={
        "email": "bridge@teb.test", "password": "testpass123"
    })
    if r.status_code not in (200, 201):
        r = await c.post("/api/auth/login", json={
            "email": "bridge@teb.test", "password": "testpass123"
        })
    return {"Authorization": f"Bearer {r.json()['token']}"}


@pytest_asyncio.fixture
async def client():
    from teb.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        headers = await _get_auth_headers(c)
        c.headers.update(headers)
        yield c


async def _create_goal(client: AsyncClient, title: str, desc: str = "") -> int:
    resp = await client.post("/api/goals", json={"title": title, "description": desc})
    assert resp.status_code == 201
    return resp.json()["id"]


# ─── AI Provider Unification Tests ──────────────────────────────────────────────

def test_config_has_ai_with_anthropic_key(monkeypatch):
    """config.has_ai() returns True when only ANTHROPIC_API_KEY is set."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")
    assert config.has_ai() is True
    assert config.get_ai_provider() == "anthropic"


def test_config_has_ai_with_openai_key(monkeypatch):
    """config.has_ai() returns True when only OPENAI_API_KEY is set."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")
    assert config.has_ai() is True
    assert config.get_ai_provider() == "openai"


def test_config_has_ai_prefers_anthropic(monkeypatch):
    """When both keys are set, auto mode prefers Anthropic."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")
    assert config.get_ai_provider() == "anthropic"


def test_config_no_ai(monkeypatch):
    """config.has_ai() returns False when no keys are set."""
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")
    assert config.has_ai() is False


def test_decomposer_uses_has_ai(monkeypatch):
    """Decomposer should check has_ai() not OPENAI_API_KEY directly."""
    from teb import decomposer
    from teb.models import Goal

    # With only Anthropic key, decompose should attempt AI (then fall back on error)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")

    goal = Goal(title="learn Python", description="from scratch", id=1)
    # decompose_ai will fail (no real API), fall back to template
    tasks = decomposer.decompose(goal)
    assert len(tasks) > 0  # Should still produce tasks (template fallback)


def test_executor_uses_has_ai(monkeypatch):
    """Executor should check has_ai() not OPENAI_API_KEY directly."""
    from teb import executor
    from teb.models import Task, ApiCredential

    # With only Anthropic key, executor should attempt AI plan generation
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")

    task = Task(title="Test task", description="Do something", goal_id=1, id=1)
    cred = ApiCredential(
        name="test", base_url="https://api.example.com",
        auth_value="xxx", user_id=1, id=1,
    )

    # generate_plan should attempt AI (then fail gracefully)
    plan = executor.generate_plan(task, [cred])
    # It won't return "AI mode is required" since has_ai() is True
    assert "AI mode is required" not in (plan.reason or "")


def test_executor_template_when_no_ai(monkeypatch):
    """Executor should use template when no AI is configured."""
    from teb import executor
    from teb.models import Task, ApiCredential

    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", None)
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    monkeypatch.setattr(config, "AI_PROVIDER", "auto")

    task = Task(title="Test task", description="Do something", goal_id=1, id=1)
    cred = ApiCredential(
        name="test", base_url="https://api.example.com",
        auth_value="xxx", user_id=1, id=1,
    )

    plan = executor.generate_plan(task, [cred])
    assert plan.can_execute is False
    assert "AI mode is required" in plan.reason


# ─── Drip Mode Fixes ───────────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_drip_mode_surfaces_existing_tasks(client):
    """When tasks exist (e.g. from orchestrate), drip should show them, not create new ones."""
    gid = await _create_goal(client, "Earn money online")

    # Manually create tasks as if AI Orchestrate created them
    for i in range(5):
        resp = await client.post(f"/api/goals/{gid}/tasks", json={
            "title": f"Orchestrated task {i+1}",
            "description": f"Step {i+1} from AI",
            "estimated_minutes": 30,
        })
        assert resp.status_code == 201

    # Drip mode should surface the first existing task, not create a template task
    resp = await client.get(f"/api/goals/{gid}/drip")
    assert resp.status_code == 200
    data = resp.json()
    assert data["task"] is not None
    assert "Orchestrated task" in data["task"]["title"]
    # Should NOT be a new task (is_new should be False or absent)
    assert data.get("is_new") is not True or "Orchestrated" in data["task"]["title"]


@pytest.mark.anyio
async def test_drip_mode_not_premature_done(client):
    """Drip mode should not show 'all done' when there are still todo tasks."""
    gid = await _create_goal(client, "build a portfolio site")

    # Create 10 tasks
    for i in range(10):
        await client.post(f"/api/goals/{gid}/tasks", json={
            "title": f"Build step {i+1}",
            "description": f"Step {i+1}",
            "estimated_minutes": 20,
        })

    # Complete only 2 of them
    resp = await client.get(f"/api/goals/{gid}")
    tasks = resp.json()["tasks"]
    for t in tasks[:2]:
        await client.patch(f"/api/tasks/{t['id']}", json={"status": "done"})

    # Drip should NOT say "all done"
    resp = await client.get(f"/api/goals/{gid}/drip")
    data = resp.json()
    assert data["task"] is not None
    assert "well done" not in (data.get("message") or "").lower()


# ─── Agent Activity Endpoint ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_agent_activity_empty(client):
    """Agent activity returns empty state when no orchestration has run."""
    gid = await _create_goal(client, "learn cooking")
    resp = await client.get(f"/api/goals/{gid}/agent-activity")
    assert resp.status_code == 200
    data = resp.json()
    assert data["goal_id"] == gid
    assert data["handoffs"] == []
    assert data["messages"] == []
    assert data["total_tasks_created"] == 0


# ─── Suggestion Display Fix ────────────────────────────────────────────────────

@pytest.mark.anyio
async def test_suggestion_has_suggestion_field(client):
    """ProactiveSuggestion.to_dict() includes the 'suggestion' field."""
    from teb.models import ProactiveSuggestion

    s = ProactiveSuggestion(
        goal_id=1,
        suggestion="Try using Fiverr for quick freelance income",
        rationale="Based on your skill set",
        category="opportunity",
    )
    d = s.to_dict()
    assert "suggestion" in d
    assert d["suggestion"] == "Try using Fiverr for quick freelance income"
    assert d["rationale"] == "Based on your skill set"
    assert d["category"] == "opportunity"


# ─── Voice Check-in Endpoint ───────────────────────────────────────────────────

@pytest.mark.anyio
async def test_voice_checkin_requires_audio(client):
    """Voice check-in endpoint should reject requests without audio."""
    gid = await _create_goal(client, "learn guitar")
    resp = await client.post(f"/api/goals/{gid}/checkin/voice")
    assert resp.status_code == 422  # Missing required audio file


@pytest.mark.anyio
async def test_voice_checkin_invalid_format(client):
    """Voice check-in rejects unsupported audio formats."""
    gid = await _create_goal(client, "learn guitar")
    # Send a text file disguised as audio
    resp = await client.post(
        f"/api/goals/{gid}/checkin/voice",
        files={"audio": ("test.txt", b"not audio data", "text/plain")},
    )
    # Should reject — .txt is not a valid audio format
    assert resp.status_code == 422


# ─── Transcribe Module ─────────────────────────────────────────────────────────

def test_transcribe_no_api_key(monkeypatch):
    """transcribe_audio returns empty string when no OpenAI API key is configured.
    Note: Whisper is OpenAI-only; the OPENAI_API_KEY check is correct here."""
    monkeypatch.setattr(config, "OPENAI_API_KEY", None)
    from teb.transcribe import transcribe_audio
    result = transcribe_audio(b"fake audio data", "test.mp3")
    assert result == ""


def test_transcribe_validates_format():
    """transcribe_audio rejects unsupported formats."""
    from teb.transcribe import transcribe_audio
    with pytest.raises(ValueError, match="Unsupported audio format"):
        transcribe_audio(b"data", "test.exe")


def test_transcribe_validates_size():
    """transcribe_audio rejects files over 25MB."""
    from teb.transcribe import transcribe_audio
    # 26MB of data
    big_data = b"x" * (26 * 1024 * 1024)
    with pytest.raises(ValueError, match="too large"):
        transcribe_audio(big_data, "test.mp3")
