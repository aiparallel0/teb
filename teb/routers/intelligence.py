"""Router for intelligence endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from teb import storage
from teb.routers import deps
from teb import intelligence


logger = logging.getLogger(__name__)

router = APIRouter(tags=["intelligence"])


# ─── Phase 4: Intelligence endpoints ──────────────────────────────────────────


class _WriteAssistBody(BaseModel):
    context: str = ""
    prompt: str = ""


class _TemplateGenBody(BaseModel):
    description: str


class _MeetingNotesBody(BaseModel):
    notes: str


class _SuggestTagsBody(BaseModel):
    text: str


@router.post("/api/goals/{goal_id}/reschedule", tags=["intelligence"])
async def reschedule_goal(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.auto_reschedule(goal_id)
    return result


@router.get("/api/users/me/focus-recommendations", tags=["intelligence"])
async def focus_recommendations(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    result = intelligence.get_focus_recommendations(uid)
    return result


@router.post("/api/ai/write", tags=["intelligence"])
async def ai_write(body: _WriteAssistBody, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.assist_writing(body.context, body.prompt)
    return result


@router.post("/api/ai/generate-template", tags=["intelligence"])
async def ai_generate_template(body: _TemplateGenBody, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.generate_template_from_description(body.description)
    return result


@router.post("/api/ai/meeting-to-tasks", tags=["intelligence"])
async def ai_meeting_to_tasks(body: _MeetingNotesBody, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.extract_tasks_from_notes(body.notes)
    return result


@router.get("/api/goals/{goal_id}/status-report", tags=["intelligence"])
async def goal_status_report(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.generate_status_report(goal_id)
    return result


@router.post("/api/ai/suggest-tags", tags=["intelligence"])
async def ai_suggest_tags(body: _SuggestTagsBody, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.suggest_tags(body.text)
    return result


@router.get("/api/users/me/workflow-suggestions", tags=["intelligence"])
async def workflow_suggestions(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    result = intelligence.get_workflow_suggestions(uid)
    return result


@router.get("/api/users/me/insights", tags=["intelligence"])
async def cross_goal_insights(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    result = intelligence.get_cross_goal_insights(uid)
    return result


@router.get("/api/users/me/skill-gaps", tags=["intelligence"])
async def skill_gaps(request: Request):
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    result = intelligence.analyze_skill_gaps(uid)
    return result


@router.get("/api/goals/{goal_id}/stagnation-check", tags=["intelligence"])
async def stagnation_check(goal_id: int, request: Request):
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    result = intelligence.detect_stagnation(goal_id)
    return result



