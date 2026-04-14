"""Tests for professional quality improvements.

Covers:
- Security headers middleware
- Request ID tracking
- Health check endpoints (ready, live, metrics)
- Input validation (field limits, format checks)
- Pagination support
- Schema version tracking
- Database health monitoring
- Structured error responses
"""

import pytest
from fastapi.testclient import TestClient

from teb import storage
from teb.main import app, _paginate, _DEFAULT_PAGE_SIZE, _MAX_PAGE_SIZE, reset_rate_limits


# ─── Test Setup ───────────────────────────────────────────────────────────────


client = TestClient(app)


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register_and_login(email: str = "proftest@teb.test", password: str = "testpass123") -> dict:
    """Register or login and return auth headers."""
    reset_rate_limits()
    r = client.post("/api/auth/register", json={"email": email, "password": password})
    if r.status_code not in (200, 201):
        r = client.post("/api/auth/login", json={"email": email, "password": password})
    token = r.json()["token"]
    return {"Authorization": f"Bearer {token}"}


# ═══════════════════════════════════════════════════════════════════════════════
# 1. Security Headers
# ═══════════════════════════════════════════════════════════════════════════════

class TestSecurityHeaders:
    """Verify that security headers are added to every response."""

    def test_x_content_type_options(self):
        r = client.get("/health")
        assert r.headers.get("x-content-type-options") == "nosniff"

    def test_x_frame_options(self):
        r = client.get("/health")
        assert r.headers.get("x-frame-options") == "DENY"

    def test_referrer_policy(self):
        r = client.get("/health")
        assert r.headers.get("referrer-policy") == "strict-origin-when-cross-origin"

    def test_csp_present(self):
        r = client.get("/health")
        csp = r.headers.get("content-security-policy", "")
        assert "default-src 'self'" in csp
        assert "frame-ancestors 'none'" in csp

    def test_permissions_policy(self):
        r = client.get("/health")
        pp = r.headers.get("permissions-policy", "")
        assert "camera=()" in pp
        assert "microphone=()" in pp

    def test_cross_origin_opener_policy(self):
        r = client.get("/health")
        assert r.headers.get("cross-origin-opener-policy") == "same-origin"

    def test_x_xss_protection(self):
        r = client.get("/health")
        assert r.headers.get("x-xss-protection") == "0"

    def test_security_headers_on_api_endpoints(self):
        """Security headers should be present on API responses too."""
        headers = _register_and_login()
        r = client.get("/api/goals", headers=headers)
        assert r.headers.get("x-content-type-options") == "nosniff"
        assert r.headers.get("x-frame-options") == "DENY"

    def test_security_headers_on_error_responses(self):
        """Security headers should be present even on error responses."""
        r = client.get("/api/goals/99999")
        assert r.headers.get("x-content-type-options") == "nosniff"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. Request ID Tracking
# ═══════════════════════════════════════════════════════════════════════════════

class TestRequestId:
    """Verify X-Request-Id is present on all responses."""

    def test_request_id_generated(self):
        r = client.get("/health")
        req_id = r.headers.get("x-request-id", "")
        assert len(req_id) == 32  # UUID hex, no dashes
        assert req_id.isalnum()

    def test_request_id_unique(self):
        r1 = client.get("/health")
        r2 = client.get("/health")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_client_request_id_accepted(self):
        """Client-provided X-Request-Id should be accepted and returned."""
        r = client.get("/health", headers={"X-Request-Id": "my-custom-id-123"})
        assert r.headers["x-request-id"] == "my-custom-id-123"

    def test_invalid_client_request_id_replaced(self):
        """Invalid client request IDs should be replaced with server-generated ones."""
        r = client.get("/health", headers={"X-Request-Id": "a" * 100})  # too long
        assert r.headers["x-request-id"] != "a" * 100
        assert len(r.headers["x-request-id"]) == 32

    def test_request_id_on_api_responses(self):
        headers = _register_and_login()
        r = client.get("/api/goals", headers=headers)
        assert "x-request-id" in r.headers


