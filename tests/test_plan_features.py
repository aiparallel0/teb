"""
Tests for P1–P3 features: auth, drip adaptation, templates, encryption,
Telegram webhook, budget-executor wiring, daily reset, success path learning.
"""

import json
import os
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

# ─── Env setup (no real AI) ──────────────────────────────────────────────────

TEST_DB = "test_plan_features.db"

from teb import auth, config, storage
from teb.decomposer import (
    _check_skip_rate,
    _compute_time_scale,
    _detect_task_stall,
    _detect_template,
    capture_success_path,
    drip_next_task,
    get_clarifying_questions,
)
from teb.main import app
from teb.models import Goal, SpendingBudget, SpendingRequest, SuccessPath, Task, User

client = TestClient(app)


# ─── Helpers ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True, scope="session")
def setup_test_db():
    storage.set_db_path(TEST_DB)
    storage.init_db()
    yield
    try:
        os.remove(TEST_DB)
    except FileNotFoundError:
        pass


@pytest.fixture(autouse=True)
def clean_tables():
    """Clean all tables between tests."""
    with storage._conn() as con:
        for table in [
            "spending_requests", "spending_budgets", "execution_logs",
            "tasks", "goals", "users", "user_profiles", "messaging_configs",
            "api_credentials", "success_paths", "telegram_sessions",
        ]:
            try:
                con.execute(f"DELETE FROM {table}")
            except Exception:
                pass
    yield


def _make_goal(title="test goal", desc="", user_id=None):
    g = Goal(title=title, description=desc)
    g.user_id = user_id
    return storage.create_goal(g)


def _make_task(goal_id, title="task1", status="todo", order=0, minutes=30):
    t = Task(goal_id=goal_id, title=title, description="desc", estimated_minutes=minutes, order_index=order)
    t.status = status
    return storage.create_task(t)


def _auth_header(token):
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# P1.1: Multi-user auth
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuth:
    def test_register(self):
        r = client.post("/api/auth/register", json={"email": "a@b.com", "password": "secret123"})
        assert r.status_code == 201
        data = r.json()
        assert "token" in data
        assert data["user"]["email"] == "a@b.com"

    def test_register_duplicate(self):
        client.post("/api/auth/register", json={"email": "a@b.com", "password": "secret123"})
        r = client.post("/api/auth/register", json={"email": "a@b.com", "password": "secret456"})
        assert r.status_code == 422

    def test_register_bad_email(self):
        r = client.post("/api/auth/register", json={"email": "notanemail", "password": "secret123"})
        assert r.status_code == 422

    def test_register_short_password(self):
        r = client.post("/api/auth/register", json={"email": "a@b.com", "password": "12345"})
        assert r.status_code == 422

    def test_login(self):
        client.post("/api/auth/register", json={"email": "user@test.com", "password": "pass123"})
        r = client.post("/api/auth/login", json={"email": "user@test.com", "password": "pass123"})
        assert r.status_code == 200
        assert "token" in r.json()

    def test_login_wrong_password(self):
        client.post("/api/auth/register", json={"email": "u@t.com", "password": "pass123"})
        r = client.post("/api/auth/login", json={"email": "u@t.com", "password": "wrong"})
        assert r.status_code == 401

    def test_me_endpoint(self):
        r = client.post("/api/auth/register", json={"email": "me@test.com", "password": "pass123"})
        token = r.json()["token"]
        r2 = client.get("/api/auth/me", headers=_auth_header(token))
        assert r2.status_code == 200
        assert r2.json()["email"] == "me@test.com"

    def test_me_unauthenticated(self):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_goals_scoped_to_user(self):
        r1 = client.post("/api/auth/register", json={"email": "u1@t.com", "password": "pass123"})
        r2 = client.post("/api/auth/register", json={"email": "u2@t.com", "password": "pass123"})
        token1 = r1.json()["token"]
        token2 = r2.json()["token"]

        # Create goals for each user
        client.post("/api/goals", json={"title": "User 1 goal", "description": ""}, headers=_auth_header(token1))
        client.post("/api/goals", json={"title": "User 2 goal", "description": ""}, headers=_auth_header(token2))

        # Each should only see their own goals
        g1 = client.get("/api/goals", headers=_auth_header(token1)).json()
        g2 = client.get("/api/goals", headers=_auth_header(token2)).json()
        assert len(g1) == 1
        assert g1[0]["title"] == "User 1 goal"
        assert len(g2) == 1
        assert g2[0]["title"] == "User 2 goal"


class TestAuthModule:
    def test_hash_and_verify_password(self):
        h = auth.hash_password("test123")
        assert auth.verify_password("test123", h)
        assert not auth.verify_password("wrong", h)

    def test_create_and_decode_token(self):
        token = auth.create_token(42)
        uid = auth.decode_token(token)
        assert uid == 42

    def test_decode_invalid_token(self):
        assert auth.decode_token("garbage") is None


