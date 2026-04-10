"""
DAG (Directed Acyclic Graph) Execution Planner (Phase 2, Step 5).

Builds and validates a task dependency graph, then produces an execution
order that respects dependencies while maximizing parallelism.

Usage:
    from teb.dag import build_execution_plan, ExecutionBatch
    batches = build_execution_plan(tasks)
    # batches[0] can all run in parallel, batches[1] after batches[0] completes, etc.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set

from teb.models import Task

logger = logging.getLogger(__name__)


@dataclass
class ExecutionBatch:
    """A batch of tasks that can all be executed in parallel."""
    batch_index: int
    task_ids: List[int]

    def to_dict(self) -> dict:
        return {
            "batch_index": self.batch_index,
            "task_ids": self.task_ids,
        }


@dataclass
class DAGValidation:
    """Result of validating a task dependency graph."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "is_valid": self.is_valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def _get_deps(task: Task) -> List[int]:
    """Parse the depends_on field of a task."""
    try:
        deps = json.loads(task.depends_on) if task.depends_on else []
        return [int(d) for d in deps if isinstance(d, (int, float))]
    except (json.JSONDecodeError, ValueError):
        return []


def validate_dag(tasks: List[Task]) -> DAGValidation:
    """Validate the dependency graph for a set of tasks.

    Checks for:
    - References to non-existent tasks
    - Self-references
    - Cycles
    """
    errors: List[str] = []
    warnings: List[str] = []
    task_ids = {t.id for t in tasks}

    # Build adjacency (task -> deps)
    adj: Dict[int, List[int]] = {}
    for t in tasks:
        deps = _get_deps(t)
        adj[t.id] = deps

        # Check for self-reference
        if t.id in deps:
            errors.append(f"Task {t.id} ({t.title}) depends on itself")

        # Check for missing dependencies
        for d in deps:
            if d not in task_ids:
                errors.append(f"Task {t.id} ({t.title}) depends on non-existent task {d}")

    # Cycle detection via DFS
    visited: Set[int] = set()
    in_stack: Set[int] = set()

    def _dfs(node: int) -> bool:
        if node in in_stack:
            return True  # cycle
        if node in visited:
            return False
        visited.add(node)
        in_stack.add(node)
        for dep in adj.get(node, []):
            if dep in task_ids and _dfs(dep):
                return True
        in_stack.discard(node)
        return False

    for tid in adj:
        if _dfs(tid):
            errors.append(f"Dependency cycle detected involving task {tid}")
            break  # one cycle error is enough

    # Warnings for tasks with many deps
    for t in tasks:
        deps = _get_deps(t)
        if len(deps) > 10:
            warnings.append(f"Task {t.id} ({t.title}) has {len(deps)} dependencies — consider simplifying")

    return DAGValidation(is_valid=len(errors) == 0, errors=errors, warnings=warnings)


def build_execution_plan(tasks: List[Task]) -> List[ExecutionBatch]:
    """Build an execution plan from task dependencies.

    Returns a list of ExecutionBatch objects, where each batch contains
    tasks that can be executed in parallel. Batch N+1 depends on batch N.

    Only includes tasks that are not yet done/skipped.
    """
    # Filter to actionable tasks
    actionable = [t for t in tasks if t.status in ("todo", "in_progress", "executing", "failed")]
    if not actionable:
        return []

    task_map = {t.id: t for t in tasks}
    done_ids = {t.id for t in tasks if t.status in ("done", "skipped")}

    # Build remaining dependency graph
    remaining = {t.id for t in actionable}
    adj: Dict[int, List[int]] = {}
    for t in actionable:
        deps = _get_deps(t)
        # Only count dependencies that are still pending (not done)
        adj[t.id] = [d for d in deps if d in remaining]

    batches: List[ExecutionBatch] = []
    resolved: Set[int] = set(done_ids)
    batch_idx = 0

    while remaining:
        # Find tasks whose dependencies are all resolved
        ready = []
        for tid in remaining:
            deps = adj.get(tid, [])
            if all(d in resolved for d in deps):
                ready.append(tid)

        if not ready:
            # All remaining tasks have unresolved deps (cycle or missing)
            # Force the remaining into a final batch
            logger.warning("DAG has unresolvable dependencies — forcing remaining tasks into final batch")
            batches.append(ExecutionBatch(batch_index=batch_idx, task_ids=sorted(remaining)))
            break

        batches.append(ExecutionBatch(batch_index=batch_idx, task_ids=sorted(ready)))
        resolved.update(ready)
        remaining -= set(ready)
        batch_idx += 1

    return batches


def get_critical_path(tasks: List[Task]) -> List[int]:
    """Find the critical path — the longest chain of dependent tasks.

    Returns task IDs in order of execution.
    """
    task_map = {t.id: t for t in tasks}
    adj: Dict[int, List[int]] = {}
    for t in tasks:
        adj[t.id] = _get_deps(t)

    # Memoized longest path from each node
    cache: Dict[int, List[int]] = {}

    def _longest_from(tid: int, visited: Set[int]) -> List[int]:
        if tid in cache:
            return cache[tid]
        if tid in visited:
            return []  # cycle guard
        visited = visited | {tid}
        deps = [d for d in adj.get(tid, []) if d in task_map]
        if not deps:
            cache[tid] = [tid]
            return [tid]
        best: List[int] = []
        for d in deps:
            path = _longest_from(d, visited)
            if len(path) > len(best):
                best = path
        result = best + [tid]
        cache[tid] = result
        return result

    longest: List[int] = []
    for tid in task_map:
        path = _longest_from(tid, set())
        if len(path) > len(longest):
            longest = path

    return longest
