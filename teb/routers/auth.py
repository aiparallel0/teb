"""Authentication endpoints: register, login, refresh, logout."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from teb import auth as auth_module, storage
from teb.routers.deps import require_user

router = APIRouter(prefix="/api/auth", tags=["auth"])


# ─── Request schemas ──────────────────────────────────────────────────────────

class AuthRegister(BaseModel):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not v or "@" not in v:
            raise ValueError("Invalid email address")
        return v


class AuthLogin(BaseModel):
    email: str
    password: str


class AuthRefresh(BaseModel):
    refresh_token: str


class AuthLogout(BaseModel):
    refresh_token: str = ""


# ─── Rate limiting reference (imported from main at include time) ─────────────
# These will be set by main.py when the router is included
_check_rate_limit = None


def set_rate_limiter(fn) -> None:
    """Allow main.py to inject its rate-limiting function."""
    global _check_rate_limit
    _check_rate_limit = fn


# ─── Routes ───────────────────────────────────────────────────────────────────

@router.post("/register", status_code=201)
async def register(body: AuthRegister, request: Request) -> dict:
    """Register a new user and return a JWT token."""
    if _check_rate_limit:
        _check_rate_limit(request)
    try:
        result = auth_module.register_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return result


@router.post("/login")
async def login(body: AuthLogin, request: Request) -> dict:
    """Log in and return a JWT token."""
    if _check_rate_limit:
        _check_rate_limit(request)
    try:
        result = auth_module.login_user(body.email, body.password)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return result


@router.get("/me")
async def auth_me(request: Request) -> dict:
    """Get the current authenticated user."""
    uid = require_user(request)
    user = storage.get_user(uid)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user.to_dict()


@router.post("/refresh")
async def auth_refresh(request: Request, body: AuthRefresh) -> dict:
    """Exchange a refresh token for a new access token + refresh token."""
    if _check_rate_limit:
        _check_rate_limit(request)
    try:
        result = auth_module.refresh_access_token(body.refresh_token)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    return result


@router.post("/logout")
async def auth_logout(request: Request, body: AuthLogout) -> dict:
    """Revoke refresh tokens."""
    uid = require_user(request)
    auth_module.logout_user(uid, body.refresh_token)
    return {"message": "Logged out"}
