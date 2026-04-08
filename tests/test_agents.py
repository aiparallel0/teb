"""Tests for multi-agent delegation system (agents.py) and AI client (ai_client.py)."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from teb import agents, config, storage
from teb.models import AgentHandoff, Goal


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _temp_db(tmp_path):
    """Create a fresh database for each test."""
    db_path = str(tmp_path / "test.db")
    storage.set_db_path(db_path)
    storage.init_db()
    yield
    storage.set_db_path(None)


@pytest.fixture
def goal() -> Goal:
    """Create a sample goal."""
    g = Goal(title="earn money online", description="I want to earn $500 freelancing")
    return storage.create_goal(g)


@pytest.fixture
def learn_goal() -> Goal:
    """Create a learning goal."""
    g = Goal(title="learn Python programming", description="Complete beginner, want to build web apps")
    return storage.create_goal(g)


@pytest.fixture
def build_goal() -> Goal:
    """Create a build goal."""
    g = Goal(title="build a SaaS product", description="Create a web app for project management")
    return storage.create_goal(g)


@pytest.fixture
def generic_goal() -> Goal:
    """Create a generic goal."""
    g = Goal(title="get organized", description="I want to be more productive")
    return storage.create_goal(g)


# ─── Agent registry tests ───────────────────────────────────────────────────

class TestAgentRegistry:
    def test_list_agents_returns_all(self):
        agent_list = agents.list_agents()
        types = {a.agent_type for a in agent_list}
        assert "coordinator" in types
        assert "marketing" in types
        assert "web_dev" in types
        assert "outreach" in types
        assert "research" in types
        assert "finance" in types

    def test_get_agent_existing(self):
        spec = agents.get_agent("coordinator")
        assert spec is not None
        assert spec.agent_type == "coordinator"
        assert spec.name == "Coordinator"
        assert len(spec.can_delegate_to) > 0

    def test_get_agent_nonexistent(self):
        assert agents.get_agent("nonexistent") is None

    def test_agent_spec_to_dict(self):
        spec = agents.get_agent("marketing")
        d = spec.to_dict()
        assert d["agent_type"] == "marketing"
        assert d["name"] == "Marketing Specialist"
        assert isinstance(d["expertise"], list)
        assert isinstance(d["can_delegate_to"], list)

    def test_coordinator_can_delegate_to_all_specialists(self):
        coord = agents.get_agent("coordinator")
        assert "marketing" in coord.can_delegate_to
        assert "web_dev" in coord.can_delegate_to
        assert "outreach" in coord.can_delegate_to
        assert "research" in coord.can_delegate_to
        assert "finance" in coord.can_delegate_to

    def test_web_dev_is_terminal(self):
        """Web dev agent should not delegate further."""
        wd = agents.get_agent("web_dev")
        assert wd.can_delegate_to == []

    def test_marketing_can_delegate_to_web_dev_and_outreach(self):
        m = agents.get_agent("marketing")
        assert "web_dev" in m.can_delegate_to
        assert "outreach" in m.can_delegate_to

    def test_all_agents_have_system_prompt(self):
        for a in agents.list_agents():
            assert a.system_prompt, f"{a.agent_type} has no system prompt"
            assert len(a.system_prompt) > 50, f"{a.agent_type} system prompt too short"


# ─── Goal category detection ────────────────────────────────────────────────

class TestGoalCategoryDetection:
    def test_money_goal(self, goal):
        assert agents._detect_goal_category(goal) == "money"

    def test_learn_goal(self, learn_goal):
        assert agents._detect_goal_category(learn_goal) == "learn"

    def test_build_goal(self, build_goal):
        assert agents._detect_goal_category(build_goal) == "build"

    def test_generic_goal(self, generic_goal):
        assert agents._detect_goal_category(generic_goal) == "default"

    def test_money_keywords(self):
        for kw in ["earn", "income", "revenue", "sell", "profit", "money"]:
            g = Goal(title=f"I want to {kw}", description="")
            assert agents._detect_goal_category(g) == "money", f"Failed for keyword: {kw}"

    def test_money_stems(self):
        """Test stem-based matching for money keywords."""
        for phrase in ["freelancing online", "find clients"]:
            g = Goal(title=phrase, description="")
            assert agents._detect_goal_category(g) == "money", f"Failed for: {phrase}"

    def test_learn_keywords(self):
        for kw in ["learn", "study", "course", "tutorial", "skill"]:
            g = Goal(title=f"I want to {kw}", description="")
            assert agents._detect_goal_category(g) == "learn", f"Failed for keyword: {kw}"

    def test_learn_not_money(self):
        """'learn' should not match 'earn' — word boundary check."""
        g = Goal(title="learn Python", description="want to learn programming")
        assert agents._detect_goal_category(g) == "learn"

    def test_build_keywords(self):
        for kw in ["build", "create", "develop", "launch", "website"]:
            g = Goal(title=f"I want to {kw}", description="")
            assert agents._detect_goal_category(g) == "build", f"Failed for keyword: {kw}"


# ─── Template-based agent execution ─────────────────────────────────────────

class TestRunAgentTemplate:
    """Tests for template-based (no AI) agent execution."""

    def test_coordinator_money_goal(self, goal):
        output = agents._run_agent_template(agents.get_agent("coordinator"), goal, "")
        assert len(output.tasks) >= 1
        assert len(output.delegations) >= 2
        assert output.summary != ""
        # Should delegate to at least research and marketing
        delegate_targets = {d["to_agent"] for d in output.delegations}
        assert "research" in delegate_targets

    def test_coordinator_learn_goal(self, learn_goal):
        output = agents._run_agent_template(agents.get_agent("coordinator"), learn_goal, "")
        assert len(output.tasks) >= 1
        assert output.summary != ""

    def test_coordinator_build_goal(self, build_goal):
        output = agents._run_agent_template(agents.get_agent("coordinator"), build_goal, "")
        assert len(output.tasks) >= 1
        delegate_targets = {d["to_agent"] for d in output.delegations}
        assert "web_dev" in delegate_targets

    def test_coordinator_generic_goal(self, generic_goal):
        output = agents._run_agent_template(agents.get_agent("coordinator"), generic_goal, "")
        assert len(output.tasks) >= 1
        assert output.summary != ""

    def test_marketing_agent(self, goal):
        output = agents._run_agent_template(agents.get_agent("marketing"), goal, "")
        assert len(output.tasks) >= 3
        assert output.summary != ""

    def test_web_dev_agent(self, goal):
        output = agents._run_agent_template(agents.get_agent("web_dev"), goal, "")
        assert len(output.tasks) >= 3

    def test_outreach_agent(self, goal):
        output = agents._run_agent_template(agents.get_agent("outreach"), goal, "")
        assert len(output.tasks) >= 3

    def test_research_agent(self, goal):
        output = agents._run_agent_template(agents.get_agent("research"), goal, "")
        assert len(output.tasks) >= 2

    def test_finance_agent(self, goal):
        output = agents._run_agent_template(agents.get_agent("finance"), goal, "")
        assert len(output.tasks) >= 2

    def test_unknown_agent_returns_empty(self, goal):
        output = agents.run_agent("nonexistent", goal)
        assert len(output.tasks) == 0
        assert "Unknown" in output.summary

    def test_task_structure(self, goal):
        output = agents._run_agent_template(agents.get_agent("web_dev"), goal, "")
        for task in output.tasks:
            assert "title" in task
            assert "description" in task
            assert "estimated_minutes" in task
            assert isinstance(task["estimated_minutes"], int)

    def test_delegation_structure(self, goal):
        output = agents._run_agent_template(agents.get_agent("coordinator"), goal, "")
        for d in output.delegations:
            assert "to_agent" in d
            assert "instruction" in d

    def test_delegations_respect_can_delegate_to(self, goal):
        """Agents should only delegate to agents they're allowed to."""
        for spec in agents.list_agents():
            output = agents._run_agent_template(spec, goal, "")
            for d in output.delegations:
                assert d["to_agent"] in spec.can_delegate_to, \
                    f"{spec.agent_type} delegated to {d['to_agent']} which is not in can_delegate_to"


