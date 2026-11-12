"""A gate with a fake clock and one registered agent, shared by the service tests."""

from __future__ import annotations

from datetime import datetime, timedelta

from app.approvals import ApprovalQueue
from app.audit import AuditLog
from app.events import EventBus, Journal
from app.gate import Gate
from app.policy.mandate import Mandate, sign_mandate
from policy_fixtures import NEWS_ORIGIN, NEWS_SELLER, NOW, OWNER, PARTNER

AGENT = "btc-trader"


class FakeClock:
    def __init__(self, now: datetime = NOW):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_mandate(**overrides) -> Mandate:
    base = dict(
        agent_id=AGENT,
        agent_name="BTC Trader",
        allowed_action_types=("x402_payment", "gemini_order", "transfer"),
        allowed_markets=("GEMI-BTC05M*",),
        allowed_payees=(NEWS_SELLER, PARTNER),
        allowed_endpoints=(NEWS_ORIGIN,),
        max_per_action_usd="1.00",
        max_per_day_usd="5.00",
        approval_threshold_usd="0.50",
        max_actions_per_minute=6,
        expires_at=NOW + timedelta(days=7),
    )
    base.update(overrides)
    return Mandate(**base)


class World:
    def __init__(self, **mandate_overrides):
        self.clock = FakeClock()
        self.audit = AuditLog()
        self.journal = Journal(self.audit, EventBus(), clock=self.clock)
        action_ids = iter(f"act-{n}" for n in range(1, 1000))
        approval_ids = iter(f"ap-{n}" for n in range(1, 1000))
        self.gate = Gate(
            journal=self.journal,
            approvals=ApprovalQueue(timeout_seconds=60, id_factory=lambda: next(approval_ids)),
            trusted_owner=str(OWNER.pubkey()),
            id_factory=lambda: next(action_ids),
        )
        self.gate.register_agent(sign_mandate(make_mandate(**mandate_overrides), OWNER))

    def kinds(self) -> list[str]:
        return [e.kind for e in self.audit.events()]

    def last(self, kind: str):
        return self.audit.events(kind=kind)[-1]
