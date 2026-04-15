"""Router for execution endpoints — extracted from main.py."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from teb import storage
from teb.routers import deps

logger = logging.getLogger(__name__)

router = APIRouter(tags=["execution"])


# ─── Execution Memory ─────────────────────────────────────────────────────────

@router.get("/api/goals/{goal_id}/execution-memory", tags=["execution-memory"])
async def get_execution_memory(goal_id: int, request: Request, limit: int = Query(default=50, ge=1, le=200)):
    """Get execution memory (API call history) for a goal."""
    user_id = deps.require_user(request)
    deps.get_goal_for_user(goal_id, user_id)
    from teb.memory import get_memory_for_goal
    entries = get_memory_for_goal(goal_id, limit=limit)
    return {"goal_id": goal_id, "entries": entries, "count": len(entries)}


@router.get("/api/execution-memory/stats", tags=["execution-memory"])
async def execution_memory_stats(request: Request, goal_id: Optional[int] = Query(default=None)):
    """Get aggregate execution memory statistics."""
    deps.require_user(request)
    from teb.memory import get_memory_stats
    return get_memory_stats(goal_id)


@router.get("/api/execution-memory/advice", tags=["execution-memory"])
async def execution_memory_advice(
    request: Request,
    endpoint: str = Query(...),
    method: str = Query(default="GET"),
):
    """Check execution memory for advice on whether to proceed with an API call."""
    deps.require_user(request)
    from teb.memory import should_execute
    advice = should_execute(endpoint, method)
    return advice.to_dict()


@router.get("/api/users/me/streak", tags=["gamification"])
async def get_user_streak(request: Request):
    """Get the current user's streak."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    streak = storage.get_or_create_streak(uid)
    return streak.to_dict()


@router.get("/api/leaderboard", tags=["gamification"])
async def get_leaderboard(request: Request, period: str = "weekly", limit: int = 20):
    """Get the leaderboard for a given period."""
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    if period not in ("weekly", "monthly", "all_time"):
        raise HTTPException(status_code=400, detail="period must be weekly, monthly, or all_time")
    entries = storage.get_leaderboard(period=period, limit=min(limit, 100))
    return {"period": period, "entries": [e.to_dict() for e in entries], "count": len(entries)}


@router.post("/api/challenges", status_code=201, tags=["gamification"])
async def create_challenge(request: Request):
    """Create a team challenge."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    title = body.get("title", "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Title is required")
    from teb.models import TeamChallenge as TC
    challenge = TC(
        title=title,
        description=body.get("description", ""),
        goal_type=body.get("goal_type", "tasks_completed"),
        target_value=body.get("target_value", 10),
        creator_id=uid,
        participants_json=json.dumps(body.get("participants", [uid])),
        start_date=body.get("start_date", ""),
        end_date=body.get("end_date", ""),
    )
    saved = storage.create_team_challenge(challenge)
    return saved.to_dict()


@router.get("/api/challenges", tags=["gamification"])
async def list_challenges(request: Request, status: str = ""):
    """List team challenges."""
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    challenges = storage.list_team_challenges(status=status if status else None)
    return {"challenges": [c.to_dict() for c in challenges], "count": len(challenges)}


@router.post("/api/challenges/{challenge_id}/progress", tags=["gamification"])
async def update_challenge_progress(challenge_id: int, request: Request):
    """Increment progress on a team challenge."""
    deps.check_api_rate_limit(request)
    deps.require_user(request)
    body = await request.json()
    increment = body.get("increment", 1)
    updated = storage.update_team_challenge_progress(challenge_id, increment=increment)
    if not updated:
        raise HTTPException(status_code=404, detail="Challenge not found")
    return updated.to_dict()



# ═══════════════════════════════════════════════════════════════════════════════
# Content Blocks — Recursive block-based content model
# ═══════════════════════════════════════════════════════════════════════════════


@router.get("/api/{entity_type}/{entity_id}/blocks", tags=["content-blocks"])
async def list_blocks(entity_type: str, entity_id: int, request: Request, tree: bool = Query(default=False)):
    """List content blocks for an entity.

    Use ?tree=true to get a nested tree structure instead of a flat list.
    entity_type must be 'tasks' or 'goals'.
    """
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    # Map plural route params to singular entity_type
    et = {"tasks": "task", "goals": "goal"}.get(entity_type)
    if not et:
        raise HTTPException(status_code=400, detail="entity_type must be 'tasks' or 'goals'")
    if tree:
        return storage.get_content_block_tree(et, entity_id)
    blocks = storage.list_content_blocks(et, entity_id)
    return [b.to_dict() for b in blocks]


@router.post("/api/{entity_type}/{entity_id}/blocks", status_code=201, tags=["content-blocks"])
async def create_block(entity_type: str, entity_id: int, request: Request):
    """Create a new content block for an entity."""
    import json as _json
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    et = {"tasks": "task", "goals": "goal"}.get(entity_type)
    if not et:
        raise HTTPException(status_code=400, detail="entity_type must be 'tasks' or 'goals'")
    body = await request.json()
    from teb.models import ContentBlock  # noqa: E402
    block = ContentBlock(
        entity_type=et,
        entity_id=entity_id,
        block_type=body.get("block_type", "paragraph"),
        content=body.get("content", ""),
        properties_json=_json.dumps(body.get("properties", {})),
        parent_block_id=body.get("parent_block_id"),
        order_index=body.get("order_index", 0),
    )
    created = storage.create_content_block(block)
    return created.to_dict()


@router.get("/api/blocks/{block_id}", tags=["content-blocks"])
async def get_block(block_id: int, request: Request):
    """Get a single content block by id."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    block = storage.get_content_block(block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    return block.to_dict()