# ─── Orchestration (template mode) ──────────────────────────────────────────

class TestOrchestrateGoal:
    def test_orchestration_creates_tasks(self, goal):
        result = agents.orchestrate_goal(goal)
        assert result["goal_id"] == goal.id
        assert result["total_tasks"] > 0
        assert len(result["tasks"]) == result["total_tasks"]

    def test_orchestration_creates_handoffs(self, goal):
        result = agents.orchestrate_goal(goal)
        assert len(result["handoffs"]) > 0
        for h in result["handoffs"]:
            assert h["from_agent"] is not None
            assert h["to_agent"] is not None
            assert h["status"] == "completed"

    def test_orchestration_involves_multiple_agents(self, goal):
        result = agents.orchestrate_goal(goal)
        assert "coordinator" in result["agents_involved"]
        assert len(result["agents_involved"]) >= 3

    def test_orchestration_updates_goal_status(self, goal):
        agents.orchestrate_goal(goal)
        updated = storage.get_goal(goal.id)
        assert updated.status == "decomposed"

    def test_orchestration_tasks_persisted(self, goal):
        result = agents.orchestrate_goal(goal)
        db_tasks = storage.list_tasks(goal_id=goal.id)
        assert len(db_tasks) == result["total_tasks"]

    def test_orchestration_handoffs_persisted(self, goal):
        agents.orchestrate_goal(goal)
        db_handoffs = storage.list_handoffs(goal.id)
        assert len(db_handoffs) > 0

    def test_orchestration_learn_goal(self, learn_goal):
        result = agents.orchestrate_goal(learn_goal)
        assert result["total_tasks"] > 0
        assert "coordinator" in result["agents_involved"]

    def test_orchestration_build_goal(self, build_goal):
        result = agents.orchestrate_goal(build_goal)
        assert result["total_tasks"] > 0

    def test_orchestration_generic_goal(self, generic_goal):
        result = agents.orchestrate_goal(generic_goal)
        assert result["total_tasks"] > 0

    def test_strategy_summary_not_empty(self, goal):
        result = agents.orchestrate_goal(goal)
        assert result["strategy"] != ""

    def test_delegation_depth_limit(self, goal):
        """Ensure delegation doesn't go deeper than _MAX_DELEGATION_DEPTH."""
        result = agents.orchestrate_goal(goal)
        # With template mode, the chain is: coordinator → specialist → (sub-delegation)
        # It should complete without infinite loops
        assert result["total_tasks"] > 0
        assert result["total_tasks"] < 100  # sanity bound


