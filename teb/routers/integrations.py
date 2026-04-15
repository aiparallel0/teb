"""Router for integrations endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import webhooks, importers, integrations
from teb.models import (
    Goal, Integration, IntegrationListing, IntegrationTemplate, OAuthConnection, Task, WebhookConfig, WebhookRule,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["integrations"])


# ─── Integration Registry ───────────────────────────────────────────────────

@router.get("/api/integrations")
async def list_available_integrations(category: Optional[str] = Query(default=None)):
    """List all known service integrations, optionally filtered by category."""
    db_integrations = storage.list_integrations(category=category)
    return [i.to_dict() for i in db_integrations]


@router.get("/api/integrations/catalog")
async def get_integration_catalog():
    """Get the built-in integration catalog (no DB required)."""
    return integrations.get_catalog()


@router.get("/api/integrations/match")
async def match_integrations(q: str = Query(description="Task description to match against")):
    """Find integrations relevant to a task description."""
    return integrations.find_matching_integrations(q)


@router.get("/api/integrations/{service_name}/endpoints")
async def get_service_endpoints(service_name: str):
    """Get common API endpoints for a known service."""
    endpoints = integrations.get_endpoints_for_service(service_name)
    if not endpoints:
        raise HTTPException(status_code=404, detail=f"No endpoints known for '{service_name}'")
    return {"service_name": service_name, "endpoints": endpoints}


# ─── Webhooks (Phase 2, Step 7) ──────────────────────────────────────────────

@router.post("/api/webhooks", status_code=201)
async def create_webhook(request: Request):
    uid = deps.require_user(request)
    body = await request.json()
    url = str(body.get("url", "")).strip()
    if not url:
        raise HTTPException(status_code=422, detail="url is required")
    from teb import security as _sec  # noqa: E402
    if not _sec.is_safe_url(url):
        raise HTTPException(status_code=422, detail="URL targets a private or disallowed address")
    events = body.get("events", [])
    if not isinstance(events, list):
        raise HTTPException(status_code=422, detail="events must be a list")
    wh = WebhookConfig(
        user_id=uid,
        url=url,
        events=json.dumps(events),
        secret=str(body.get("secret", "")),
        enabled=bool(body.get("enabled", True)),
    )
    wh = storage.create_webhook_config(wh)
    return wh.to_dict()


@router.get("/api/webhooks")
async def list_webhooks(request: Request):
    uid = deps.require_user(request)
    return [wh.to_dict() for wh in storage.list_webhook_configs(uid)]


@router.patch("/api/webhooks/{webhook_id}")
async def update_webhook(webhook_id: int, request: Request):
    uid = deps.require_user(request)
    wh = storage.get_webhook_config(webhook_id)
    if not wh or wh.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook not found")
    body = await request.json()
    if "url" in body:
        url = str(body["url"]).strip()
        from teb import security as _sec  # noqa: E402
        if not _sec.is_safe_url(url):
            raise HTTPException(status_code=422, detail="URL targets a private or disallowed address")
        wh.url = url
    if "events" in body:
        wh.events = json.dumps(body["events"])
    if "enabled" in body:
        wh.enabled = bool(body["enabled"])
    if "secret" in body:
        wh.secret = str(body["secret"])
    wh = storage.update_webhook_config(wh)
    return wh.to_dict()


@router.delete("/api/webhooks/{webhook_id}", status_code=200)
async def delete_webhook(webhook_id: int, request: Request):
    uid = deps.require_user(request)
    wh = storage.get_webhook_config(webhook_id)
    if not wh or wh.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook not found")
    storage.delete_webhook_config(webhook_id)
    return {"deleted": True}


# ─── Import Adapters (Phase 3, Step 9) ───────────────────────────────────────

@router.post("/api/import/trello", status_code=201)
async def import_trello_board(request: Request):
    """Import a Trello board JSON export into teb goals and tasks."""
    uid = deps.require_user(request)
    body = await request.json()
    board = body.get("board", {})
    if not board or not isinstance(board, dict):
        raise HTTPException(status_code=422, detail="board (Trello JSON export) is required")

    board_name = board.get("name", "Imported Trello Board")
    goal = Goal(title=board_name, description=board.get("desc", ""))
    goal.user_id = uid
    goal.tags = "imported,trello"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created = []
    lists_data = board.get("lists", [])
    cards_data = board.get("cards", [])

    # Map list IDs to names for tagging
    list_names = {lst["id"]: lst.get("name", "") for lst in lists_data if not lst.get("closed")}

    for idx, card in enumerate(cards_data):
        if card.get("closed"):
            continue
        list_name = list_names.get(card.get("idList", ""), "")
        status = "todo"
        if list_name.lower() in ("done", "complete", "completed", "finished"):
            status = "done"
        elif list_name.lower() in ("in progress", "doing", "wip"):
            status = "in_progress"

        task = Task(
            goal_id=goal.id,
            title=card.get("name", f"Card {idx+1}"),
            description=card.get("desc", ""),
            status=status,
            order_index=idx,
            tags=f"trello,{list_name}" if list_name else "trello",
        )
        if card.get("due"):
            task.due_date = card["due"][:10]  # extract date part
        task = storage.create_task(task)
        tasks_created.append(task)

    return {
        "goal": goal.to_dict(),
        "tasks_imported": len(tasks_created),
    }


@router.post("/api/import/asana", status_code=201)
async def import_asana_project(request: Request):
    """Import an Asana project (simplified JSON) into teb goals and tasks."""
    uid = deps.require_user(request)
    body = await request.json()
    project = body.get("project", {})
    if not project or not isinstance(project, dict):
        raise HTTPException(status_code=422, detail="project (Asana JSON) is required")

    project_name = project.get("name", "Imported Asana Project")
    goal = Goal(title=project_name, description=project.get("notes", ""))
    goal.user_id = uid
    goal.tags = "imported,asana"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created = []
    asana_tasks = project.get("tasks", [])

    for idx, at in enumerate(asana_tasks):
        completed = at.get("completed", False)
        task = Task(
            goal_id=goal.id,
            title=at.get("name", f"Task {idx+1}"),
            description=at.get("notes", ""),
            status="done" if completed else "todo",
            order_index=idx,
            tags="asana",
        )
        if at.get("due_on"):
            task.due_date = at["due_on"]
        task = storage.create_task(task)
        tasks_created.append(task)

        # Import subtasks
        for si, sub in enumerate(at.get("subtasks", [])):
            sub_task = Task(
                goal_id=goal.id,
                parent_id=task.id,
                title=sub.get("name", f"Subtask {si+1}"),
                description=sub.get("notes", ""),
                status="done" if sub.get("completed") else "todo",
                order_index=si,
                tags="asana",
            )
            sub_task = storage.create_task(sub_task)
            tasks_created.append(sub_task)

    return {
        "goal": goal.to_dict(),
        "tasks_imported": len(tasks_created),
    }


# ─── Outcome Attribution (Phase 3, Step 11) ──────────────────────────────────

@router.get("/api/goals/{goal_id}/impact")
async def get_goal_impact(goal_id: int, request: Request):
    """Trace which tasks/agents contributed most to goal outcomes."""
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)

    tasks = storage.list_tasks(goal_id=goal_id)
    metrics = storage.list_outcome_metrics(goal_id)
    audit_events = storage.list_audit_events(goal_id=goal_id)
    exec_logs: list = []
    for t in tasks:
        logs = storage.list_execution_logs(t.id)
        for log in logs:
            exec_logs.append({"task_id": t.id, "task_title": t.title, "log": log.to_dict()})

    # Build impact attribution
    agent_contributions: dict = {}
    for evt in audit_events:
        if evt.actor_type == "agent":
            agent_contributions[evt.actor_id] = agent_contributions.get(evt.actor_id, 0) + 1

    task_impact = []
    for t in tasks:
        if t.status == "done":
            logs_count = sum(1 for el in exec_logs if el["task_id"] == t.id)
            task_impact.append({
                "task_id": t.id,
                "title": t.title,
                "executions": logs_count,
            })

    return {
        "goal_id": goal_id,
        "metrics": [m.to_dict() for m in metrics],
        "agent_contributions": agent_contributions,
        "task_impact": sorted(task_impact, key=lambda x: x["executions"], reverse=True),
        "total_audit_events": len(audit_events),
    }


# ─── Sync Adapters (Phase 4, Step 13) ────────────────────────────────────────

@router.post("/api/sync/trello/export")
async def export_to_trello_format(request: Request):
    """Export a goal as Trello-compatible JSON for import."""
    uid = deps.require_user(request)
    body = await request.json()
    goal_id = body.get("goal_id")
    if not goal_id:
        raise HTTPException(status_code=422, detail="goal_id is required")
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Build Trello-compatible board structure
    lists = [
        {"id": "todo", "name": "To Do", "closed": False},
        {"id": "in_progress", "name": "In Progress", "closed": False},
        {"id": "done", "name": "Done", "closed": False},
    ]
    status_to_list = {
        "todo": "todo", "in_progress": "in_progress", "executing": "in_progress",
        "done": "done", "skipped": "done", "failed": "todo",
    }
    cards = []
    for t in tasks:
        card = {
            "name": t.title,
            "desc": t.description,
            "idList": status_to_list.get(t.status, "todo"),
            "closed": False,
        }
        if t.due_date:
            card["due"] = t.due_date
        cards.append(card)

    return {
        "name": goal.title,
        "desc": goal.description,
        "lists": lists,
        "cards": cards,
    }


@router.post("/api/sync/asana/export")
async def export_to_asana_format(request: Request):
    """Export a goal as Asana-compatible JSON for import."""
    uid = deps.require_user(request)
    body = await request.json()
    goal_id = body.get("goal_id")
    if not goal_id:
        raise HTTPException(status_code=422, detail="goal_id is required")
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)

    # Build Asana-compatible structure
    asana_tasks = []
    task_map: dict = {}
    for t in tasks:
        task_map[t.id] = t

    top_level = [t for t in tasks if t.parent_id is None]
    for t in top_level:
        subtasks = [c for c in tasks if c.parent_id == t.id]
        at = {
            "name": t.title,
            "notes": t.description,
            "completed": t.status in ("done", "skipped"),
            "subtasks": [
                {
                    "name": s.title,
                    "notes": s.description,
                    "completed": s.status in ("done", "skipped"),
                }
                for s in subtasks
            ],
        }
        if t.due_date:
            at["due_on"] = t.due_date
        asana_tasks.append(at)

    return {
        "name": goal.title,
        "notes": goal.description,
        "tasks": asana_tasks,
    }


# ─── Phase 5.1: Integration Marketplace ──────────────────────────────────────

@router.get("/api/integrations/directory", tags=["integrations"])
async def list_integration_directory(request: Request, category: Optional[str] = Query(default=None)):
    """List available integrations from the directory."""
    deps.check_api_rate_limit(request)
    listings = storage.list_integration_listings(category=category)
    return [il.to_dict() for il in listings]


@router.get("/api/integrations/directory/{listing_id}", tags=["integrations"])
async def get_integration_directory_item(listing_id: int, request: Request):
    """Get details for a specific integration listing."""
    deps.check_api_rate_limit(request)
    il = storage.get_integration_listing(listing_id)
    if not il:
        raise HTTPException(status_code=404, detail="Integration listing not found")
    return il.to_dict()


@router.post("/api/integrations/oauth/initiate", tags=["integrations"])
async def oauth_initiate(request: Request):
    """Initiate an OAuth flow for a provider. Returns the authorization URL."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    provider = body.get("provider", "")
    redirect_uri = body.get("redirect_uri", "")
    if not provider:
        raise HTTPException(status_code=400, detail="provider is required")
    auth_url = f"https://{provider}.example.com/oauth/authorize?client_id=teb&redirect_uri={redirect_uri}&state={uid}"
    return {"auth_url": auth_url, "provider": provider}


