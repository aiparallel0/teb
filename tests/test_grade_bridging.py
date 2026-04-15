"""
Comprehensive tests for the bridging plan implementation.

Tests cover all 6 phases:
- Phase 1: AI Priority Triage & Risk Assessment
- Phase 2: Persistent Auto-Scheduling
- Phase 3: Automated Progress Reporting
- Phase 4: Workload Balancing
- Phase 5: Enhanced Search with Semantic Ranking
- Phase 6: Social Accountability in Gamification
"""

import json
import os

import pytest
from fastapi.testclient import TestClient

from teb import storage
from teb.models import (
    Goal,
    LeaderboardEntry,
    ProgressReport,
    Streak,
    Task,
    TaskRisk,
    TaskSchedule,
    TeamChallenge,
)


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture()
def client():
    from teb.main import app
    return TestClient(app, raise_server_exceptions=False)


def _register_and_login(client):
    """Helper: register and login a user, return auth headers."""
    from teb.main import reset_rate_limits
    reset_rate_limits()
    resp = client.post("/api/auth/register", json={"email": "test@example.com", "password": "TestPass123!"})
    assert resp.status_code == 201
    resp = client.post("/api/auth/login", json={"email": "test@example.com", "password": "TestPass123!"})
    assert resp.status_code == 200
    token = resp.json()["token"]
    return {"Authorization": f"Bearer {token}"}


def _create_goal_with_tasks(headers, client, n=3, with_deps=False):
    """Helper: create a goal with n tasks."""
    resp = client.post("/api/goals", json={"title": "Test Goal", "description": "A test goal"}, headers=headers)
    assert resp.status_code == 201
    goal = resp.json()
    tasks = []
    for i in range(n):
        body = {
            "goal_id": goal["id"],
            "title": f"Task {i + 1}",
            "description": f"Description {i + 1}",
            "estimated_minutes": 30 + i * 15,
        }
        if with_deps and i > 0:
            body["depends_on"] = [tasks[i - 1]["id"]]
        resp = client.post("/api/tasks", json=body, headers=headers)
        assert resp.status_code == 201
        tasks.append(resp.json())
    return goal, tasks


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 1: AI Priority Triage & Risk Assessment
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskRiskModel:
    """Phase 1: TaskRisk model tests."""

    def test_task_risk_model(self):
        risk = TaskRisk(task_id=1, goal_id=1, risk_score=0.75,
                        risk_factors='["overdue", "large task"]', estimated_delay=45)
        d = risk.to_dict()
        assert d["risk_score"] == 0.75
        assert d["risk_factors"] == ["overdue", "large task"]
        assert d["estimated_delay"] == 45

    def test_task_risk_empty_factors(self):
        risk = TaskRisk(task_id=1, goal_id=1)
        d = risk.to_dict()
        assert d["risk_score"] == 0.0
        assert d["risk_factors"] == []
        assert d["estimated_delay"] == 0


class TestRiskAssessmentStorage:
    """Phase 1: Risk assessment CRUD in storage."""

    def test_create_and_get_risk(self):
        risk = TaskRisk(task_id=999, goal_id=1, risk_score=0.5,
                        risk_factors='["test factor"]', estimated_delay=30)
        saved = storage.create_risk_assessment(risk)
        assert saved.id is not None
        fetched = storage.get_risk_assessment(999)
        assert fetched is not None
        assert fetched.risk_score == 0.5

    def test_get_nonexistent_risk(self):
        result = storage.get_risk_assessment(99999)
        assert result is None

    def test_list_risks_by_goal(self):
        for i in range(3):
            storage.create_risk_assessment(
                TaskRisk(task_id=i + 1, goal_id=42, risk_score=0.1 * (i + 1))
            )
        risks = storage.list_risk_assessments(42)
        assert len(risks) == 3
        # Should be ordered by risk_score DESC
        assert risks[0].risk_score >= risks[1].risk_score


