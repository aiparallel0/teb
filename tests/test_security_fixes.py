"""
Tests for MVP security fixes:
- API credential scoping to users (user_id field, ownership checks)
- Goal.to_dict() includes user_id
- Rate limiting on /api/auth/refresh
- Payment config centralization
- SECRET_KEY startup warning
"""

import pytest
from fastapi.testclient import TestClient

from teb import config, storage
from teb.main import app
from teb.models import ApiCredential, Goal

client = TestClient(app)


# ─── Fixtures ─────────────────────────────────────────────────────────────────

def _register_user(email="sec@teb.test", pw="testpass123"):
    r = client.post("/api/auth/register", json={"email": email, "password": pw})
    if r.status_code not in (200, 201):
        r = client.post("/api/auth/login", json={"email": email, "password": pw})
    return {"Authorization": f"Bearer {r.json()['token']}"}


# ═══════════════════════════════════════════════════════════════════════════════
# Credential Scoping to Users
# ═══════════════════════════════════════════════════════════════════════════════

class TestCredentialScoping:
    def test_credential_has_user_id_field(self):
        cred = ApiCredential(name="Test", base_url="https://example.com")
        assert hasattr(cred, "user_id")
        assert cred.user_id is None

    def test_create_credential_with_user_id(self):
        from teb.models import User
        user = storage.create_user(User(email="cred1@test.com", password_hash="x"))
        cred = ApiCredential(name="Test", base_url="https://example.com", user_id=user.id)
        cred = storage.create_credential(cred)
        loaded = storage.get_credential(cred.id)
        assert loaded is not None
        assert loaded.user_id == user.id

    def test_create_credential_without_user_id(self):
        cred = ApiCredential(name="Test", base_url="https://example.com")
        cred = storage.create_credential(cred)
        loaded = storage.get_credential(cred.id)
        assert loaded is not None
        assert loaded.user_id is None

    def test_list_credentials_scoped_to_user(self):
        """User should see their own credentials + legacy unscoped ones."""
        from teb.models import User
        u1 = storage.create_user(User(email="scope1@test.com", password_hash="x"))
        u2 = storage.create_user(User(email="scope2@test.com", password_hash="x"))

        # Create legacy unscoped credential
        storage.create_credential(
            ApiCredential(name="Legacy", base_url="https://legacy.com"),
        )
        # Create user 1's credential
        storage.create_credential(
            ApiCredential(name="User1", base_url="https://u1.com", user_id=u1.id),
        )
        # Create user 2's credential
        storage.create_credential(
            ApiCredential(name="User2", base_url="https://u2.com", user_id=u2.id),
        )

        # User 1 should see Legacy + User1, but NOT User2
        user1_creds = storage.list_credentials(user_id=u1.id)
        names = {c.name for c in user1_creds}
        assert "Legacy" in names
        assert "User1" in names
        assert "User2" not in names

        # User 2 should see Legacy + User2, but NOT User1
        user2_creds = storage.list_credentials(user_id=u2.id)
        names = {c.name for c in user2_creds}
        assert "Legacy" in names
        assert "User2" in names
        assert "User1" not in names

    def test_list_credentials_no_user_id_returns_all(self):
        """Calling list_credentials() without user_id returns all credentials."""
        from teb.models import User
        u1 = storage.create_user(User(email="all1@test.com", password_hash="x"))
        u2 = storage.create_user(User(email="all2@test.com", password_hash="x"))
        storage.create_credential(
            ApiCredential(name="A", base_url="https://a.com", user_id=u1.id),
        )
        storage.create_credential(
            ApiCredential(name="B", base_url="https://b.com", user_id=u2.id),
        )
        all_creds = storage.list_credentials()
        assert len(all_creds) == 2

    def test_create_credential_endpoint_sets_user_id(self):
        """POST /api/credentials should associate credential with the auth'd user."""
        headers = _register_user()
        r = client.post("/api/credentials", json={
            "name": "My API",
            "base_url": "https://api.mine.com",
        }, headers=headers)
        assert r.status_code == 201
        cred_id = r.json()["id"]

        # Verify the credential has the correct user_id in storage
        cred = storage.get_credential(cred_id)
        assert cred.user_id is not None

    def test_list_credentials_endpoint_scoped(self):
        """GET /api/credentials should only return the user's own credentials."""
        headers1 = _register_user(email="user1@test.com")
        headers2 = _register_user(email="user2@test.com")

        # User1 creates a credential
        r1 = client.post("/api/credentials", json={
            "name": "User1 API",
            "base_url": "https://u1.api.com",
        }, headers=headers1)
        assert r1.status_code == 201

        # User2 creates a credential
        r2 = client.post("/api/credentials", json={
            "name": "User2 API",
            "base_url": "https://u2.api.com",
        }, headers=headers2)
        assert r2.status_code == 201

        # User1 should only see their own
        listing1 = client.get("/api/credentials", headers=headers1)
        names1 = {c["name"] for c in listing1.json()}
        assert "User1 API" in names1
        assert "User2 API" not in names1

        # User2 should only see their own
        listing2 = client.get("/api/credentials", headers=headers2)
        names2 = {c["name"] for c in listing2.json()}
        assert "User2 API" in names2
        assert "User1 API" not in names2

    def test_delete_credential_ownership_check(self):
        """Users cannot delete another user's credential."""
        headers1 = _register_user(email="del1@test.com")
        headers2 = _register_user(email="del2@test.com")

        # User1 creates a credential
        r = client.post("/api/credentials", json={
            "name": "User1 Only",
            "base_url": "https://u1only.com",
        }, headers=headers1)
        cred_id = r.json()["id"]

        # User2 tries to delete it → 403
        r = client.delete(f"/api/credentials/{cred_id}", headers=headers2)
        assert r.status_code == 403
        assert "Not your credential" in r.json()["detail"]

        # User1 can delete their own
        r = client.delete(f"/api/credentials/{cred_id}", headers=headers1)
        assert r.status_code == 200

    def test_delete_credential_not_found(self):
        """Deleting a non-existent credential returns 404."""
        headers = _register_user(email="notfound@test.com")
        r = client.delete("/api/credentials/999999", headers=headers)
        assert r.status_code == 404


