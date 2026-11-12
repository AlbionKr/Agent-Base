"""The objects both HTTP apps share, built in one place."""

from __future__ import annotations

from dataclasses import dataclass

from app.approvals import DEFAULT_TIMEOUT_SECONDS, ApprovalQueue
from app.audit import AuditLog
from app.events import Clock, EventBus, Journal, utcnow
from app.gate import Gate
from app.quicknode.sentinel import Freezer, Sentinel
from app.quicknode.verify import NonceCache


@dataclass
class Services:
    audit: AuditLog
    bus: EventBus
    journal: Journal
    gate: Gate
    sentinel: Sentinel
    nonces: NonceCache
    webhook_secret: str
    allowed_origins: frozenset[str]
    clock: Clock


def build_services(
    *,
    audit: AuditLog,
    trusted_owner: str,
    usdc_mint: str,
    webhook_secret: str,
    allowed_origins: frozenset[str],
    clock: Clock = utcnow,
    freezer: Freezer | None = None,
    approval_timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
) -> Services:
    bus = EventBus()
    journal = Journal(audit, bus, clock=clock)
    gate = Gate(
        journal=journal,
        approvals=ApprovalQueue(timeout_seconds=approval_timeout_seconds),
        trusted_owner=trusted_owner,
    )
    sentinel = Sentinel(journal=journal, gate=gate, usdc_mint=usdc_mint, freezer=freezer)
    return Services(
        audit=audit,
        bus=bus,
        journal=journal,
        gate=gate,
        sentinel=sentinel,
        nonces=NonceCache(),
        webhook_secret=webhook_secret,
        allowed_origins=frozenset(allowed_origins),
        clock=clock,
    )
