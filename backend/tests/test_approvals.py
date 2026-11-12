"""Approval queue: a human decides, silence means no."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.approvals import (
    AlreadyResolved,
    ApprovalQueue,
    ApprovalStatus,
    UnknownApproval,
)

T0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)


def make_queue(timeout: int = 60) -> ApprovalQueue:
    ids = iter(f"ap-{n}" for n in range(1, 100))
    return ApprovalQueue(timeout_seconds=timeout, id_factory=lambda: next(ids))


def request(queue: ApprovalQueue, action_id: str = "act-1", now: datetime = T0):
    return queue.request(
        action_id=action_id,
        agent_id="a1",
        summary="buy 12 YES on GEMI-BTC05M at $0.50 ($6.00)",
        amount_usd=Decimal("6.00"),
        now=now,
    )


def test_a_request_is_pending_with_a_sixty_second_deadline():
    queue = make_queue()
    approval = request(queue)

    assert approval.approval_id == "ap-1"
    assert approval.status is ApprovalStatus.PENDING
    assert approval.deadline == T0 + timedelta(seconds=60)
    assert queue.pending() == [approval]


def test_approving_in_time_approves():
    queue = make_queue()
    approval = request(queue)

    resolved = queue.resolve(approval.approval_id, approve=True, via="voice", now=T0 + timedelta(seconds=10))

    assert resolved.status is ApprovalStatus.APPROVED
    assert resolved.resolved_via == "voice"
    assert resolved.resolved_at == T0 + timedelta(seconds=10)
    assert queue.pending() == []
    assert queue.get(approval.approval_id) == resolved


def test_denying_denies():
    queue = make_queue()
    approval = request(queue)
    resolved = queue.resolve(approval.approval_id, approve=False, via="dashboard", now=T0)
    assert resolved.status is ApprovalStatus.DENIED


@pytest.mark.parametrize("seconds_late", [0, 1, 3600])
def test_an_approval_at_or_after_the_deadline_is_a_timeout_not_an_approval(seconds_late):
    queue = make_queue()
    approval = request(queue)

    resolved = queue.resolve(
        approval.approval_id,
        approve=True,
        via="voice",
        now=approval.deadline + timedelta(seconds=seconds_late),
    )

    assert resolved.status is ApprovalStatus.EXPIRED
    assert resolved.resolved_via == "timeout"


def test_expire_turns_overdue_requests_into_timeouts_and_leaves_the_rest():
    queue = make_queue()
    old = request(queue, "act-old", now=T0)
    fresh = request(queue, "act-new", now=T0 + timedelta(seconds=30))

    expired = queue.expire(T0 + timedelta(seconds=60))

    assert [a.approval_id for a in expired] == [old.approval_id]
    assert expired[0].status is ApprovalStatus.EXPIRED
    assert expired[0].resolved_via == "timeout"
    assert [a.approval_id for a in queue.pending()] == [fresh.approval_id]
    assert queue.expire(T0 + timedelta(seconds=60)) == []


def test_a_decision_cannot_be_changed_afterwards():
    queue = make_queue()
    approval = request(queue)
    queue.resolve(approval.approval_id, approve=False, via="voice", now=T0)

    with pytest.raises(AlreadyResolved):
        queue.resolve(approval.approval_id, approve=True, via="voice", now=T0)
    assert queue.get(approval.approval_id).status is ApprovalStatus.DENIED


def test_an_expired_request_cannot_be_approved_later():
    queue = make_queue()
    approval = request(queue)
    queue.expire(T0 + timedelta(seconds=61))
    with pytest.raises(AlreadyResolved):
        queue.resolve(approval.approval_id, approve=True, via="voice", now=T0 + timedelta(seconds=62))


def test_unknown_ids_are_rejected():
    queue = make_queue()
    with pytest.raises(UnknownApproval):
        queue.resolve("ap-does-not-exist", approve=True, via="voice", now=T0)
    assert queue.get("ap-does-not-exist") is None


def test_pending_is_ordered_oldest_first():
    queue = make_queue()
    first = request(queue, "act-1", now=T0)
    second = request(queue, "act-2", now=T0 + timedelta(seconds=5))
    assert [a.approval_id for a in queue.pending()] == [first.approval_id, second.approval_id]


def test_timeout_must_be_positive():
    with pytest.raises(ValueError):
        ApprovalQueue(timeout_seconds=0)
