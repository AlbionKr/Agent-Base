"""Audit log: append-only events plus the action lifecycle the policy state is built from."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.audit import ActionStatus, AuditLog
from app.policy.models import Verdict

T0 = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)


def record(log: AuditLog, action_id: str, status: ActionStatus, *, at=T0, agent="a1", amount="1.25"):
    log.record_action(
        action_id=action_id,
        agent_id=agent,
        at=at,
        action_type="x402_payment",
        destination="seller",
        amount_usd=Decimal(amount),
        verdict=Verdict.ALLOW,
        status=status,
        action_json={"type": "x402_payment"},
    )


def test_append_assigns_increasing_ids_and_round_trips_the_payload():
    log = AuditLog()
    first = log.append("decision", {"amount": "0.001", "nested": {"ok": True}}, at=T0, agent_id="a1")
    second = log.append("freeze", {}, at=T0 + timedelta(seconds=1))

    assert second.id == first.id + 1
    stored = log.events()
    assert [e.id for e in stored] == [first.id, second.id]
    assert stored[0].payload == {"amount": "0.001", "nested": {"ok": True}}
    assert stored[0].at == T0
    assert stored[0].agent_id == "a1"
    assert stored[1].agent_id is None


def test_events_can_be_read_after_an_id_and_filtered():
    log = AuditLog()
    ids = [log.append("decision", {"n": n}, at=T0, agent_id="a1" if n % 2 else "a2").id for n in range(6)]
    log.append("freeze", {}, at=T0, agent_id="a1")

    assert [e.id for e in log.events(after_id=ids[3])] == [ids[4], ids[5], ids[5] + 1]
    assert [e.id for e in log.events(limit=2)] == ids[:2]
    assert {e.kind for e in log.events(kind="freeze")} == {"freeze"}
    assert all(e.agent_id == "a2" for e in log.events(agent_id="a2"))
    assert log.last_id() == ids[5] + 1


def test_payload_must_be_json_without_floats():
    log = AuditLog()
    with pytest.raises(TypeError):
        log.append("decision", {"amount": 0.1}, at=T0)
    with pytest.raises(TypeError):
        log.append("decision", {"amount": Decimal("0.1")}, at=T0)


def test_naive_timestamps_are_rejected():
    log = AuditLog()
    with pytest.raises(ValueError):
        log.append("decision", {}, at=datetime(2026, 11, 14, 12, 0, 0))


def test_events_survive_reopening_the_file(tmp_path):
    path = tmp_path / "audit.sqlite"
    log = AuditLog(path)
    log.append("decision", {"n": 1}, at=T0)
    log.close()

    reopened = AuditLog(path)
    assert [e.payload for e in reopened.events()] == [{"n": 1}]
    assert reopened.append("decision", {"n": 2}, at=T0).id == 2


def test_history_returns_past_actions_for_one_agent_since_a_time():
    log = AuditLog()
    record(log, "old", ActionStatus.EXECUTED, at=T0 - timedelta(hours=25))
    record(log, "mine", ActionStatus.EXECUTED, at=T0 - timedelta(minutes=5), amount="2.50")
    record(log, "other", ActionStatus.EXECUTED, at=T0 - timedelta(minutes=5), agent="a2")

    history = log.history("a1", since=T0 - timedelta(hours=24))

    assert len(history) == 1
    past = history[0]
    assert past.at == T0 - timedelta(minutes=5)
    assert past.action_type == "x402_payment"
    assert past.destination == "seller"
    assert past.amount_usd == Decimal("2.50")
    assert past.verdict is Verdict.ALLOW
    assert past.executed is True


@pytest.mark.parametrize(
    "status, counts",
    [
        (ActionStatus.ALLOWED, True),
        (ActionStatus.PENDING_APPROVAL, True),
        (ActionStatus.APPROVED, True),
        (ActionStatus.EXECUTING, True),
        (ActionStatus.EXECUTED, True),
        # A failed execution may still have moved money: fail closed.
        (ActionStatus.FAILED, True),
        (ActionStatus.DENIED, False),
        (ActionStatus.APPROVAL_DENIED, False),
        (ActionStatus.EXPIRED, False),
    ],
)
def test_only_actions_that_were_not_refused_count_as_spent(status, counts):
    log = AuditLog()
    record(log, "x", status)
    assert log.history("a1", since=T0 - timedelta(hours=1))[0].executed is counts


def test_status_can_only_move_along_the_allowed_lifecycle():
    log = AuditLog()
    record(log, "x", ActionStatus.ALLOWED)

    assert log.transition("x", {ActionStatus.ALLOWED}, ActionStatus.EXECUTING) is True
    # A second claim of the same action finds it no longer ALLOWED.
    assert log.transition("x", {ActionStatus.ALLOWED}, ActionStatus.EXECUTING) is False
    assert log.action("x").status is ActionStatus.EXECUTING
    assert log.transition("missing", {ActionStatus.ALLOWED}, ActionStatus.EXECUTING) is False


def test_action_record_returns_what_was_stored():
    log = AuditLog()
    record(log, "x", ActionStatus.ALLOWED, amount="0.001")
    stored = log.action("x")
    assert stored.agent_id == "a1"
    assert stored.amount_usd == Decimal("0.001")
    assert stored.action_json == {"type": "x402_payment"}
    assert log.action("nope") is None


def test_duplicate_action_ids_are_rejected():
    log = AuditLog()
    record(log, "x", ActionStatus.ALLOWED)
    with pytest.raises(ValueError):
        record(log, "x", ActionStatus.ALLOWED)


def test_stale_pending_approvals_are_expired_on_startup():
    log = AuditLog()
    record(log, "waiting", ActionStatus.PENDING_APPROVAL)
    record(log, "fine", ActionStatus.ALLOWED)

    assert log.expire_pending_approvals() == ["waiting"]
    assert log.action("waiting").status is ActionStatus.EXPIRED
    assert log.action("fine").status is ActionStatus.ALLOWED


def test_outflows_are_recorded_once_per_signature_and_wallet():
    log = AuditLog()
    assert log.record_outflow(
        signature="sig1", wallet="w1", agent_id="a1", at=T0, amount_units=1000, matched_action_id="x"
    ) is True
    assert log.record_outflow(
        signature="sig1", wallet="w1", agent_id="a1", at=T0, amount_units=1000, matched_action_id=None
    ) is False

    assert log.matched_action_ids("a1") == {"x"}
    assert log.outflow_times("a1", since=T0 - timedelta(seconds=60)) == [T0]
    assert log.outflow_times("a1", since=T0 + timedelta(seconds=1)) == []
