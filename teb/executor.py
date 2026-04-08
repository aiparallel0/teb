"""
Task execution engine.

Uses registered API credentials + AI to autonomously execute tasks.

Flow:
  1. Given a task and a list of available API credentials, ask the AI to
     produce an execution plan (which API to call, method, path, body).
  2. Execute the plan via httpx.
  3. Log every step in execution_logs.
  4. Update the task status to done/failed.

When OPENAI_API_KEY is not set, the executor uses a template-based
approach that returns a "manual execution required" result.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from teb import config
from teb.models import ApiCredential, ExecutionLog, Task


# ─── Execution plan ──────────────────────────────────────────────────────────

@dataclass
class ExecutionStep:
    """A single API call the executor will make."""
    credential_id: int
    method: str          # GET, POST, PUT, DELETE, PATCH
    path: str            # appended to credential.base_url
    headers: Dict[str, str]
    body: Optional[Dict[str, Any]]
    description: str     # human-readable explanation

    def to_dict(self) -> dict:
        return {
            "credential_id": self.credential_id,
            "method": self.method,
            "path": self.path,
            "headers": {k: v for k, v in self.headers.items()},
            "body": self.body,
            "description": self.description,
        }


@dataclass
class ExecutionPlan:
    """The AI-generated plan for executing a task."""
    can_execute: bool
    reason: str
    steps: List[ExecutionStep]

    def to_dict(self) -> dict:
        return {
            "can_execute": self.can_execute,
            "reason": self.reason,
            "steps": [s.to_dict() for s in self.steps],
        }


@dataclass
class StepResult:
    """Result of executing a single step."""
    step: ExecutionStep
    status_code: Optional[int]
    response_body: str
    success: bool
    error: str = ""


# ─── Plan generation ─────────────────────────────────────────────────────────

_VALID_METHODS = {"GET", "POST", "PUT", "DELETE", "PATCH"}


def _sanitize_path(path: str) -> str:
    """Ensure path starts with / and contains no scheme/host."""
    parsed = urlparse(path)
    # If someone passes a full URL, extract just the path
    clean = parsed.path or "/"
    if not clean.startswith("/"):
        clean = "/" + clean
    return clean


def generate_plan(
    task: Task,
    credentials: List[ApiCredential],
) -> ExecutionPlan:
    """
    Ask the AI to produce an execution plan for a task given available APIs.

    Falls back to a "cannot execute" plan when no API key is configured.
    """
    if not config.OPENAI_API_KEY:
        return _generate_plan_template(task, credentials)
    return _generate_plan_ai(task, credentials)


def _generate_plan_template(
    task: Task,
    credentials: List[ApiCredential],
) -> ExecutionPlan:
    """Template-based fallback: can only execute if credentials are available."""
    if not credentials:
        return ExecutionPlan(
            can_execute=False,
            reason="No API credentials registered. Add an API to enable automated execution.",
            steps=[],
        )
    return ExecutionPlan(
        can_execute=False,
        reason=(
            "AI mode is required for automated execution (set OPENAI_API_KEY). "
            f"{len(credentials)} API(s) available but plan generation needs AI."
        ),
        steps=[],
    )


def _generate_plan_ai(
    task: Task,
    credentials: List[ApiCredential],
) -> ExecutionPlan:
    """Use AI to produce an execution plan."""
    if not credentials:
        return ExecutionPlan(
            can_execute=False,
            reason="No API credentials registered. Add an API to enable automated execution.",
            steps=[],
        )

    try:
        from openai import OpenAI  # noqa: PLC0415

        client = OpenAI(
            api_key=config.OPENAI_API_KEY,
            base_url=config.OPENAI_BASE_URL,
        )

        cred_descriptions = "\n".join(
            f"- id={c.id}, name={c.name}, base_url={c.base_url}, description={c.description}"
            for c in credentials
        )

        # Include budget context if available so the AI can factor in cost constraints
        budget_context = ""
        if task.goal_id:
            try:
                from teb import storage as _storage  # noqa: PLC0415
                budgets = _storage.list_spending_budgets(task.goal_id)
                if budgets:
                    lines = [
                        f"  - ${b.daily_limit:.2f}/day, ${b.total_limit:.2f} total ({b.category})"
                        for b in budgets
                    ]
                    budget_context = "\nSpending budgets for this goal:\n" + "\n".join(lines)
            except Exception:
                pass

        system_prompt = (
            "You are an API execution planner. Given a task and available API credentials, "
            "determine if the task can be accomplished via API calls. "
            "If yes, produce a step-by-step execution plan as JSON. "
            "If no, explain why.\n\n"
            "Return ONLY valid JSON in this format:\n"
            "{\n"
            '  "can_execute": true/false,\n'
            '  "reason": "explanation",\n'
            '  "steps": [\n'
            "    {\n"
            '      "credential_id": <int>,\n'
            '      "method": "GET|POST|PUT|DELETE|PATCH",\n'
            '      "path": "/api/endpoint",\n'
            '      "headers": {"Content-Type": "application/json"},\n'
            '      "body": {"key": "value"} or null,\n'
            '      "description": "what this step does"\n'
            "    }\n"
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Only use credential IDs from the list provided\n"
            "- path is appended to the credential's base_url\n"
            "- Keep steps minimal and precise\n"
            "- Never fabricate API endpoints — only use plausible REST patterns\n"
            "- If the task clearly cannot be done via the available APIs, set can_execute=false"
        )

        user_prompt = (
            f"Task: {task.title}\n"
            f"Description: {task.description}\n\n"
            f"Available APIs:\n{cred_descriptions}"
            f"{budget_context}\n\n"
            f"Can this task be executed via the available APIs? If so, produce the plan."
        )

        response = client.chat.completions.create(
            model=config.MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        raw = response.choices[0].message.content or "{}"
        data = json.loads(raw)
        return _parse_plan(data, credentials)
    except Exception as exc:
        return ExecutionPlan(
            can_execute=False,
            reason=f"Failed to generate execution plan: {exc}",
            steps=[],
        )


def _parse_plan(data: dict, credentials: List[ApiCredential]) -> ExecutionPlan:
    """Parse AI JSON response into a validated ExecutionPlan."""
    can_execute = bool(data.get("can_execute", False))
    reason = str(data.get("reason", ""))
    raw_steps = data.get("steps", [])

    if not can_execute or not isinstance(raw_steps, list):
        return ExecutionPlan(can_execute=False, reason=reason or "AI determined task cannot be automated.", steps=[])

    cred_ids = {c.id for c in credentials}
    steps: List[ExecutionStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        cred_id = int(item.get("credential_id", -1))
        if cred_id not in cred_ids:
            continue
        method = str(item.get("method", "GET")).upper()
        if method not in _VALID_METHODS:
            continue
        path = _sanitize_path(str(item.get("path", "/")))
        raw_headers = item.get("headers", {})
        headers = {str(k): str(v) for k, v in raw_headers.items()} if isinstance(raw_headers, dict) else {}
        body = item.get("body")
        if body is not None and not isinstance(body, dict):
            body = None
        desc = str(item.get("description", "API call"))
        steps.append(ExecutionStep(
            credential_id=cred_id,
            method=method,
            path=path,
            headers=headers,
            body=body,
            description=desc,
        ))

    if not steps:
        return ExecutionPlan(can_execute=False, reason=reason or "No valid execution steps produced.", steps=[])

    return ExecutionPlan(can_execute=True, reason=reason, steps=steps)


# ─── Step execution ──────────────────────────────────────────────────────────

_MIN_SECRET_LENGTH_FOR_MASKING = 12
_MAX_RESPONSE_LOG_SIZE = 2000
_MAX_SUMMARY_LENGTH = 500


def _mask_secret(value: str) -> str:
    """Show only the first 4 and last 4 characters of a secret."""
    if len(value) <= _MIN_SECRET_LENGTH_FOR_MASKING:
        return "****"
    return value[:4] + "****" + value[-4:]


def execute_step(
    step: ExecutionStep,
    credentials_by_id: Dict[int, ApiCredential],
) -> StepResult:
    """Execute a single API call and return the result."""
    cred = credentials_by_id.get(step.credential_id)
    if not cred:
        return StepResult(
            step=step, status_code=None, response_body="",
            success=False, error=f"Credential {step.credential_id} not found",
        )

    url = cred.base_url.rstrip("/") + step.path

    # Build headers: add auth, merge step-specific headers
    headers = dict(step.headers)
    if cred.auth_value:
        headers[cred.auth_header] = cred.auth_value

    try:
        with httpx.Client(timeout=config.EXECUTOR_TIMEOUT) as client:
            response = client.request(
                method=step.method,
                url=url,
                headers=headers,
                json=step.body if step.body else None,
            )
        body_text = response.text[:_MAX_RESPONSE_LOG_SIZE]  # cap log size
        success = 200 <= response.status_code < 400
        return StepResult(
            step=step,
            status_code=response.status_code,
            response_body=body_text,
            success=success,
        )
    except httpx.TimeoutException:
        return StepResult(
            step=step, status_code=None, response_body="",
            success=False, error="Request timed out",
        )
    except httpx.RequestError as exc:
        return StepResult(
            step=step, status_code=None, response_body="",
            success=False, error=f"Request failed: {exc}",
        )


# ─── Full execution ─────────────────────────────────────────────────────────

def execute_plan(
    plan: ExecutionPlan,
    credentials_by_id: Dict[int, ApiCredential],
) -> List[StepResult]:
    """Execute all steps in a plan sequentially. Stop on first failure."""
    results: List[StepResult] = []
    for step in plan.steps:
        result = execute_step(step, credentials_by_id)
        results.append(result)
        if not result.success:
            break
    return results


def build_request_summary(step: ExecutionStep, cred: Optional[ApiCredential]) -> str:
    """Build a safe request summary for logging (no secrets)."""
    parts = [f"{step.method} {step.path}"]
    if cred:
        parts.append(f"API: {cred.name} ({cred.base_url})")
    if step.body:
        # Truncate body representation
        body_str = json.dumps(step.body)
        if len(body_str) > _MAX_SUMMARY_LENGTH:
            body_str = body_str[:_MAX_SUMMARY_LENGTH] + "..."
        parts.append(f"Body: {body_str}")
    return " | ".join(parts)


def build_response_summary(result: StepResult) -> str:
    """Build a safe response summary for logging."""
    if result.error:
        return f"Error: {result.error}"
    parts = [f"Status: {result.status_code}"]
    if result.response_body:
        body = result.response_body
        if len(body) > _MAX_SUMMARY_LENGTH:
            body = body[:_MAX_SUMMARY_LENGTH] + "..."
        parts.append(f"Body: {body}")
    return " | ".join(parts)
