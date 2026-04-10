"""
Project import adapters (Phase 3, Step 9).

Convert external project data (Trello board exports, Asana project exports)
into teb's Goal + Task hierarchy.

Usage from Python:
    from teb.importers import import_trello_board, import_asana_project
    goal, tasks = import_trello_board(user_id, board_json)
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from teb import storage
from teb.models import Goal, Task

logger = logging.getLogger(__name__)


def import_trello_board(user_id: int, board_data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import a Trello board JSON export into a teb goal with tasks.

    Args:
        user_id: The teb user ID to associate with.
        board_data: The raw JSON from Trello's board export.

    Returns:
        Tuple of (Goal, list of created Tasks).
    """
    board_name = board_data.get("name", "Imported Trello Board")
    goal = Goal(title=board_name, description=board_data.get("desc", ""))
    goal.user_id = user_id
    goal.tags = "imported,trello"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    lists_data = board_data.get("lists", [])
    cards_data = board_data.get("cards", [])

    list_names = {lst["id"]: lst.get("name", "") for lst in lists_data if not lst.get("closed")}

    tasks_created: List[Task] = []
    for idx, card in enumerate(cards_data):
        if card.get("closed"):
            continue

        list_name = list_names.get(card.get("idList", ""), "")
        status = _trello_list_to_status(list_name)

        task = Task(
            goal_id=goal.id,
            title=card.get("name", f"Card {idx+1}"),
            description=card.get("desc", ""),
            status=status,
            order_index=idx,
            tags=f"trello,{list_name}" if list_name else "trello",
        )
        if card.get("due"):
            task.due_date = card["due"][:10]

        task = storage.create_task(task)
        tasks_created.append(task)

        # Import checklist items as subtasks
        checklists = card.get("checklists", [])
        for cl in checklists:
            for ci_idx, item in enumerate(cl.get("checkItems", [])):
                sub = Task(
                    goal_id=goal.id,
                    parent_id=task.id,
                    title=item.get("name", f"Checklist item {ci_idx+1}"),
                    description="",
                    status="done" if item.get("state") == "complete" else "todo",
                    order_index=ci_idx,
                    tags="trello,checklist",
                )
                sub = storage.create_task(sub)
                tasks_created.append(sub)

    logger.info("Imported Trello board '%s' as goal %d with %d tasks", board_name, goal.id, len(tasks_created))
    return goal, tasks_created


def _trello_list_to_status(list_name: str) -> str:
    """Map a Trello list name to a teb task status."""
    name = list_name.lower().strip()
    if name in ("done", "complete", "completed", "finished"):
        return "done"
    if name in ("in progress", "doing", "wip", "active"):
        return "in_progress"
    return "todo"


def import_asana_project(user_id: int, project_data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import an Asana project JSON into a teb goal with tasks.

    Args:
        user_id: The teb user ID.
        project_data: Asana project data with name, notes, tasks.

    Returns:
        Tuple of (Goal, list of created Tasks).
    """
    project_name = project_data.get("name", "Imported Asana Project")
    goal = Goal(title=project_name, description=project_data.get("notes", ""))
    goal.user_id = user_id
    goal.tags = "imported,asana"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []

    for idx, at in enumerate(project_data.get("tasks", [])):
        completed = at.get("completed", False)
        task = Task(
            goal_id=goal.id,
            title=at.get("name", f"Task {idx+1}"),
            description=at.get("notes", ""),
            status="done" if completed else "todo",
            order_index=idx,
            tags="asana",
        )
        if at.get("due_on"):
            task.due_date = at["due_on"]

        task = storage.create_task(task)
        tasks_created.append(task)

        # Import subtasks
        for si, sub in enumerate(at.get("subtasks", [])):
            sub_task = Task(
                goal_id=goal.id,
                parent_id=task.id,
                title=sub.get("name", f"Subtask {si+1}"),
                description=sub.get("notes", ""),
                status="done" if sub.get("completed") else "todo",
                order_index=si,
                tags="asana",
            )
            sub_task = storage.create_task(sub_task)
            tasks_created.append(sub_task)

    logger.info("Imported Asana project '%s' as goal %d with %d tasks", project_name, goal.id, len(tasks_created))
    return goal, tasks_created
