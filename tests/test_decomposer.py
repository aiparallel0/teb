"""Unit tests for teb.decomposer"""
import pytest

from teb.models import Goal, Task
from teb.decomposer import (
    _detect_template,
    decompose_template,
    get_clarifying_questions,
    get_next_question,
)


def _goal(title: str, desc: str = "") -> Goal:
    g = Goal(title=title, description=desc)
    g.id = 1
    return g


# ─── Template detection ───────────────────────────────────────────────────────

class TestDetectTemplate:
    def test_make_money_online_title(self):
        assert _detect_template(_goal("earn money online")) == "make_money_online"

    def test_make_money_online_desc(self):
        assert _detect_template(_goal("side project", "I want to earn passive income on the internet")) == "make_money_online"

    def test_make_money_online_variations(self):
        for phrase in ["make money online", "income from web", "earn profit on internet"]:
            assert _detect_template(_goal(phrase)) == "make_money_online", phrase

    def test_learn_skill(self):
        assert _detect_template(_goal("learn Python programming")) == "learn_skill"

    def test_learn_skill_variations(self):
        for phrase in ["study machine learning", "master guitar", "understand quantum physics"]:
            assert _detect_template(_goal(phrase)) == "learn_skill", phrase

    def test_get_fit(self):
        assert _detect_template(_goal("get fit and lose weight")) == "get_fit"

    def test_get_fit_variations(self):
        for phrase in ["start working out", "go to the gym", "lose weight", "improve cardio"]:
            assert _detect_template(_goal(phrase)) == "get_fit", phrase

    def test_build_project(self):
        assert _detect_template(_goal("build a web app")) == "build_project"

    def test_build_project_variations(self):
        for phrase in ["create a website", "develop a tool", "make an app"]:
            assert _detect_template(_goal(phrase)) == "build_project", phrase

    def test_generic_fallback(self):
        assert _detect_template(_goal("visit Japan someday")) == "generic"


# ─── decompose_template ───────────────────────────────────────────────────────

class TestDecomposeTemplate:
    def test_returns_tasks(self):
        tasks = decompose_template(_goal("earn money online"))
        assert len(tasks) > 0

    def test_tasks_have_required_fields(self):
        tasks = decompose_template(_goal("learn Python"))
        for t in tasks:
            assert t.title
            assert t.description
            assert t.estimated_minutes > 0
            assert t.goal_id == 1

    def test_tasks_have_order_index(self):
        tasks = decompose_template(_goal("get fit"))
        indices = [t.order_index for t in tasks]
        assert indices == sorted(indices)

    def test_make_money_online_tasks_count(self):
        tasks = decompose_template(_goal("earn money online"))
        assert 4 <= len(tasks) <= 10

    def test_generic_template_tasks(self):
        tasks = decompose_template(_goal("visit Japan someday"))
        assert len(tasks) >= 3

    def test_subtask_templates_attached(self):
        """Top-level tasks with subtasks have _subtask_templates attribute."""
        tasks = decompose_template(_goal("earn money online"))
        tasks_with_subs = [t for t in tasks if getattr(t, "_subtask_templates", [])]
        assert len(tasks_with_subs) > 0


# ─── Clarifying questions ─────────────────────────────────────────────────────

class TestClarifyingQuestions:
    def test_questions_returned(self):
        goal = _goal("earn money online")
        qs = get_clarifying_questions(goal)
        assert len(qs) > 0

    def test_questions_have_key_and_text(self):
        goal = _goal("earn money online")
        for q in get_clarifying_questions(goal):
            assert q.key
            assert q.text

    def test_questions_ordered_consistently(self):
        goal = _goal("learn Python")
        qs1 = get_clarifying_questions(goal)
        qs2 = get_clarifying_questions(goal)
        assert [q.key for q in qs1] == [q.key for q in qs2]

    def test_no_duplicate_keys(self):
        goal = _goal("earn money online")
        qs = get_clarifying_questions(goal)
        keys = [q.key for q in qs]
        assert len(keys) == len(set(keys))

    def test_get_next_question_returns_first_unanswered(self):
        goal = _goal("learn Python")
        qs = get_clarifying_questions(goal)
        first = qs[0]
        q = get_next_question(goal)
        assert q is not None
        assert q.key == first.key

    def test_get_next_question_skips_answered(self):
        goal = _goal("earn money online")
        qs = get_clarifying_questions(goal)
        first_key = qs[0].key
        goal.answers[first_key] = "my answer"
        q = get_next_question(goal)
        assert q is not None
        assert q.key != first_key

    def test_get_next_question_none_when_all_answered(self):
        goal = _goal("earn money online")
        qs = get_clarifying_questions(goal)
        for q in qs:
            goal.answers[q.key] = "answered"
        assert get_next_question(goal) is None

    def test_generic_has_generic_questions(self):
        goal = _goal("visit Japan")
        qs = get_clarifying_questions(goal)
        keys = {q.key for q in qs}
        assert "time_per_day" in keys
        assert "timeline" in keys


# ─── Task-level decomposition ─────────────────────────────────────────────────

