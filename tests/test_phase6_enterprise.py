"""Tests for Phase 6 — Enterprise: 2FA & Session Management."""
import pytest
from starlette.testclient import TestClient

from teb import storage
from teb.main import app, reset_rate_limits

_counter = 0


@pytest.fixture(autouse=True)
def _fresh_db(tmp_path):
    db = str(tmp_path / "test_phase6.db")
    storage.set_db_path(db)
    storage.init_db()
    yield
    storage.set_db_path(None)


@pytest.fixture()
def client():
    return TestClient(app, raise_server_exceptions=False)


def _register(client, email=None, password="TestPass123!"):
    global _counter
    _counter += 1
    if email is None:
        email = f"p6user{_counter}@test.com"
    reset_rate_limits()
    resp = client.post("/api/auth/register", json={"email": email, "password": password})
    assert resp.status_code == 201
    data = resp.json()
    return data["token"], data["user"]["id"], email


# ── TOTP unit tests ──────────────────────────────────────────────────────────

class TestTOTP:
    def test_generate_secret(self):
        from teb.totp import generate_secret
        s = generate_secret()
        assert len(s) == 32

    def test_generate_and_verify(self):
        from teb.totp import generate_secret, generate_totp, verify_totp
        secret = generate_secret()
        code = generate_totp(secret)
        assert verify_totp(secret, code)

    def test_verify_bad_code(self):
        from teb.totp import generate_secret, verify_totp
        secret = generate_secret()
        assert not verify_totp(secret, "000000")

    def test_get_totp_uri(self):
        from teb.totp import generate_secret, get_totp_uri
        secret = generate_secret()
        uri = get_totp_uri(secret, "test@example.com")
        assert uri.startswith("otpauth://totp/")
        assert secret in uri

    def test_backup_codes(self):
        from teb.totp import generate_backup_codes
        codes = generate_backup_codes(5)
        assert len(codes) == 5
        assert all(len(c) == 8 for c in codes)
        assert len(set(codes)) == 5


# ── 2FA API endpoint tests ──────────────────────────────────────────────────