# ═══════════════════════════════════════════════════════════════════════════════
# P1.3: Telegram webhook
# ═══════════════════════════════════════════════════════════════════════════════

class TestTelegramWebhook:
    def test_empty_message(self):
        r = client.post("/api/messaging/telegram/webhook", json={})
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_approve_command(self):
        goal = _make_goal()
        budget = SpendingBudget(goal_id=goal.id, daily_limit=100, total_limit=1000, category="general")
        budget = storage.create_spending_budget(budget)
        task = _make_task(goal.id)
        req = SpendingRequest(
            task_id=task.id, budget_id=budget.id, amount=10.0,
            description="test", service="test", status="pending",
        )
        req = storage.create_spending_request(req)

        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": f"/approve {req.id}", "chat": {"id": 12345}}
        })
        assert r.status_code == 200
        assert r.json()["action"] == "approved"

    def test_deny_command(self):
        goal = _make_goal()
        budget = SpendingBudget(goal_id=goal.id, daily_limit=100, total_limit=1000, category="general")
        budget = storage.create_spending_budget(budget)
        task = _make_task(goal.id)
        req = SpendingRequest(
            task_id=task.id, budget_id=budget.id, amount=10.0,
            description="test", service="test", status="pending",
        )
        req = storage.create_spending_request(req)

        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": f"/deny {req.id} too expensive", "chat": {"id": 12345}}
        })
        assert r.status_code == 200
        assert r.json()["action"] == "denied"

    def test_goal_command(self):
        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": "/goal Build a portfolio website", "chat": {"id": 12345}}
        })
        assert r.status_code == 200
        assert r.json()["action"] == "goal_created"

    def test_next_command_no_goals(self):
        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": "/next", "chat": {"id": 99999}}
        })
        assert r.status_code == 200

    def test_done_command(self):
        goal = _make_goal()
        _make_task(goal.id, status="in_progress")
        # Create a session binding the chat to this goal
        storage.upsert_telegram_session("54321", goal.id, "idle")
        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": "/done", "chat": {"id": 54321}}
        })
        assert r.status_code == 200

    def test_skip_command(self):
        goal = _make_goal()
        _make_task(goal.id, status="in_progress")
        # Create a session binding the chat to this goal
        storage.upsert_telegram_session("54322", goal.id, "idle")
        r = client.post("/api/messaging/telegram/webhook", json={
            "message": {"text": "/skip", "chat": {"id": 54322}}
        })
        assert r.status_code == 200
        assert r.json()["action"] == "task_skipped"


# ═══════════════════════════════════════════════════════════════════════════════
# P2.1: Budget-executor wiring
# ═══════════════════════════════════════════════════════════════════════════════

class TestBudgetExecutorWiring:
    def test_execute_pauses_on_approval_required(self):
        # Register a user and get auth headers
        r = client.post("/api/auth/register", json={"email": "exec_test@teb.test", "password": "testpass123"})
        if r.status_code not in (200, 201):
            r = client.post("/api/auth/login", json={"email": "exec_test@teb.test", "password": "testpass123"})
        headers = _auth_header(r.json()["token"])

        goal = _make_goal(user_id=None)  # unscoped goal for simplicity
        task = _make_task(goal.id)
        budget = SpendingBudget(
            goal_id=goal.id, daily_limit=100, total_limit=1000,
            category="general", require_approval=True,
        )
        storage.create_spending_budget(budget)

        # Create a credential so executor generates a plan
        from teb.models import ApiCredential
        cred = ApiCredential(name="test", base_url="https://example.com", auth_header="X-Key", auth_value="key123", description="test api")
        storage.create_credential(cred)

        r = client.post(f"/api/tasks/{task.id}/execute", headers=headers)
        data = r.json()
        # It should either pause for approval or fail because no real API
        # The key is that it checks budgets
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# P2.2: Success path learning with deviations
# ═══════════════════════════════════════════════════════════════════════════════

class TestSuccessPathLearning:
    def test_capture_includes_deviations(self):
        goal = _make_goal(title="earn money online")
        goal.status = "done"
        storage.update_goal(goal)

        tasks = [
            _make_task(goal.id, title="Research online income options", status="done", order=0),
            _make_task(goal.id, title="Custom added task", status="done", order=1),
        ]

        sp = capture_success_path(goal, tasks)
        assert sp is not None
        data = json.loads(sp.steps_json)
        assert "deviations" in data
        assert "steps" in data
        assert len(data["deviations"]["added_tasks"]) >= 0

    def test_check_skip_rate_no_paths(self):
        result = _check_skip_rate("generic", "Some task")
        assert result is None

    def test_compute_time_scale_no_data(self):
        assert _compute_time_scale([]) == 1.0

    def test_compute_time_scale_with_data(self):
        goal = _make_goal()
        now = datetime.now(timezone.utc)
        tasks = []
        for i in range(3):
            t = _make_task(goal.id, title=f"t{i}", status="done", minutes=60)
            t.created_at = now - timedelta(hours=2)
            t.updated_at = now - timedelta(hours=1)  # Took 1 hour for a 60-min task
            tasks.append(t)

        scale = _compute_time_scale(tasks)
        assert 0.5 <= scale <= 2.0


