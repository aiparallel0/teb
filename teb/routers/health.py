"""Health check, readiness/liveness probes, and metrics endpoints."""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from teb import config, storage

logger = logging.getLogger(__name__)

router = APIRouter(tags=["health"])

# Module-level state (set from main.py at startup)
_APP_START_TIME: float = time.monotonic()

_metrics: Dict[str, Any] = {
    "requests_total": 0,
    "requests_by_status": {},
    "errors_total": 0,
}


def set_start_time(t: float) -> None:
    """Called from main.py lifespan to record actual startup time."""
    global _APP_START_TIME
    _APP_START_TIME = t


def increment_request_metrics(status_code: int) -> None:
    """Track request metrics (called from middleware)."""
    _metrics["requests_total"] += 1
    key = str(status_code)
    _metrics["requests_by_status"][key] = _metrics["requests_by_status"].get(key, 0) + 1
    if status_code >= 500:
        _metrics["errors_total"] += 1


@router.get("/health")
async def health_check() -> JSONResponse:
    """Comprehensive health check — database, AI, payments, uptime, and version."""
    import platform
    import shutil

    components: dict = {}

    try:
        with storage._conn() as con:
            con.execute("SELECT 1")
            row = con.execute(
                "SELECT COUNT(*) AS cnt FROM sqlite_master WHERE type='table'"
            ).fetchone()
            table_count = row["cnt"] if row else 0
        components["database"] = {"status": "ok", "tables": table_count}
        db_ok = True
    except Exception as exc:
        components["database"] = {"status": "error", "detail": str(exc)}
        db_ok = False

    ai_provider = config.get_ai_provider()
    components["ai"] = {
        "status": "ok" if ai_provider else "unconfigured",
        "provider": ai_provider or "none",
    }
    ai_active = ai_provider is not None

    from teb import payments as _pay
    providers = _pay.list_providers()
    components["payments"] = {
        "status": "ok" if any(p["configured"] for p in providers) else "unconfigured",
        "providers": providers,
    }

    try:
        disk = shutil.disk_usage("/")
        disk_free_mb = round(disk.free / (1024 * 1024), 1)
        disk_pct = round((disk.used / disk.total) * 100, 1)
        components["disk"] = {
            "status": "ok" if disk_pct < 90 else "warning",
            "free_mb": disk_free_mb,
            "used_percent": disk_pct,
        }
    except Exception:
        components["disk"] = {"status": "unknown"}

    warnings: list[str] = []
    if db_ok and config.TEB_ENV == "production" and not ai_active:
        status = "degraded"
        warnings.append(
            "AI provider not configured — running in template-only mode. "
            "Set ANTHROPIC_API_KEY or OPENAI_API_KEY in .env"
        )
    else:
        status = "healthy" if db_ok else "degraded"
    code = 200 if db_ok else 503
    uptime_seconds = round(time.monotonic() - _APP_START_TIME, 1)

    body: dict[str, Any] = {
        "status": status,
        "version": "2.0.0",
        "uptime_seconds": uptime_seconds,
        "python_version": platform.python_version(),
        "ai_active": ai_active,
        "components": components,
    }
    if warnings:
        body["warnings"] = warnings

    return JSONResponse(status_code=code, content=body)


@router.get("/api/health/ready")
async def readiness_probe() -> JSONResponse:
    """Readiness probe for container orchestration."""
    try:
        with storage._conn() as con:
            con.execute("SELECT 1")
        return JSONResponse(status_code=200, content={"ready": True})
    except Exception:
        logger.warning("Readiness probe failed", exc_info=True)
        return JSONResponse(
            status_code=503,
            content={"ready": False, "detail": "Database connectivity check failed"},
        )


@router.get("/api/health/live")
async def liveness_probe() -> dict:
    """Liveness probe — always returns 200 if the process is running."""
    return {"alive": True}


@router.get("/api/metrics")
async def get_metrics() -> dict:
    """Basic application metrics (request counts, uptime, error rate)."""
    uptime_seconds = round(time.monotonic() - _APP_START_TIME, 1)
    total = max(_metrics["requests_total"], 1)
    error_rate = round(_metrics["errors_total"] / total * 100, 2)

    return {
        "uptime_seconds": uptime_seconds,
        "requests_total": _metrics["requests_total"],
        "requests_by_status": _metrics["requests_by_status"],
        "errors_total": _metrics["errors_total"],
        "error_rate_percent": error_rate,
    }