@router.post("/api/integrations/oauth/callback", tags=["integrations"])
async def oauth_callback(request: Request):
    """Handle OAuth callback and store encrypted tokens."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    provider = body.get("provider", "")
    access_token = body.get("access_token", "")
    refresh_token = body.get("refresh_token", "")
    if not provider or not access_token:
        raise HTTPException(status_code=400, detail="provider and access_token are required")
    oc = OAuthConnection(
        user_id=uid,
        provider=provider,
        access_token_encrypted=access_token,
        refresh_token_encrypted=refresh_token,
    )
    oc = storage.upsert_oauth_connection(oc)
    return oc.to_dict()


@router.get("/api/integrations/templates", tags=["integrations"])
async def list_integration_templates(request: Request):
    """List all integration templates."""
    deps.check_api_rate_limit(request)
    templates = storage.list_integration_templates()
    return [t.to_dict() for t in templates]


@router.post("/api/integrations/templates/{template_id}/apply", tags=["integrations"])
async def apply_integration_template(template_id: int, request: Request):
    """Apply an integration template to set up a new integration mapping."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    template = storage.get_integration_template(template_id)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")
    return {"applied": True, "template": template.to_dict(), "user_id": uid}


# ─── Webhook Rules (Builder) ────────────────────────────────────────────────