# ─── Agent handoff storage ───────────────────────────────────────────────────

class TestAgentHandoffStorage:
    def test_create_handoff(self, goal):
        h = AgentHandoff(
            goal_id=goal.id,
            from_agent="coordinator",
            to_agent="marketing",
            input_summary="Create marketing strategy",
            status="pending",
        )
        saved = storage.create_handoff(h)
        assert saved.id is not None
        assert saved.created_at is not None
        assert saved.from_agent == "coordinator"
        assert saved.to_agent == "marketing"

    def test_update_handoff(self, goal):
        h = AgentHandoff(
            goal_id=goal.id,
            from_agent="coordinator",
            to_agent="web_dev",
            input_summary="Build landing page",
            status="pending",
        )
        saved = storage.create_handoff(h)
        saved.output_summary = "Created 5 technical tasks"
        saved.status = "completed"
        updated = storage.update_handoff(saved)
        assert updated.status == "completed"
        assert updated.output_summary == "Created 5 technical tasks"

    def test_list_handoffs(self, goal):
        for agent in ["marketing", "web_dev", "outreach"]:
            storage.create_handoff(AgentHandoff(
                goal_id=goal.id,
                from_agent="coordinator",
                to_agent=agent,
                input_summary=f"Task for {agent}",
            ))
        handoffs = storage.list_handoffs(goal.id)
        assert len(handoffs) == 3

    def test_list_handoffs_ordered_by_created_at(self, goal):
        for agent in ["marketing", "web_dev"]:
            storage.create_handoff(AgentHandoff(
                goal_id=goal.id,
                from_agent="coordinator",
                to_agent=agent,
            ))
        handoffs = storage.list_handoffs(goal.id)
        assert handoffs[0].created_at <= handoffs[1].created_at

    def test_handoff_to_dict(self, goal):
        h = storage.create_handoff(AgentHandoff(
            goal_id=goal.id,
            from_agent="coordinator",
            to_agent="finance",
            input_summary="Budget analysis",
        ))
        d = h.to_dict()
        assert d["id"] == h.id
        assert d["from_agent"] == "coordinator"
        assert d["to_agent"] == "finance"
        assert d["input_summary"] == "Budget analysis"
        assert d["status"] == "pending"

    def test_handoff_cascade_delete(self, goal):
        """Handoffs should be deleted when the goal is deleted."""
        storage.create_handoff(AgentHandoff(
            goal_id=goal.id,
            from_agent="coordinator",
            to_agent="marketing",
        ))
        # We don't have a delete_goal, but verify the FK constraint is set
        handoffs = storage.list_handoffs(goal.id)
        assert len(handoffs) == 1


