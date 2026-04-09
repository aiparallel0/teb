"""
Browser automation execution engine.

Generates and executes browser-based task plans when API calls are
insufficient.  Uses AI to produce step-by-step browser actions
(navigate, click, type, extract, screenshot, wait) and executes them
via Playwright when available, or records them as a guided plan for the
user to follow.

Flow:
  1. Given a task (and optionally registered integrations), ask AI to
     produce a browser automation plan.
  2. If Playwright is installed, execute the plan headlessly.
  3. Log every action in browser_actions via storage.
  4. If Playwright is *not* installed, return the plan as a guided
     step-by-step the user can follow manually.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from teb import config, security
from teb.models import BrowserAction, Integration, Task


# ─── Browser step / plan ─────────────────────────────────────────────────────

_VALID_ACTION_TYPES = {"navigate", "click", "type", "extract", "screenshot", "wait", "select",
                       "scroll", "hover", "upload", "accept_dialog"}


@dataclass
class BrowserStep:
    """A single browser automation action."""
    action_type: str       # navigate | click | type | extract | screenshot | wait | select
    target: str            # URL (navigate), CSS selector (click/type/extract/select), or description
    value: str = ""        # text to type, option to select, or seconds to wait
    description: str = ""  # human-readable explanation

    def to_dict(self) -> dict:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "value": self.value,
            "description": self.description,
        }


@dataclass
class BrowserPlan:
    """AI-generated browser automation plan."""
    can_automate: bool
    reason: str
    steps: List[BrowserStep]
    requires_login: bool = False
    target_url: str = ""

    def to_dict(self) -> dict:
        return {
            "can_automate": self.can_automate,
            "reason": self.reason,
            "steps": [s.to_dict() for s in self.steps],
            "requires_login": self.requires_login,
            "target_url": self.target_url,
        }


@dataclass
class BrowserStepResult:
    """Result of executing a single browser step."""
    step: BrowserStep
    success: bool
    extracted_text: str = ""
    screenshot_path: str = ""
    error: str = ""


# ─── Plan generation ─────────────────────────────────────────────────────────

def generate_browser_plan(
    task: Task,
    integrations: Optional[List[Integration]] = None,
) -> BrowserPlan:
    """
    Ask AI to produce a browser automation plan for a task.

    Falls back to a template-based plan when no AI key is configured.
    """
    if config.has_ai():
        return _generate_browser_plan_ai(task, integrations or [])
    return _generate_browser_plan_template(task, integrations or [])


def _generate_browser_plan_template(
    task: Task,
    integrations: List[Integration],
) -> BrowserPlan:
    """Template-based browser plan: produce a guided walkthrough the user can follow."""
    text = f"{task.title} {task.description}".lower()

    # Detect common browser-automatable tasks
    if any(kw in text for kw in ("sign up", "register", "create account", "create profile")):
        return BrowserPlan(
            can_automate=True,
            reason="Account creation detected — browser automation plan generated.",
            requires_login=False,
            target_url="",
            steps=[
                BrowserStep("navigate", "", "", "Open the target website"),
                BrowserStep("click", "a[href*='signup'], a[href*='register'], button:has-text('Sign up')",
                            "", "Click the sign-up / register button"),
                BrowserStep("type", "input[name='email'], input[type='email']",
                            "user@example.com", "Enter email address"),
                BrowserStep("type", "input[name='password'], input[type='password']",
                            "", "Enter password"),
                BrowserStep("click", "button[type='submit'], button:has-text('Create')",
                            "", "Submit the registration form"),
                BrowserStep("screenshot", "", "", "Capture confirmation page"),
            ],
        )

    if any(kw in text for kw in ("fill form", "submit form", "application", "apply")):
        return BrowserPlan(
            can_automate=True,
            reason="Form submission detected — browser automation plan generated.",
            requires_login=False,
            target_url="",
            steps=[
                BrowserStep("navigate", "", "", "Open the target page"),
                BrowserStep("type", "input, textarea", "", "Fill in form fields"),
                BrowserStep("click", "button[type='submit']", "", "Submit the form"),
                BrowserStep("screenshot", "", "", "Capture result"),
            ],
        )

    if any(kw in text for kw in ("search", "find", "look up", "research")):
        return BrowserPlan(
            can_automate=True,
            reason="Web research detected — browser automation plan generated.",
            requires_login=False,
            target_url="https://www.google.com",
            steps=[
                BrowserStep("navigate", "https://www.google.com", "", "Open Google"),
                BrowserStep("type", "textarea[name='q'], input[name='q']",
                            task.title, "Type search query"),
                BrowserStep("click", "input[name='btnK'], button[type='submit']",
                            "", "Submit search"),
                BrowserStep("wait", "", "2", "Wait for results to load"),
                BrowserStep("extract", "div#search", "", "Extract search results"),
                BrowserStep("screenshot", "", "", "Capture results page"),
            ],
        )

    # Generic fallback: can describe but not automate
    return BrowserPlan(
        can_automate=False,
        reason=(
            "AI mode is required for browser automation plan generation. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY to enable."
        ),
        steps=[],
    )


def _generate_browser_plan_ai(
    task: Task,
    integrations: List[Integration],
) -> BrowserPlan:
    """Use AI to produce a browser automation plan."""
    try:
        from teb.ai_client import ai_chat_json  # noqa: PLC0415

        integrations_desc = ""
        if integrations:
            integrations_desc = "\n\nKnown integrations:\n" + "\n".join(
                f"- {i.service_name}: {i.base_url} ({i.category})"
                for i in integrations
            )

        system_prompt = (
            "You are a browser automation planner. Given a task, produce a step-by-step "
            "browser automation plan using these action types:\n"
            "- navigate: go to a URL\n"
            "- click: click an element (target = CSS selector)\n"
            "- type: type text into a field (target = CSS selector, value = text)\n"
            "- select: select a dropdown option (target = CSS selector, value = option text)\n"
            "- extract: extract text from an element (target = CSS selector)\n"
            "- screenshot: take a screenshot of the current page\n"
            "- wait: wait for a number of seconds (value = seconds)\n"
            "- scroll: scroll the page (value = 'down', 'up', or pixel amount)\n"
            "- hover: hover over an element (target = CSS selector)\n"
            "- upload: upload a file (target = file input selector, value = file path)\n"
            "- accept_dialog: accept a browser dialog/alert\n\n"
            "Return ONLY valid JSON:\n"
            "{\n"
            '  "can_automate": true/false,\n'
            '  "reason": "why this can or cannot be automated",\n'
            '  "requires_login": true/false,\n'
            '  "target_url": "starting URL",\n'
            '  "steps": [\n'
            '    {"action_type": "navigate", "target": "https://...", "value": "", '
            '"description": "what this does"}\n'
            "  ]\n"
            "}\n\n"
            "Rules:\n"
            "- Use realistic CSS selectors\n"
            "- Include waits after page loads\n"
            "- Always end with a screenshot to verify the result\n"
            "- If the task requires authentication, set requires_login=true\n"
            "- Be specific about what to type and where to click"
        )

        user_prompt = (
            f"Task: {task.title}\n"
            f"Description: {task.description}"
            f"{integrations_desc}\n\n"
            f"Create a browser automation plan for this task."
        )

        data = ai_chat_json(system_prompt, user_prompt, temperature=0.2)
        return _parse_browser_plan(data)
    except Exception as exc:
        return BrowserPlan(
            can_automate=False,
            reason=f"Failed to generate browser plan: {exc}",
            steps=[],
        )


def _parse_browser_plan(data: dict) -> BrowserPlan:
    """Parse AI JSON response into a validated BrowserPlan."""
    can_automate = bool(data.get("can_automate", False))
    reason = str(data.get("reason", ""))
    requires_login = bool(data.get("requires_login", False))
    target_url = str(data.get("target_url", ""))
    raw_steps = data.get("steps", [])

    if not can_automate or not isinstance(raw_steps, list):
        return BrowserPlan(
            can_automate=False,
            reason=reason or "AI determined task cannot be browser-automated.",
            steps=[],
            requires_login=requires_login,
            target_url=target_url,
        )

    steps: List[BrowserStep] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        action_type = str(item.get("action_type", "")).lower()
        if action_type not in _VALID_ACTION_TYPES:
            continue
        target = str(item.get("target", ""))
        value = str(item.get("value", ""))
        description = str(item.get("description", ""))
        steps.append(BrowserStep(
            action_type=action_type,
            target=target,
            value=value,
            description=description,
        ))

    if not steps:
        return BrowserPlan(
            can_automate=False,
            reason=reason or "No valid browser steps produced.",
            steps=[],
            requires_login=requires_login,
            target_url=target_url,
        )

    return BrowserPlan(
        can_automate=True,
        reason=reason,
        steps=steps,
        requires_login=requires_login,
        target_url=target_url,
    )


# ─── Browser execution ──────────────────────────────────────────────────────

def is_playwright_available() -> bool:
    """Check if Playwright is installed and can be used."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: PLC0415, F401
        return True
    except ImportError:
        return False