@router.post("/api/webhooks/rules", status_code=201, tags=["webhooks"])
async def create_webhook_rule(request: Request):
    """Create a new webhook routing rule."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    wr = WebhookRule(
        user_id=uid,
        name=body.get("name", ""),
        event_type=body.get("event_type", ""),
        filter_json=json.dumps(body.get("filter", {})),
        target_url=body.get("target_url", ""),
        headers_json=json.dumps(body.get("headers", {})),
        active=body.get("active", True),
    )
    if not wr.target_url:
        raise HTTPException(status_code=400, detail="target_url is required")
    wr = storage.create_webhook_rule(wr)
    return wr.to_dict()


@router.get("/api/webhooks/rules", tags=["webhooks"])
async def list_webhook_rules(request: Request):
    """List all webhook rules for the current user."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    rules = storage.list_webhook_rules(uid)
    return [r.to_dict() for r in rules]


@router.put("/api/webhooks/rules/{rule_id}", tags=["webhooks"])
async def update_webhook_rule(rule_id: int, request: Request):
    """Update an existing webhook rule."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    existing = storage.get_webhook_rule(rule_id)
    if not existing or existing.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook rule not found")
    body = await request.json()
    existing.name = body.get("name", existing.name)
    existing.event_type = body.get("event_type", existing.event_type)
    if "filter" in body:
        existing.filter_json = json.dumps(body["filter"])
    existing.target_url = body.get("target_url", existing.target_url)
    if "headers" in body:
        existing.headers_json = json.dumps(body["headers"])
    if "active" in body:
        existing.active = body["active"]
    existing = storage.update_webhook_rule(existing)
    return existing.to_dict()


@router.delete("/api/webhooks/rules/{rule_id}", tags=["webhooks"])
async def delete_webhook_rule(rule_id: int, request: Request):
    """Delete a webhook rule."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    existing = storage.get_webhook_rule(rule_id)
    if not existing or existing.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook rule not found")
    storage.delete_webhook_rule(rule_id, uid)
    return {"deleted": rule_id}


