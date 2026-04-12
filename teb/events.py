"""
Real-time Event Streaming (Step 4).

Server-Sent Events (SSE) system for teb. Replaces frontend polling with
push-based event delivery.

Event types:
- task_completed: A task was completed
- execution_result: An execution step finished
- spending_request: A spending approval is needed
- checkin_nudge: A check-in nudge was generated
- agent_handoff: An agent delegated to another
- goal_milestone: A milestone was achieved
- goal_updated: A goal's status changed
- audit_event: A new audit trail entry (for debug/admin)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


HEARTBEAT_INTERVAL_SECONDS = 30
"""Seconds between SSE heartbeat comments to keep connections alive."""

_MAX_SUBSCRIBER_QUEUE_SIZE = 200
"""Maximum events queued per subscriber before events are dropped."""


@dataclass
class SSEEvent:
    """A single server-sent event."""
    event_type: str
    data: Dict[str, Any]
    id: Optional[str] = None
    retry: Optional[int] = None

    def serialize(self) -> str:
        """Serialize to SSE wire format."""
        lines: list[str] = []
        if self.id:
            lines.append(f"id: {self.id}")
        if self.retry is not None:
            lines.append(f"retry: {self.retry}")
        lines.append(f"event: {self.event_type}")
        lines.append(f"data: {json.dumps(self.data)}")
        lines.append("")  # trailing blank line
        return "\n".join(lines) + "\n"


# ─── Event Bus ────────────────────────────────────────────────────────────────

class EventBus:
    """In-memory event bus with per-user subscriber queues."""

    def __init__(self, max_backlog: int = 100):
        self._subscribers: Dict[int, List[asyncio.Queue]] = defaultdict(list)
        self._backlog: List[SSEEvent] = []
        self._max_backlog = max_backlog
        self._event_counter = 0

    def publish(self, user_id: int, event_type: str, data: Dict[str, Any]) -> SSEEvent:
        """Publish an event to all subscribers for a user."""
        self._event_counter += 1
        event = SSEEvent(
            event_type=event_type,
            data=data,
            id=str(self._event_counter),
        )

        # Add to backlog for reconnection
        self._backlog.append(event)
        if len(self._backlog) > self._max_backlog:
            self._backlog = self._backlog[-self._max_backlog:]

        # Push to all active subscriber queues for this user
        dead_queues: list[asyncio.Queue] = []
        for queue in self._subscribers.get(user_id, []):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.append(queue)

        # Clean up dead/full queues
        for dq in dead_queues:
            if dq in self._subscribers.get(user_id, []):
                self._subscribers[user_id].remove(dq)

        return event

    def publish_broadcast(self, event_type: str, data: Dict[str, Any]) -> SSEEvent:
        """Publish an event to ALL subscribers."""
        self._event_counter += 1
        event = SSEEvent(
            event_type=event_type,
            data=data,
            id=str(self._event_counter),
        )

        self._backlog.append(event)
        if len(self._backlog) > self._max_backlog:
            self._backlog = self._backlog[-self._max_backlog:]

        for user_id, queues in self._subscribers.items():
            for queue in queues:
                try:
                    queue.put_nowait(event)
                except asyncio.QueueFull:
                    pass
        return event

    def subscribe(self, user_id: int) -> asyncio.Queue:
        """Create a new subscription queue for a user."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=_MAX_SUBSCRIBER_QUEUE_SIZE)
        self._subscribers[user_id].append(queue)
        return queue

    def unsubscribe(self, user_id: int, queue: asyncio.Queue) -> None:
        """Remove a subscription queue."""
        if user_id in self._subscribers:
            if queue in self._subscribers[user_id]:
                self._subscribers[user_id].remove(queue)
            if not self._subscribers[user_id]:
                del self._subscribers[user_id]

    def get_backlog_since(self, last_event_id: Optional[str]) -> List[SSEEvent]:
        """Get all events after a given event ID (for reconnection)."""
        if not last_event_id:
            return []
        try:
            target_id = int(last_event_id)
        except (ValueError, TypeError):
            return []
        return [e for e in self._backlog if e.id and int(e.id) > target_id]

    @property
    def subscriber_count(self) -> int:
        return sum(len(queues) for queues in self._subscribers.values())


# ─── Global event bus instance ────────────────────────────────────────────────

event_bus = EventBus()


# ─── Convenience publishers ──────────────────────────────────────────────────

def emit_task_completed(user_id: int, task_id: int, task_title: str, goal_id: int) -> None:
    event_bus.publish(user_id, "task_completed", {
        "task_id": task_id, "task_title": task_title, "goal_id": goal_id,
    })


def emit_execution_result(user_id: int, task_id: int, success: bool, summary: str) -> None:
    event_bus.publish(user_id, "execution_result", {
        "task_id": task_id, "success": success, "summary": summary,
    })


def emit_spending_request(user_id: int, request_id: int, amount: float, description: str) -> None:
    event_bus.publish(user_id, "spending_request", {
        "request_id": request_id, "amount": amount, "description": description,
    })


def emit_checkin_nudge(user_id: int, goal_id: int, message: str) -> None:
    event_bus.publish(user_id, "checkin_nudge", {
        "goal_id": goal_id, "message": message,
    })


def emit_agent_handoff(user_id: int, goal_id: int, from_agent: str, to_agent: str) -> None:
    event_bus.publish(user_id, "agent_handoff", {
        "goal_id": goal_id, "from_agent": from_agent, "to_agent": to_agent,
    })


def emit_goal_milestone(user_id: int, goal_id: int, milestone_title: str, status: str) -> None:
    event_bus.publish(user_id, "goal_milestone", {
        "goal_id": goal_id, "milestone_title": milestone_title, "status": status,
    })


def emit_goal_updated(user_id: int, goal_id: int, status: str) -> None:
    event_bus.publish(user_id, "goal_updated", {
        "goal_id": goal_id, "status": status,
    })


def emit_webhook_event(user_id: int, event_type: str, data: Dict[str, Any]) -> None:
    """Emit an event that should also trigger webhook delivery."""
    event_bus.publish(user_id, event_type, data)


def emit_report_generated(user_id: int, goal_id: int, report_id: int, summary: str) -> None:
    """Emit an event when a progress report is generated."""
    event_bus.publish(user_id, "report_generated", {
        "goal_id": goal_id, "report_id": report_id, "summary": summary[:200],
    })


async def stream_events(user_id: int, last_event_id: Optional[str] = None) -> AsyncGenerator[str, None]:
    """Async generator that yields SSE-formatted strings for a user.

    Yields backlog events first (if reconnecting), then streams live events.
    Sends a heartbeat comment every 30 seconds to keep the connection alive.
    """
    # Replay any missed events
    backlog = event_bus.get_backlog_since(last_event_id)
    for event in backlog:
        yield event.serialize()

    # Subscribe for live events
    queue = event_bus.subscribe(user_id)
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS)
                yield event.serialize()
            except asyncio.TimeoutError:
                # Heartbeat to keep connection alive
                yield ": heartbeat\n\n"
    finally:
        event_bus.unsubscribe(user_id, queue)
