"""Router for plugins endpoints — extracted from main.py."""
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
from teb import plugins
from teb.models import (
    CustomFieldDefinition, PluginListing, PluginManifest, PluginView, Theme,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["plugins"])


# ─── Phase 5.2: Plugin & Extension System ───────────────────────────────────

@router.get("/api/plugins/marketplace", tags=["plugins"])
async def list_plugin_marketplace(request: Request):
    """List plugins available in the marketplace."""
    deps.check_api_rate_limit(request)
    listings = storage.list_plugin_listings()
    return [pl.to_dict() for pl in listings]


@router.get("/api/plugins/marketplace/{listing_id}", tags=["plugins"])
async def get_plugin_marketplace_item(listing_id: int, request: Request):
    """Get details for a specific plugin listing."""
    deps.check_api_rate_limit(request)
    pl = storage.get_plugin_listing(listing_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Plugin listing not found")
    return pl.to_dict()


@router.post("/api/plugins/marketplace/{listing_id}/install", status_code=201, tags=["plugins"])
async def install_plugin_from_marketplace(listing_id: int, request: Request):
    """Install a plugin from the marketplace."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    pl = storage.get_plugin_listing(listing_id)
    if not pl:
        raise HTTPException(status_code=404, detail="Plugin listing not found")
    storage.increment_plugin_downloads(listing_id)
    existing = storage.get_plugin(pl.name)
    if existing:
        return {"installed": True, "plugin": existing.to_dict(), "already_installed": True}
    plugin = PluginManifest(
        name=pl.name,
        version=pl.version,
        description=pl.description,
        task_types="[]",
        required_credentials="[]",
        module_path="",
    )
    plugin = storage.create_plugin(plugin)
    return {"installed": True, "plugin": plugin.to_dict(), "already_installed": False}


@router.post("/api/plugins/fields", status_code=201, tags=["plugins"])
async def create_custom_field_definition(request: Request):
    """Create a custom field definition provided by a plugin."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    cfd = CustomFieldDefinition(
        plugin_id=body.get("plugin_id", 0),
        field_type=body.get("field_type", "text"),
        label=body.get("label", ""),
        options_json=json.dumps(body.get("options", [])),
    )
    if not cfd.label:
        raise HTTPException(status_code=400, detail="label is required")
    cfd = storage.create_custom_field_definition(cfd)
    return cfd.to_dict()


@router.get("/api/plugins/fields", tags=["plugins"])
async def list_custom_field_definitions(request: Request, plugin_id: Optional[int] = Query(default=None)):
    """List custom field definitions, optionally filtered by plugin."""
    deps.check_api_rate_limit(request)
    fields = storage.list_custom_field_definitions(plugin_id=plugin_id)
    return [f.to_dict() for f in fields]


@router.post("/api/plugins/views", status_code=201, tags=["plugins"])
async def create_plugin_view(request: Request):
    """Create a custom view provided by a plugin."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    pv = PluginView(
        plugin_id=body.get("plugin_id", 0),
        name=body.get("name", ""),
        view_type=body.get("view_type", "board"),
        config_json=json.dumps(body.get("config", {})),
    )
    if not pv.name:
        raise HTTPException(status_code=400, detail="name is required")
    pv = storage.create_plugin_view(pv)
    return pv.to_dict()


@router.get("/api/plugins/views", tags=["plugins"])
async def list_plugin_views(request: Request, plugin_id: Optional[int] = Query(default=None)):
    """List custom views, optionally filtered by plugin."""
    deps.check_api_rate_limit(request)
    views = storage.list_plugin_views(plugin_id=plugin_id)
    return [v.to_dict() for v in views]


@router.get("/api/themes", tags=["themes"])
async def list_themes(request: Request):
    """List all available themes."""
    deps.check_api_rate_limit(request)
    themes = storage.list_themes()
    return [t.to_dict() for t in themes]


@router.post("/api/themes", status_code=201, tags=["themes"])
async def create_theme(request: Request):
    """Create a new theme."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    body = await request.json()
    theme = Theme(
        name=body.get("name", ""),
        author=body.get("author", ""),
        css_variables_json=json.dumps(body.get("css_variables", {})),
    )
    if not theme.name:
        raise HTTPException(status_code=400, detail="name is required")
    theme = storage.create_theme(theme)
    return theme.to_dict()


@router.put("/api/themes/{theme_id}/activate", tags=["themes"])
async def activate_theme(theme_id: int, request: Request):
    """Activate a theme (deactivates all others)."""
    uid = deps.require_user(request)
    deps.check_api_rate_limit(request)
    theme = storage.get_theme(theme_id)
    if not theme:
        raise HTTPException(status_code=404, detail="Theme not found")
    storage.activate_theme(theme_id)
    return {"activated": theme_id, "name": theme.name}


@router.get("/api/themes/active", tags=["themes"])
async def get_active_theme(request: Request):
    """Get the currently active theme."""
    deps.check_api_rate_limit(request)
    theme = storage.get_active_theme()
    if not theme:
        return {"active_theme": None}
    return theme.to_dict()


@router.get("/api/plugins/sdk/docs", tags=["plugins"])
async def get_plugin_sdk_docs(request: Request):
    """Return plugin SDK documentation as JSON."""
    deps.check_api_rate_limit(request)
    return {
        "sdk_version": "1.0.0",
        "overview": "The teb Plugin SDK allows developers to extend teb with custom functionality.",
        "plugin_manifest": {
            "description": "Every plugin must include a manifest.json in its directory.",
            "fields": {
                "name": "Unique plugin name (string, required)",
                "version": "Semantic version (string, required)",
                "description": "Human-readable description (string)",
                "task_types": "List of task types this plugin handles (array of strings)",
                "required_credentials": "Credential names needed (array of strings)",
                "module_path": "Python module path to the plugin entry point",
            },
        },
        "hooks": {
            "on_task_execute": "Called when a task matching plugin task_types is executed. Receives task_context dict.",
            "on_goal_created": "Called when a new goal is created.",
            "on_task_completed": "Called when a task status changes to done.",
        },
        "custom_fields": {
            "description": "Plugins can define custom field types via POST /api/plugins/fields.",
            "supported_types": ["text", "number", "date", "select", "multi_select", "url", "email", "checkbox"],
        },
        "custom_views": {
            "description": "Plugins can register custom views via POST /api/plugins/views.",
            "supported_view_types": ["board", "list", "calendar", "timeline", "chart"],
        },
        "api_endpoints": {
            "register_plugin": "POST /api/plugins",
            "list_plugins": "GET /api/plugins",
            "execute_plugin": "POST /api/plugins/{name}/execute",
            "plugin_marketplace": "GET /api/plugins/marketplace",
        },
    }