@router.post("/api/webhooks/rules/{rule_id}/test", tags=["webhooks"])
async def test_webhook_rule(rule_id: int, request: Request):
    """Send a test payload to a webhook rule's target URL."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    rule = storage.get_webhook_rule(rule_id)
    if not rule or rule.user_id != uid:
        raise HTTPException(status_code=404, detail="Webhook rule not found")
    test_payload = {
        "event": rule.event_type,
        "test": True,
        "message": "This is a test webhook delivery from teb.",
    }
    return {"sent": True, "target_url": rule.target_url, "payload": test_payload}


# ─── Zapier/Make Native App ─────────────────────────────────────────────────

@router.get("/api/integrations/zapier/triggers", tags=["integrations"])
async def zapier_list_triggers(request: Request):
    """List available triggers for Zapier/Make integration."""
    deps.check_api_rate_limit(request)
    return {"triggers": [
        {"key": "goal_created", "label": "Goal Created", "description": "Triggers when a new goal is created."},
        {"key": "task_completed", "label": "Task Completed", "description": "Triggers when a task is marked done."},
        {"key": "goal_completed", "label": "Goal Completed", "description": "Triggers when a goal is completed."},
        {"key": "task_created", "label": "Task Created", "description": "Triggers when a new task is created."},
        {"key": "checkin_submitted", "label": "Check-in Submitted", "description": "Triggers when a check-in is submitted."},
    ]}


@router.get("/api/integrations/zapier/actions", tags=["integrations"])
async def zapier_list_actions(request: Request):
    """List available actions for Zapier/Make integration."""
    deps.check_api_rate_limit(request)
    return {"actions": [
        {"key": "create_goal", "label": "Create Goal", "description": "Create a new goal in teb."},
        {"key": "create_task", "label": "Create Task", "description": "Create a task under a goal."},
        {"key": "update_task_status", "label": "Update Task Status", "description": "Update the status of a task."},
        {"key": "add_comment", "label": "Add Comment", "description": "Add a comment to a task."},
    ]}


@router.post("/api/integrations/zapier/subscribe", tags=["integrations"])
async def zapier_subscribe(request: Request):
    """Subscribe to a trigger event (Zapier subscription endpoint)."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    event_type = body.get("event_type", "")
    target_url = body.get("target_url", "")
    if not event_type or not target_url:
        raise HTTPException(status_code=400, detail="event_type and target_url are required")
    sub_id = storage.create_zapier_subscription(uid, event_type, target_url)
    return {"id": sub_id, "event_type": event_type, "target_url": target_url}


