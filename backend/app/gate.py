"""The gate: the AI proposes, the policy engine decides, and only what was decided
ALLOW (or approved by a human) is ever released to the executor — once.

    propose()  raw action from the LLM -> validated -> evaluate() -> recorded
    claim()    executor takes an allowed action; single use
    report_execution()  executor says what happened

The executor never sees raw LLM output. It can only run what `claim` hands it.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any, Callable

from pydantic import ValidationError

from app.approvals import Approval, ApprovalQueue, ApprovalStatus
from app.audit import ActionStatus, AuditEvent
from app.events import Journal
from app.policy.engine import INPUT_INVALID, describe_action, evaluate
from app.policy.ledger import ContextLedger
from app.policy.mandate import SignedMandate, compile_mandate
from app.policy.models import (
    Action,
    Decision,
    Policy,
    PolicyState,
    RuleHit,
    Verdict,
    amount_usd,
    destination,
    parse_action,
)

HISTORY_WINDOW = timedelta(hours=24)
MAX_RAW_LOGGED = 2000
MAX_REASON = 300

_STATUS_FOR_VERDICT = {
    Verdict.ALLOW: ActionStatus.ALLOWED,
    Verdict.REQUIRE_APPROVAL: ActionStatus.PENDING_APPROVAL,
    Verdict.DENY: ActionStatus.DENIED,
}
_STATUS_FOR_APPROVAL = {
    ApprovalStatus.APPROVED: ActionStatus.APPROVED,
    ApprovalStatus.DENIED: ActionStatus.APPROVAL_DENIED,
    ApprovalStatus.EXPIRED: ActionStatus.EXPIRED,
}


class UnknownAgent(KeyError):
    pass


class NotExecutable(RuntimeError):
    """The action is not in a state the executor may run."""


@dataclass(frozen=True)
class Proposal:
    action_id: str
    agent_id: str
    decision: Decision
    approval: Approval | None
    event_id: int

    def to_json(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "agent_id": self.agent_id,
            "decision": self.decision.model_dump(mode="json"),
            "approval": self.approval.to_json() if self.approval else None,
            "event_id": self.event_id,
        }


@dataclass
class _Agent:
    policy: Policy
    name: str
    ledger: ContextLedger
    frozen: bool = False


class Gate:
    def __init__(
        self,
        *,
        journal: Journal,
        approvals: ApprovalQueue,
        trusted_owner: str,
        id_factory: Callable[[], str] = lambda: uuid.uuid4().hex,
    ):
        self._journal = journal
        self._audit = journal.audit
        self._approvals = approvals
        self._trusted_owner = trusted_owner
        self._new_id = id_factory
        self._agents: dict[str, _Agent] = {}

    # ------------------------------------------------------------- agents

    def register_agent(self, signed: SignedMandate) -> Policy:
        """Compile a mandate signed by the vault owner. Raises MandateError."""
        policy = compile_mandate(signed, trusted_owner=self._trusted_owner)
        existing = self._agents.get(policy.agent_id)
        self._agents[policy.agent_id] = _Agent(
            policy=policy,
            name=signed.mandate.agent_name,
            ledger=existing.ledger if existing else ContextLedger(),
            frozen=existing.frozen if existing else False,
        )
        self._journal.emit(
            "agent_registered",
            {
                "agent_name": signed.mandate.agent_name,
                "mandate_hash": policy.mandate_hash.hex(),
                "mandate": signed.mandate.model_dump(mode="json"),
                "signer": signed.signer,
            },
            agent_id=policy.agent_id,
        )
        return policy

    def agents(self) -> list[dict[str, Any]]:
        return [
            {
                "agent_id": agent_id,
                "agent_name": agent.name,
                "frozen": agent.frozen,
                "mandate_hash": agent.policy.mandate_hash.hex(),
                "expires_at": agent.policy.expires_at.isoformat(),
                "max_per_action_usd": str(agent.policy.max_per_action_usd),
                "max_per_day_usd": str(agent.policy.max_per_day_usd),
                "approval_threshold_usd": str(agent.policy.approval_threshold_usd),
                "context_items": len(agent.ledger),
            }
            for agent_id, agent in self._agents.items()
        ]

    def policy(self, agent_id: str) -> Policy:
        return self._agent(agent_id).policy

    def set_frozen(self, agent_id: str, frozen: bool) -> None:
        self._agent(agent_id).frozen = frozen

    def _agent(self, agent_id: str) -> _Agent:
        try:
            return self._agents[agent_id]
        except KeyError:
            raise UnknownAgent(agent_id) from None

    # ------------------------------------------------------------ context

    def observe(self, agent_id: str, source: str, item_id: str, text: str) -> None:
        """Register untrusted content before the LLM reads it."""
        self._agent(agent_id).ledger.register(source, item_id, text)
        self._journal.emit(
            "context",
            {"source": source, "item_id": item_id, "text": text, "trusted": False},
            agent_id=agent_id,
        )

    # ----------------------------------------------------------- proposals

    def propose(self, agent_id: str, raw: Any) -> Proposal:
        agent = self._agent(agent_id)
        now = self._journal.clock()
        action_id = self._new_id()

        started = time.perf_counter_ns()
        action, decision = self._decide(agent, agent_id, action_id, raw, now)
        latency_us = (time.perf_counter_ns() - started) // 1000

        amount = amount_usd(action) if action else Decimal(0)
        self._audit.record_action(
            action_id=action_id,
            agent_id=agent_id,
            at=now,
            action_type=action.type if action else _raw_type(raw),
            destination=destination(action) if action else "",
            amount_usd=amount,
            verdict=decision.verdict,
            status=_STATUS_FOR_VERDICT[decision.verdict],
            action_json=action.model_dump(mode="json") if action else {},
        )

        approval = None
        if decision.verdict is Verdict.REQUIRE_APPROVAL:
            approval = self._approvals.request(
                action_id=action_id,
                agent_id=agent_id,
                summary=describe_action(action),
                amount_usd=amount,
                now=now,
            )

        payload: dict[str, Any] = {
            "action_id": action_id,
            "action": action.model_dump(mode="json") if action else None,
            "amount_usd": str(amount),
            "verdict": decision.verdict.value,
            "rules_fired": list(decision.rules_fired),
            "reason": decision.reason,
            "hits": [hit.model_dump(mode="json") for hit in decision.hits],
            "taint": [finding.model_dump(mode="json") for finding in decision.taint],
            "latency_us": latency_us,
            "approval_id": approval.approval_id if approval else None,
        }
        if action is None:
            payload["raw"] = json.dumps(raw, default=str, ensure_ascii=False)[:MAX_RAW_LOGGED]
        event = self._journal.emit("decision", payload, agent_id=agent_id)

        if approval:
            self._journal.emit("approval_requested", approval.to_json(), agent_id=agent_id)
        return Proposal(action_id, agent_id, decision, approval, event.id)

    def _decide(
        self, agent: _Agent, agent_id: str, action_id: str, raw: Any, now
    ) -> tuple[Action | None, Decision]:
        try:
            if not isinstance(raw, dict):
                raise TypeError("a proposed action must be a JSON object")
            # The id is ours. Whatever the LLM put there is discarded.
            action = parse_action({**raw, "action_id": action_id})
        except (ValidationError, TypeError) as exc:
            # Field names in the error come from the LLM's output: keep it short.
            reason = f"The proposed action is malformed: {_first_error(exc)}"[:MAX_REASON]
            hit = RuleHit(rule_id=INPUT_INVALID, verdict=Verdict.DENY, reason=reason)
            return None, Decision(
                verdict=Verdict.DENY, rules_fired=(INPUT_INVALID,), reason=reason, hits=(hit,)
            )
        state = PolicyState(
            now=now,
            history=self._audit.history(agent_id, since=now - HISTORY_WINDOW),
            frozen=agent.frozen,
        )
        return action, evaluate(action, agent.policy, state, agent.ledger.snapshot())

    # ----------------------------------------------------------- approvals

    def pending_approvals(self) -> list[Approval]:
        return self._approvals.pending()

    def resolve_approval(self, approval_id: str, *, approve: bool, via: str) -> Approval:
        """Raises UnknownApproval / AlreadyResolved."""
        resolved = self._approvals.resolve(
            approval_id, approve=approve, via=via, now=self._journal.clock()
        )
        self._close_approval(resolved)
        return resolved

    def expire_approvals(self) -> list[Approval]:
        expired = self._approvals.expire(self._journal.clock())
        for approval in expired:
            self._close_approval(approval)
        return expired

    def _close_approval(self, approval: Approval) -> None:
        self._audit.transition(
            approval.action_id,
            {ActionStatus.PENDING_APPROVAL},
            _STATUS_FOR_APPROVAL[approval.status],
        )
        self._journal.emit("approval_resolved", approval.to_json(), agent_id=approval.agent_id)

    # ----------------------------------------------------------- execution

    def claim(self, action_id: str) -> Action:
        """Hand an allowed action to the executor. Works once per action."""
        record = self._audit.action(action_id)
        if record is None:
            raise NotExecutable(f"action {action_id} does not exist")
        agent = self._agents.get(record.agent_id)
        if agent is None or agent.frozen:
            raise NotExecutable(f"agent {record.agent_id} is frozen or unknown")
        released = self._audit.transition(
            action_id, {ActionStatus.ALLOWED, ActionStatus.APPROVED}, ActionStatus.EXECUTING
        )
        if not released:
            raise NotExecutable(f"action {action_id} is {record.status.value}, not executable")
        self._journal.emit("execution_started", {"action_id": action_id}, agent_id=record.agent_id)
        return parse_action(record.action_json)

    def report_execution(self, action_id: str, *, ok: bool, detail: dict[str, Any]) -> AuditEvent:
        record = self._audit.action(action_id)
        new = ActionStatus.EXECUTED if ok else ActionStatus.FAILED
        if record is None or not self._audit.transition(action_id, {ActionStatus.EXECUTING}, new):
            raise NotExecutable(f"action {action_id} was not claimed for execution")
        return self._journal.emit(
            "execution",
            {"action_id": action_id, "ok": ok, "detail": detail},
            agent_id=record.agent_id,
        )


def _raw_type(raw: Any) -> str:
    kind = raw.get("type") if isinstance(raw, dict) else None
    return kind[:40] if isinstance(kind, str) and kind else "unknown"


def _first_error(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        first = exc.errors()[0]
        where = ".".join(str(part) for part in first["loc"]) or "action"
        return f"{where}: {first['msg']}."
    return f"{exc}."