# ═══════════════════════════════════════════════════════════════════════════════
# 3. Health Check Endpoints
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoints:
    """Test comprehensive health check system."""

    def test_health_check_returns_200(self):
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "healthy"
        assert data["version"] == "2.0.0"
        assert "uptime_seconds" in data
        assert "python_version" in data

    def test_health_check_components(self):
        r = client.get("/health")
        data = r.json()
        components = data["components"]
        assert "database" in components
        assert components["database"]["status"] == "ok"
        assert "ai" in components
        assert "payments" in components
        assert "disk" in components

    def test_health_check_disk_info(self):
        r = client.get("/health")
        disk = r.json()["components"]["disk"]
        assert "free_mb" in disk
        assert "used_percent" in disk
        assert disk["status"] in ("ok", "warning")

    def test_readiness_probe(self):
        r = client.get("/api/health/ready")
        assert r.status_code == 200
        assert r.json()["ready"] is True

    def test_liveness_probe(self):
        r = client.get("/api/health/live")
        assert r.status_code == 200
        assert r.json()["alive"] is True

    def test_metrics_endpoint(self):
        r = client.get("/api/metrics")
        assert r.status_code == 200
        data = r.json()
        assert "uptime_seconds" in data
        assert "requests_total" in data
        assert "requests_by_status" in data
        assert "errors_total" in data
        assert "error_rate_percent" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 4. Input Validation
# ═══════════════════════════════════════════════════════════════════════════════

class TestInputValidation:
    """Test comprehensive input validation on request schemas."""

    def test_register_short_password(self):
        reset_rate_limits()
        r = client.post("/api/auth/register", json={"email": "bad@test.com", "password": "12345"})
        assert r.status_code == 422

    def test_register_valid_password(self):
        reset_rate_limits()
        r = client.post("/api/auth/register", json={"email": "validpw@test.com", "password": "validpass"})
        assert r.status_code in (201, 422)  # 422 if email already exists from prior run

    def test_register_invalid_email_format(self):
        reset_rate_limits()
        r = client.post("/api/auth/register", json={"email": "not-an-email", "password": "validpass123"})
        assert r.status_code == 422

    def test_register_email_normalized(self):
        """Email should be lowercased and trimmed."""
        reset_rate_limits()
        r = client.post("/api/auth/register", json={"email": " CaseNorm@TEB.Test ", "password": "validpass123"})
        if r.status_code == 201:
            assert r.json()["user"]["email"] == "casenorm@teb.test"

    def test_goal_title_required(self):
        headers = _register_and_login()
        r = client.post("/api/goals", json={"title": ""}, headers=headers)
        assert r.status_code == 422

    def test_goal_title_max_length(self):
        headers = _register_and_login()
        r = client.post("/api/goals", json={"title": "x" * 501}, headers=headers)
        assert r.status_code == 422

    def test_goal_description_max_length(self):
        headers = _register_and_login()
        r = client.post("/api/goals", json={"title": "test", "description": "x" * 10001}, headers=headers)
        assert r.status_code == 422

    def test_task_estimated_minutes_too_low(self):
        headers = _register_and_login()
        g = client.post("/api/goals", json={"title": "test goal"}, headers=headers)
        gid = g.json()["id"]
        r = client.post("/api/tasks", json={"goal_id": gid, "title": "t", "estimated_minutes": 0}, headers=headers)
        assert r.status_code == 422

    def test_task_estimated_minutes_too_high(self):
        headers = _register_and_login()
        g = client.post("/api/goals", json={"title": "test goal hi"}, headers=headers)
        gid = g.json()["id"]
        r = client.post("/api/tasks", json={"goal_id": gid, "title": "t", "estimated_minutes": 99999}, headers=headers)
        assert r.status_code == 422

    def test_budget_negative_limit(self):
        headers = _register_and_login()
        g = client.post("/api/goals", json={"title": "budget test"}, headers=headers)
        gid = g.json()["id"]
        r = client.post("/api/budgets", json={"goal_id": gid, "daily_limit": -1}, headers=headers)
        assert r.status_code == 422

    def test_spending_action_invalid(self):
        headers = _register_and_login()
        r = client.post("/api/spending/1/action", json={"action": "invalid"}, headers=headers)
        assert r.status_code == 422

    def test_messaging_channel_invalid(self):
        headers = _register_and_login()
        r = client.post("/api/messaging/config", json={"channel": "invalid_channel"}, headers=headers)
        assert r.status_code == 422