class TestTriageEndpoint:
    """Phase 1: POST /api/goals/{id}/triage endpoint."""

    def test_triage_happy_path(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        resp = client.post(f"/api/goals/{goal['id']}/triage", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "triage" in data
        assert data["count"] == 3
        for entry in data["triage"]:
            assert "task_id" in entry
            assert "priority" in entry
            assert "score" in entry

    def test_triage_empty_goal(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "Empty", "description": ""}, headers=headers)
        goal_id = resp.json()["id"]
        resp = client.post(f"/api/goals/{goal_id}/triage", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_triage_requires_auth(self, client):
        resp = client.post("/api/goals/1/triage")
        assert resp.status_code == 401

    def test_triage_nonexistent_goal(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals/99999/triage", headers=headers)
        assert resp.status_code == 404


class TestRiskEndpoint:
    """Phase 1: GET /api/tasks/{id}/risk endpoint."""

    def test_risk_happy_path(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        resp = client.get(f"/api/tasks/{tasks[0]['id']}/risk", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "risk_score" in data
        assert "risk_factors" in data
        assert "risk_level" in data

    def test_risk_nonexistent_task(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/tasks/99999/risk", headers=headers)
        assert resp.status_code == 404

    def test_risk_requires_auth(self, client):
        resp = client.get("/api/tasks/1/risk")
        assert resp.status_code == 401


class TestTriageTemplate:
    """Phase 1: Template-based triage logic."""

    def test_triage_with_dependencies(self):
        from teb.decomposer import _triage_template
        tasks = [
            Task(goal_id=1, title="Base", description="", id=1, depends_on="[]"),
            Task(goal_id=1, title="Depends", description="", id=2, depends_on="[1]"),
            Task(goal_id=1, title="Also depends", description="", id=3, depends_on="[1]"),
        ]
        results = _triage_template(tasks)
        # Task 1 blocks 2 others, should have higher score
        task1_entry = [r for r in results if r["task_id"] == 1][0]
        assert "blocks 2 task(s)" in task1_entry["reason"]

    def test_triage_with_overdue(self):
        from teb.decomposer import _triage_template
        tasks = [
            Task(goal_id=1, title="Overdue", description="", id=1, due_date="2020-01-01"),
            Task(goal_id=1, title="Future", description="", id=2, due_date="2099-12-31"),
        ]
        results = _triage_template(tasks)
        overdue = [r for r in results if r["task_id"] == 1][0]
        assert overdue["priority"] in ("high", "medium")
        assert "overdue" in overdue["reason"]


class TestEstimateRisk:
    """Phase 1: estimate_risk() function tests."""

    def test_risk_low_for_simple_task(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=1)
        from teb.decomposer import estimate_risk
        result = estimate_risk(tasks[0]["id"])
        assert result["risk_score"] >= 0
        assert result["risk_level"] in ("low", "medium", "high", "critical")

    def test_risk_higher_for_large_task(self):
        # Create tasks directly
        g = storage.create_goal(Goal(title="Test", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="Big task", description="", estimated_minutes=300))
        from teb.decomposer import estimate_risk
        result = estimate_risk(t.id)
        assert result["risk_score"] > 0
        assert "large task" in " ".join(result["risk_factors"])

    def test_risk_nonexistent_task(self):
        from teb.decomposer import estimate_risk
        result = estimate_risk(99999)
        assert "error" in result


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 2: Persistent Auto-Scheduling
# ═══════════════════════════════════════════════════════════════════════════════

class TestTaskScheduleModel:
    """Phase 2: TaskSchedule model tests."""

    def test_task_schedule_model(self):
        sched = TaskSchedule(task_id=1, goal_id=1, user_id=1,
                             scheduled_start="2025-06-15T09:00:00", scheduled_end="2025-06-15T10:00:00",
                             calendar_slot=1)
        d = sched.to_dict()
        assert d["scheduled_start"] == "2025-06-15T09:00:00"
        assert d["calendar_slot"] == 1


class TestScheduleStorage:
    """Phase 2: Schedule CRUD in storage."""

    def test_create_and_list_schedules(self):
        sched = TaskSchedule(task_id=1, goal_id=1, user_id=1,
                             scheduled_start="2025-06-15T09:00:00",
                             scheduled_end="2025-06-15T10:00:00")
        saved = storage.create_task_schedule(sched)
        assert saved.id is not None
        schedules = storage.list_task_schedules(goal_id=1)
        assert len(schedules) == 1

    def test_list_by_user(self):
        for i in range(2):
            storage.create_task_schedule(
                TaskSchedule(task_id=i + 1, goal_id=i + 1, user_id=42,
                             scheduled_start=f"2025-06-1{i}T09:00:00",
                             scheduled_end=f"2025-06-1{i}T10:00:00")
            )
        schedules = storage.list_task_schedules(user_id=42)
        assert len(schedules) == 2

    def test_delete_schedules(self):
        storage.create_task_schedule(
            TaskSchedule(task_id=1, goal_id=10, user_id=1,
                         scheduled_start="2025-06-15T09:00:00",
                         scheduled_end="2025-06-15T10:00:00")
        )
        deleted = storage.delete_task_schedules(10)
        assert deleted == 1
        assert len(storage.list_task_schedules(goal_id=10)) == 0


class TestAutoScheduleEndpoint:
    """Phase 2: POST /api/goals/{id}/auto-schedule endpoint."""

    def test_auto_schedule_happy_path(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        resp = client.post(f"/api/goals/{goal['id']}/auto-schedule", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["count"] == 3
        assert len(data["schedules"]) == 3
        # Each schedule should have times
        for s in data["schedules"]:
            assert s["scheduled_start"] is not None
            assert s["scheduled_end"] is not None

    def test_auto_schedule_empty_goal(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "Empty", "description": ""}, headers=headers)
        goal_id = resp.json()["id"]
        resp = client.post(f"/api/goals/{goal_id}/auto-schedule", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0

    def test_auto_schedule_requires_auth(self, client):
        resp = client.post("/api/goals/1/auto-schedule")
        assert resp.status_code == 401

    def test_reschedule_clears_old(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        # Schedule twice
        client.post(f"/api/goals/{goal['id']}/auto-schedule", headers=headers)
        resp = client.post(f"/api/goals/{goal['id']}/auto-schedule", headers=headers)
        assert resp.status_code == 200
        # Should only have the latest schedule entries
        assert resp.json()["count"] == 2


class TestUserScheduleEndpoint:
    """Phase 2: GET /api/users/me/schedule endpoint."""

    def test_get_user_schedule(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        client.post(f"/api/goals/{goal['id']}/auto-schedule", headers=headers)
        resp = client.get("/api/users/me/schedule", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_user_schedule_requires_auth(self, client):
        resp = client.get("/api/users/me/schedule")
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 3: Automated Progress Reporting
# ═══════════════════════════════════════════════════════════════════════════════

class TestProgressReportModel:
    """Phase 3: ProgressReport model tests."""

    def test_progress_report_model(self):
        report = ProgressReport(goal_id=1, user_id=1, summary="Test summary",
                                metrics_json='{"pct": 50}', blockers_json='["blocker1"]',
                                next_actions_json='["action1"]')
        d = report.to_dict()
        assert d["summary"] == "Test summary"
        assert d["metrics"]["pct"] == 50
        assert d["blockers"] == ["blocker1"]
        assert d["next_actions"] == ["action1"]


class TestProgressReportStorage:
    """Phase 3: Progress report CRUD in storage."""

    def test_create_and_list_reports(self):
        report = ProgressReport(goal_id=1, user_id=1, summary="Test",
                                metrics_json='{"total": 10}')
        saved = storage.create_progress_report(report)
        assert saved.id is not None
        reports = storage.list_progress_reports(1)
        assert len(reports) == 1
        assert reports[0].summary == "Test"

    def test_list_empty(self):
        reports = storage.list_progress_reports(9999)
        assert len(reports) == 0


class TestReportEndpoint:
    """Phase 3: POST /api/goals/{id}/report endpoint."""

    def test_generate_report_happy_path(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        # Complete one task using PATCH
        client.patch(f"/api/tasks/{tasks[0]['id']}", json={"status": "done"}, headers=headers)
        resp = client.post(f"/api/goals/{goal['id']}/report", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "summary" in data
        assert "metrics" in data
        assert "blockers" in data
        assert "next_actions" in data
        assert data["metrics"]["total_tasks"] == 3
        assert data["metrics"]["completed_tasks"] == 1

    def test_generate_report_all_done(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        for t in tasks:
            client.patch(f"/api/tasks/{t['id']}", json={"status": "done"}, headers=headers)
        resp = client.post(f"/api/goals/{goal['id']}/report", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["metrics"]["percent_complete"] == 100.0

    def test_generate_report_requires_auth(self, client):
        resp = client.post("/api/goals/1/report")
        assert resp.status_code == 401

    def test_report_nonexistent_goal(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals/99999/report", headers=headers)
        assert resp.status_code == 404


class TestListReportsEndpoint:
    """Phase 3: GET /api/goals/{id}/reports endpoint."""

    def test_list_reports(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        # Generate two reports
        client.post(f"/api/goals/{goal['id']}/report", headers=headers)
        client.post(f"/api/goals/{goal['id']}/report", headers=headers)
        resp = client.get(f"/api/goals/{goal['id']}/reports", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_list_reports_empty(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "Empty", "description": ""}, headers=headers)
        goal_id = resp.json()["id"]
        resp = client.get(f"/api/goals/{goal_id}/reports", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 0


class TestReportingModule:
    """Phase 3: Template reporting logic."""

    def test_template_report_with_blocked_tasks(self):
        from teb.reporting import _generate_template_report
        goal = storage.create_goal(Goal(title="Test", description=""))
        t1 = storage.create_task(Task(goal_id=goal.id, title="Done task", description="", status="done"))
        t2 = storage.create_task(Task(goal_id=goal.id, title="Blocked", description="",
                                       depends_on=json.dumps([t1.id])))
        t3 = storage.create_task(Task(goal_id=goal.id, title="Failed", description="", status="failed"))
        tasks = storage.list_tasks(goal_id=goal.id)
        report = _generate_template_report(goal, tasks, 1)
        assert "Failed" in report.summary or "failed" in report.summary.lower() or report.id is not None
        blockers = json.loads(report.blockers_json)
        assert any("failed" in b.lower() or "Failed" in b for b in blockers)


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 4: Workload Balancing
# ═══════════════════════════════════════════════════════════════════════════════

class TestWorkloadCapacity:
    """Phase 4: Workload capacity analysis."""

    def test_get_capacity_no_tasks(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/users/me/workload", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["assigned_tasks"] == 0
        assert data["overloaded"] is False

    def test_get_capacity_requires_auth(self, client):
        resp = client.get("/api/users/me/workload")
        assert resp.status_code == 401


class TestRebalanceEndpoint:
    """Phase 4: POST /api/goals/{id}/rebalance endpoint."""

    def test_rebalance_happy_path(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=3)
        resp = client.post(f"/api/goals/{goal['id']}/rebalance", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "suggestions" in data
        assert data["total_actionable"] == 3

    def test_rebalance_empty_goal(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals", json={"title": "Empty", "description": ""}, headers=headers)
        goal_id = resp.json()["id"]
        resp = client.post(f"/api/goals/{goal_id}/rebalance", headers=headers)
        assert resp.status_code == 200
        assert "No tasks found" in resp.json()["message"]

    def test_rebalance_requires_auth(self, client):
        resp = client.post("/api/goals/1/rebalance")
        assert resp.status_code == 401

    def test_rebalance_nonexistent_goal(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/goals/99999/rebalance", headers=headers)
        assert resp.status_code == 404


class TestWorkloadModule:
    """Phase 4: Workload module logic."""

    def test_balance_with_unassigned(self):
        from teb.workload import balance_workload
        g = storage.create_goal(Goal(title="Test", description=""))
        for i in range(3):
            storage.create_task(Task(goal_id=g.id, title=f"Task {i}", description=""))
        result = balance_workload(g.id, 1)
        assert result["unassigned_count"] == 3
        # Should suggest assigning tasks
        assert any(s["type"] == "no_assignments" for s in result["suggestions"])

    def test_capacity_with_assigned_tasks(self):
        from teb.workload import get_user_capacity
        g = storage.create_goal(Goal(title="Test", description=""))
        t = storage.create_task(Task(goal_id=g.id, title="Assigned", description="",
                                      estimated_minutes=60))
        # Assign the task via update_task
        t.assigned_to = 42
        storage.update_task(t)
        result = get_user_capacity(42)
        assert result["assigned_tasks"] == 1
        assert result["total_estimated_minutes"] == 60


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 5: Enhanced Search
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnhancedSearch:
    """Phase 5: Search with semantic=true parameter."""

    def test_search_with_semantic_false(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        resp = client.get("/api/search?q=Task", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["semantic"] is False
        assert data["count"] >= 0

    def test_search_with_semantic_true(self, client):
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=2)
        # Without AI configured, semantic=true should fall back to normal search
        resp = client.get("/api/search?q=Task&semantic=true", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["semantic"] is True
        assert data["count"] >= 0

    def test_search_empty_query(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/search?q=", headers=headers)
        assert resp.status_code == 400


class TestSearchModule:
    """Phase 5: Search module logic."""

    def test_quick_search_with_semantic_flag(self):
        from teb.search import quick_search
        g = storage.create_goal(Goal(title="Find me", description="searchable goal"))
        storage.create_task(Task(goal_id=g.id, title="Searchable task", description=""))
        # Without AI, semantic=True should still return results (template fallback)
        results = quick_search("searchable", semantic=True)
        assert len(results) >= 1

    def test_quick_search_empty(self):
        from teb.search import quick_search
        assert quick_search("") == []
        assert quick_search("   ") == []


# ═══════════════════════════════════════════════════════════════════════════════
# PHASE 6: Social Gamification
# ═══════════════════════════════════════════════════════════════════════════════

class TestStreakModel:
    """Phase 6: Streak model tests."""

    def test_streak_model(self):
        streak = Streak(user_id=1, current_streak=5, longest_streak=10,
                        last_activity_date="2025-06-15", streak_type="daily")
        d = streak.to_dict()
        assert d["current_streak"] == 5
        assert d["longest_streak"] == 10
        assert d["streak_type"] == "daily"


class TestLeaderboardModel:
    """Phase 6: LeaderboardEntry model tests."""

    def test_leaderboard_model(self):
        entry = LeaderboardEntry(user_id=1, score=100, rank=1, period="weekly")
        d = entry.to_dict()
        assert d["score"] == 100
        assert d["period"] == "weekly"


class TestTeamChallengeModel:
    """Phase 6: TeamChallenge model tests."""

    def test_team_challenge_model(self):
        challenge = TeamChallenge(title="Sprint Challenge", description="Complete 10 tasks",
                                  goal_type="tasks_completed", target_value=10,
                                  participants_json="[1, 2, 3]")
        d = challenge.to_dict()
        assert d["title"] == "Sprint Challenge"
        assert d["participants"] == [1, 2, 3]
        assert d["target_value"] == 10


class TestStreakStorage:
    """Phase 6: Streak CRUD in storage."""

    def test_get_or_create_streak(self):
        streak = storage.get_or_create_streak(1)
        assert streak.user_id == 1
        assert streak.current_streak == 0

    def test_update_streak(self):
        streak = storage.update_streak(1)
        assert streak.current_streak == 1

    def test_streak_types(self):
        daily = storage.get_or_create_streak(1, "daily")
        weekly = storage.get_or_create_streak(1, "weekly")
        assert daily.streak_type == "daily"
        assert weekly.streak_type == "weekly"


class TestLeaderboardStorage:
    """Phase 6: Leaderboard CRUD in storage."""

    def test_update_and_get_leaderboard(self):
        storage.update_leaderboard(1, score=100, period="weekly")
        storage.update_leaderboard(2, score=200, period="weekly")
        entries = storage.get_leaderboard(period="weekly")
        assert len(entries) == 2
        assert entries[0].score > entries[1].score
        assert entries[0].rank == 1
        assert entries[1].rank == 2

    def test_update_existing_score(self):
        storage.update_leaderboard(1, score=50, period="monthly")
        storage.update_leaderboard(1, score=150, period="monthly")
        entries = storage.get_leaderboard(period="monthly")
        assert len(entries) == 1
        assert entries[0].score == 150

    def test_empty_leaderboard(self):
        entries = storage.get_leaderboard(period="all_time")
        assert len(entries) == 0


class TestTeamChallengeStorage:
    """Phase 6: Team challenge CRUD in storage."""

    def test_create_and_get_challenge(self):
        ch = TeamChallenge(title="Test", description="desc", target_value=5, creator_id=1)
        saved = storage.create_team_challenge(ch)
        assert saved.id is not None
        fetched = storage.get_team_challenge(saved.id)
        assert fetched is not None
        assert fetched.title == "Test"

    def test_list_challenges(self):
        storage.create_team_challenge(TeamChallenge(title="Active", status="active"))
        storage.create_team_challenge(TeamChallenge(title="Done", status="completed"))
        all_ch = storage.list_team_challenges()
        assert len(all_ch) == 2
        active = storage.list_team_challenges(status="active")
        assert len(active) == 1

    def test_update_challenge_progress(self):
        ch = storage.create_team_challenge(TeamChallenge(title="Test", target_value=3))
        updated = storage.update_team_challenge_progress(ch.id, increment=2)
        assert updated.current_value == 2
        assert updated.status == "active"
        # Complete it
        updated = storage.update_team_challenge_progress(ch.id, increment=1)
        assert updated.current_value == 3
        assert updated.status == "completed"

    def test_update_nonexistent_challenge(self):
        result = storage.update_team_challenge_progress(99999)
        assert result is None


class TestStreakEndpoint:
    """Phase 6: GET /api/users/me/streak endpoint."""

    def test_get_streak(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/users/me/streak", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "current_streak" in data
        assert "longest_streak" in data

    def test_streak_requires_auth(self, client):
        resp = client.get("/api/users/me/streak")
        assert resp.status_code == 401


class TestLeaderboardEndpoint:
    """Phase 6: GET /api/leaderboard endpoint."""

    def test_get_leaderboard_endpoint(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/leaderboard", headers=headers)
        assert resp.status_code == 200
        data = resp.json()
        assert "entries" in data
        assert data["period"] == "weekly"

    def test_leaderboard_with_period(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/leaderboard?period=monthly", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["period"] == "monthly"

    def test_leaderboard_invalid_period(self, client):
        headers = _register_and_login(client)
        resp = client.get("/api/leaderboard?period=invalid", headers=headers)
        assert resp.status_code == 400

    def test_leaderboard_requires_auth(self, client):
        resp = client.get("/api/leaderboard")
        assert resp.status_code == 401


class TestChallengeEndpoints:
    """Phase 6: POST /api/challenges and GET /api/challenges endpoints."""

    def test_create_challenge(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/challenges", json={
            "title": "Sprint Challenge",
            "description": "Complete 10 tasks",
            "target_value": 10,
        }, headers=headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "Sprint Challenge"
        assert data["target_value"] == 10

    def test_create_challenge_no_title(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/challenges", json={"description": "no title"}, headers=headers)
        assert resp.status_code == 400

    def test_list_challenges_endpoint(self, client):
        headers = _register_and_login(client)
        client.post("/api/challenges", json={"title": "Ch1"}, headers=headers)
        client.post("/api/challenges", json={"title": "Ch2"}, headers=headers)
        resp = client.get("/api/challenges", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 2

    def test_update_challenge_progress(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/challenges", json={"title": "Test", "target_value": 5}, headers=headers)
        ch_id = resp.json()["id"]
        resp = client.post(f"/api/challenges/{ch_id}/progress", json={"increment": 3}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["current_value"] == 3

    def test_challenge_progress_nonexistent(self, client):
        headers = _register_and_login(client)
        resp = client.post("/api/challenges/99999/progress", json={"increment": 1}, headers=headers)
        assert resp.status_code == 404

    def test_challenge_requires_auth(self, client):
        resp = client.post("/api/challenges", json={"title": "Test"})
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# CROSS-PHASE INTEGRATION TESTS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIntegration:
    """Cross-phase integration tests."""

    def test_full_workflow(self, client):
        """Create goal, triage, schedule, generate report — all in one flow."""
        headers = _register_and_login(client)
        goal, tasks = _create_goal_with_tasks(headers, client, n=4, with_deps=True)

        # Phase 1: Triage
        resp = client.post(f"/api/goals/{goal['id']}/triage", headers=headers)
        assert resp.status_code == 200
        triage = resp.json()["triage"]
        assert len(triage) == 4

        # Phase 1: Risk assessment
        resp = client.get(f"/api/tasks/{tasks[0]['id']}/risk", headers=headers)
        assert resp.status_code == 200

        # Phase 2: Auto-schedule
        resp = client.post(f"/api/goals/{goal['id']}/auto-schedule", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 4

        # Check user schedule
        resp = client.get("/api/users/me/schedule", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["count"] == 4

        # Complete a task
        client.patch(f"/api/tasks/{tasks[0]['id']}", json={"status": "done"}, headers=headers)

        # Phase 3: Generate report
        resp = client.post(f"/api/goals/{goal['id']}/report", headers=headers)
        assert resp.status_code == 200
        assert resp.json()["metrics"]["completed_tasks"] == 1

        # Phase 4: Workload analysis
        resp = client.get("/api/users/me/workload", headers=headers)
        assert resp.status_code == 200

        # Phase 4: Rebalance
        resp = client.post(f"/api/goals/{goal['id']}/rebalance", headers=headers)
        assert resp.status_code == 200

    def test_gamification_flow(self, client):
        """Create challenge, update progress, check streak and leaderboard."""
        headers = _register_and_login(client)

        # Create challenge
        resp = client.post("/api/challenges", json={
            "title": "Team Sprint",
            "target_value": 5,
        }, headers=headers)
        assert resp.status_code == 201
        ch_id = resp.json()["id"]

        # Update progress
        resp = client.post(f"/api/challenges/{ch_id}/progress", json={"increment": 3}, headers=headers)
        assert resp.status_code == 200
        assert resp.json()["current_value"] == 3

        # Check streak
        resp = client.get("/api/users/me/streak", headers=headers)
        assert resp.status_code == 200

        # Check leaderboard
        resp = client.get("/api/leaderboard", headers=headers)
        assert resp.status_code == 200