def execute_browser_plan(plan: BrowserPlan, user_id: Optional[int] = None) -> List[BrowserStepResult]:
    """
    Execute a browser plan.

    If Playwright is available, runs in a headless browser.
    Otherwise returns each step as "manual" with instructions.

    Args:
        plan: The browser automation plan to execute
        user_id: Optional user ID for session persistence (6.1)
    """
    if not plan.can_automate or not plan.steps:
        return []

    if is_playwright_available():
        # 6.1: Use per-user session storage for cookie persistence
        storage_state = None
        if user_id is not None:
            import tempfile
            state_dir = os.path.join(tempfile.gettempdir(), "teb_browser_sessions")
            os.makedirs(state_dir, mode=0o700, exist_ok=True)
            storage_state = os.path.join(state_dir, f"user_{user_id}.json")
        return _execute_with_playwright(plan, storage_state=storage_state)
    return _execute_manual_fallback(plan)


def _execute_manual_fallback(plan: BrowserPlan) -> List[BrowserStepResult]:
    """Return steps as manual instructions when Playwright is not available."""
    results: List[BrowserStepResult] = []
    for step in plan.steps:
        results.append(BrowserStepResult(
            step=step,
            success=True,
            extracted_text=f"Manual step: {step.description}",
        ))
    return results


