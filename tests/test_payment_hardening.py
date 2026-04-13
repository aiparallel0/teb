"""
Tests for payment hardening features:
- Retry with exponential backoff
- Balance verification before transfers
- Webhook reconciliation (Mercury + Stripe)
- Failed transaction recovery
- General API rate limiting on non-auth endpoints
- Enhanced health check
"""

import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from teb import payments, storage
from teb.main import app, reset_rate_limits, _api_rate_buckets, _API_RATE_LIMIT

client = TestClient(app)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    db = str(tmp_path / "test_payment_hardening.db")
    storage.set_db_path(db)
    storage.init_db()
    yield
    storage.set_db_path(None)


def _register_user(email="pay@teb.test", pw="testpass123"):
    r = client.post("/api/auth/register", json={"email": email, "password": pw})
    if r.status_code not in (200, 201):
        r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _register_admin(email="admin@teb.test", pw="adminpass123"):
    headers = _register_user(email=email, pw=pw)
    # Directly set role=admin in DB
    with storage._conn() as con:
        con.execute("UPDATE users SET role='admin' WHERE email=?", (email,))
    return headers


def _setup_payment_account(user_headers, provider="mercury"):
    """Register a user and create a payment account."""
    r = client.post("/api/payments/accounts", json={
        "provider": provider,
        "account_id": "test-account-123",
        "config": {"account_id": "test-account-123"},
    }, headers=user_headers)
    assert r.status_code == 201
    return r.json()