@router.delete("/api/integrations/zapier/unsubscribe/{sub_id}", tags=["integrations"])
async def zapier_unsubscribe(sub_id: int, request: Request):
    """Unsubscribe from a trigger event."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    storage.delete_zapier_subscription(sub_id, uid)
    return {"deleted": sub_id}


# ─── Phase 5.3: Import/Export Ecosystem ──────────────────────────────────────

@router.post("/api/import/monday", status_code=201, tags=["import"])
async def import_monday(request: Request):
    """Import a Monday.com board JSON into teb goals and tasks."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    board = body.get("board", {})
    if not board or not isinstance(board, dict):
        raise HTTPException(status_code=422, detail="board (Monday.com JSON) is required")
    from teb import importers
    goal, tasks = importers.import_from_monday(uid, board)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@router.post("/api/import/jira", status_code=201, tags=["import"])
async def import_jira(request: Request):
    """Import Jira project/sprint data into teb goals and tasks."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    project = body.get("project", {})
    if not project or not isinstance(project, dict):
        raise HTTPException(status_code=422, detail="project (Jira JSON) is required")
    from teb import importers
    goal, tasks = importers.import_from_jira(uid, project)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@router.post("/api/import/clickup", status_code=201, tags=["import"])
async def import_clickup(request: Request):
    """Import ClickUp list data into teb goals and tasks."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    list_data = body.get("list", {})
    if not list_data or not isinstance(list_data, dict):
        raise HTTPException(status_code=422, detail="list (ClickUp JSON) is required")
    from teb import importers
    goal, tasks = importers.import_from_clickup(uid, list_data)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@router.post("/api/import/csv", status_code=201, tags=["import"])
