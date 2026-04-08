"""
Authentication module.

Provides JWT-based authentication for the teb API.
Users register with email/password, log in to get a JWT token,
and include it as a Bearer token or cookie on subsequent requests.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

import bcrypt
import jwt

from teb import config, storage
from teb.models import User


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    """Verify a password against a bcrypt hash."""
    return bcrypt.checkpw(password.encode(), hashed.encode())


def create_token(user_id: int) -> str:
    """Create a JWT token for a user."""
    payload = {
        "sub": user_id,
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


def register_user(email: str, password: str) -> dict:
    """Register a new user. Returns {"user": ..., "token": ...} or raises ValueError."""
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
    return {"user": user.to_dict(), "token": token}


def login_user(email: str, password: str) -> dict:
    """Authenticate a user. Returns {"user": ..., "token": ...} or raises ValueError."""
    email = email.strip().lower()
    user = storage.get_user_by_email(email)
    if not user or not verify_password(password, user.password_hash):
        raise ValueError("Invalid email or password")

    token = create_token(user.id)  # type: ignore[arg-type]
    return {"user": user.to_dict(), "token": token}
