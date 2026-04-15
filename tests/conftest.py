"""Shared test configuration and fixtures.

Provides:
  - ``fresh_db``  (autouse) – per-test isolated SQLite database
  - ``test_client``        – ``TestClient(app, raise_server_exceptions=False)``
  - ``register_and_login`` – callable returning ``(client, headers)``
  - ``make_admin``         – callable returning ``(client, headers, user_id)``
"""

import pytest

from teb import storage
from teb.main import app, reset_rate_limits

# ---------------------------------------------------------------------------
# Core fixtures
# ---------------------------------------------------------------------------

_counter = 0


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Create a per-test isolated SQLite database and reset rate limits."""
    global _counter
    _counter += 1
    db = str(tmp_path / f"teb_test_{_counter}.db")
    storage.set_db_path(db)
    storage.init_db()
    reset_rate_limits()
    yield db
    storage.set_db_path(None)


@pytest.fixture()
def test_client():
    """A ``TestClient`` configured **not** to raise on server errors."""
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Helper factories (importable or usable as fixtures)
# ---------------------------------------------------------------------------

from starlette.testclient import TestClient  # noqa: E402


def _register_and_login_helper(
    email: str = "test@example.com",
    password: str = "StrongPass123!",
):
    """Register + login, returning ``(client, auth_headers)``."""
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/api/auth/register", json={"email": email, "password": password})
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    token = resp.json()["token"]
    return client, {"Authorization": f"Bearer {token}"}


def _make_admin_helper(
    email: str = "admin@example.com",
    password: str = "StrongPass123!",
):
    """Register + login + promote to admin.  Returns ``(client, headers, user_id)``."""
    client = TestClient(app, raise_server_exceptions=False)
    client.post("/api/auth/register", json={"email": email, "password": password})
    resp = client.post("/api/auth/login", json={"email": email, "password": password})
    data = resp.json()
    token = data["token"]
    user_id = data["user"]["id"]
    with storage._conn() as con:
        con.execute("UPDATE users SET role = 'admin' WHERE id = ?", (user_id,))
    return client, {"Authorization": f"Bearer {token}"}, user_id


@pytest.fixture()
def register_and_login():
    """Fixture returning ``_register_and_login_helper`` callable."""
    return _register_and_login_helper


@pytest.fixture()
def make_admin():
    """Fixture returning ``_make_admin_helper`` callable."""
    return _make_admin_helper