def _execute_with_playwright(plan: BrowserPlan, storage_state: Optional[str] = None) -> List[BrowserStepResult]:
    """Execute browser steps using Playwright headless browser.

    Supports session persistence via Playwright's storage_state feature (6.1).
    """
    from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout  # noqa: PLC0415

    results: List[BrowserStepResult] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)

        # 6.1: Load persisted session state (cookies, localStorage) if available
        context_kwargs: Dict[str, Any] = {}
        if storage_state and os.path.isfile(storage_state):
            context_kwargs["storage_state"] = storage_state

        context = browser.new_context(**context_kwargs)
        page = context.new_page()

        # 6.2: Set up dialog auto-accept handler
        page.on("dialog", lambda dialog: dialog.accept())

        try:
            for step in plan.steps:
                result = _execute_single_step(page, step)
                results.append(result)
                if not result.success:
                    break
        finally:
            # 6.1: Save session state for reuse
            if storage_state:
                try:
                    context.storage_state(path=storage_state)
                except Exception:
                    pass
            browser.close()

    return results


def _execute_single_step(page: Any, step: BrowserStep) -> BrowserStepResult:
    """Execute a single browser step on a Playwright page."""
    try:
        if step.action_type == "navigate":
            url = step.target or step.value
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            if not security.is_safe_url(url):
                return BrowserStepResult(
                    step=step, success=False,
                    error=f"Blocked: URL '{url}' targets a private or disallowed address",
                )
            page.goto(url, timeout=30000)
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "click":
            page.click(step.target, timeout=10000)
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "type":
            page.fill(step.target, step.value, timeout=10000)
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "select":
            page.select_option(step.target, label=step.value, timeout=10000)
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "extract":
            text = page.text_content(step.target, timeout=10000) or ""
            return BrowserStepResult(step=step, success=True, extracted_text=text[:2000])

        elif step.action_type == "screenshot":
            path = security.safe_screenshot_path(step.value)
            page.screenshot(path=path)
            return BrowserStepResult(step=step, success=True, screenshot_path=path)

        elif step.action_type == "wait":
            seconds = float(step.value) if step.value else 1.0
            seconds = min(seconds, 30.0)  # cap at 30 seconds
            page.wait_for_timeout(int(seconds * 1000))
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "scroll":
            # 6.2: Scroll the page
            value = step.value.lower() if step.value else "down"
            if value == "down":
                page.evaluate("window.scrollBy(0, 500)")
            elif value == "up":
                page.evaluate("window.scrollBy(0, -500)")
            else:
                # Try parsing as pixel amount
                try:
                    pixels = int(value)
                    page.evaluate(f"window.scrollBy(0, {pixels})")
                except ValueError:
                    page.evaluate("window.scrollBy(0, 500)")
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "hover":
            # 6.2: Hover over an element
            page.hover(step.target, timeout=10000)
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "upload":
            # 6.2: Upload a file
            page.set_input_files(step.target, step.value, timeout=10000)
            return BrowserStepResult(step=step, success=True)

        elif step.action_type == "accept_dialog":
            # 6.2: Dialog acceptance is handled by the page-level handler
            # This step is a no-op marker; the dialog handler auto-accepts
            return BrowserStepResult(step=step, success=True)

        else:
            return BrowserStepResult(
                step=step, success=False,
                error=f"Unknown action type: {step.action_type}",
            )
    except Exception as exc:
        return BrowserStepResult(step=step, success=False, error=str(exc))
