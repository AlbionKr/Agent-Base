"""Approval queue. A pure state machine: time is passed in, nothing sleeps here.

PENDING -> APPROVED | DENIED | EXPIRED, and never anywhere else afterwards.
An answer that arrives at or after the deadline is a timeout, even if it says yes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from decimal import Decimal
from enum import Enum
from typing import Callable

DEFAULT_TIMEOUT_SECONDS = 60


class ApprovalStatus(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"
    EXPIRED = "expired"


class UnknownApproval(KeyError):
    pass


class AlreadyResolved(RuntimeError):
    pass


@dataclass(frozen=True)
class Approval:
    approval_id: str
    action_id: str
    agent_id: str
    summary: str
    amount_usd: Decimal
    requested_at: datetime
    deadline: datetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    resolved_at: datetime | None = None
    resolved_via: str | None = None

    def to_json(self) -> dict:
        return {
            "approval_id": self.approval_id,
            "action_id": self.action_id,
            "agent_id": self.agent_id,
            "summary": self.summary,
            "amount_usd": str(self.amount_usd),
            "requested_at": self.requested_at.isoformat(),
            "deadline": self.deadline.isoformat(),
            "status": self.status.value,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolved_via": self.resolved_via,
        }


class ApprovalQueue:
    def __init__(
        self,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._timeout = timedelta(seconds=timeout_seconds)
        self._new_id = id_factory
        self._items: dict[str, Approval] = {}

    def request(
        self, *, action_id: str, agent_id: str, summary: str, amount_usd: Decimal, now: datetime
    ) -> Approval:
        approval = Approval(
            approval_id=self._new_id(),
            action_id=action_id,
            agent_id=agent_id,
            summary=summary,
            amount_usd=amount_usd,
            requested_at=now,
            deadline=now + self._timeout,
        )
        self._items[approval.approval_id] = approval
        return approval

    def get(self, approval_id: str) -> Approval | None:
        return self._items.get(approval_id)

    def pending(self) -> list[Approval]:
        waiting = [a for a in self._items.values() if a.status is ApprovalStatus.PENDING]
        return sorted(waiting, key=lambda a: a.requested_at)

    def resolve(self, approval_id: str, *, approve: bool, via: str, now: datetime) -> Approval:
        approval = self._items.get(approval_id)
        if approval is None:
            raise UnknownApproval(approval_id)
        if approval.status is not ApprovalStatus.PENDING:
            raise AlreadyResolved(f"approval {approval_id} is already {approval.status.value}")
        if now >= approval.deadline:
            return self._finish(approval, ApprovalStatus.EXPIRED, "timeout", now)
        status = ApprovalStatus.APPROVED if approve else ApprovalStatus.DENIED
        return self._finish(approval, status, via, now)

    def expire(self, now: datetime) -> list[Approval]:
        overdue = [a for a in self.pending() if now >= a.deadline]
        return [self._finish(a, ApprovalStatus.EXPIRED, "timeout", now) for a in overdue]

    def _finish(self, approval: Approval, status: ApprovalStatus, via: str, now: datetime) -> Approval:
        done = replace(approval, status=status, resolved_at=now, resolved_via=via)
        self._items[approval.approval_id] = done
        return done