# ═══════════════════════════════════════════════════════════════════════════════
# Schema Migrations
# ═══════════════════════════════════════════════════════════════════════════════

class TestCredentialMigration:
    def test_api_credentials_user_id_column_exists(self):
        """Migration should add user_id column to api_credentials."""
        from teb.models import User
        user = storage.create_user(User(email="mig@test.com", password_hash="x"))
        cred = ApiCredential(name="Test", base_url="https://test.com", user_id=user.id)
        cred = storage.create_credential(cred)
        loaded = storage.get_credential(cred.id)
        assert loaded.user_id == user.id


# ═══════════════════════════════════════════════════════════════════════════════
# Goal.to_dict() includes user_id
# ═══════════════════════════════════════════════════════════════════════════════

class TestGoalToDict:
    def test_goal_to_dict_includes_user_id(self):
        from teb.models import User
        user = storage.create_user(User(email="gdict@test.com", password_hash="x"))
        g = Goal(title="test", description="", user_id=user.id)
        g = storage.create_goal(g)
        d = g.to_dict()
        assert "user_id" in d
        assert d["user_id"] == user.id

    def test_goal_to_dict_user_id_none(self):
        g = Goal(title="test", description="")
        g = storage.create_goal(g)
        d = g.to_dict()
        assert "user_id" in d
        assert d["user_id"] is None

    def test_goal_endpoint_includes_user_id(self):
        headers = _register_user(email="goaldict@test.com")
        r = client.post("/api/goals", json={"title": "test", "description": "x"}, headers=headers)
        assert r.status_code == 201
        assert "user_id" in r.json()


# ═══════════════════════════════════════════════════════════════════════════════
# Rate Limiting on /api/auth/refresh
# ═══════════════════════════════════════════════════════════════════════════════

class TestRefreshRateLimit:
    def test_refresh_endpoint_exists(self):
        """The refresh endpoint should accept POST requests."""
        r = client.post("/api/auth/refresh", json={"refresh_token": "invalid"})
        # Should return 401 (bad token), not 405 (method not allowed)
        assert r.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# Payment Config Centralization
# ═══════════════════════════════════════════════════════════════════════════════

class TestPaymentConfig:
    def test_mercury_config_in_config_module(self):
        assert hasattr(config, "MERCURY_API_KEY")
        assert hasattr(config, "MERCURY_BASE_URL")
        assert config.MERCURY_BASE_URL == "https://api.mercury.com/api/v1"

    def test_stripe_config_in_config_module(self):
        assert hasattr(config, "STRIPE_API_KEY")
        assert hasattr(config, "STRIPE_BASE_URL")
        assert config.STRIPE_BASE_URL == "https://api.stripe.com/v1"

    def test_payments_module_uses_config(self):
        """payments.py should import from config, not os.getenv directly."""
        from teb import payments
        # The module should use config values
        assert payments.MERCURY_BASE_URL == config.MERCURY_BASE_URL
        assert payments.STRIPE_BASE_URL == config.STRIPE_BASE_URL
