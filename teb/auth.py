"""
Authentication module.

Provides JWT-based authentication for the teb API.
Users register with email/password, log in to get a JWT token,
and include it as a Bearer token or cookie on subsequent requests.

Security features:
- Short-lived access tokens (configurable via JWT_EXPIRE_HOURS)
- Refresh tokens for session continuity
- Login attempt tracking and account locking
- Role-based access control (user / admin)
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from teb import config, storage
from teb.models import User

_MAX_FAILED_LOGINS = 10
_LOCK_DURATION_MINUTES = 30
_REFRESH_TOKEN_DAYS = 30

# Pre-computed dummy hash used to perform a constant-time bcrypt check when the
# supplied email address does not exist, preventing timing-based user enumeration.
_DUMMY_HASH: str = bcrypt.hashpw(b"teb-dummy-password", bcrypt.gensalt(rounds=12)).decode()


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash.

    Returns False (rather than raising) on any error so that callers always
    receive a boolean and never expose internal details via exceptions.
    """
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except Exception:
        return False


def create_token(user_id: int) -> str:
    """Create a JWT access token for a user."""
    payload = {
        "sub": str(user_id),
        "exp": datetime.now(timezone.utc) + timedelta(hours=config.JWT_EXPIRE_HOURS),
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, config.JWT_SECRET, algorithm=config.JWT_ALGORITHM)


def decode_token(token: str) -> Optional[int]:
    """Decode a JWT token and return the user_id, or None if invalid."""
    try:
        payload = jwt.decode(token, config.JWT_SECRET, algorithms=[config.JWT_ALGORITHM])
        return int(payload["sub"])
    except (jwt.ExpiredSignatureError, jwt.InvalidTokenError, KeyError, ValueError):
        return None


def _create_refresh_token(user_id: int) -> str:
    """Create and store a refresh token. Returns the raw token."""
    raw_token = secrets.token_urlsafe(48)
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    expires = datetime.now(timezone.utc) + timedelta(days=_REFRESH_TOKEN_DAYS)
    storage.create_refresh_token(user_id, token_hash, expires)
    return raw_token


def _check_account_locked(user: User) -> None:
    """Raise ValueError if account is locked."""
    if user.locked_until and user.locked_until > datetime.now(timezone.utc):
        remaining = int((user.locked_until - datetime.now(timezone.utc)).total_seconds() / 60)
        raise ValueError(f"Account locked. Try again in {remaining} minutes.")


def register_user(email: str, password: str) -> dict:
    """Register a new user. Returns {"user": ..., "token": ..., "refresh_token": ...} or raises ValueError."""
    email = email.strip().lower()
    if not email or "@" not in email:
        raise ValueError("Invalid email address")
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")

    existing = storage.get_user_by_email(email)
    if existing:
        raise ValueError("Email already registered")

    user = User(email=email, password_hash=hash_password(password))
    user = storage.create_user(user)

    # Create a profile for the new user
    storage.get_or_create_profile(user_id=user.id)

    token = create_token(user.id)  # type: ignore[arg-type]
    refresh_token = _create_refresh_token(user.id)  # type: ignore[arg-type]
    return {"user": user.to_dict(), "token": token, "refresh_token": refresh_token}


def login_user(email: str, password: str) -> dict:
    """Authenticate a user. Returns {"user": ..., "token": ..., "refresh_token": ...} or raises ValueError."""
    email = email.strip().lower()
    user = storage.get_user_by_email(email)
    if not user:
        # Perform a dummy bcrypt check so that invalid-email and invalid-password
        # responses take the same amount of time, preventing user enumeration.
        verify_password(password, _DUMMY_HASH)
        raise ValueError("Invalid email or password")

    # Check if account is locked
    _check_account_locked(user)

    if not verify_password(password, user.password_hash):
        # Track failed login attempts
        attempts = storage.record_failed_login(user.id)  # type: ignore[arg-type]
        if attempts >= _MAX_FAILED_LOGINS:
            lock_until = datetime.now(timezone.utc) + timedelta(minutes=_LOCK_DURATION_MINUTES)
            storage.lock_user(user.id, lock_until)  # type: ignore[arg-type]
            raise ValueError(f"Too many failed attempts. Account locked for {_LOCK_DURATION_MINUTES} minutes.")
        raise ValueError("Invalid email or password")

    # Successful login — reset failed attempts
    storage.reset_failed_logins(user.id)  # type: ignore[arg-type]

    token = create_token(user.id)  # type: ignore[arg-type]
    refresh_token = _create_refresh_token(user.id)  # type: ignore[arg-type]
    return {"user": user.to_dict(), "token": token, "refresh_token": refresh_token}


def refresh_access_token(raw_refresh_token: str) -> dict:
    """Exchange a valid refresh token for a new access token.

    Returns {"token": ..., "refresh_token": ...} or raises ValueError.
    """
    token_hash = hashlib.sha256(raw_refresh_token.encode()).hexdigest()
    stored = storage.get_refresh_token(token_hash)
    if not stored:
        raise ValueError("Invalid refresh token")

    # Check expiry
    expires_at = datetime.fromisoformat(stored["expires_at"])
    if expires_at < datetime.now(timezone.utc):
        storage.revoke_refresh_token(token_hash)
        raise ValueError("Refresh token expired")

    user_id = stored["user_id"]

    # Revoke the old refresh token (rotation)
    storage.revoke_refresh_token(token_hash)

    # Issue new tokens
    new_access = create_token(user_id)
    new_refresh = _create_refresh_token(user_id)
    return {"token": new_access, "refresh_token": new_refresh, "user_id": user_id}


def logout_user(user_id: int, raw_refresh_token: Optional[str] = None) -> None:
    """Revoke a specific refresh token or all tokens for the user."""
    if raw_refresh_token:
        token_hash = hashlib.sha256(raw_refresh_token.encode()).hexdigest()
        storage.revoke_refresh_token(token_hash)
    else:
        storage.revoke_all_refresh_tokens(user_id)
