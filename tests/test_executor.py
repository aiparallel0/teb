"""Unit tests for teb.executor"""
import json

import pytest

from teb.models import ApiCredential, Task
from teb.executor import (
    ExecutionPlan,
    ExecutionStep,
    StepResult,
    _mask_secret,
    _parse_plan,
    _sanitize_path,
    build_request_summary,
    build_response_summary,
    execute_plan,
    generate_plan,
)


def _cred(cred_id: int = 1, name: str = "TestAPI", base_url: str = "https://api.example.com") -> ApiCredential:
    c = ApiCredential(name=name, base_url=base_url, description="A test API")
    c.id = cred_id
    return c


def _task(title: str = "Test task", desc: str = "Do something") -> Task:
    t = Task(goal_id=1, title=title, description=desc, estimated_minutes=30)
    t.id = 10
    return t


# ─── _sanitize_path ──────────────────────────────────────────────────────────

class TestSanitizePath:
    def test_normal_path(self):
        assert _sanitize_path("/v1/domains") == "/v1/domains"

    def test_adds_leading_slash(self):
        assert _sanitize_path("v1/domains") == "/v1/domains"

    def test_strips_scheme_and_host(self):
        assert _sanitize_path("https://api.example.com/v1/domains") == "/v1/domains"

    def test_empty_becomes_slash(self):
        assert _sanitize_path("") == "/"

    def test_preserves_query_string(self):
        # urlparse puts query in a separate field, path stays clean
        result = _sanitize_path("/search?q=test")
        assert result == "/search"


# ─── _mask_secret ─────────────────────────────────────────────────────────────

class TestMaskSecret:
    def test_long_secret(self):
        result = _mask_secret("sk-1234567890abcdef")
        assert result == "sk-1****cdef"

    def test_short_secret(self):
        assert _mask_secret("abc") == "****"

    def test_exactly_12_chars(self):
        assert _mask_secret("123456789012") == "****"

    def test_13_chars(self):
        result = _mask_secret("1234567890123")
        assert result.startswith("1234")
        assert result.endswith("0123")
        assert "****" in result


# ─── _parse_plan ──────────────────────────────────────────────────────────────

class TestParsePlan:
    def test_valid_plan(self):
        cred = _cred(cred_id=1)
        data = {
            "can_execute": True,
            "reason": "Can register domain",
            "steps": [
                {
                    "credential_id": 1,
                    "method": "POST",
                    "path": "/v1/domains/register",
                    "headers": {"Content-Type": "application/json"},
                    "body": {"domain": "example.com"},
                    "description": "Register the domain",
                }
            ],
        }
        plan = _parse_plan(data, [cred])
        assert plan.can_execute is True
        assert len(plan.steps) == 1
        assert plan.steps[0].method == "POST"
        assert plan.steps[0].path == "/v1/domains/register"

    def test_can_execute_false(self):
        plan = _parse_plan({"can_execute": False, "reason": "Not possible"}, [_cred()])
        assert plan.can_execute is False
        assert len(plan.steps) == 0

    def test_invalid_credential_id_filtered(self):
        cred = _cred(cred_id=1)
        data = {
            "can_execute": True,
            "reason": "ok",
            "steps": [
                {"credential_id": 999, "method": "GET", "path": "/test", "headers": {}, "body": None, "description": "bad"},
            ],
        }
        plan = _parse_plan(data, [cred])
        assert plan.can_execute is False  # no valid steps → can't execute

    def test_invalid_method_filtered(self):
        cred = _cred(cred_id=1)
        data = {
            "can_execute": True,
            "reason": "ok",
            "steps": [
                {"credential_id": 1, "method": "HACK", "path": "/test", "headers": {}, "body": None, "description": "bad"},
            ],
        }
        plan = _parse_plan(data, [cred])
        assert plan.can_execute is False

    def test_mixed_valid_invalid_steps(self):
        cred = _cred(cred_id=1)
        data = {
            "can_execute": True,
            "reason": "ok",
            "steps": [
                {"credential_id": 1, "method": "GET", "path": "/valid", "headers": {}, "body": None, "description": "good"},
                {"credential_id": 999, "method": "GET", "path": "/bad", "headers": {}, "body": None, "description": "bad cred"},
            ],
        }
        plan = _parse_plan(data, [cred])
        assert plan.can_execute is True
        assert len(plan.steps) == 1
        assert plan.steps[0].path == "/valid"

    def test_missing_steps_key(self):
        plan = _parse_plan({"can_execute": True, "reason": "ok"}, [_cred()])
        assert plan.can_execute is False

    def test_non_dict_steps_filtered(self):
        cred = _cred(cred_id=1)
        data = {
            "can_execute": True,
            "reason": "ok",
            "steps": ["not a dict", 42],
        }
        plan = _parse_plan(data, [cred])
        assert plan.can_execute is False