# ═══════════════════════════════════════════════════════════════════════════════
# 5. Pagination
# ═══════════════════════════════════════════════════════════════════════════════

class TestPagination:
    """Test pagination support on list endpoints."""

    def test_paginate_helper(self):
        items = list(range(25))
        result = _paginate(items, page=1, per_page=10)
        assert len(result["data"]) == 10
        assert result["pagination"]["page"] == 1
        assert result["pagination"]["per_page"] == 10
        assert result["pagination"]["total"] == 25
        assert result["pagination"]["pages"] == 3

    def test_paginate_last_page(self):
        items = list(range(25))
        result = _paginate(items, page=3, per_page=10)
        assert len(result["data"]) == 5
        assert result["pagination"]["page"] == 3

    def test_paginate_empty(self):
        result = _paginate([], page=1, per_page=10)
        assert len(result["data"]) == 0
        assert result["pagination"]["total"] == 0
        assert result["pagination"]["pages"] == 1

    def test_paginate_caps_per_page(self):
        items = list(range(200))
        result = _paginate(items, page=1, per_page=200)
        assert len(result["data"]) == _MAX_PAGE_SIZE
        assert result["pagination"]["per_page"] == _MAX_PAGE_SIZE

    def test_list_goals_no_pagination(self):
        """Without page/per_page, should return plain list (backward compat)."""
        headers = _register_and_login(email="pagtest@teb.test")
        client.post("/api/goals", json={"title": "pag test 1"}, headers=headers)
        r = client.get("/api/goals", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_goals_with_pagination(self):
        """With page param, should return paginated response."""
        headers = _register_and_login(email="pagtest2@teb.test")
        client.post("/api/goals", json={"title": "pag test 2"}, headers=headers)
        r = client.get("/api/goals?page=1&per_page=10", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "pagination" in data
        assert isinstance(data["data"], list)
        assert data["pagination"]["page"] == 1

    def test_list_tasks_no_pagination(self):
        """Without page/per_page, should return plain list."""
        headers = _register_and_login(email="pagtest3@teb.test")
        g = client.post("/api/goals", json={"title": "task pag test"}, headers=headers)
        gid = g.json()["id"]
        client.post(f"/api/goals/{gid}/decompose", json={}, headers=headers)
        r = client.get(f"/api/tasks?goal_id={gid}", headers=headers)
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_list_tasks_with_pagination(self):
        """With page param, should return paginated response."""
        headers = _register_and_login(email="pagtest4@teb.test")
        g = client.post("/api/goals", json={"title": "task pag test 2"}, headers=headers)
        gid = g.json()["id"]
        client.post(f"/api/goals/{gid}/decompose", json={}, headers=headers)
        r = client.get(f"/api/tasks?goal_id={gid}&page=1&per_page=5", headers=headers)
        assert r.status_code == 200
        data = r.json()
        assert "data" in data
        assert "pagination" in data


# ═══════════════════════════════════════════════════════════════════════════════
# 6. Schema Version Tracking
# ═══════════════════════════════════════════════════════════════════════════════

class TestSchemaVersioning:
    """Test schema version tracking in database."""

    def test_schema_versions_table_exists(self):
        versions = storage.get_schema_versions()
        assert isinstance(versions, list)

    def test_current_schema_version_recorded(self):
        versions = storage.get_schema_versions()
        assert len(versions) >= 1
        latest = versions[-1]
        assert latest["version"] == "2.0.0"
        assert "applied_at" in latest

    def test_schema_version_idempotent(self):
        """Running init_db again should not duplicate the version entry."""
        storage.init_db()
        versions = storage.get_schema_versions()
        version_2_entries = [v for v in versions if v["version"] == "2.0.0"]
        assert len(version_2_entries) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# 7. Database Health Monitoring
# ═══════════════════════════════════════════════════════════════════════════════

class TestDatabaseHealth:
    """Test database health monitoring functions."""

    def test_database_health_returns_dict(self):
        health = storage.get_database_health()
        assert isinstance(health, dict)

    def test_database_health_status(self):
        health = storage.get_database_health()
        assert health["status"] == "ok"

    def test_database_health_table_count(self):
        health = storage.get_database_health()
        assert health["table_count"] > 0

    def test_database_health_journal_mode(self):
        health = storage.get_database_health()
        assert health["journal_mode"] == "wal"

    def test_database_health_integrity(self):
        health = storage.get_database_health()
        assert health["integrity"] == "ok"

    def test_database_health_schema_version(self):
        health = storage.get_database_health()
        assert health["schema_version"] == "2.0.0"

    def test_database_health_size(self):
        health = storage.get_database_health()
        assert health["size_mb"] >= 0


# ═══════════════════════════════════════════════════════════════════════════════
# 8. Structured Error Responses
# ═══════════════════════════════════════════════════════════════════════════════

class TestStructuredErrors:
    """Test that error responses have consistent structure."""

    def test_404_includes_detail(self):
        headers = _register_and_login()
        r = client.get("/api/goals/99999", headers=headers)
        assert r.status_code == 404
        assert "detail" in r.json()

    def test_422_includes_detail(self):
        reset_rate_limits()
        r = client.post("/api/auth/register", json={"email": "bad", "password": "123"})
        assert r.status_code == 422
        body = r.json()
        assert "detail" in body

    def test_401_for_unauthenticated(self):
        r = client.get("/api/auth/me")
        assert r.status_code == 401

    def test_error_response_helper(self):
        from teb.main import _error_response
        import json
        resp = _error_response(400, "INVALID_INPUT", "Bad data", details=["field1"], request_id="abc123")
        data = json.loads(resp.body.decode())
        assert data["error"]["code"] == "INVALID_INPUT"
        assert data["error"]["message"] == "Bad data"
        assert data["error"]["details"] == ["field1"]
        assert data["error"]["request_id"] == "abc123"


# ═══════════════════════════════════════════════════════════════════════════════
# 9. Middleware Module
# ═══════════════════════════════════════════════════════════════════════════════

class TestMiddlewareModule:
    """Test middleware module utilities."""

    def test_add_security_headers(self):
        from teb.middleware import add_security_headers, _SECURITY_HEADERS
        from starlette.responses import Response
        resp = Response("ok")
        add_security_headers(resp)
        for header in _SECURITY_HEADERS:
            assert header.lower() in {k.lower() for k in resp.headers.keys()}

    def test_generate_request_id(self):
        from teb.middleware import _generate_request_id
        rid = _generate_request_id()
        assert len(rid) == 32
        assert rid.isalnum()

    def test_generate_request_ids_unique(self):
        from teb.middleware import _generate_request_id
        ids = {_generate_request_id() for _ in range(100)}
        assert len(ids) == 100


# ═══════════════════════════════════════════════════════════════════════════════
# 10. EventBus Shutdown
# ═══════════════════════════════════════════════════════════════════════════════

class TestEventBusShutdown:
    """Test graceful EventBus shutdown."""

    def test_shutdown_clears_subscribers(self):
        from teb.events import EventBus
        bus = EventBus()
        bus.subscribe(1)
        bus.subscribe(2)
        assert bus.subscriber_count == 2
        bus.shutdown()
        assert bus.subscriber_count == 0

    def test_shutdown_idempotent(self):
        from teb.events import EventBus
        bus = EventBus()
        bus.subscribe(1)
        bus.shutdown()
        bus.shutdown()  # Should not raise
        assert bus.subscriber_count == 0
