"""
Execution State Machine with Checkpointing (WP-01).

Provides resumable goal execution. When execution fails mid-way,
resuming picks up at exactly the failed step with all prior context intact.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from teb import storage
from teb.models import ExecutionCheckpoint, Task

logger = logging.getLogger(__name__)


@dataclass
class ExecutionState:
    """Typed state for a goal execution session."""
    goal_id: int
    current_task_index: int = 0
    completed_task_ids: List[int] = field(default_factory=list)
    failed_task_ids: List[int] = field(default_factory=list)
    skipped_task_ids: List[int] = field(default_factory=list)
    results: Dict[int, Dict[str, Any]] = field(default_factory=dict)
    context: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "goal_id": self.goal_id,
            "current_task_index": self.current_task_index,
            "completed_task_ids": self.completed_task_ids,
            "failed_task_ids": self.failed_task_ids,
            "skipped_task_ids": self.skipped_task_ids,
            "results": {str(k): v for k, v in self.results.items()},
            "context": self.context,
        })

    @classmethod
    def from_json(cls, goal_id: int, data: str) -> "ExecutionState":
        parsed = json.loads(data)
        return cls(
            goal_id=goal_id,
            current_task_index=parsed.get("current_task_index", 0),
            completed_task_ids=parsed.get("completed_task_ids", []),
            failed_task_ids=parsed.get("failed_task_ids", []),
            skipped_task_ids=parsed.get("skipped_task_ids", []),
            results={int(k): v for k, v in parsed.get("results", {}).items()},
            context=parsed.get("context", {}),
        )

    def to_dict(self) -> dict:
        return {
            "goal_id": self.goal_id,
            "current_task_index": self.current_task_index,
            "completed_tasks": len(self.completed_task_ids),
            "failed_tasks": len(self.failed_task_ids),
            "skipped_tasks": len(self.skipped_task_ids),
            "total_results": len(self.results),
        }


TRANSITION_TABLE: Dict[str, List[str]] = {
    "pending": ["executing"],
    "executing": ["checkpoint", "completed", "failed"],
    "checkpoint": ["executing", "failed"],
    "completed": [],
    "failed": ["executing"],
}


def validate_transition(current: str, target: str) -> bool:
    return target in TRANSITION_TABLE.get(current, [])


def create_execution(goal_id: int, tasks: List[Task]) -> ExecutionState:
    state = ExecutionState(goal_id=goal_id)
    if not tasks:
        return state
    first_task = tasks[0]
    cp = ExecutionCheckpoint(
        goal_id=goal_id, task_id=first_task.id or 0,
        step_index=0, state_json=state.to_json(), status="active",
    )
    storage.create_checkpoint(cp)
    return state


def save_checkpoint(goal_id: int, task_id: int, step_index: int,
                    state: ExecutionState) -> ExecutionCheckpoint:
    existing = storage.get_active_checkpoint(goal_id)
    if existing and existing.id:
        storage.update_checkpoint(existing.id, status="completed")
    cp = ExecutionCheckpoint(
        goal_id=goal_id, task_id=task_id,
        step_index=step_index, state_json=state.to_json(), status="active",
    )
    return storage.create_checkpoint(cp)


def resume_execution(goal_id: int) -> Optional[ExecutionState]:
    cp = storage.get_active_checkpoint(goal_id)
    if not cp:
        return None
    state = ExecutionState.from_json(goal_id, cp.state_json)
    state.current_task_index = cp.step_index
    if cp.id:
        storage.update_checkpoint(cp.id, status="resumed")
    return state


def advance_execution(state: ExecutionState, task: Task,
                      success: bool, result: Optional[Dict[str, Any]] = None) -> ExecutionState:
    task_id = task.id or 0
    if success:
        state.completed_task_ids.append(task_id)
    else:
        state.failed_task_ids.append(task_id)
    if result:
        state.results[task_id] = result
    state.current_task_index += 1
    save_checkpoint(goal_id=state.goal_id, task_id=task_id,
                    step_index=state.current_task_index, state=state)
    return state


def get_execution_summary(goal_id: int) -> Dict[str, Any]:
    checkpoints = storage.list_checkpoints(goal_id)
    active = storage.get_active_checkpoint(goal_id)
    active_state = None
    if active:
        try:
            es = ExecutionState.from_json(goal_id, active.state_json)
            active_state = es.to_dict()
        except (json.JSONDecodeError, KeyError):
            pass
    return {
        "goal_id": goal_id,
        "total_checkpoints": len(checkpoints),
        "has_active_checkpoint": active is not None,
        "active_state": active_state,
        "checkpoints": [cp.to_dict() for cp in checkpoints[:10]],
    }