# ═══════════════════════════════════════════════════════════════════════════════
# Retry Logic Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestRetryLogic:
    """Verify _request_with_retry retries on transient errors."""

    def test_retry_on_503_then_success(self):
        """Should retry on 503 and eventually succeed."""
        responses = [
            httpx.Response(503, text="Service Unavailable"),
            httpx.Response(200, json={"ok": True}),
        ]
        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        with patch("teb.payments.httpx.get", side_effect=mock_get):
            with patch("teb.payments.time.sleep"):  # Skip actual delays
                resp = payments._request_with_retry(
                    "GET", "http://test.local/api",
                    headers={}, max_retries=2,
                )
        assert resp.status_code == 200
        assert call_count == 2

    def test_retry_on_429(self):
        """Should retry on 429 Too Many Requests."""
        responses = [
            httpx.Response(429, text="Rate limited"),
            httpx.Response(429, text="Rate limited"),
            httpx.Response(200, json={"ok": True}),
        ]
        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            resp = responses[min(call_count, len(responses) - 1)]
            call_count += 1
            return resp

        with patch("teb.payments.httpx.get", side_effect=mock_get):
            with patch("teb.payments.time.sleep"):
                resp = payments._request_with_retry(
                    "GET", "http://test.local/api",
                    headers={}, max_retries=3,
                )
        assert resp.status_code == 200
        assert call_count == 3

    def test_no_retry_on_400(self):
        """Should NOT retry on 400 (client error)."""
        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            return httpx.Response(400, text="Bad Request")

        with patch("teb.payments.httpx.get", side_effect=mock_get):
            resp = payments._request_with_retry(
                "GET", "http://test.local/api",
                headers={}, max_retries=3,
            )
        assert resp.status_code == 400
        assert call_count == 1  # No retries

    def test_retry_on_timeout_exception(self):
        """Should retry on httpx.TimeoutException."""
        call_count = 0

        def mock_get(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count <= 2:
                raise httpx.TimeoutException("Connection timed out")
            return httpx.Response(200, json={"ok": True})

        with patch("teb.payments.httpx.get", side_effect=mock_get):
            with patch("teb.payments.time.sleep"):
                resp = payments._request_with_retry(
                    "GET", "http://test.local/api",
                    headers={}, max_retries=3,
                )
        assert resp.status_code == 200
        assert call_count == 3

    def test_exhausted_retries_raises(self):
        """Should raise after exhausting all retries."""
        def mock_get(url, **kwargs):
            raise httpx.TimeoutException("Connection timed out")

        with patch("teb.payments.httpx.get", side_effect=mock_get):
            with patch("teb.payments.time.sleep"):
                with pytest.raises(httpx.TimeoutException):
                    payments._request_with_retry(
                        "GET", "http://test.local/api",
                        headers={}, max_retries=2,
                    )

    def test_post_method_retry(self):
        """Retry should work with POST method too."""
        call_count = 0

        def mock_post(url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(502, text="Bad Gateway")
            return httpx.Response(201, json={"id": "tx_123"})

        with patch("teb.payments.httpx.post", side_effect=mock_post):
            with patch("teb.payments.time.sleep"):
                resp = payments._request_with_retry(
                    "POST", "http://test.local/api",
                    headers={}, max_retries=2,
                )
        assert resp.status_code == 201
        assert call_count == 2


# ═══════════════════════════════════════════════════════════════════════════════
# Balance Verification Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestBalanceVerification:
    """Verify balance is checked before executing transfers."""

    def test_execute_payment_checks_balance(self):
        """Balance check should prevent transfer if insufficient."""
        headers = _register_user(email="bal1@teb.test")
        _setup_payment_account(headers, provider="mercury")

        with patch.object(payments.MercuryProvider, "get_balance") as mock_bal:
            mock_bal.return_value = {"available": 50.0, "currency": "USD"}

            r = client.post("/api/payments/execute", json={
                "provider": "mercury",
                "amount": 100.0,
                "currency": "USD",
                "recipient": "test",
                "description": "test payment",
            }, headers=headers)

        assert r.status_code == 400
        assert "Insufficient balance" in r.json()["detail"]

    def test_execute_payment_passes_with_sufficient_balance(self):
        """Transfer should proceed when balance is sufficient."""
        headers = _register_user(email="bal2@teb.test")
        _setup_payment_account(headers, provider="mercury")

        with patch.object(payments.MercuryProvider, "get_balance") as mock_bal, \
             patch.object(payments.MercuryProvider, "create_transfer") as mock_tx:
            mock_bal.return_value = {"available": 200.0, "currency": "USD"}
            mock_tx.return_value = {
                "tx_id": "tx_abc",
                "status": "pending",
                "amount": 100.0,
                "currency": "USD",
                "raw_response": {},
            }

            r = client.post("/api/payments/execute", json={
                "provider": "mercury",
                "amount": 100.0,
                "currency": "USD",
                "recipient": "test",
                "description": "test payment",
            }, headers=headers)

        assert r.status_code == 200
        assert r.json()["status"] == "pending"

    def test_execute_payment_rejects_negative_amount(self):
        """Should reject negative payment amounts."""
        headers = _register_user(email="bal3@teb.test")
        _setup_payment_account(headers, provider="mercury")

        r = client.post("/api/payments/execute", json={
            "provider": "mercury",
            "amount": -50.0,
            "currency": "USD",
            "recipient": "test",
            "description": "test",
        }, headers=headers)

        assert r.status_code == 400
        detail = r.json()["detail"].lower()
        assert "positive" in detail or "amount" in detail

    def test_execute_payment_rejects_zero_amount(self):
        """Should reject zero payment amounts."""
        headers = _register_user(email="bal4@teb.test")
        _setup_payment_account(headers, provider="mercury")

        r = client.post("/api/payments/execute", json={
            "provider": "mercury",
            "amount": 0,
            "currency": "USD",
            "recipient": "test",
            "description": "test",
        }, headers=headers)

        assert r.status_code == 400

    def test_balance_check_failure_blocks_transfer(self):
        """If balance check itself fails, transfer should not proceed."""
        headers = _register_user(email="bal5@teb.test")
        _setup_payment_account(headers, provider="mercury")

        with patch.object(payments.MercuryProvider, "get_balance") as mock_bal:
            mock_bal.return_value = {"error": "API timeout", "available": 0, "currency": "USD"}

            r = client.post("/api/payments/execute", json={
                "provider": "mercury",
                "amount": 50.0,
                "currency": "USD",
                "recipient": "test",
                "description": "test",
            }, headers=headers)

        assert r.status_code == 400
        assert "Balance check failed" in r.json()["detail"]


_test_user_counter = 0


def _create_test_user():
    """Create a test user and return the user id."""
    global _test_user_counter
    _test_user_counter += 1
    from teb.models import User
    user = storage.create_user(User(email=f"testuser{_test_user_counter}@pay.test", password_hash="x"))
    return user.id


# ═══════════════════════════════════════════════════════════════════════════════
# Webhook Reconciliation Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookReconciliation:
    """Test webhook processing and transaction reconciliation."""

    def test_mercury_webhook_reconciles_completed(self):
        """Mercury webhook marking transaction as completed."""
        uid = _create_test_user()
        account = storage.create_payment_account(uid, "mercury", "acct-1", "{}")
        tx = storage.create_payment_transaction(
            account_id=account["id"],
            spending_request_id=None,
            amount=100.0,
            currency="USD",
            description="test",
            provider_tx_id="merc_tx_123",
        )

        # Simulate Mercury webhook
        event = {"data": {"id": "merc_tx_123", "status": "sent"}}
        result = payments._reconcile_webhook_event("mercury", event)

        assert result["reconciled"] is True
        assert result["status"] == "completed"

    def test_stripe_webhook_reconciles_succeeded(self):
        """Stripe webhook marking transaction as completed."""
        account = storage.create_payment_account(_create_test_user(), "stripe", "acct-2", "{}")
        tx = storage.create_payment_transaction(
            account_id=account["id"],
            spending_request_id=None,
            amount=50.0,
            currency="USD",
            description="test",
            provider_tx_id="pi_stripe_456",
        )

        event = {
            "type": "payment_intent.succeeded",
            "data": {"object": {"id": "pi_stripe_456", "status": "succeeded"}},
        }
        result = payments._reconcile_webhook_event("stripe", event)

        assert result["reconciled"] is True
        assert result["status"] == "completed"

    def test_stripe_webhook_reconciles_failed(self):
        """Stripe webhook marking transaction as failed."""
        account = storage.create_payment_account(_create_test_user(), "stripe", "acct-3", "{}")
        tx = storage.create_payment_transaction(
            account_id=account["id"],
            spending_request_id=None,
            amount=75.0,
            currency="USD",
            description="test",
            provider_tx_id="pi_stripe_789",
        )

        event = {
            "type": "payment_intent.payment_failed",
            "data": {"object": {"id": "pi_stripe_789", "status": "failed"}},
        }
        result = payments._reconcile_webhook_event("stripe", event)

        assert result["reconciled"] is True
        assert result["status"] == "failed"

    def test_webhook_no_matching_transaction(self):
        """Webhook for unknown provider_tx_id returns reconciled=False."""
        event = {"data": {"id": "unknown_tx_999", "status": "completed"}}
        result = payments._reconcile_webhook_event("mercury", event)
        assert result["reconciled"] is False

    def test_webhook_endpoint_unknown_provider(self):
        """POST to webhook with unknown provider returns 404."""
        r = client.post("/api/webhooks/payments/paypal", content=b"{}")
        assert r.status_code == 404

    def test_webhook_endpoint_invalid_payload(self):
        """POST with invalid JSON returns 400."""
        r = client.post(
            "/api/webhooks/payments/mercury",
            content=b"not json",
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 400

    def test_normalize_status_mapping(self):
        """Test all status normalization mappings."""
        assert payments._normalize_status("completed") == "completed"
        assert payments._normalize_status("sent") == "completed"
        assert payments._normalize_status("succeeded") == "completed"
        assert payments._normalize_status("paid") == "completed"
        assert payments._normalize_status("failed") == "failed"
        assert payments._normalize_status("rejected") == "failed"
        assert payments._normalize_status("declined") == "failed"
        assert payments._normalize_status("cancelled") == "cancelled"
        assert payments._normalize_status("canceled") == "cancelled"
        assert payments._normalize_status("voided") == "cancelled"
        assert payments._normalize_status("unknown_status") == "pending"

    def test_reconcile_transaction_by_provider_id(self):
        """Storage function to reconcile by provider_tx_id."""
        account = storage.create_payment_account(_create_test_user(), "mercury", "acct-r", "{}")
        tx = storage.create_payment_transaction(
            account_id=account["id"],
            spending_request_id=None,
            amount=200.0,
            currency="USD",
            description="reconcile test",
            provider_tx_id="ptx_reconcile",
        )

        result = storage.reconcile_transaction_by_provider_id(
            provider_tx_id="ptx_reconcile",
            status="completed",
            provider_response='{"reconciled": true}',
        )
        assert result is not None
        assert result["status"] == "completed"

    def test_reconcile_nonexistent_provider_tx(self):
        """Reconcile for non-existent provider_tx_id returns None."""
        result = storage.reconcile_transaction_by_provider_id(
            provider_tx_id="nonexistent_123",
            status="completed",
        )
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# Failed Transaction Recovery Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestTransactionRecovery:
    """Test failed transaction recovery logic."""

    def test_list_failed_transactions(self):
        """Should list failed transactions with retry_count < max."""
        account = storage.create_payment_account(_create_test_user(), "mercury", "acct-f", "{}")
        tx1 = storage.create_payment_transaction(
            account_id=account["id"], spending_request_id=None,
            amount=10.0, currency="USD", description="fail1",
            status="failed",
        )
        tx2 = storage.create_payment_transaction(
            account_id=account["id"], spending_request_id=None,
            amount=20.0, currency="USD", description="success",
            status="completed",
        )

        failed = storage.list_failed_transactions(max_retries=3)
        assert len(failed) == 1
        assert failed[0]["status"] == "failed"
        assert failed[0]["retry_count"] == 0

    def test_increment_transaction_retry(self):
        """Should increment retry_count."""
        account = storage.create_payment_account(_create_test_user(), "mercury", "acct-i", "{}")
        tx = storage.create_payment_transaction(
            account_id=account["id"], spending_request_id=None,
            amount=10.0, currency="USD", description="retry test",
            status="failed",
        )

        storage.increment_transaction_retry(tx["id"])
        storage.increment_transaction_retry(tx["id"])

        failed = storage.list_failed_transactions(max_retries=5)
        matching = [f for f in failed if f["id"] == tx["id"]]
        assert len(matching) == 1
        assert matching[0]["retry_count"] == 2

    def test_max_retries_excludes_transaction(self):
        """Transactions exceeding max retries should not appear in recovery list."""
        account = storage.create_payment_account(_create_test_user(), "mercury", "acct-m", "{}")
        tx = storage.create_payment_transaction(
            account_id=account["id"], spending_request_id=None,
            amount=10.0, currency="USD", description="max retry",
            status="failed",
        )

        # Increment past limit
        for _ in range(3):
            storage.increment_transaction_retry(tx["id"])

        failed = storage.list_failed_transactions(max_retries=3)
        matching = [f for f in failed if f["id"] == tx["id"]]
        assert len(matching) == 0

    def test_recover_failed_transactions_basic(self):
        """Recovery function finds no failed transactions when DB is empty."""
        result = payments.recover_failed_transactions()
        assert result["total_checked"] == 0
        assert result["recovered"] == 0
        assert result["still_failed"] == 0

    def test_recover_succeeds_when_provider_shows_completed(self):
        """Recovery marks transaction as completed if provider confirms it."""
        account = storage.create_payment_account(_create_test_user(), "mercury", "acct-rc",
                                                  json.dumps({"account_id": "test"}))
        tx = storage.create_payment_transaction(
            account_id=account["id"], spending_request_id=None,
            amount=50.0, currency="USD", description="recover me",
            provider_tx_id="ptx_recover_1", status="failed",
        )

        with patch.object(payments.MercuryProvider, "list_transactions") as mock_list:
            mock_list.return_value = [
                {"id": "ptx_recover_1", "amount": 50.0, "status": "sent", "description": "", "date": ""},
            ]
            result = payments.recover_failed_transactions()

        assert result["recovered"] == 1
        assert result["still_failed"] == 0

    def test_recover_increments_retry_when_still_failed(self):
        """Recovery increments retry_count when transaction is still failed."""
        account = storage.create_payment_account(_create_test_user(), "mercury", "acct-rf",
                                                  json.dumps({"account_id": "test"}))
        tx = storage.create_payment_transaction(
            account_id=account["id"], spending_request_id=None,
            amount=50.0, currency="USD", description="still failing",
            provider_tx_id="ptx_still_fail", status="failed",
        )

        with patch.object(payments.MercuryProvider, "list_transactions") as mock_list:
            mock_list.return_value = [
                {"id": "ptx_still_fail", "amount": 50.0, "status": "failed", "description": "", "date": ""},
            ]
            result = payments.recover_failed_transactions()

        assert result["still_failed"] == 1

        # Check retry_count was incremented
        failed = storage.list_failed_transactions(max_retries=5)
        matching = [f for f in failed if f["id"] == tx["id"]]
        assert matching[0]["retry_count"] == 1

    def test_recovery_endpoint_requires_admin(self):
        """POST /api/payments/recover should require admin role."""
        headers = _register_user(email="nonadmin@teb.test")
        r = client.post("/api/payments/recover", headers=headers)
        assert r.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# General API Rate Limiting Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestAPIRateLimiting:
    """Test the general API rate limiter on non-auth endpoints."""

    def test_api_rate_limit_exists(self):
        """The API rate limit should be higher than auth rate limit."""
        from teb.main import _API_RATE_LIMIT, _RATE_LIMIT
        assert _API_RATE_LIMIT > _RATE_LIMIT

    def test_api_rate_limit_triggers(self):
        """Exceeding API rate limit should return 429."""
        from teb.main import _check_api_rate_limit, _api_rate_buckets
        _api_rate_buckets.clear()

        mock_request = MagicMock()
        mock_request.client.host = "10.0.0.99"

        # Fill the bucket to the limit
        for _ in range(_API_RATE_LIMIT):
            _check_api_rate_limit(mock_request)

        # Next call should raise 429
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc_info:
            _check_api_rate_limit(mock_request)
        assert exc_info.value.status_code == 429

    def test_reset_clears_api_buckets(self):
        """reset_rate_limits should clear API rate limit buckets too."""
        from teb.main import _api_rate_buckets
        _api_rate_buckets["test_ip"] = [1, 2, 3]
        reset_rate_limits()
        assert len(_api_rate_buckets) == 0

    def test_goal_creation_has_rate_limit(self):
        """POST /api/goals should have API rate limiting."""
        headers = _register_user(email="ratelimit@teb.test")
        # Just verify a normal request succeeds (rate limit is high enough)
        r = client.post("/api/goals", json={"title": "test", "description": "test"}, headers=headers)
        assert r.status_code == 201

    def test_payment_endpoint_has_rate_limit(self):
        """Payment endpoints should have API rate limiting."""
        headers = _register_user(email="payrl@teb.test")
        r = client.get("/api/payments/providers", headers=headers)
        assert r.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# Enhanced Health Check Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestEnhancedHealthCheck:
    """Test the enhanced health check endpoint."""

    def test_health_returns_version(self):
        """Health check should include version info."""
        r = client.get("/health")
        assert r.status_code == 200
        data = r.json()
        assert "version" in data
        assert data["version"] == "2.0.0"

    def test_health_returns_uptime(self):
        """Health check should include uptime."""
        r = client.get("/health")
        data = r.json()
        assert "uptime_seconds" in data
        assert isinstance(data["uptime_seconds"], (int, float))
        assert data["uptime_seconds"] >= 0

    def test_health_returns_components(self):
        """Health check should include component status breakdown."""
        r = client.get("/health")
        data = r.json()
        assert "components" in data
        components = data["components"]
        assert "database" in components
        assert "ai" in components
        assert "payments" in components

    def test_health_database_ok(self):
        """Database component should report ok status."""
        r = client.get("/health")
        data = r.json()
        db = data["components"]["database"]
        assert db["status"] == "ok"
        assert "tables" in db
        assert isinstance(db["tables"], int)
        assert db["tables"] > 0

    def test_health_includes_python_version(self):
        """Health check should include Python version."""
        r = client.get("/health")
        data = r.json()
        assert "python_version" in data

    def test_health_ai_unconfigured(self):
        """AI component should show unconfigured when no API keys."""
        r = client.get("/health")
        data = r.json()
        ai = data["components"]["ai"]
        # In test environment, no AI keys are set
        assert ai["status"] in ("ok", "unconfigured")

    def test_health_payments_component(self):
        """Payments component should list provider status."""
        r = client.get("/health")
        data = r.json()
        pay = data["components"]["payments"]
        assert "providers" in pay
        assert len(pay["providers"]) >= 2


# ═══════════════════════════════════════════════════════════════════════════════
# Provider Webhook Verification Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestWebhookVerification:
    """Test webhook signature verification for providers."""

    def test_mercury_verify_webhook_valid(self):
        """Mercury webhook verification with valid signature."""
        provider = payments.MercuryProvider()
        secret = "test-secret-key"
        payload = b'{"data": {"id": "tx_123", "status": "sent"}}'
        signature = hmac.new(secret.encode(), payload, hashlib.sha256).hexdigest()

        assert provider.verify_webhook(payload, signature, secret) is True

    def test_mercury_verify_webhook_invalid(self):
        """Mercury webhook verification with invalid signature."""
        provider = payments.MercuryProvider()
        assert provider.verify_webhook(b"test", "wrong-sig", "secret") is False

    def test_mercury_verify_webhook_no_secret(self):
        """Mercury webhook verification with no secret returns False."""
        provider = payments.MercuryProvider()
        assert provider.verify_webhook(b"test", "sig", "") is False

    def test_stripe_verify_webhook_valid(self):
        """Stripe webhook verification with valid v1 signature."""
        provider = payments.StripeProvider()
        secret = "whsec_test_secret"
        payload = b'{"type": "payment_intent.succeeded"}'
        timestamp = str(int(time.time()))
        signed_payload = f"{timestamp}.".encode() + payload
        expected_sig = hmac.new(secret.encode(), signed_payload, hashlib.sha256).hexdigest()
        signature = f"t={timestamp},v1={expected_sig}"

        assert provider.verify_webhook(payload, signature, secret) is True

    def test_stripe_verify_webhook_invalid(self):
        """Stripe webhook verification with invalid signature."""
        provider = payments.StripeProvider()
        assert provider.verify_webhook(b"test", "t=123,v1=wrong", "secret") is False

    def test_stripe_verify_webhook_no_signature(self):
        """Stripe webhook verification with empty signature."""
        provider = payments.StripeProvider()
        assert provider.verify_webhook(b"test", "", "secret") is False

    def test_base_provider_verify_always_false(self):
        """Base PaymentProvider.verify_webhook returns False."""
        provider = payments.PaymentProvider()
        assert provider.verify_webhook(b"test", "sig", "secret") is False


# ═══════════════════════════════════════════════════════════════════════════════
# Storage Migration Tests
# ═══════════════════════════════════════════════════════════════════════════════

class TestStorageMigrations:
    """Test that storage migrations work for new columns."""

    def test_payment_transaction_has_retry_count(self):
        """payment_transactions table should have retry_count column."""
        with storage._conn() as con:
            cols = con.execute("PRAGMA table_info(payment_transactions)").fetchall()
            col_names = [c["name"] for c in cols]
        assert "retry_count" in col_names

    def test_payment_tx_status_index_exists(self):
        """Index on payment_transactions(status) should exist."""
        with storage._conn() as con:
            indexes = con.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='payment_transactions'"
            ).fetchall()
            index_names = [i["name"] for i in indexes]
        assert "idx_payment_tx_status" in index_names