# ─── generate_plan (template mode, no OPENAI_API_KEY) ────────────────────────

class TestGeneratePlanTemplate:
    def test_no_credentials(self):
        plan = generate_plan(_task(), [])
        assert plan.can_execute is False
        assert "No API credentials" in plan.reason

    def test_with_credentials_but_no_ai(self):
        plan = generate_plan(_task(), [_cred()])
        assert plan.can_execute is False
        assert "AI mode" in plan.reason or "OPENAI_API_KEY" in plan.reason


# ─── ExecutionStep.to_dict ────────────────────────────────────────────────────

class TestExecutionStepToDict:
    def test_to_dict(self):
        step = ExecutionStep(
            credential_id=1,
            method="POST",
            path="/v1/test",
            headers={"Content-Type": "application/json"},
            body={"key": "value"},
            description="Test step",
        )
        d = step.to_dict()
        assert d["method"] == "POST"
        assert d["path"] == "/v1/test"
        assert d["body"] == {"key": "value"}


# ─── ExecutionPlan.to_dict ────────────────────────────────────────────────────

class TestExecutionPlanToDict:
    def test_to_dict(self):
        plan = ExecutionPlan(
            can_execute=True,
            reason="All good",
            steps=[
                ExecutionStep(credential_id=1, method="GET", path="/", headers={}, body=None, description="test"),
            ],
        )
        d = plan.to_dict()
        assert d["can_execute"] is True
        assert len(d["steps"]) == 1

    def test_empty_plan_to_dict(self):
        plan = ExecutionPlan(can_execute=False, reason="nope", steps=[])
        d = plan.to_dict()
        assert d["can_execute"] is False
        assert d["steps"] == []


# ─── build_request_summary / build_response_summary ──────────────────────────

class TestSummaryBuilders:
    def test_request_summary_basic(self):
        step = ExecutionStep(credential_id=1, method="GET", path="/test", headers={}, body=None, description="test")
        cred = _cred()
        summary = build_request_summary(step, cred)
        assert "GET /test" in summary
        assert "TestAPI" in summary

    def test_request_summary_with_body(self):
        step = ExecutionStep(credential_id=1, method="POST", path="/test", headers={}, body={"foo": "bar"}, description="test")
        summary = build_request_summary(step, None)
        assert "POST /test" in summary
        assert "foo" in summary

    def test_response_summary_success(self):
        step = ExecutionStep(credential_id=1, method="GET", path="/test", headers={}, body=None, description="test")
        result = StepResult(step=step, status_code=200, response_body='{"ok": true}', success=True)
        summary = build_response_summary(result)
        assert "200" in summary
        assert "ok" in summary

    def test_response_summary_error(self):
        step = ExecutionStep(credential_id=1, method="GET", path="/test", headers={}, body=None, description="test")
        result = StepResult(step=step, status_code=None, response_body="", success=False, error="Connection refused")
        summary = build_response_summary(result)
        assert "Connection refused" in summary


# ─── execute_plan (unit-level with mock) ─────────────────────────────────────

class TestExecutePlan:
    def test_empty_plan(self):
        plan = ExecutionPlan(can_execute=True, reason="ok", steps=[])
        results = execute_plan(plan, {})
        assert results == []

    def test_missing_credential_fails(self):
        step = ExecutionStep(credential_id=999, method="GET", path="/test", headers={}, body=None, description="test")
        plan = ExecutionPlan(can_execute=True, reason="ok", steps=[step])
        results = execute_plan(plan, {})
        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in results[0].error

    def test_stops_on_first_failure(self):
        step1 = ExecutionStep(credential_id=999, method="GET", path="/a", headers={}, body=None, description="first")
        step2 = ExecutionStep(credential_id=999, method="GET", path="/b", headers={}, body=None, description="second")
        plan = ExecutionPlan(can_execute=True, reason="ok", steps=[step1, step2])
        results = execute_plan(plan, {})
        assert len(results) == 1  # stopped after first failure
