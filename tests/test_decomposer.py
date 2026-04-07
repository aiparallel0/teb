"""Unit tests for teb.decomposer"""
import pytest

from teb.models import Goal
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
