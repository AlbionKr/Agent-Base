"""Event bus and the SSE frame stream the frontend reads."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from app.audit import AuditLog
from app.events import EventBus, Journal, format_sse, sse_frames

T0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)


def make_journal() -> tuple[Journal, AuditLog, EventBus]:
    audit = AuditLog()
    bus = EventBus()
    return Journal(audit, bus, clock=lambda: T0), audit, bus


async def take(frames, n: int, timeout: float = 2.0) -> list[str]:
    out = []
    async with asyncio.timeout(timeout):
        async for frame in frames:
            out.append(frame)
            if len(out) == n:
                break
    return out


def test_emit_writes_to_the_audit_log_and_publishes_the_same_event():
    async def run():
        journal, audit, bus = make_journal()
        with bus.subscribe() as sub:
            event = journal.emit("decision", {"verdict": "DENY"}, agent_id="a1")
            received = await asyncio.wait_for(sub.get(), 1)
        return event, received, audit.events()

    event, received, stored = asyncio.run(run())
    assert received == event
    assert stored == [event]
    assert event.at == T0


def test_format_sse_carries_the_audit_id_kind_and_json():
    journal, _, _ = make_journal()
    event = journal.emit("decision", {"reason": "line one\nline two"}, agent_id="a1")

    frame = format_sse(event)

    lines = frame.split("\n")
    assert lines[0] == f"id: {event.id}"
    assert lines[1].startswith("data: ")
    assert not any(line.startswith("event:") for line in lines)
    assert frame.endswith("\n\n")
    # One data line only: a newline inside the payload must not break the frame.
    assert len([line for line in lines if line.startswith("data:")]) == 1
    body = json.loads(lines[1][len("data: "):])
    assert body == {
        "id": event.id,
        "at": "2026-11-14T12:00:00+00:00",
        "kind": "decision",
        "agent_id": "a1",
        "payload": {"reason": "line one\nline two"},
    }


def test_stream_replays_missed_events_then_continues_live():
    async def run():
        journal, audit, bus = make_journal()
        first = journal.emit("decision", {"n": 1})
        second = journal.emit("decision", {"n": 2})
        frames = sse_frames(audit, bus, after_id=first.id)
        replayed = await take(frames, 1)
        third = journal.emit("freeze", {"n": 3})
        live = await take(frames, 1)
        await frames.aclose()
        return second, third, replayed + live

    second, third, frames = asyncio.run(run())
    assert frames[0].startswith(f"id: {second.id}\n")
    assert frames[1].startswith(f"id: {third.id}\ndata: ")
    assert '"kind":"freeze"' in frames[1]


def test_stream_never_sends_an_event_twice():
    """An event emitted while the replay is still paging is in the audit log (so a
    later page returns it) and in the live queue. It must be delivered once."""

    async def run():
        journal, audit, bus = make_journal()
        first = journal.emit("decision", {"n": 1})
        second = journal.emit("decision", {"n": 2})
        frames = sse_frames(audit, bus, after_id=0, heartbeat_seconds=0.01, replay_page=1)
        got = await take(frames, 1)  # generator is now paused mid-replay, already subscribed
        third = journal.emit("decision", {"n": 3})
        got += await take(frames, 3)
        await frames.aclose()
        return [first.id, second.id, third.id], got

    ids, frames = asyncio.run(run())
    assert [frame.splitlines()[0] for frame in frames[:3]] == [f"id: {i}" for i in ids]
    assert frames[3].startswith(": keep-alive")


def test_stream_sends_a_heartbeat_comment_when_idle():
    async def run():
        _, audit, bus = make_journal()
        frames = sse_frames(audit, bus, after_id=0, heartbeat_seconds=0.01)
        got = await take(frames, 1)
        await frames.aclose()
        return got

    assert asyncio.run(run()) == [": keep-alive\n\n"]


def test_a_subscriber_that_falls_behind_is_closed_not_silently_skipped():
    async def run():
        journal, audit, bus = make_journal()
        with bus.subscribe(max_queue=2) as sub:
            for n in range(3):
                journal.emit("decision", {"n": n})
            return sub.closed, bus.subscriber_count

    closed, count = asyncio.run(run())
    assert closed is True
    assert count == 0


def test_unsubscribing_removes_the_subscriber():
    async def run():
        _, _, bus = make_journal()
        with bus.subscribe():
            inside = bus.subscriber_count
        return inside, bus.subscriber_count

    assert asyncio.run(run()) == (1, 0)
