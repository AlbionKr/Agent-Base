"""In-process event bus and the Server-Sent Events stream built on it.

`Journal.emit` is the only way anything reaches the UI: it writes the event to the
audit log first, then publishes that same stored event. Nothing is shown that was
not recorded.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, Iterator

from app.audit import AuditEvent, AuditLog

Clock = Callable[[], datetime]

DEFAULT_QUEUE = 1000
REPLAY_PAGE = 500


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Subscription:
    def __init__(self, max_queue: int):
        self._queue: asyncio.Queue[AuditEvent] = asyncio.Queue(maxsize=max_queue)
        self.closed = False

    async def get(self) -> AuditEvent:
        return await self._queue.get()

    def _offer(self, event: AuditEvent) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except asyncio.QueueFull:
            return False


class EventBus:
    """Fan-out to live subscribers. A subscriber that cannot keep up is closed;
    its client reconnects with Last-Event-ID and replays from the audit log, so
    events are never silently skipped."""

    def __init__(self) -> None:
        self._subscribers: set[Subscription] = set()

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    @contextmanager
    def subscribe(self, max_queue: int = DEFAULT_QUEUE) -> Iterator[Subscription]:
        subscription = Subscription(max_queue)
        self._subscribers.add(subscription)
        try:
            yield subscription
        finally:
            self._subscribers.discard(subscription)

    def publish(self, event: AuditEvent) -> None:
        for subscription in list(self._subscribers):
            if not subscription._offer(event):
                subscription.closed = True
                self._subscribers.discard(subscription)


class Journal:
    """Record, then broadcast."""

    def __init__(self, audit: AuditLog, bus: EventBus, clock: Clock = utcnow):
        self.audit = audit
        self.bus = bus
        self.clock = clock

    def emit(self, kind: str, payload: dict[str, Any], *, agent_id: str | None = None) -> AuditEvent:
        event = self.audit.append(kind, payload, at=self.clock(), agent_id=agent_id)
        self.bus.publish(event)
        return event


def format_sse(event: AuditEvent) -> str:
    # json.dumps escapes newlines, so the payload always fits on one data line.
    data = json.dumps(event.to_json(), separators=(",", ":"), ensure_ascii=False)
    return f"id: {event.id}\nevent: {event.kind}\ndata: {data}\n\n"


async def sse_frames(
    audit: AuditLog,
    bus: EventBus,
    *,
    after_id: int = 0,
    heartbeat_seconds: float = 15.0,
    replay_page: int = REPLAY_PAGE,
) -> AsyncIterator[str]:
    """Replay everything after `after_id` from the audit log, then follow live.

    Subscribes before replaying so nothing emitted in between is lost, and tracks
    the last id sent so nothing is delivered twice.
    """
    with bus.subscribe() as subscription:
        last_sent = after_id
        while True:
            page = audit.events(after_id=last_sent, limit=replay_page)
            for event in page:
                last_sent = event.id
                yield format_sse(event)
            if len(page) < replay_page:
                break

        while not subscription.closed:
            try:
                event = await asyncio.wait_for(subscription.get(), heartbeat_seconds)
            except TimeoutError:
                yield ": keep-alive\n\n"
                continue
            if event.id <= last_sent:
                continue
            last_sent = event.id
            yield format_sse(event)