class TestDecomposeTask:
    def test_decompose_task_returns_subtasks(self):
        from teb.decomposer import decompose_task
        parent = Task(goal_id=1, title="Research income options", description="Look into ways to earn", estimated_minutes=60)
        parent.id = 10
        subtasks = decompose_task(parent)
        assert len(subtasks) == 3  # research, execute, verify
        for s in subtasks:
            assert s.parent_id == 10
            assert s.goal_id == 1
            assert s.estimated_minutes > 0
            assert s.estimated_minutes <= 25

    def test_decompose_task_order_indices(self):
        from teb.decomposer import decompose_task
        parent = Task(goal_id=1, title="Do something", description="", estimated_minutes=45)
        parent.id = 20
        subtasks = decompose_task(parent)
        indices = [s.order_index for s in subtasks]
        assert indices == sorted(indices)
        assert indices == [0, 1, 2]

    def test_decompose_task_small_time(self):
        """Even for a 10-minute task, decomposition should produce valid subtasks."""
        from teb.decomposer import decompose_task
        parent = Task(goal_id=1, title="Quick task", description="", estimated_minutes=10)
        parent.id = 30
        subtasks = decompose_task(parent)
        assert len(subtasks) == 3
        for s in subtasks:
            assert s.estimated_minutes >= 5


# ─── Focus mode ───────────────────────────────────────────────────────────────

class TestGetFocusTask:
    def test_focus_returns_first_todo(self):
        from teb.decomposer import get_focus_task
        tasks = [
            Task(goal_id=1, title="A", description="", order_index=0, status="todo"),
            Task(goal_id=1, title="B", description="", order_index=1, status="todo"),
        ]
        tasks[0].id = 1
        tasks[1].id = 2
        focus = get_focus_task(tasks)
        assert focus is not None
        assert focus.title == "A"

    def test_focus_skips_done_tasks(self):
        from teb.decomposer import get_focus_task
        tasks = [
            Task(goal_id=1, title="A", description="", order_index=0, status="done"),
            Task(goal_id=1, title="B", description="", order_index=1, status="todo"),
        ]
        tasks[0].id = 1
        tasks[1].id = 2
        focus = get_focus_task(tasks)
        assert focus is not None
        assert focus.title == "B"

    def test_focus_prefers_in_progress(self):
        from teb.decomposer import get_focus_task
        tasks = [
            Task(goal_id=1, title="A", description="", order_index=0, status="todo"),
            Task(goal_id=1, title="B", description="", order_index=1, status="in_progress"),
        ]
        tasks[0].id = 1
        tasks[1].id = 2
        focus = get_focus_task(tasks)
        assert focus is not None
        assert focus.title == "B"

    def test_focus_dives_into_subtasks(self):
        from teb.decomposer import get_focus_task
        parent = Task(goal_id=1, title="Parent", description="", order_index=0, status="todo")
        parent.id = 1
        child = Task(goal_id=1, title="Child", description="", order_index=0, status="todo", parent_id=1)
        child.id = 2
        focus = get_focus_task([parent, child])
        assert focus is not None
        assert focus.title == "Child"

    def test_focus_returns_none_when_all_done(self):
        from teb.decomposer import get_focus_task
        tasks = [
            Task(goal_id=1, title="A", description="", order_index=0, status="done"),
            Task(goal_id=1, title="B", description="", order_index=1, status="skipped"),
        ]
        tasks[0].id = 1
        tasks[1].id = 2
        assert get_focus_task(tasks) is None

    def test_focus_empty_list(self):
        from teb.decomposer import get_focus_task
        assert get_focus_task([]) is None


# ─── Progress summary ─────────────────────────────────────────────────────────

class TestProgressSummary:
    def test_basic_progress(self):
        from teb.decomposer import get_progress_summary
        tasks = [
            Task(goal_id=1, title="A", description="", order_index=0, status="done", estimated_minutes=30),
            Task(goal_id=1, title="B", description="", order_index=1, status="todo", estimated_minutes=60),
        ]
        tasks[0].id = 1
        tasks[1].id = 2
        summary = get_progress_summary(tasks)
        assert summary["total_tasks"] == 2
        assert summary["done"] == 1
        assert summary["todo"] == 1
        assert summary["completion_pct"] == 50
        assert summary["estimated_remaining_minutes"] == 60

    def test_progress_excludes_subtasks_from_count(self):
        from teb.decomposer import get_progress_summary
        parent = Task(goal_id=1, title="P", description="", order_index=0, status="todo", estimated_minutes=30)
        parent.id = 1
        child = Task(goal_id=1, title="C", description="", order_index=0, status="todo", parent_id=1, estimated_minutes=15)
        child.id = 2
        summary = get_progress_summary([parent, child])
        assert summary["total_tasks"] == 1  # only top-level
        assert summary["estimated_remaining_minutes"] == 45  # both included in time

    def test_empty_progress(self):
        from teb.decomposer import get_progress_summary
        summary = get_progress_summary([])
        assert summary["total_tasks"] == 0
        assert summary["completion_pct"] == 0
