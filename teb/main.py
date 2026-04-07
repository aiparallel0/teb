from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from teb import decomposer, storage
from teb.models import Goal, Task


# ─── Startup / lifespan ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    storage.init_db()
    yield


app = FastAPI(title="teb — Task Execution Bridge", lifespan=lifespan)

# Static files
_STATIC_DIR = Path(__file__).parent / "static"
_TEMPLATES_DIR = Path(__file__).parent / "templates"

app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")


# ─── Request / Response schemas ───────────────────────────────────────────────

class GoalCreate(BaseModel):
    title: str
    description: str = ""


class TaskPatch(BaseModel):
    status: Optional[str] = None
    notes: Optional[str] = None   # stored in description for simplicity


class ClarifyAnswer(BaseModel):
    key: str
    answer: str


# ─── Frontend ─────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    html = (_TEMPLATES_DIR / "index.html").read_text()
    return HTMLResponse(content=html)


# ─── Goals ────────────────────────────────────────────────────────────────────

@app.post("/api/goals", status_code=201)
async def create_goal(body: GoalCreate):
    goal = Goal(title=body.title.strip(), description=body.description.strip())
    if not goal.title:
        raise HTTPException(status_code=422, detail="title must not be empty")
    goal = storage.create_goal(goal)
    return goal.to_dict()


@app.get("/api/goals")
async def list_goals():
    return [g.to_dict() for g in storage.list_goals()]


@app.get("/api/goals/{goal_id}")
async def get_goal(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    data = goal.to_dict()
    data["tasks"] = [t.to_dict() for t in tasks]
    return data


@app.post("/api/goals/{goal_id}/decompose")
async def decompose_goal(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Clear any previous tasks
    storage.delete_tasks_for_goal(goal_id)

    # Decompose
    tasks = decomposer.decompose(goal)

    # Persist tasks; handle subtask relationships
    saved: list[dict] = []
    for task in tasks:
        subtask_templates = getattr(task, "_subtask_templates", [])
        saved_task = storage.create_task(task)
        saved.append(saved_task.to_dict())

        for s_idx, sub_template in enumerate(subtask_templates):
            if isinstance(sub_template, decomposer._TemplateTask):
                sub = Task(
                    goal_id=goal_id,
                    parent_id=saved_task.id,
                    title=sub_template.title,
                    description=sub_template.description,
                    estimated_minutes=sub_template.estimated_minutes,
                    order_index=s_idx,
                )
            else:
                # AI mode: sub_template is a dict
                sub = Task(
                    goal_id=goal_id,
                    parent_id=saved_task.id,
                    title=str(sub_template.get("title", "Subtask")),
                    description=str(sub_template.get("description", "")),
                    estimated_minutes=int(sub_template.get("estimated_minutes", 15)),
                    order_index=s_idx,
                )
            saved_sub = storage.create_task(sub)
            saved.append(saved_sub.to_dict())

    # Update goal status
    goal.status = "decomposed"
    storage.update_goal(goal)

    return {"goal_id": goal_id, "tasks": saved}


# ─── Task-level decomposition ─────────────────────────────────────────────────

@app.post("/api/tasks/{task_id}/decompose")
async def decompose_task(task_id: int):
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    # Don't re-decompose if already has children
    existing = storage.list_tasks(goal_id=task.goal_id)
    has_children = any(t.parent_id == task_id for t in existing)
    if has_children:
        raise HTTPException(status_code=409, detail="Task already has sub-tasks")

    subtasks = decomposer.decompose_task(task)

    saved: list[dict] = []
    for sub in subtasks:
        saved_sub = storage.create_task(sub)
        saved.append(saved_sub.to_dict())

    return {"task_id": task_id, "subtasks": saved}


# ─── Focus mode ───────────────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/focus")
async def get_focus(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    focus = decomposer.get_focus_task(tasks)
    if focus is None:
        return {"focus_task": None, "message": "All tasks completed — well done!"}
    return {"focus_task": focus.to_dict()}


# ─── Progress summary ─────────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/progress")
async def get_progress(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    tasks = storage.list_tasks(goal_id=goal_id)
    summary = decomposer.get_progress_summary(tasks)
    summary["goal_id"] = goal_id
    summary["goal_status"] = goal.status
    return summary


# ─── Clarifying questions ─────────────────────────────────────────────────────

@app.get("/api/goals/{goal_id}/next_question")
async def next_question(goal_id: int):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    q = decomposer.get_next_question(goal)
    if q is None:
        return {"done": True, "question": None}
    return {"done": False, "question": {"key": q.key, "text": q.text, "hint": q.hint}}


@app.post("/api/goals/{goal_id}/clarify")
async def submit_clarify(goal_id: int, body: ClarifyAnswer):
    goal = storage.get_goal(goal_id)
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")
    goal.answers[body.key] = body.answer
    goal.status = "clarifying"
    storage.update_goal(goal)
    next_q = decomposer.get_next_question(goal)
    if next_q is None:
        return {"done": True, "next_question": None}
    return {
        "done": False,
        "next_question": {"key": next_q.key, "text": next_q.text, "hint": next_q.hint},
    }


# ─── Tasks ────────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def list_tasks(
    goal_id: Optional[int] = Query(default=None),
    status: Optional[str] = Query(default=None),
):
    return [t.to_dict() for t in storage.list_tasks(goal_id=goal_id, status=status)]


@app.patch("/api/tasks/{task_id}")
async def update_task(task_id: int, body: TaskPatch):
    task = storage.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    valid_statuses = {"todo", "in_progress", "done", "skipped"}
    if body.status is not None:
        if body.status not in valid_statuses:
            raise HTTPException(status_code=422, detail=f"status must be one of {valid_statuses}")
        task.status = body.status

    if body.notes is not None:
        task.description = body.notes

    # Update the goal's status if all tasks are done
    task = storage.update_task(task)

    all_tasks = storage.list_tasks(goal_id=task.goal_id)
    top_level = [t for t in all_tasks if t.parent_id is None]
    if top_level and all(t.status in ("done", "skipped") for t in top_level):
        goal = storage.get_goal(task.goal_id)
        if goal and goal.status != "done":
            goal.status = "done"
            storage.update_goal(goal)
    elif any(t.status == "in_progress" for t in all_tasks):
        goal = storage.get_goal(task.goal_id)
        if goal and goal.status == "decomposed":
            goal.status = "in_progress"
            storage.update_goal(goal)

    return task.to_dict()