async def import_csv(request: Request):
    """Import tasks from CSV text."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    csv_text = body.get("csv", "")
    if not csv_text:
        raise HTTPException(status_code=422, detail="csv (CSV text content) is required")
    from teb import importers
    goal, tasks = importers.import_from_csv(uid, csv_text)
    return {"goal": goal.to_dict(), "tasks_imported": len(tasks)}


@router.post("/api/import/langchain", status_code=201, tags=["import"])
async def import_langchain_workflow(request: Request):
    """Import a LangChain agent/chain workflow export."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    data = body.get("data", body)
    from teb import importers
    goal, tasks = importers.import_from_langchain(uid, data)
    return {"goal": goal.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.post("/api/import/crewai", status_code=201, tags=["import"])
async def import_crewai_crew(request: Request):
    """Import a CrewAI crew export."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    data = body.get("data", body)
    from teb import importers
    goal, tasks = importers.import_from_crewai(uid, data)
    return {"goal": goal.to_dict(), "tasks": [t.to_dict() for t in tasks]}


@router.get("/api/goals/{goal_id}/export/full", tags=["export"])
async def export_full_project(goal_id: int, request: Request):
    """Export a full goal with all tasks, comments, and artifacts."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    result = storage.export_project(goal_id)
    if not result:
        raise HTTPException(status_code=404, detail="Goal not found")
    return result


@router.get("/api/export/schema", tags=["export"])
async def export_schema_docs(request: Request):
    """Return the data schema documentation for exports."""
    deps.check_api_rate_limit(request)
    return {
        "version": "1.0.0",
        "description": "teb data export schema documentation",
        "entities": {
            "goal": {
                "fields": {
                    "id": "integer – unique goal identifier",
                    "user_id": "integer – owner user ID",
                    "title": "string – goal title",
                    "description": "string – goal description",
                    "status": "string – drafting | decomposed | in_progress | completed | archived",
                    "answers": "object – structured answers from goal questionnaire",
                    "tags": "string – comma-separated tags",
                    "created_at": "ISO 8601 datetime",
                    "updated_at": "ISO 8601 datetime",
                },
            },
            "task": {
                "fields": {
                    "id": "integer – unique task identifier",
                    "goal_id": "integer – parent goal ID",
                    "parent_id": "integer | null – parent task ID for subtasks",
                    "title": "string – task title",
                    "description": "string – task description",
                    "status": "string – todo | in_progress | done | failed | blocked",
                    "estimated_minutes": "integer – estimated duration",
                    "order_index": "integer – sort order",
                    "due_date": "string | null – YYYY-MM-DD",
                    "tags": "string – comma-separated tags",
                    "created_at": "ISO 8601 datetime",
                    "updated_at": "ISO 8601 datetime",
                },
            },
            "task_comment": {
                "fields": {
                    "id": "integer",
                    "task_id": "integer – parent task ID",
                    "content": "string – comment text",
                    "author": "string – comment author",
                    "created_at": "ISO 8601 datetime",
                },
            },
            "task_artifact": {
                "fields": {
                    "id": "integer",
                    "task_id": "integer – parent task ID",
                    "artifact_type": "string – file type or artifact category",
                    "content": "string – artifact content or URL",
                    "created_at": "ISO 8601 datetime",
                },
            },
        },
        "export_endpoint": "GET /api/goals/{id}/export/full",
        "import_endpoints": {
            "trello": "POST /api/import/trello",
            "asana": "POST /api/import/asana",
            "monday": "POST /api/import/monday",
            "jira": "POST /api/import/jira",
            "clickup": "POST /api/import/clickup",
            "csv": "POST /api/import/csv",
            "langchain": "POST /api/import/langchain",
            "crewai": "POST /api/import/crewai",
        },
    }



