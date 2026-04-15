"""Router for enterprise endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import auth, config, workload
from teb import cache
from teb.models import (
    BrandingConfig, IPAllowlist, Organization, SSOConfig,
)

logger = logging.getLogger(__name__)

router = APIRouter(tags=["enterprise"])


# ─── Phase 6.1: SSO/SAML Integration ────────────────────────────────────────

@router.post("/api/admin/sso/configure", tags=["enterprise"])
async def configure_sso(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    existing = storage.get_sso_config(org_id)
    cfg = SSOConfig(
        org_id=org_id,
        provider=body.get("provider", ""),
        entity_id=body.get("entity_id", ""),
        sso_url=body.get("sso_url", ""),
        certificate=body.get("certificate", ""),
    )
    if existing:
        cfg.id = existing.id
        cfg = storage.update_sso_config(cfg)
    else:
        cfg = storage.create_sso_config(cfg)
    return cfg.to_dict()


@router.get("/api/admin/sso/config", tags=["enterprise"])
async def get_sso_config(request: Request, org_id: int = Query(default=1)):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    cfg = storage.get_sso_config(org_id)
    if not cfg:
        return {"configured": False, "org_id": org_id}
    data = cfg.to_dict()
    data["configured"] = True
    return data


@router.post("/api/auth/sso/initiate", tags=["enterprise"])
async def sso_initiate(request: Request):
    deps.check_api_rate_limit(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    cfg = storage.get_sso_config(org_id)
    if not cfg or not cfg.sso_url:
        raise HTTPException(status_code=404, detail="SSO not configured for this organization")
    import secrets as _secrets
    relay_state = _secrets.token_urlsafe(32)
    redirect_url = f"{cfg.sso_url}?SAMLRequest=authn_request&RelayState={relay_state}"
    return {"redirect_url": redirect_url, "relay_state": relay_state, "provider": cfg.provider}


@router.post("/api/auth/sso/callback", tags=["enterprise"])
async def sso_callback(request: Request):
    deps.check_api_rate_limit(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    saml_response = body.get("SAMLResponse", "")
    cfg = storage.get_sso_config(org_id)
    if not cfg:
        raise HTTPException(status_code=404, detail="SSO not configured")
    email = body.get("email", "")
    if not email:
        raise HTTPException(status_code=422, detail="Email not provided in SSO response")
    user = storage.get_user_by_email(email)
    if not user:
        from teb.models import User as _User
        user = storage.create_user(_User(email=email, password_hash="sso_managed"))
    token = auth.create_token(user.id)
    return {"user": user.to_dict(), "token": token, "sso_provider": cfg.provider}


# ─── Phase 6.1: IP Allowlisting ─────────────────────────────────────────────

@router.get("/api/admin/ip-allowlist", tags=["enterprise"])
async def list_ip_allowlist(request: Request, org_id: int = Query(default=1)):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    entries = storage.list_ip_allowlist(org_id)
    return [e.to_dict() for e in entries]


@router.post("/api/admin/ip-allowlist", status_code=201, tags=["enterprise"])
async def create_ip_allowlist(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    body = await request.json()
    entry = IPAllowlist(
        org_id=body.get("org_id", 1),
        cidr_range=body.get("cidr_range", ""),
        description=body.get("description", ""),
    )
    if not entry.cidr_range:
        raise HTTPException(status_code=422, detail="cidr_range is required")
    entry = storage.create_ip_allowlist_entry(entry)
    return entry.to_dict()


@router.delete("/api/admin/ip-allowlist/{entry_id}", tags=["enterprise"])
async def delete_ip_allowlist(entry_id: int, request: Request, org_id: int = Query(default=1)):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    deleted = storage.delete_ip_allowlist_entry(entry_id, org_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entry not found")
    return {"deleted": True}


# ─── Phase 6.1: Audit Log Viewer ────────────────────────────────────────────

@router.get("/api/admin/audit-log", tags=["enterprise"])
async def audit_log_viewer(
    request: Request,
    user_id: Optional[str] = Query(default=None),
    event_type: Optional[str] = Query(default=None),
    since: Optional[str] = Query(default=None),
    until: Optional[str] = Query(default=None),
    limit: int = Query(default=100, le=1000),
):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    events = storage.search_audit_events(
        user_id=user_id, event_type=event_type,
        since=since, until=until, limit=limit,
    )
    return [e.to_dict() for e in events]


# ─── Phase 6.2: Organization Management ─────────────────────────────────────

@router.post("/api/orgs", status_code=201, tags=["enterprise"])
async def create_organization(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="name is required")
    slug = body.get("slug", "").strip() or name.lower().replace(" ", "-")
    import re as _re
    slug = _re.sub(r"[^a-z0-9-]", "", slug)
    org = Organization(name=name, slug=slug, owner_id=uid,
                       settings_json=json.dumps(body.get("settings", {})))
    try:
        org = storage.create_org(org)
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="Organization slug already exists")
        raise
    storage.add_org_member(org.id, uid, role="owner")
    return org.to_dict()


@router.get("/api/orgs", tags=["enterprise"])
async def list_organizations(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    return [o.to_dict() for o in storage.list_orgs()]


@router.get("/api/orgs/{org_id}", tags=["enterprise"])
async def get_organization(org_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    org = storage.get_org(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return org.to_dict()


@router.put("/api/orgs/{org_id}", tags=["enterprise"])
async def update_organization(org_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    org = storage.get_org(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    body = await request.json()
    if "name" in body:
        org.name = body["name"].strip()
    if "slug" in body:
        org.slug = body["slug"].strip()
    if "settings" in body:
        org.settings_json = json.dumps(body["settings"])
    org = storage.update_org(org)
    return org.to_dict()


@router.post("/api/orgs/{org_id}/members", status_code=201, tags=["enterprise"])
async def add_org_member(org_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    org = storage.get_org(org_id)
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    body = await request.json()
    user_id = body.get("user_id")
    role = body.get("role", "member")
    if not user_id:
        raise HTTPException(status_code=422, detail="user_id is required")
    result = storage.add_org_member(org_id, user_id, role)
    return result


@router.get("/api/orgs/{org_id}/members", tags=["enterprise"])
async def list_org_members(org_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    return storage.list_org_members(org_id)


# ─── Phase 6.2: Usage Analytics ─────────────────────────────────────────────

@router.get("/api/admin/analytics", tags=["enterprise"])
async def usage_analytics(
    request: Request,
    org_id: Optional[int] = Query(default=None),
    since: Optional[str] = Query(default=None),
):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    return storage.get_usage_analytics(org_id=org_id, since=since)


# ─── Phase 6.2: SCIM User Provisioning ──────────────────────────────────────

@router.get("/api/scim/v2/Users", tags=["scim"])
async def scim_list_users(request: Request, startIndex: int = Query(default=1), count: int = Query(default=100)):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    with storage._conn() as con:
        rows = con.execute("SELECT * FROM users ORDER BY id LIMIT ? OFFSET ?", (count, startIndex - 1)).fetchall()
        total = con.execute("SELECT COUNT(*) as cnt FROM users").fetchone()["cnt"]
    resources = []
    for r in rows:
        resources.append({
            "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
            "id": str(r["id"]),
            "userName": r["email"],
            "active": not bool(r["locked_until"]),
            "emails": [{"value": r["email"], "primary": True}],
            "meta": {"resourceType": "User", "created": r["created_at"]},
        })
    return {
        "schemas": ["urn:ietf:params:scim:api:messages:2.0:ListResponse"],
        "totalResults": total,
        "startIndex": startIndex,
        "itemsPerPage": count,
        "Resources": resources,
    }


@router.post("/api/scim/v2/Users", status_code=201, tags=["scim"])
async def scim_create_user(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    body = await request.json()
    email = body.get("userName", "")
    if not email:
        emails = body.get("emails", [])
        if emails:
            email = emails[0].get("value", "")
    if not email:
        raise HTTPException(status_code=422, detail="userName or emails required")
    import secrets as _secrets
    from teb.models import User as _User
    user = _User(email=email, password_hash=auth.hash_password(_secrets.token_urlsafe(16)))
    existing = storage.get_user_by_email(email)
    if existing:
        raise HTTPException(status_code=409, detail="User already exists")
    user = storage.create_user(user)
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "active": True,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None},
    }


@router.get("/api/scim/v2/Users/{user_id}", tags=["scim"])
async def scim_get_user(user_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "active": user.locked_until is None,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None},
    }


@router.put("/api/scim/v2/Users/{user_id}", tags=["scim"])
async def scim_update_user(user_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    body = await request.json()
    new_email = body.get("userName")
    active = body.get("active")
    if new_email:
        with storage._conn() as con:
            con.execute("UPDATE users SET email = ? WHERE id = ?", (new_email, user_id))
    if active is False:
        from datetime import timedelta
        storage.lock_user(user_id, datetime.now(timezone.utc) + timedelta(days=3650))
    elif active is True:
        with storage._conn() as con:
            con.execute("UPDATE users SET locked_until = NULL WHERE id = ?", (user_id,))
    user = storage.get_user(user_id)
    return {
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:User"],
        "id": str(user.id),
        "userName": user.email,
        "active": user.locked_until is None,
        "emails": [{"value": user.email, "primary": True}],
        "meta": {"resourceType": "User", "created": user.created_at.isoformat() if user.created_at else None},
    }


@router.delete("/api/scim/v2/Users/{user_id}", status_code=204, tags=["scim"])
async def scim_delete_user(user_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    user = storage.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    with storage._conn() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))
    return None


# ─── Phase 6.2: Custom Branding ─────────────────────────────────────────────

@router.get("/api/admin/branding", tags=["enterprise"])
async def get_branding(request: Request, org_id: int = Query(default=1)):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    cfg = storage.get_branding_config(org_id)
    if not cfg:
        return BrandingConfig(org_id=org_id).to_dict()
    return cfg.to_dict()


@router.put("/api/admin/branding", tags=["enterprise"])
async def update_branding(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    body = await request.json()
    org_id = body.get("org_id", 1)
    cfg = BrandingConfig(
        org_id=org_id,
        logo_url=body.get("logo_url", ""),
        primary_color=body.get("primary_color", "#1a1a2e"),
        secondary_color=body.get("secondary_color", "#16213e"),
        app_name=body.get("app_name", "teb"),
        favicon_url=body.get("favicon_url", ""),
    )
    cfg = storage.upsert_branding_config(cfg)
    return cfg.to_dict()


# ─── Phase 6.2: Compliance Reports ──────────────────────────────────────────

@router.get("/api/admin/compliance/report", tags=["enterprise"])
async def compliance_report(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    return storage.get_compliance_report()


@router.get("/api/admin/compliance/export", tags=["enterprise"])
async def compliance_export(request: Request, format: str = Query(default="json")):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    report = storage.get_compliance_report()
    if format == "json":
        return JSONResponse(content=report, headers={
            "Content-Disposition": "attachment; filename=compliance_report.json"
        })
    return report


# ─── Phase 6.3: Database Status ─────────────────────────────────────────────

@router.get("/api/admin/database/status", tags=["enterprise"])
async def database_status(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    status = storage.get_database_status()
    from teb import pg_migrate
    status["migration_plan"] = pg_migrate.migrate_to_postgres()
    return status


# ─── Phase 6.3: Cache Stats ─────────────────────────────────────────────────

@router.get("/api/admin/cache/stats", tags=["enterprise"])
async def cache_stats(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    from teb.cache import get_cache
    cache = get_cache()
    stats = cache.stats()
    stats["redis_url_configured"] = bool(config.REDIS_URL)
    stats["redis_instructions"] = (
        "Set REDIS_URL environment variable (e.g. redis://localhost:6379/0) "
        "and install the 'redis' package to enable Redis caching."
    )
    return stats


# ─── Prometheus-compatible metrics ───────────────────────────────────────────

@router.get("/api/admin/metrics", tags=["enterprise"])
async def admin_metrics(request: Request):
    """Prometheus-compatible metrics: active users, goals, tasks, AI latency, executor success rate."""
    deps.check_api_rate_limit(request)
    deps.require_admin(request)

    user_count = 0
    goal_count = 0
    task_count = 0
    done_goals = 0
    done_tasks = 0
    try:
        with storage._conn() as con:
            user_count = con.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            goal_count = con.execute("SELECT COUNT(*) FROM goals").fetchone()[0]
            done_goals = con.execute("SELECT COUNT(*) FROM goals WHERE status='done'").fetchone()[0]
            task_count = con.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
            done_tasks = con.execute("SELECT COUNT(*) FROM tasks WHERE status='done'").fetchone()[0]
    except Exception:
        pass

    # Execution memory stats (if available)
    exec_stats = {}
    try:
        from teb.memory import get_memory_stats
        exec_stats = get_memory_stats()
    except Exception:
        pass

    # Success graph stats
    graph_stats = {}
    try:
        from teb.success_graph import get_graph_stats
        graph_stats = get_graph_stats()
    except Exception:
        pass

    from teb.routers.health import _APP_START_TIME
    uptime = round(time.monotonic() - _APP_START_TIME, 1)

    return {
        "uptime_seconds": uptime,
        "users_total": user_count,
        "goals_total": goal_count,
        "goals_completed": done_goals,
        "goal_completion_rate": round(done_goals / max(goal_count, 1), 3),
        "tasks_total": task_count,
        "tasks_completed": done_tasks,
        "task_completion_rate": round(done_tasks / max(task_count, 1), 3),
        "executor": {
            "total_calls": exec_stats.get("total_calls", 0),
            "success_rate": exec_stats.get("success_rate", 0),
            "avg_latency_ms": exec_stats.get("avg_latency_ms", 0),
        },
        "success_graph": {
            "nodes": graph_stats.get("nodes", 0),
            "edges": graph_stats.get("edges", 0),
            "observations": graph_stats.get("total_observations", 0),
        },
    }


# ─── Phase 6.3: CDN Config ──────────────────────────────────────────────────

@router.get("/api/admin/cdn/config", tags=["enterprise"])
async def cdn_config(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    return {
        "cdn_url": config.TEB_CDN_URL or None,
        "configured": bool(config.TEB_CDN_URL),
        "usage": "When TEB_CDN_URL is set, static asset URLs in the HTML template are prefixed with this URL.",
        "static_assets": [
            "static/style.css",
            "static/app.js",
            "static/manifest.json",
            "static/views/kanban.js",
            "static/views/calendar.js",
            "static/views/timeline.js",
            "static/views/gantt.js",
            "static/views/table.js",
            "static/views/workload.js",
            "static/views/mindmap.js",
            "static/views/charts.js",
        ],
    }


# ─── Phase 6.3: Horizontal Scaling Config ───────────────────────────────────

@router.get("/api/admin/scaling/config", tags=["enterprise"])
async def scaling_config(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    return {
        "stateless": True,
        "recommendations": [
            "Set TEB_JWT_SECRET to a fixed value so tokens work across instances.",
            "Migrate from SQLite to PostgreSQL for shared database access.",
            "Set REDIS_URL for shared caching across instances.",
            "Use a load balancer (nginx, ALB, or Kubernetes Ingress) in front of multiple teb instances.",
            "Store uploaded files in object storage (S3, GCS) instead of local filesystem.",
            "Use sticky sessions or token-based auth (already implemented via JWT).",
        ],
        "current_config": {
            "database": "sqlite" if "sqlite" in config.DATABASE_URL else "postgresql",
            "cache": "redis" if config.REDIS_URL else "memory",
            "jwt_secret_set": bool(os.getenv("TEB_JWT_SECRET")),
            "region": config.REGION,
        },
    }


# ─── Phase 6.3: Multi-Region Support ────────────────────────────────────────

@router.get("/api/admin/regions", tags=["enterprise"])
async def list_regions(request: Request):
    deps.check_api_rate_limit(request)
    deps.require_admin(request)
    regions_env = os.getenv("TEB_REGIONS", "")
    configured_regions = [r.strip() for r in regions_env.split(",") if r.strip()] if regions_env else [config.REGION]
    return {
        "current_region": config.REGION,
        "configured_regions": configured_regions,
        "multi_region_enabled": len(configured_regions) > 1,
        "setup_instructions": (
            "Set TEB_REGION to identify this instance's region. "
            "Set TEB_REGIONS to a comma-separated list of all regions. "
            "Each region should have its own database and cache, with "
            "cross-region replication configured at the database level."
        ),
    }