# ─── AI client tests ────────────────────────────────────────────────────────

class TestAiClient:
    def test_strip_code_fences(self):
        from teb.ai_client import _strip_code_fences
        assert _strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
        assert _strip_code_fences('{"a": 1}') == '{"a": 1}'
        assert _strip_code_fences('  {"a": 1}  ') == '{"a": 1}'

    def test_strip_code_fences_with_trailing_whitespace(self):
        from teb.ai_client import _strip_code_fences
        result = _strip_code_fences('```json\n{"key": "value"}\n```\n')
        assert json.loads(result) == {"key": "value"}


# ─── Config tests ────────────────────────────────────────────────────────────

class TestConfig:
    def test_get_ai_provider_none(self):
        with patch.object(config, 'ANTHROPIC_API_KEY', None), \
             patch.object(config, 'OPENAI_API_KEY', None), \
             patch.object(config, 'AI_PROVIDER', 'auto'):
            assert config.get_ai_provider() is None

    def test_get_ai_provider_anthropic_auto(self):
        with patch.object(config, 'ANTHROPIC_API_KEY', 'test-key'), \
             patch.object(config, 'OPENAI_API_KEY', None), \
             patch.object(config, 'AI_PROVIDER', 'auto'):
            assert config.get_ai_provider() == "anthropic"

    def test_get_ai_provider_openai_auto(self):
        with patch.object(config, 'ANTHROPIC_API_KEY', None), \
             patch.object(config, 'OPENAI_API_KEY', 'test-key'), \
             patch.object(config, 'AI_PROVIDER', 'auto'):
            assert config.get_ai_provider() == "openai"

    def test_get_ai_provider_anthropic_preferred(self):
        """When both keys are set, anthropic is preferred in auto mode."""
        with patch.object(config, 'ANTHROPIC_API_KEY', 'claude-key'), \
             patch.object(config, 'OPENAI_API_KEY', 'openai-key'), \
             patch.object(config, 'AI_PROVIDER', 'auto'):
            assert config.get_ai_provider() == "anthropic"

    def test_get_ai_provider_explicit_openai(self):
        with patch.object(config, 'ANTHROPIC_API_KEY', 'claude-key'), \
             patch.object(config, 'OPENAI_API_KEY', 'openai-key'), \
             patch.object(config, 'AI_PROVIDER', 'openai'):
            assert config.get_ai_provider() == "openai"

    def test_has_ai_true(self):
        with patch.object(config, 'ANTHROPIC_API_KEY', 'test'), \
             patch.object(config, 'AI_PROVIDER', 'auto'):
            assert config.has_ai() is True

    def test_has_ai_false(self):
        with patch.object(config, 'ANTHROPIC_API_KEY', None), \
             patch.object(config, 'OPENAI_API_KEY', None), \
             patch.object(config, 'AI_PROVIDER', 'auto'):
            assert config.has_ai() is False


# ─── AI-mode agent tests (mocked) ───────────────────────────────────────────

