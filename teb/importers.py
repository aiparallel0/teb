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


def import_from_monday(user_id: int, data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import a Monday.com board JSON into a teb goal with tasks.

    Expected structure: {"name": "...", "columns": [...], "items": [{"name": "...", "column_values": [...], "subitems": [...]}]}
    """
    board_name = data.get("name", "Imported Monday.com Board")
    goal = Goal(title=board_name, description=data.get("description", ""))
    goal.user_id = user_id
    goal.tags = "imported,monday"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []
    items = data.get("items", [])

    for idx, item in enumerate(items):
        status = _monday_status(item)
        task = Task(
            goal_id=goal.id,
            title=item.get("name", f"Item {idx+1}"),
            description=item.get("description", ""),
            status=status,
            order_index=idx,
            tags="monday",
        )
        task = storage.create_task(task)
        tasks_created.append(task)

        for si, sub in enumerate(item.get("subitems", [])):
            sub_task = Task(
                goal_id=goal.id,
                parent_id=task.id,
                title=sub.get("name", f"Sub-item {si+1}"),
                description="",
                status=_monday_status(sub),
                order_index=si,
                tags="monday",
            )
            sub_task = storage.create_task(sub_task)
            tasks_created.append(sub_task)

    logger.info("Imported Monday.com board '%s' as goal %d with %d tasks", board_name, goal.id, len(tasks_created))
    return goal, tasks_created


def _monday_status(item: Dict[str, Any]) -> str:
    """Extract status from a Monday.com item."""
    for cv in item.get("column_values", []):
        if cv.get("type") == "status" or cv.get("id", "").startswith("status"):
            text = (cv.get("text") or "").lower().strip()
            if text in ("done", "complete", "completed"):
                return "done"
            if text in ("working on it", "in progress", "active"):
                return "in_progress"
    return "todo"


def import_from_jira(user_id: int, data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import Jira project/sprint data into a teb goal with tasks.

    Expected structure: {"project": {"name": "...", "key": "..."}, "issues": [{"key": "...", "fields": {"summary": "...", ...}}]}
    """
    project = data.get("project", {})
    project_name = project.get("name", data.get("name", "Imported Jira Project"))
    goal = Goal(title=project_name, description=project.get("description", ""))
    goal.user_id = user_id
    goal.tags = "imported,jira"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []
    issues = data.get("issues", [])

    parent_map: Dict[str, int] = {}

    for idx, issue in enumerate(issues):
        fields = issue.get("fields", {})
        jira_status = (fields.get("status", {}).get("name", "") or "").lower()
        if jira_status in ("done", "closed", "resolved"):
            status = "done"
        elif jira_status in ("in progress", "in review", "in development"):
            status = "in_progress"
        else:
            status = "todo"

        parent_key = fields.get("parent", {}).get("key")
        parent_id = parent_map.get(parent_key) if parent_key else None

        task = Task(
            goal_id=goal.id,
            parent_id=parent_id,
            title=fields.get("summary", issue.get("key", f"Issue {idx+1}")),
            description=fields.get("description", "") or "",
            status=status,
            order_index=idx,
            tags=f"jira,{issue.get('key', '')}",
        )
        if fields.get("duedate"):
            task.due_date = fields["duedate"][:10]

        task = storage.create_task(task)
        tasks_created.append(task)

        key = issue.get("key")
        if key:
            parent_map[key] = task.id

    logger.info("Imported Jira project '%s' as goal %d with %d tasks", project_name, goal.id, len(tasks_created))
    return goal, tasks_created


def import_from_clickup(user_id: int, data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import ClickUp space/list data into a teb goal with tasks.

    Expected structure: {"name": "...", "tasks": [{"name": "...", "status": {"status": "..."}, "subtasks": [...]}]}
    """
    list_name = data.get("name", "Imported ClickUp List")
    goal = Goal(title=list_name, description=data.get("description", ""))
    goal.user_id = user_id
    goal.tags = "imported,clickup"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []
    clickup_tasks = data.get("tasks", [])

    for idx, ct in enumerate(clickup_tasks):
        status_obj = ct.get("status", {})
        raw_status = (status_obj.get("status", "") if isinstance(status_obj, dict) else str(status_obj)).lower()
        if raw_status in ("complete", "closed", "done"):
            status = "done"
        elif raw_status in ("in progress", "active", "review"):
            status = "in_progress"
        else:
            status = "todo"

        task = Task(
            goal_id=goal.id,
            title=ct.get("name", f"Task {idx+1}"),
            description=ct.get("description", "") or ct.get("text_content", ""),
            status=status,
            order_index=idx,
            tags="clickup",
        )
        if ct.get("due_date"):
            task.due_date = ct["due_date"][:10]

        task = storage.create_task(task)
        tasks_created.append(task)

        for si, sub in enumerate(ct.get("subtasks", [])):
            sub_status_obj = sub.get("status", {})
            sub_raw = (sub_status_obj.get("status", "") if isinstance(sub_status_obj, dict) else str(sub_status_obj)).lower()
            sub_task = Task(
                goal_id=goal.id,
                parent_id=task.id,
                title=sub.get("name", f"Subtask {si+1}"),
                description=sub.get("description", ""),
                status="done" if sub_raw in ("complete", "closed", "done") else "todo",
                order_index=si,
                tags="clickup",
            )
            sub_task = storage.create_task(sub_task)
            tasks_created.append(sub_task)

    logger.info("Imported ClickUp list '%s' as goal %d with %d tasks", list_name, goal.id, len(tasks_created))
    return goal, tasks_created


def import_from_csv(user_id: int, csv_text: str) -> Tuple[Goal, List[Task]]:
    """Import tasks from CSV text. Expected columns: title, description, status, due_date (optional).

    First row must be headers.
    """
    import csv
    import io

    reader = csv.DictReader(io.StringIO(csv_text))
    goal = Goal(title="CSV Import", description="Imported from CSV")
    goal.user_id = user_id
    goal.tags = "imported,csv"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []
    for idx, row in enumerate(reader):
        raw_status = (row.get("status") or "todo").lower().strip()
        if raw_status in ("done", "complete", "completed"):
            status = "done"
        elif raw_status in ("in progress", "in_progress", "active", "wip"):
            status = "in_progress"
        else:
            status = "todo"

        task = Task(
            goal_id=goal.id,
            title=row.get("title") or row.get("name") or f"Task {idx+1}",
            description=row.get("description") or row.get("notes") or "",
            status=status,
            order_index=idx,
            tags="csv",
        )
        if row.get("due_date"):
            task.due_date = row["due_date"][:10]

        task = storage.create_task(task)
        tasks_created.append(task)

    logger.info("Imported CSV as goal %d with %d tasks", goal.id, len(tasks_created))
    return goal, tasks_created


def import_from_langchain(user_id: int, data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import a LangChain agent/chain workflow export into a teb goal with tasks.

    Expected structure:
        {
            "name": "...",
            "description": "...",
            "agents": [{"name": "...", "role": "...", "tools": [...], "tasks": [...]}],
            "chains": [{"name": "...", "steps": [{"name": "...", "type": "..."}]}]
        }
    """
    workflow_name = data.get("name", "Imported LangChain Workflow")
    goal = Goal(title=workflow_name, description=data.get("description", ""))
    goal.user_id = user_id
    goal.tags = "imported,langchain"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []
    order = 0

    # Import agents and their tasks
    for agent in data.get("agents", []):
        agent_name = agent.get("name", agent.get("role", "Agent"))
        agent_role = agent.get("role", agent_name)
        tools = agent.get("tools", [])
        tools_desc = f" (tools: {', '.join(tools)})" if tools else ""

        parent_task = Task(
            goal_id=goal.id,
            title=f"[{agent_role}] {agent_name}{tools_desc}",
            description=f"Agent: {agent_role}",
            status="todo",
            order_index=order,
            tags="langchain",
        )
        parent_task = storage.create_task(parent_task)
        tasks_created.append(parent_task)
        order += 1

        for si, at in enumerate(agent.get("tasks", [])):
            expected = at.get("expected_output", "")
            desc = at.get("description", f"Agent task {si + 1}")
            child_task = Task(
                goal_id=goal.id,
                parent_id=parent_task.id,
                title=desc,
                description=f"Expected output: {expected}" if expected else "",
                status="todo",
                order_index=si,
                tags="langchain",
            )
            child_task = storage.create_task(child_task)
            tasks_created.append(child_task)

    # Import chains and their steps
    for chain in data.get("chains", []):
        chain_name = chain.get("name", "Chain")

        parent_task = Task(
            goal_id=goal.id,
            title=f"[Chain] {chain_name}",
            description=f"LangChain chain: {chain_name}",
            status="todo",
            order_index=order,
            tags="langchain",
        )
        parent_task = storage.create_task(parent_task)
        tasks_created.append(parent_task)
        order += 1

        for si, step in enumerate(chain.get("steps", [])):
            step_name = step.get("name", f"Step {si + 1}")
            step_type = step.get("type", "unknown")
            child_task = Task(
                goal_id=goal.id,
                parent_id=parent_task.id,
                title=step_name,
                description=f"Chain step type: {step_type}",
                status="todo",
                order_index=si,
                tags="langchain",
            )
            child_task = storage.create_task(child_task)
            tasks_created.append(child_task)

    logger.info("Imported LangChain workflow '%s' as goal %d with %d tasks", workflow_name, goal.id, len(tasks_created))
    return goal, tasks_created


def import_from_crewai(user_id: int, data: Dict[str, Any]) -> Tuple[Goal, List[Task]]:
    """Import a CrewAI crew export into a teb goal with tasks.

    Expected structure:
        {
            "name": "...",
            "description": "...",
            "agents": [{"role": "...", "goal": "...", "backstory": "...", "tools": [...]}],
            "tasks": [{"description": "...", "agent": "...", "expected_output": "...", "context": [...]}],
            "process": "sequential" | "hierarchical"
        }
    """
    crew_name = data.get("name", "Imported CrewAI Crew")
    goal = Goal(title=crew_name, description=data.get("description", ""))
    goal.user_id = user_id
    goal.tags = "imported,crewai"
    goal.status = "decomposed"
    goal = storage.create_goal(goal)

    tasks_created: List[Task] = []
    process_type = data.get("process", "sequential")

    # Build agent lookup for backstory enrichment
    agents_map: Dict[str, Dict[str, Any]] = {}
    for agent in data.get("agents", []):
        role = agent.get("role", "")
        agents_map[role] = agent

    crew_tasks = data.get("tasks", [])
    for idx, ct in enumerate(crew_tasks):
        agent_role = ct.get("agent", "Unassigned")
        description = ct.get("description", f"Task {idx + 1}")
        expected_output = ct.get("expected_output", "")

        # Build description from task + agent backstory
        agent_info = agents_map.get(agent_role, {})
        agent_goal = agent_info.get("goal", "")
        backstory = agent_info.get("backstory", "")
        desc_parts = []
        if expected_output:
            desc_parts.append(f"Expected output: {expected_output}")
        if agent_goal:
            desc_parts.append(f"Agent goal: {agent_goal}")
        if backstory:
            desc_parts.append(f"Backstory: {backstory}")

        # Sequential = ordered, hierarchical = all parallel (order 0)
        order_index = idx if process_type == "sequential" else 0

        task = Task(
            goal_id=goal.id,
            title=f"[{agent_role}] {description}",
            description="\n".join(desc_parts),
            status="todo",
            order_index=order_index,
            tags="crewai",
        )
        task = storage.create_task(task)
        tasks_created.append(task)

    logger.info("Imported CrewAI crew '%s' as goal %d with %d tasks", crew_name, goal.id, len(tasks_created))
    return goal, tasks_created
