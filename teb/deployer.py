"""
Infrastructure lifecycle: deploy, monitor, and fix.

Supports deployment to cloud hosting services (Vercel, Railway, Render)
via their REST APIs, plus health monitoring and auto-recovery.

Without API credentials, returns template-based deployment plans that the
user can follow manually.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

from teb import config, storage
from teb.models import ApiCredential, Task

logger = logging.getLogger(__name__)

_DEPLOY_TIMEOUT = 30  # seconds


# ─── Deployment Plan ─────────────────────────────────────────────────────────

@dataclass
class DeploymentPlan:
    """Plan for deploying and monitoring an app."""
    service: str              # vercel | railway | render
    project_name: str
    repository_url: str       # GitHub repo URL
    branch: str = "main"
    environment_vars: Dict[str, str] = field(default_factory=dict)
    domain: Optional[str] = None
    can_deploy: bool = False
    reason: str = ""

    def to_dict(self) -> dict:
        return {
            "service": self.service,
            "project_name": self.project_name,
            "repository_url": self.repository_url,
            "branch": self.branch,
            "environment_vars": {k: "***" for k in self.environment_vars},
            "domain": self.domain,
            "can_deploy": self.can_deploy,
            "reason": self.reason,
        }


# ─── Plan generation ─────────────────────────────────────────────────────────

_SERVICE_KEYWORDS = {
    "vercel": ["vercel", "next.js", "nextjs", "react", "frontend"],
    "railway": ["railway", "backend", "api", "python", "fastapi", "django", "node"],
    "render": ["render", "docker", "flask", "express"],
}


def _detect_service(text: str) -> str:
    """Detect which hosting service to use based on task description."""
    lower = text.lower()
    for service, keywords in _SERVICE_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return service
    return "railway"  # default


def _extract_repo_url(text: str) -> str:
    """Extract a GitHub repository URL from task description."""
    match = re.search(r'(?:https?://)?github\.com/[\w.-]+/[\w.-]+', text)
    return match.group(0) if match else ""


def _extract_project_name(text: str, repo_url: str) -> str:
    """Extract project name from repo URL or task description."""
    if repo_url:
        return repo_url.rstrip("/").split("/")[-1]
    words = re.findall(r'\b\w+\b', text.lower())
    skip = {"deploy", "to", "the", "a", "an", "my", "on", "app", "application", "project"}
    meaningful = [w for w in words if w not in skip]
    return meaningful[0] if meaningful else "teb-project"


def generate_deployment_plan(task: Task, credentials: List[ApiCredential]) -> DeploymentPlan:
    """Create a deployment plan from a task and available credentials."""
    text = f"{task.title} {task.description}"
    service = _detect_service(text)
    repo_url = _extract_repo_url(text)
    project_name = _extract_project_name(text, repo_url)

    # Check if we have credentials for the chosen service
    cred = _find_credential(service, credentials)

    if not cred:
        return DeploymentPlan(
            service=service,
            project_name=project_name,
            repository_url=repo_url,
            can_deploy=False,
            reason=f"No API credentials found for {service}. "
                   f"Register a credential with name containing '{service}' via POST /api/credentials.",
        )

    if not repo_url:
        return DeploymentPlan(
            service=service,
            project_name=project_name,
            repository_url="",
            can_deploy=False,
            reason="No repository URL found in task description. "
                   "Include a GitHub URL (e.g. github.com/user/repo) in the task description.",
        )

    return DeploymentPlan(
        service=service,
        project_name=project_name,
        repository_url=repo_url,
        can_deploy=True,
        reason=f"Ready to deploy {project_name} to {service}.",
    )


def _find_credential(service: str, credentials: List[ApiCredential]) -> Optional[ApiCredential]:
    """Find a credential matching the service name."""
    for cred in credentials:
        if service.lower() in cred.name.lower():
            return cred
    return None


# ─── Deployment execution ────────────────────────────────────────────────────

def deploy(plan: DeploymentPlan, credentials: List[ApiCredential],
           task: Task) -> Dict[str, Any]:
    """Execute a deployment based on the plan."""
    if not plan.can_deploy:
        return {"success": False, "error": plan.reason}

    cred = _find_credential(plan.service, credentials)
    if not cred:
        return {"success": False, "error": f"No credential found for {plan.service}"}

    dispatchers = {
        "vercel": _deploy_vercel,
        "railway": _deploy_railway,
        "render": _deploy_render,
    }

    handler = dispatchers.get(plan.service)
    if not handler:
        return {"success": False, "error": f"Unsupported deployment service: {plan.service}"}

    result = handler(plan, cred)

    # Record deployment in storage
    deploy_url = result.get("url", "")
    status = "deployed" if result.get("success") else "failed"
    storage.create_deployment(
        task_id=task.id or 0,
        goal_id=task.goal_id,
        service=plan.service,
        project_name=plan.project_name,
        repository_url=plan.repository_url,
        deploy_url=deploy_url,
        provider_data=json.dumps(result),
    )
    # Update deployment status
    deployments = storage.list_deployments(task.goal_id)
    if deployments:
        storage.update_deployment(deployments[0]["id"], status=status, deploy_url=deploy_url)

    return result


def _deploy_vercel(plan: DeploymentPlan, cred: ApiCredential) -> Dict[str, Any]:
    """Deploy to Vercel via their API."""
    headers = {"Authorization": f"Bearer {cred.auth_value}"}
    base = cred.base_url.rstrip("/") if cred.base_url else "https://api.vercel.com"

    try:
        # Create deployment from git repo
        deploy_data: Dict[str, Any] = {
            "name": plan.project_name,
            "gitSource": {
                "type": "github",
                "repo": plan.repository_url.replace("https://github.com/", ""),
                "ref": plan.branch,
            },
        }

        if plan.environment_vars:
            deploy_data["env"] = plan.environment_vars

        resp = httpx.post(
            f"{base}/v13/deployments",
            headers=headers,
            json=deploy_data,
            timeout=_DEPLOY_TIMEOUT,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            return {
                "success": True,
                "service": "vercel",
                "deployment_id": data.get("id", ""),
                "url": data.get("url", ""),
                "status": data.get("readyState", data.get("status", "building")),
                "project_name": plan.project_name,
            }
        return {
            "success": False,
            "service": "vercel",
            "error": f"Vercel API returned {resp.status_code}: {resp.text[:500]}",
        }
    except httpx.TimeoutException:
        return {"success": False, "service": "vercel", "error": "Request timed out"}
    except httpx.RequestError as exc:
        return {"success": False, "service": "vercel", "error": f"Request failed: {exc}"}


def _deploy_railway(plan: DeploymentPlan, cred: ApiCredential) -> Dict[str, Any]:
    """Deploy to Railway via their API."""
    headers = {"Authorization": f"Bearer {cred.auth_value}"}
    base = cred.base_url.rstrip("/") if cred.base_url else "https://backboard.railway.app"

    try:
        # Railway uses GraphQL
        query = """
        mutation deployFromRepo($repo: String!, $branch: String) {
            deploymentCreate(input: {repo: $repo, branch: $branch}) {
                id
                status
            }
        }
        """
        resp = httpx.post(
            f"{base}/graphql/v2",
            headers={**headers, "Content-Type": "application/json"},
            json={
                "query": query,
                "variables": {
                    "repo": plan.repository_url,
                    "branch": plan.branch,
                },
            },
            timeout=_DEPLOY_TIMEOUT,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            deployment = data.get("data", {}).get("deploymentCreate", {})
            return {
                "success": True,
                "service": "railway",
                "deployment_id": deployment.get("id", ""),
                "status": deployment.get("status", "building"),
                "project_name": plan.project_name,
                "url": "",
            }
        return {
            "success": False,
            "service": "railway",
            "error": f"Railway API returned {resp.status_code}: {resp.text[:500]}",
        }
    except httpx.TimeoutException:
        return {"success": False, "service": "railway", "error": "Request timed out"}
    except httpx.RequestError as exc:
        return {"success": False, "service": "railway", "error": f"Request failed: {exc}"}


def _deploy_render(plan: DeploymentPlan, cred: ApiCredential) -> Dict[str, Any]:
    """Deploy to Render via their API."""
    headers = {"Authorization": f"Bearer {cred.auth_value}"}
    base = cred.base_url.rstrip("/") if cred.base_url else "https://api.render.com"

    try:
        service_data = {
            "type": "web_service",
            "name": plan.project_name,
            "repo": plan.repository_url,
            "branch": plan.branch,
            "autoDeploy": "yes",
        }

        if plan.environment_vars:
            service_data["envVars"] = [
                {"key": k, "value": v} for k, v in plan.environment_vars.items()
            ]

        resp = httpx.post(
            f"{base}/v1/services",
            headers=headers,
            json=service_data,
            timeout=_DEPLOY_TIMEOUT,
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            service_info = data.get("service", data)
            return {
                "success": True,
                "service": "render",
                "service_id": service_info.get("id", ""),
                "url": service_info.get("serviceDetails", {}).get("url", ""),
                "status": "building",
                "project_name": plan.project_name,
            }
        return {
            "success": False,
            "service": "render",
            "error": f"Render API returned {resp.status_code}: {resp.text[:500]}",
        }
    except httpx.TimeoutException:
        return {"success": False, "service": "render", "error": "Request timed out"}
    except httpx.RequestError as exc:
        return {"success": False, "service": "render", "error": f"Request failed: {exc}"}


# ─── Health monitoring ───────────────────────────────────────────────────────

def check_health(deploy_url: str, timeout: int = 10) -> Dict[str, Any]:
    """Check if a deployed service is healthy by hitting its URL."""
    if not deploy_url:
        return {"status": "unknown", "error": "No deployment URL"}

    url = deploy_url if deploy_url.startswith("http") else f"https://{deploy_url}"

    try:
        resp = httpx.get(url, timeout=timeout, follow_redirects=True)
        return {
            "status": "healthy" if 200 <= resp.status_code < 500 else "unhealthy",
            "status_code": resp.status_code,
            "response_time_ms": int(resp.elapsed.total_seconds() * 1000),
        }
    except httpx.TimeoutException:
        return {"status": "unhealthy", "error": "Health check timed out"}
    except httpx.RequestError as exc:
        return {"status": "down", "error": f"Connection failed: {exc}"}


def monitor_deployment(deploy_id: int) -> Dict[str, Any]:
    """Run a health check on a tracked deployment and update its status."""
    deployment = storage.get_deployment(deploy_id)
    if not deployment:
        return {"error": "Deployment not found"}

    health = check_health(deployment.get("deploy_url", ""))
    health_status = health.get("status", "unknown")
    storage.update_deployment(deploy_id, health_status=health_status)

    return {
        "deployment_id": deploy_id,
        "service": deployment["service"],
        "deploy_url": deployment.get("deploy_url", ""),
        "health": health,
    }


def monitor_all_deployments(goal_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Run health checks on all active deployments for a goal."""
    deployments = storage.list_deployments(goal_id)
    results = []
    for d in deployments:
        if d["status"] == "deployed" and d.get("deploy_url"):
            result = monitor_deployment(d["id"])
            results.append(result)
    return results