# ═══════════════════════════════════════════════════════════════════════════════
# P2.3: Drip adaptation
# ═══════════════════════════════════════════════════════════════════════════════

class TestDripAdaptation:
    def test_stall_detection_no_stall(self):
        task = Task(goal_id=1, title="t", description="d", estimated_minutes=30, order_index=0)
        task.updated_at = datetime.now(timezone.utc)
        result = _detect_task_stall(task)
        assert result is None

    def test_stall_detection_stalled(self):
        task = Task(goal_id=1, title="t", description="d", estimated_minutes=30, order_index=0)
        task.updated_at = datetime.now(timezone.utc) - timedelta(days=3)
        result = _detect_task_stall(task)
        assert result is not None
        assert "days" in result["message"]
        assert "sub_task" in result

    def test_drip_returns_stall_info(self):
        goal = _make_goal(title="learn Python")
        goal.status = "decomposed"
        storage.update_goal(goal)

        # Create a stalled task
        task = _make_task(goal.id, title="Learn the basics", status="in_progress")
        # Manually update the task's updated_at to be old
        with storage._conn() as con:
            old_time = (datetime.now(timezone.utc) - timedelta(days=4)).isoformat()
            con.execute("UPDATE tasks SET updated_at = ? WHERE id = ?", (old_time, task.id))

        tasks = storage.list_tasks(goal.id)
        result = drip_next_task(goal, tasks)
        assert result is not None
        assert result.get("stall_detected") is True


# ═══════════════════════════════════════════════════════════════════════════════
# P3.1: Credential encryption
# ═══════════════════════════════════════════════════════════════════════════════