@router.patch("/api/blocks/{block_id}", tags=["content-blocks"])
async def update_block(block_id: int, request: Request):
    """Update a content block's fields."""
    import json as _json
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    body = await request.json()
    kwargs = {}
    if "block_type" in body:
        kwargs["block_type"] = body["block_type"]
    if "content" in body:
        kwargs["content"] = body["content"]
    if "properties" in body:
        kwargs["properties_json"] = _json.dumps(body["properties"])
    if "parent_block_id" in body:
        kwargs["parent_block_id"] = body["parent_block_id"]
    if "order_index" in body:
        kwargs["order_index"] = body["order_index"]
    updated = storage.update_content_block(block_id, **kwargs)
    if not updated:
        raise HTTPException(status_code=404, detail="Block not found")
    return updated.to_dict()


@router.delete("/api/blocks/{block_id}", status_code=204, tags=["content-blocks"])
async def delete_block(block_id: int, request: Request):
    """Delete a content block and its children."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    block = storage.get_content_block(block_id)
    if not block:
        raise HTTPException(status_code=404, detail="Block not found")
    storage.delete_content_block(block_id)


@router.post("/api/{entity_type}/{entity_id}/blocks/reorder", tags=["content-blocks"])
async def reorder_blocks(entity_type: str, entity_id: int, request: Request):
    """Reorder content blocks by providing an ordered list of block IDs."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    et = {"tasks": "task", "goals": "goal"}.get(entity_type)
    if not et:
        raise HTTPException(status_code=400, detail="entity_type must be 'tasks' or 'goals'")
    body = await request.json()
    block_ids = body.get("block_ids", [])
    if not isinstance(block_ids, list):
        raise HTTPException(status_code=400, detail="block_ids must be a list of integers")
    storage.reorder_content_blocks(et, entity_id, block_ids)
    return {"status": "ok"}




# ---------------------------------------------------------------------------
# DAG execution planner endpoints
# ---------------------------------------------------------------------------


@router.get("/api/goals/{goal_id}/dag", tags=["dag"])
async def get_goal_dag(goal_id: int, request: Request):
    """Return the DAG structure (execution plan + critical path) for a goal's tasks."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    from teb import dag as _dag  # noqa: E402
    validation = _dag.validate_dag(tasks)
    if not validation.is_valid:
        return {"valid": False, "errors": validation.errors, "plan": [], "critical_path": []}
    plan = _dag.build_execution_plan(tasks)
    critical_ids = _dag.get_critical_path(tasks)
    task_map = {t.id: t for t in tasks}
    return {
        "valid": True,
        "errors": [],
        "plan": [
            {
                "batch": batch.batch_index,
                "task_ids": batch.task_ids,
                "titles": [task_map[tid].title for tid in batch.task_ids if tid in task_map],
            }
            for batch in plan
        ],
        "critical_path": [
            {"id": tid, "title": task_map[tid].title}
            for tid in critical_ids
            if tid in task_map
        ],
    }


@router.post("/api/goals/{goal_id}/dag/validate", tags=["dag"])
async def validate_goal_dag(goal_id: int, request: Request):
    """Validate the DAG for a goal's tasks, reporting any cycles or missing dependencies."""
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    from teb import dag as _dag  # noqa: E402
    validation = _dag.validate_dag(tasks)
    return {"valid": validation.is_valid, "errors": validation.errors}


@router.post("/api/goals/{goal_id}/execute-dag", tags=["dag"])
async def execute_goal_dag(goal_id: int, request: Request):
    """Execute tasks in DAG order -- returns the execution plan with batch sequence.

    Validates the DAG first, then marks batch 0 tasks as in_progress.
    Frontend can poll or use SSE for progress updates.
    """
    deps.check_api_rate_limit(request)
    uid = deps.require_user(request)
    goal = deps.get_goal_for_user(goal_id, uid)
    tasks = storage.list_tasks(goal_id=goal_id)
    from teb import dag as _dag  # noqa: E402
    validation = _dag.validate_dag(tasks)
    if not validation.is_valid:
        raise HTTPException(status_code=400, detail=f"DAG validation failed: {'; '.join(validation.errors)}")
    plan = _dag.build_execution_plan(tasks)
    task_map = {t.id: t for t in tasks}
    # Start first batch
    started: list[int] = []
    if plan:
        for tid in plan[0].task_ids:
            task = task_map.get(tid)
            if task and task.status == "todo":
                task.status = "in_progress"
                storage.update_task(task)
                started.append(tid)
    return {
        "status": "executing",
        "batches": len(plan),
        "started_task_ids": started,
        "plan": [
            {"batch": batch.batch_index, "task_ids": batch.task_ids}
            for batch in plan
        ],
    }