class TestTwoFactorEndpoints:
    def test_2fa_status_default_off(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        r = client.get("/api/auth/2fa/status", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_2fa_setup(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        r = client.post("/api/auth/2fa/setup", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        data = r.json()
        assert "secret" in data
        assert "uri" in data
        assert "backup_codes" in data
        assert len(data["backup_codes"]) == 8

    def test_2fa_verify_and_enable(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        setup = client.post("/api/auth/2fa/setup", headers={"Authorization": f"Bearer {token}"})
        secret = setup.json()["secret"]
        from teb.totp import generate_totp
        code = generate_totp(secret)
        reset_rate_limits()
        r = client.post("/api/auth/2fa/verify",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"code": code})
        assert r.status_code == 200
        assert r.json()["enabled"] is True
        reset_rate_limits()
        status = client.get("/api/auth/2fa/status", headers={"Authorization": f"Bearer {token}"})
        assert status.json()["enabled"] is True

    def test_2fa_verify_bad_code(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        client.post("/api/auth/2fa/setup", headers={"Authorization": f"Bearer {token}"})
        reset_rate_limits()
        r = client.post("/api/auth/2fa/verify",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"code": "000000"})
        assert r.status_code == 400

    def test_2fa_disable(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        setup = client.post("/api/auth/2fa/setup", headers={"Authorization": f"Bearer {token}"})
        secret = setup.json()["secret"]
        from teb.totp import generate_totp
        code = generate_totp(secret)
        reset_rate_limits()
        client.post("/api/auth/2fa/verify",
                     headers={"Authorization": f"Bearer {token}"},
                     json={"code": code})
        code2 = generate_totp(secret)
        reset_rate_limits()
        r = client.post("/api/auth/2fa/disable",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"code": code2})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

    def test_2fa_disable_when_not_enabled(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        r = client.post("/api/auth/2fa/disable",
                         headers={"Authorization": f"Bearer {token}"},
                         json={"code": "123456"})
        assert r.status_code == 400

    def test_2fa_setup_already_enabled(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        setup = client.post("/api/auth/2fa/setup", headers={"Authorization": f"Bearer {token}"})
        secret = setup.json()["secret"]
        from teb.totp import generate_totp
        code = generate_totp(secret)
        reset_rate_limits()
        client.post("/api/auth/2fa/verify",
                     headers={"Authorization": f"Bearer {token}"},
                     json={"code": code})
        reset_rate_limits()
        r = client.post("/api/auth/2fa/setup", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 400


# ── Session Management tests ────────────────────────────────────────────────

class TestSessionEndpoints:
    def test_list_sessions_empty(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        r = client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert "sessions" in r.json()

    def test_create_and_list_sessions(self, client):
        from teb.models import UserSession
        import secrets
        token, uid, _ = _register(client)
        storage.create_session(UserSession(
            user_id=uid, session_token=secrets.token_hex(16),
            ip_address="127.0.0.1", user_agent="TestBrowser/1.0",
        ))
        reset_rate_limits()
        r = client.get("/api/auth/sessions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert len(r.json()["sessions"]) >= 1

    def test_revoke_session(self, client):
        from teb.models import UserSession
        import secrets
        token, uid, _ = _register(client)
        s = storage.create_session(UserSession(
            user_id=uid, session_token=secrets.token_hex(16),
            ip_address="10.0.0.1", user_agent="Test/2.0",
        ))
        reset_rate_limits()
        r = client.delete(f"/api/auth/sessions/{s.id}", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["revoked"] is True

    def test_revoke_session_not_found(self, client):
        token, uid, _ = _register(client)
        reset_rate_limits()
        r = client.delete("/api/auth/sessions/99999", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 404

    def test_revoke_all_sessions(self, client):
        from teb.models import UserSession
        import secrets
        token, uid, _ = _register(client)
        for _ in range(3):
            storage.create_session(UserSession(
                user_id=uid, session_token=secrets.token_hex(16),
                ip_address="10.0.0.1", user_agent="Test/3.0",
            ))
        reset_rate_limits()
        r = client.delete("/api/auth/sessions", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
        assert r.json()["revoked_count"] >= 3


# ── Storage CRUD unit tests ──────────────────────────────────────────────────

class TestStorageCRUD:
    def test_session_crud(self):
        from teb.models import UserSession
        import secrets
        s = storage.create_session(UserSession(
            user_id=999, session_token=secrets.token_hex(16),
            ip_address="1.2.3.4", user_agent="Test",
        ))
        assert s.id is not None
        sessions = storage.list_user_sessions(999)
        assert len(sessions) >= 1
        ok = storage.revoke_session(s.id, 999)
        assert ok is True
        sessions_after = storage.list_user_sessions(999)
        assert len(sessions_after) == len(sessions) - 1

    def test_session_revoke_all(self):
        from teb.models import UserSession
        import secrets
        uid = 998
        for _ in range(3):
            storage.create_session(UserSession(
                user_id=uid, session_token=secrets.token_hex(16),
                ip_address="5.6.7.8", user_agent="Test",
            ))
        count = storage.revoke_all_sessions(uid)
        assert count >= 3
        assert len(storage.list_user_sessions(uid)) == 0

    def test_2fa_config_crud(self):
        from teb.models import TwoFactorConfig
        cfg = TwoFactorConfig(user_id=997, totp_secret="ABCDEFGHIJ234567", is_enabled=False)
        storage.save_two_factor_config(cfg)
        loaded = storage.get_two_factor_config(997)
        assert loaded is not None
        assert loaded.totp_secret == "ABCDEFGHIJ234567"
        loaded.is_enabled = True
        storage.save_two_factor_config(loaded)
        loaded2 = storage.get_two_factor_config(997)
        assert loaded2.is_enabled is True
        ok = storage.disable_two_factor(997)
        assert ok is True
        loaded3 = storage.get_two_factor_config(997)
        assert loaded3.is_enabled is False

    def test_2fa_config_not_found(self):
        assert storage.get_two_factor_config(99999) is None

    def test_update_session_activity(self):
        from teb.models import UserSession
        import secrets
        s = storage.create_session(UserSession(
            user_id=996, session_token=secrets.token_hex(16),
            ip_address="10.10.10.10", user_agent="Test",
        ))
        storage.update_session_activity(s.id)