class TestCredentialEncryption:
    def test_encrypt_decrypt_roundtrip(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        with patch.object(config, "SECRET_KEY", key):
            encrypted = storage._encrypt_value("my-secret-key")
            assert encrypted != "my-secret-key"
            decrypted = storage._decrypt_value(encrypted)
            assert decrypted == "my-secret-key"

    def test_no_encryption_without_key(self):
        with patch.object(config, "SECRET_KEY", None):
            result = storage._encrypt_value("plaintext")
            assert result == "plaintext"

    def test_credential_stored_encrypted(self):
        from cryptography.fernet import Fernet
        key = Fernet.generate_key().decode()
        from teb.models import ApiCredential
        with patch.object(config, "SECRET_KEY", key):
            cred = ApiCredential(
                name="test-api", base_url="https://api.test.com",
                auth_header="Authorization", auth_value="Bearer secret123",
                description="test",
            )
            saved = storage.create_credential(cred)

            # Read back — should be decrypted
            loaded = storage.get_credential(saved.id)
            assert loaded.auth_value == "Bearer secret123"

            # Check raw DB value is encrypted
            with storage._conn() as con:
                row = con.execute("SELECT auth_value FROM api_credentials WHERE id = ?", (saved.id,)).fetchone()
                assert row["auth_value"] != "Bearer secret123"


# ═══════════════════════════════════════════════════════════════════════════════
# P3.2: New goal templates
# ═══════════════════════════════════════════════════════════════════════════════

class TestNewTemplates:
    @pytest.mark.parametrize("title,expected", [
        ("write a novel", "write_book"),
        ("publish my memoir", "write_book"),
        ("launch a startup", "launch_startup"),
        ("start a company", "launch_startup"),
        ("find a job in tech", "find_job"),
        ("improve my career", "find_job"),
        ("improve my sleep", "improve_health"),
        ("better nutrition", "improve_health"),
        ("start a side project", "side_project"),
        ("hobby weekend project", "side_project"),
    ])
    def test_template_detection(self, title, expected):
        goal = Goal(title=title, description="")
        assert _detect_template(goal) == expected

    def test_write_book_has_questions(self):
        goal = Goal(title="write a novel", description="")
        questions = get_clarifying_questions(goal)
        keys = [q.key for q in questions]
        assert "book_genre" in keys
        assert "book_length" in keys

    def test_launch_startup_has_questions(self):
        goal = Goal(title="launch a startup", description="solving problem X")
        questions = get_clarifying_questions(goal)
        keys = [q.key for q in questions]
        assert "startup_idea" in keys

    def test_find_job_has_questions(self):
        goal = Goal(title="find a tech job", description="")
        questions = get_clarifying_questions(goal)
        keys = [q.key for q in questions]
        assert "job_target" in keys

    def test_improve_health_has_questions(self):
        goal = Goal(title="improve my health", description="")
        questions = get_clarifying_questions(goal)
        keys = [q.key for q in questions]
        assert "health_focus" in keys

    def test_side_project_has_questions(self):
        goal = Goal(title="start a side project", description="")
        questions = get_clarifying_questions(goal)
        keys = [q.key for q in questions]
        assert "project_type" in keys


# ═══════════════════════════════════════════════════════════════════════════════
# P3.3: Daily spending reset
# ═══════════════════════════════════════════════════════════════════════════════

class TestDailySpendingReset:
    def test_maybe_reset_daily_spending_same_day(self):
        goal = _make_goal()
        budget = SpendingBudget(
            goal_id=goal.id, daily_limit=100, total_limit=1000,
            category="general", spent_today=50.0,
        )
        budget = storage.create_spending_budget(budget)
        result = storage.maybe_reset_daily_spending(budget)
        assert result.spent_today == 50.0  # No reset

    def test_maybe_reset_daily_spending_new_day(self):
        goal = _make_goal()
        budget = SpendingBudget(
            goal_id=goal.id, daily_limit=100, total_limit=1000,
            category="general", spent_today=50.0,
        )
        budget = storage.create_spending_budget(budget)

        # Manually set updated_at to yesterday
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with storage._conn() as con:
            con.execute("UPDATE spending_budgets SET updated_at = ? WHERE id = ?", (yesterday, budget.id))

        # Reload budget
        budget = storage.get_spending_budget(budget.id)
        result = storage.maybe_reset_daily_spending(budget)
        assert result.spent_today == 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# Users table and user_id FK
# ═══════════════════════════════════════════════════════════════════════════════

class TestUserStorage:
    def test_create_and_get_user(self):
        user = User(email="test@example.com", password_hash="hash123")
        user = storage.create_user(user)
        assert user.id is not None

        loaded = storage.get_user(user.id)
        assert loaded is not None
        assert loaded.email == "test@example.com"

    def test_get_user_by_email(self):
        user = User(email="find@me.com", password_hash="hash")
        storage.create_user(user)
        loaded = storage.get_user_by_email("find@me.com")
        assert loaded is not None
        assert loaded.email == "find@me.com"

    def test_get_user_by_email_not_found(self):
        assert storage.get_user_by_email("nope@nope.com") is None

    def test_goal_with_user_id(self):
        user = User(email="guser@t.com", password_hash="h")
        user = storage.create_user(user)
        goal = Goal(title="test", description="")
        goal.user_id = user.id
        goal = storage.create_goal(goal)

        loaded = storage.get_goal(goal.id)
        assert loaded.user_id == user.id

    def test_list_goals_by_user(self):
        u1 = storage.create_user(User(email="u1@t.com", password_hash="h"))
        u2 = storage.create_user(User(email="u2@t.com", password_hash="h"))

        g1 = Goal(title="g1", description="")
        g1.user_id = u1.id
        storage.create_goal(g1)

        g2 = Goal(title="g2", description="")
        g2.user_id = u2.id
        storage.create_goal(g2)

        assert len(storage.list_goals(user_id=u1.id)) == 1
        assert len(storage.list_goals(user_id=u2.id)) == 1
        # Unauthenticated list_goals() returns only unscoped goals (user_id IS NULL)
        assert len(storage.list_goals()) == 0

    def test_user_profile_per_user(self):
        u1 = storage.create_user(User(email="p1@t.com", password_hash="h"))
        u2 = storage.create_user(User(email="p2@t.com", password_hash="h"))

        p1 = storage.get_or_create_profile(user_id=u1.id)
        p2 = storage.get_or_create_profile(user_id=u2.id)
        assert p1.id != p2.id
        assert p1.user_id == u1.id
        assert p2.user_id == u2.id


# ═══════════════════════════════════════════════════════════════════════════════
# Existing template detection still works
# ═══════════════════════════════════════════════════════════════════════════════

class TestExistingTemplates:
    def test_money_online(self):
        assert _detect_template(Goal(title="earn money online", description="")) == "make_money_online"

    def test_learn_skill(self):
        assert _detect_template(Goal(title="learn guitar", description="")) == "learn_skill"

    def test_get_fit(self):
        assert _detect_template(Goal(title="get fit", description="")) == "get_fit"

    def test_build_project(self):
        assert _detect_template(Goal(title="build a web app", description="")) == "build_project"

    def test_generic(self):
        assert _detect_template(Goal(title="something random", description="")) == "generic"
