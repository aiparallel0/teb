"""Settings endpoints: credentials and user profile."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from teb import storage
from teb.models import ApiCredential
from teb.routers.deps import require_user

router = APIRouter(tags=["settings"])


# ─── Request schemas ──────────────────────────────────────────────────────────

class CredentialCreate(BaseModel):
    name: str
    base_url: str
    auth_header: str = "Authorization"
    auth_value: str = ""
    description: str = ""


# ─── Credentials ──────────────────────────────────────────────────────────────

@router.post("/api/credentials", status_code=201)
async def create_credential(body: CredentialCreate, request: Request) -> dict:
    uid = require_user(request)
    name = body.name.strip()
    base_url = body.base_url.strip()
    if not name:
        raise HTTPException(status_code=422, detail="name must not be empty")
    if not base_url:
        raise HTTPException(status_code=422, detail="base_url must not be empty")
    cred = ApiCredential(
        name=name,
        base_url=base_url,
        auth_header=body.auth_header.strip() or "Authorization",
        auth_value=body.auth_value,
        description=body.description.strip(),
        user_id=uid,
    )
    cred = storage.create_credential(cred)
    return cred.to_dict()


@router.get("/api/credentials")
async def list_credentials(request: Request) -> list:
    uid = require_user(request)
    return [c.to_dict() for c in storage.list_credentials(user_id=uid)]


@router.delete("/api/credentials/{cred_id}", status_code=200)
async def delete_credential(cred_id: int, request: Request) -> dict:
    uid = require_user(request)
    cred = storage.get_credential(cred_id)
    if not cred:
        raise HTTPException(status_code=404, detail="Credential not found")
    if cred.user_id is not None and cred.user_id != uid:
        raise HTTPException(status_code=403, detail="Not your credential")
    storage.delete_credential(cred_id)
    return {"deleted": cred_id}


# ─── Profile ─────────────────────────────────────────────────────────────────

@router.get("/api/profile")
async def get_profile(request: Request) -> dict:
    uid = require_user(request)
    profile = storage.get_or_create_profile(user_id=uid)
    return profile.to_dict()


@router.patch("/api/profile")
async def update_profile(body: dict, request: Request) -> dict:
    uid = require_user(request)
    profile = storage.get_or_create_profile(user_id=uid)
    for key in ("skills", "available_hours_per_day", "experience_level",
                "interests", "preferred_learning_style"):
        if key in body:
            setattr(profile, key, body[key])
    profile = storage.update_profile(profile)
    return profile.to_dict()
