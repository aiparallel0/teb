"""Router for mcp endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from teb import storage
from teb.routers import deps
from teb import mcp_server
from teb.models import (
    PluginManifest,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["mcp"])


# ─── MCP Client (outbound tool calls to external MCP servers) ────────────────

@router.get("/api/mcp/servers", tags=["mcp-client"])
async def list_mcp_servers_endpoint(request: Request):
    """List all registered external MCP servers."""
    deps.require_user(request)
    from teb import mcp_client  # noqa: E402
    return [s.to_dict() for s in mcp_client.list_mcp_servers()]


@router.post("/api/mcp/servers", status_code=201, tags=["mcp-client"])
async def register_mcp_server_endpoint(request: Request):
    """Register an external MCP server for teb to use as a tool source."""
    deps.require_user(request)
    body = await request.json()
    name = body.get("name", "").strip()
    url = body.get("url", "").strip()
    if not name or not url:
        raise HTTPException(status_code=422, detail="name and url are required")
    from teb import mcp_client  # noqa: E402
    try:
        server = mcp_client.register_mcp_server(
            name=name, url=url,
            description=body.get("description", ""),
            auth_header=body.get("auth_header", ""),
            auth_value=body.get("auth_value", ""),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return server.to_dict()


@router.delete("/api/mcp/servers/{server_name}", tags=["mcp-client"])
async def unregister_mcp_server_endpoint(server_name: str, request: Request):
    """Remove an external MCP server from the registry."""
    deps.require_user(request)
    from teb import mcp_client  # noqa: E402
    ok = mcp_client.unregister_mcp_server(server_name)
    if not ok:
        raise HTTPException(status_code=404, detail=f"Server '{server_name}' not found")
    return {"deleted": server_name}


@router.post("/api/mcp/servers/{server_name}/discover", tags=["mcp-client"])
async def discover_mcp_tools_endpoint(server_name: str, request: Request):
    """Discover available tools on a registered MCP server."""
    deps.require_user(request)
    from teb import mcp_client  # noqa: E402
    tools = mcp_client.discover_tools(server_name)
    return {"server": server_name, "tools": [t.to_dict() for t in tools]}


@router.post("/api/mcp/call", tags=["mcp-client"])
async def call_mcp_tool_endpoint(request: Request):
    """Call a tool on an external MCP server.

    Body: {"server": "name", "tool": "tool_name", "arguments": {...}}
    """
    deps.require_user(request)
    body = await request.json()
    server_name = body.get("server", "")
    tool_name = body.get("tool", "")
    arguments = body.get("arguments", {})
    if not server_name or not tool_name:
        raise HTTPException(status_code=422, detail="server and tool are required")
    from teb import mcp_client  # noqa: E402
    result = mcp_client.call_tool(server_name, tool_name, arguments)
    return result.to_dict()


@router.post("/api/mcp/find-tools", tags=["mcp-client"])
async def find_mcp_tools_endpoint(request: Request):
    """Find MCP tools relevant to a task description."""
    deps.require_user(request)
    body = await request.json()
    description = body.get("description", "")
    if not description:
        raise HTTPException(status_code=422, detail="description is required")
    from teb import mcp_client  # noqa: E402
    tools = mcp_client.find_tools_for_task(description)
    return {"tools": [t.to_dict() for t in tools], "count": len(tools)}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 8: Execution Sandbox Isolation
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/goals/{goal_id}/sandbox")
async def get_execution_sandbox(goal_id: int, request: Request):
    """Get or create the isolated execution context for a goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    ctx = storage.get_or_create_execution_context(goal_id)
    return ctx.to_dict()


@router.patch("/api/goals/{goal_id}/sandbox")
async def update_execution_sandbox(goal_id: int, request: Request):
    """Update sandbox configuration (credential scope, etc.)."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    ctx = storage.get_or_create_execution_context(goal_id)
    body = await request.json()
    if "credential_scope" in body:
        scope = body["credential_scope"]
        if isinstance(scope, list):
            ctx.credential_scope = json.dumps(scope)
        elif isinstance(scope, str):
            ctx.credential_scope = scope
        else:
            raise HTTPException(status_code=400, detail="credential_scope must be a list or JSON string")
    ctx = storage.update_execution_context(ctx)
    return ctx.to_dict()


@router.post("/api/goals/{goal_id}/sandbox/cleanup")
async def cleanup_execution_sandbox(goal_id: int, request: Request):
    """Clean up the execution sandbox for a completed goal."""
    uid = deps.require_user(request)
    deps.get_goal_for_user(goal_id, uid)
    storage.cleanup_execution_context(goal_id)
    return {"cleaned_up": True, "goal_id": goal_id}


# ═══════════════════════════════════════════════════════════════════════════════
# Step 1: Execution Plugin System
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/plugins")
async def list_plugins(request: Request,
                       enabled_only: bool = Query(default=False)):
    """List registered execution plugins."""
    deps.require_user(request)
    plugins = storage.list_plugins(enabled_only=enabled_only)
    return {"plugins": [p.to_dict() for p in plugins]}


@router.post("/api/plugins", status_code=201)
async def register_plugin(request: Request):
    """Register a new execution plugin."""
    deps.require_admin(request)
    body = await request.json()
    plugin = PluginManifest(
        name=body.get("name", ""),
        version=body.get("version", "0.1.0"),
        description=body.get("description", ""),
        task_types=json.dumps(body.get("task_types", [])),
        required_credentials=json.dumps(body.get("required_credentials", [])),
        module_path=body.get("module_path", ""),
    )
    if not plugin.name:
        raise HTTPException(status_code=400, detail="Plugin name is required")
    existing = storage.get_plugin(plugin.name)
    if existing:
        raise HTTPException(status_code=409, detail="Plugin already exists")
    plugin = storage.create_plugin(plugin)
    return plugin.to_dict()


@router.delete("/api/plugins/{name}")
async def delete_plugin(name: str, request: Request):
    """Delete a plugin by name."""
    deps.require_admin(request)
    existing = storage.get_plugin(name)
    if not existing:
        raise HTTPException(status_code=404, detail="Plugin not found")
    storage.delete_plugin(name)
    from teb import plugins as _plugins  # noqa: E402
    _plugins.unregister_executor(name)
    return {"deleted": name}


@router.post("/api/plugins/{name}/execute")
async def execute_plugin(name: str, request: Request):
    """Execute a plugin with given task context and credentials."""
    uid = deps.require_user(request)
    body = await request.json()
    from teb import plugins as _plugins  # noqa: E402
    result = _plugins.execute_plugin(
        name,
        task_context=body.get("task_context", {}),
        credentials=body.get("credentials", {}),
    )
    return result.to_dict()


@router.get("/api/plugins/match")
async def match_plugins_for_task(request: Request,
                                  task_type: str = Query()):
    """Find plugins that can handle a given task type."""
    deps.require_user(request)
    from teb import plugins as _plugins  # noqa: E402
    matches = _plugins.find_plugins_for_task(task_type)
    return {"plugins": [p.to_dict() for p in matches]}


