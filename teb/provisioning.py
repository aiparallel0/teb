"""
Auto-provisioning engine: automatically sign up for services.

Uses browser automation (from teb.browser) to sign up for services like
Vercel, Stripe, SendGrid, etc. — then extracts and stores resulting
credentials (API keys, tokens) for use by the executor.

Without Playwright, returns step-by-step instructions the user can follow.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from teb import config, storage
from teb.browser import BrowserPlan, BrowserStep, generate_browser_plan
from teb.models import Task

logger = logging.getLogger(__name__)


# ─── Known service provisioning templates ────────────────────────────────────

@dataclass
class ProvisioningPlan:
    """A plan for signing up and provisioning a service."""
    service_name: str
    signup_url: str
    steps: List[BrowserStep]
    credential_selectors: List[str]  # CSS selectors to extract API keys
    can_provision: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "service_name": self.service_name,
            "signup_url": self.signup_url,
            "steps": [s.to_dict() for s in self.steps],
            "credential_selectors": self.credential_selectors,
            "can_provision": self.can_provision,
            "reason": self.reason,
        }


_SERVICE_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "vercel": {
        "signup_url": "https://vercel.com/signup",
        "steps": [
            BrowserStep("navigate", "https://vercel.com/signup", "", "Open Vercel signup page"),
            BrowserStep("click", "a[href*='github'], button:has-text('Continue with GitHub')",
                        "", "Click 'Continue with GitHub'"),
            BrowserStep("wait", "", "3", "Wait for GitHub OAuth redirect"),
            BrowserStep("screenshot", "", "", "Capture the dashboard after signup"),
        ],
        "credential_selectors": [
            "[data-testid='api-token']", "code", "pre", "input[value*='token']",
        ],
        "post_signup": "After signup, go to https://vercel.com/account/tokens to create an API token.",
    },
    "stripe": {
        "signup_url": "https://dashboard.stripe.com/register",
        "steps": [
            BrowserStep("navigate", "https://dashboard.stripe.com/register", "",
                        "Open Stripe registration page"),
            BrowserStep("type", "input[name='email']", "", "Enter email address"),
            BrowserStep("type", "input[name='full_name'], input[name='name']", "", "Enter full name"),
            BrowserStep("type", "input[name='password']", "", "Enter password"),
            BrowserStep("click", "button[type='submit']", "", "Submit registration"),
            BrowserStep("wait", "", "3", "Wait for confirmation"),
            BrowserStep("screenshot", "", "", "Capture dashboard"),
        ],
        "credential_selectors": [
            "[data-testid='secret-key']", "code", "pre",
        ],
        "post_signup": "After signup, go to https://dashboard.stripe.com/apikeys for API keys.",
    },
    "sendgrid": {
        "signup_url": "https://signup.sendgrid.com/",
        "steps": [
            BrowserStep("navigate", "https://signup.sendgrid.com/", "", "Open SendGrid signup"),
            BrowserStep("type", "input[name='email']", "", "Enter email address"),
            BrowserStep("type", "input[name='password']", "", "Enter password"),
            BrowserStep("click", "button[type='submit']", "", "Submit registration"),
            BrowserStep("wait", "", "3", "Wait for confirmation"),
            BrowserStep("screenshot", "", "", "Capture result"),
        ],
        "credential_selectors": [
            "[class*='api-key']", "code", "pre",
        ],
        "post_signup": "After signup, go to Settings > API Keys in SendGrid dashboard.",
    },
    "github": {
        "signup_url": "https://github.com/signup",
        "steps": [
            BrowserStep("navigate", "https://github.com/signup", "", "Open GitHub signup"),
            BrowserStep("type", "input[name='email'], #email", "", "Enter email"),
            BrowserStep("click", "button:has-text('Continue'), button[type='submit']", "", "Continue"),
            BrowserStep("type", "input[name='password'], #password", "", "Enter password"),
            BrowserStep("click", "button:has-text('Continue'), button[type='submit']", "", "Continue"),
            BrowserStep("type", "input[name='login'], #login", "", "Enter username"),
            BrowserStep("click", "button:has-text('Continue'), button[type='submit']", "", "Submit"),
            BrowserStep("wait", "", "3", "Wait for verification"),
            BrowserStep("screenshot", "", "", "Capture result"),
        ],
        "credential_selectors": [],
        "post_signup": "After signup, go to Settings > Developer settings > Personal access tokens to create a token.",
    },
    "cloudflare": {
        "signup_url": "https://dash.cloudflare.com/sign-up",
        "steps": [
            BrowserStep("navigate", "https://dash.cloudflare.com/sign-up", "",
                        "Open Cloudflare signup"),
            BrowserStep("type", "input[name='email'], input[type='email']", "", "Enter email"),
            BrowserStep("type", "input[name='password'], input[type='password']", "", "Enter password"),
            BrowserStep("click", "button[type='submit']", "", "Submit registration"),
            BrowserStep("wait", "", "3", "Wait for confirmation"),
            BrowserStep("screenshot", "", "", "Capture dashboard"),
        ],
        "credential_selectors": [
            "[class*='api-key']", "code",
        ],
        "post_signup": "After signup, go to Profile > API Tokens in Cloudflare dashboard.",
    },
    "namecheap": {
        "signup_url": "https://www.namecheap.com/myaccount/signup/",
        "steps": [
            BrowserStep("navigate", "https://www.namecheap.com/myaccount/signup/", "",
                        "Open Namecheap signup"),
            BrowserStep("type", "input[name='UserName'], #UserName", "", "Enter username"),
            BrowserStep("type", "input[name='FirstName']", "", "Enter first name"),
            BrowserStep("type", "input[name='LastName']", "", "Enter last name"),
            BrowserStep("type", "input[name='email'], input[type='email']", "", "Enter email"),
            BrowserStep("type", "input[name='Password'], input[type='password']", "", "Enter password"),
            BrowserStep("click", "button[type='submit'], input[type='submit']", "", "Submit"),
            BrowserStep("wait", "", "3", "Wait for confirmation"),
            BrowserStep("screenshot", "", "", "Capture result"),
        ],
        "credential_selectors": [],
        "post_signup": "After signup, enable API access at Profile > Tools > API Access.",
    },
}


# ─── Plan generation ─────────────────────────────────────────────────────────

def _detect_service(text: str) -> Optional[str]:
    """Detect which service to provision based on task text."""
    lower = text.lower()
    for service_name in _SERVICE_TEMPLATES:
        if service_name in lower:
            return service_name
    # Check for common phrases
    _ALIASES = {
        "domain": "namecheap",
        "email service": "sendgrid",
        "transactional email": "sendgrid",
        "payment": "stripe",
        "hosting": "vercel",
        "deploy": "vercel",
        "cdn": "cloudflare",
        "dns": "cloudflare",
        "repository": "github",
        "source code": "github",
    }
    for phrase, service in _ALIASES.items():
        if phrase in lower:
            return service
    return None


def generate_provisioning_plan(task: Task) -> ProvisioningPlan:
    """Generate a provisioning plan for a service signup."""
    text = f"{task.title} {task.description}"
    service_name = _detect_service(text)

    if not service_name:
        return ProvisioningPlan(
            service_name="unknown",
            signup_url="",
            steps=[],
            credential_selectors=[],
            can_provision=False,
            reason="Could not detect which service to provision. "
                   "Include a service name (e.g. Vercel, Stripe, SendGrid) in the task description.",
        )

    template = _SERVICE_TEMPLATES[service_name]
    return ProvisioningPlan(
        service_name=service_name,
        signup_url=template["signup_url"],
        steps=list(template["steps"]),
        credential_selectors=template["credential_selectors"],
        can_provision=True,
        reason=f"Provisioning plan ready for {service_name}. "
               f"{template.get('post_signup', '')}",
    )


def provision_service(task: Task) -> Dict[str, Any]:
    """Attempt to provision a service (sign up + extract credentials).

    With Playwright: executes browser automation.
    Without Playwright: returns step-by-step manual instructions.
    """
    plan = generate_provisioning_plan(task)

    if not plan.can_provision:
        storage.create_provisioning_log(
            task_id=task.id or 0,
            service_name=plan.service_name,
            action="signup",
            status="failed",
            error=plan.reason,
        )
        return {
            "success": False,
            "service": plan.service_name,
            "error": plan.reason,
            "plan": plan.to_dict(),
        }

    # Try browser automation
    has_playwright = _check_playwright()

    if has_playwright:
        result = _execute_provisioning(plan, task)
    else:
        result = {
            "success": False,
            "service": plan.service_name,
            "mode": "manual",
            "message": "Playwright is not installed. Follow the steps below manually.",
            "plan": plan.to_dict(),
            "manual_steps": [
                f"Step {i+1}: {s.description}"
                + (f" — target: {s.target}" if s.target else "")
                + (f" — value: {s.value}" if s.value else "")
                for i, s in enumerate(plan.steps)
            ],
        }

    # Log provisioning attempt
    storage.create_provisioning_log(
        task_id=task.id or 0,
        service_name=plan.service_name,
        action="signup",
        status="success" if result.get("success") else "manual_required",
        result_data=json.dumps({
            k: v for k, v in result.items()
            if k not in ("credentials",)  # don't log raw credentials
        }),
        error=result.get("error", ""),
    )

    return result


def _check_playwright() -> bool:
    """Check if Playwright is available."""
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _execute_provisioning(plan: ProvisioningPlan, task: Task) -> Dict[str, Any]:
    """Execute provisioning via Playwright browser automation."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"success": False, "error": "Playwright not available"}

    extracted: Dict[str, str] = {}
    actions_taken: list[str] = []

    try:
        with sync_playwright() as p:
            browser_inst = p.chromium.launch(headless=True)
            page = browser_inst.new_page()
            page.on("dialog", lambda dialog: dialog.accept())

            for step in plan.steps:
                try:
                    if step.action_type == "navigate":
                        page.goto(step.target, timeout=15000)
                        actions_taken.append(f"Navigated to {step.target}")
                    elif step.action_type == "click":
                        page.click(step.target, timeout=5000)
                        actions_taken.append(f"Clicked {step.target}")
                    elif step.action_type == "type":
                        if step.value:
                            page.fill(step.target, step.value, timeout=5000)
                            actions_taken.append(f"Typed into {step.target}")
                    elif step.action_type == "wait":
                        wait_ms = int(float(step.value) * 1000) if step.value else 2000
                        page.wait_for_timeout(wait_ms)
                        actions_taken.append(f"Waited {step.value}s")
                    elif step.action_type == "screenshot":
                        actions_taken.append("Screenshot captured")
                except Exception as e:
                    actions_taken.append(f"Step failed ({step.action_type}): {e}")

            # Try to extract credentials from the page
            for selector in plan.credential_selectors:
                try:
                    elements = page.query_selector_all(selector)
                    for el in elements:
                        text = el.text_content()
                        if text and len(text.strip()) >= 10:
                            extracted[f"extracted_{selector[:20]}"] = text.strip()[:200]
                except Exception:
                    pass

            browser_inst.close()

        return {
            "success": True,
            "service": plan.service_name,
            "mode": "automated",
            "actions_taken": actions_taken,
            "credentials_found": len(extracted) > 0,
            "extracted_count": len(extracted),
        }

    except Exception as exc:
        return {
            "success": False,
            "service": plan.service_name,
            "error": f"Browser automation failed: {exc}",
            "actions_taken": actions_taken,
        }


def list_provisionable_services() -> List[Dict[str, str]]:
    """List all services that can be auto-provisioned."""
    return [
        {
            "service_name": name,
            "signup_url": template["signup_url"],
            "post_signup_instructions": template.get("post_signup", ""),
        }
        for name, template in _SERVICE_TEMPLATES.items()
    ]