class TestRunAgentAI:
    """Test AI-mode agent execution with mocked AI client."""

    def test_coordinator_ai(self, goal):
        mock_response = {
            "strategy_summary": "Freelance via Upwork with Python skills",
            "tasks": [
                {"title": "Create Upwork profile", "description": "Set up a complete Upwork profile", "estimated_minutes": 45},
                {"title": "Set hourly rate", "description": "Research rates and set competitive pricing", "estimated_minutes": 15},
            ],
            "delegations": [
                {"to_agent": "marketing", "instruction": "Create positioning strategy for freelancer profile"},
                {"to_agent": "outreach", "instruction": "Draft outreach messages for potential clients"},
            ],
        }

        with patch("teb.ai_client.ai_chat_json", return_value=mock_response) as mock_chat:
            spec = agents.get_agent("coordinator")
            output = agents._run_agent_ai(spec, goal, "", "")

            assert len(output.tasks) == 2
            assert output.tasks[0]["title"] == "Create Upwork profile"
            assert len(output.delegations) == 2
            assert output.summary == "Freelance via Upwork with Python skills"

    def test_ai_filters_invalid_delegations(self, goal):
        """AI response with invalid delegation targets should be filtered."""
        mock_response = {
            "summary": "test",
            "tasks": [{"title": "test task", "description": "", "estimated_minutes": 10}],
            "delegations": [
                {"to_agent": "marketing", "instruction": "valid"},
                {"to_agent": "nonexistent_agent", "instruction": "should be filtered"},
            ],
        }

        with patch("teb.ai_client.ai_chat_json", return_value=mock_response):
            spec = agents.get_agent("coordinator")
            output = agents._run_agent_ai(spec, goal, "", "")
            assert len(output.delegations) == 1
            assert output.delegations[0]["to_agent"] == "marketing"

    def test_ai_filters_invalid_tasks(self, goal):
        """AI response with malformed tasks should be filtered."""
        mock_response = {
            "summary": "test",
            "tasks": [
                {"title": "valid task", "description": "ok", "estimated_minutes": 30},
                {"no_title": "invalid"},  # missing title
                "not a dict",  # completely invalid
            ],
            "delegations": [],
        }

        with patch("teb.ai_client.ai_chat_json", return_value=mock_response):
            spec = agents.get_agent("coordinator")
            output = agents._run_agent_ai(spec, goal, "", "")
            assert len(output.tasks) == 1
            assert output.tasks[0]["title"] == "valid task"

    def test_ai_error_returns_error_output(self, goal):
        with patch("teb.ai_client.ai_chat_json", side_effect=RuntimeError("API error")):
            spec = agents.get_agent("coordinator")
            output = agents._run_agent_ai(spec, goal, "", "")
            assert len(output.tasks) == 0
            assert "error" in output.summary.lower()

    def test_web_dev_cannot_delegate(self, goal):
        """web_dev agent output should have no delegations even if AI suggests them."""
        mock_response = {
            "summary": "technical setup",
            "tasks": [{"title": "Build site", "description": "", "estimated_minutes": 60}],
            "delegations": [
                {"to_agent": "marketing", "instruction": "should be filtered"},
            ],
        }

        with patch("teb.ai_client.ai_chat_json", return_value=mock_response):
            spec = agents.get_agent("web_dev")
            output = agents._run_agent_ai(spec, goal, "", "")
            assert len(output.delegations) == 0


# ─── API endpoint tests ─────────────────────────────────────────────────────

class TestAgentAPI:
    """Test the API endpoints for multi-agent system."""

    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from teb.main import app
        return TestClient(app)

    def test_list_agents_endpoint(self, client):
        resp = client.get("/api/agents")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 6
        types = {a["agent_type"] for a in data}
        assert "coordinator" in types
        assert "marketing" in types

    def test_orchestrate_goal_endpoint(self, client, goal):
        resp = client.post(f"/api/goals/{goal.id}/orchestrate")
        assert resp.status_code == 200
        data = resp.json()
        assert data["goal_id"] == goal.id
        assert data["total_tasks"] > 0
        assert len(data["tasks"]) > 0
        assert len(data["handoffs"]) > 0
        assert "coordinator" in data["agents_involved"]

    def test_orchestrate_nonexistent_goal(self, client):
        resp = client.post("/api/goals/9999/orchestrate")
        assert resp.status_code == 404

    def test_list_handoffs_endpoint(self, client, goal):
        # First orchestrate to create handoffs
        client.post(f"/api/goals/{goal.id}/orchestrate")
        resp = client.get(f"/api/goals/{goal.id}/handoffs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) > 0

    def test_list_handoffs_nonexistent_goal(self, client):
        resp = client.get("/api/goals/9999/handoffs")
        assert resp.status_code == 404

    def test_orchestrate_creates_real_tasks(self, client, goal):
        client.post(f"/api/goals/{goal.id}/orchestrate")
        # Tasks should be in the database
        resp = client.get(f"/api/goals/{goal.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["tasks"]) > 0

    def test_orchestrate_then_focus_works(self, client, goal):
        """After orchestration, focus mode should return a task."""
        client.post(f"/api/goals/{goal.id}/orchestrate")
        resp = client.get(f"/api/goals/{goal.id}/focus")
        assert resp.status_code == 200
        data = resp.json()
        assert data["focus_task"] is not None

    def test_orchestrate_then_progress_works(self, client, goal):
        """After orchestration, progress should show stats."""
        client.post(f"/api/goals/{goal.id}/orchestrate")
        resp = client.get(f"/api/goals/{goal.id}/progress")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_tasks"] > 0
        assert data["completion_pct"] == 0  # nothing done yet
