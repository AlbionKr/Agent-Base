"""Sentinel: watches agent hot wallets through the QuickNode stream and pulls the
brake when money moves that the gate did not allow.

On an anomaly it does two things, in this order:
1. freezes the agent in the gate (instant, offchain: no new decisions, no claims)
2. asks the guardian to send `freeze_agent` onchain (the part the agent cannot undo)

The `freeze` event states exactly which of the two happened.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Any, Protocol

from app.audit import REFUSED, ActionStatus
from app.chain.units import units_to_usdc, usdc_to_units
from app.events import Journal
from app.gate import Gate
from app.quicknode.anomaly import ExpectedSpend, Outflow, assess, parse_payload, usdc_outflows

MATCH_WINDOW = timedelta(minutes=5)
BURST_WINDOW = timedelta(seconds=60)


class Freezer(Protocol):
    async def freeze(self, agent_id: str, reason: str) -> str:
        """Send the guardian's freeze_agent transaction; return its signature."""


class Sentinel:
    def __init__(
        self,
        *,
        journal: Journal,
        gate: Gate,
        usdc_mint: str,
        freezer: Freezer | None = None,
    ):
        self._journal = journal
        self._audit = journal.audit
        self._gate = gate
        self._mint = usdc_mint
        self._freezer = freezer
        self._wallets: dict[str, str] = {}
        self._frozen_by_us: set[str] = set()

    def watch(self, agent_id: str, wallet: str) -> None:
        self._gate.policy(agent_id)  # raises UnknownAgent
        self._wallets[wallet] = agent_id

    def watched(self) -> dict[str, str]:
        return dict(self._wallets)

    async def handle(self, payload: Any) -> None:
        """Process one authenticated delivery. Raises PayloadError on a bad shape."""
        for tx in parse_payload(payload):
            for outflow in usdc_outflows(tx, mint=self._mint, watched=self._wallets.keys()):
                await self._judge(outflow)

    async def _judge(self, outflow: Outflow) -> None:
        if self._audit.has_outflow(outflow.signature, outflow.wallet):
            return  # redelivery
        agent_id = self._wallets[outflow.wallet]
        now = self._journal.clock()
        at = outflow.at or now
        policy = self._gate.policy(agent_id)

        result = assess(
            outflow,
            expected=self._expected(agent_id, now),
            allowed_destinations=policy.allowed_payees,
            recent_outflows=len(self._audit.outflow_times(agent_id, since=at - BURST_WINDOW)),
            max_outflows_per_minute=policy.max_actions_per_minute,
        )
        self._audit.record_outflow(
            signature=outflow.signature,
            wallet=outflow.wallet,
            agent_id=agent_id,
            at=at,
            amount_units=outflow.amount_units,
            matched_action_id=result.matched_action_id,
        )
        self._journal.emit(
            "outflow_observed",
            {
                "signature": outflow.signature,
                "wallet": outflow.wallet,
                "amount_units": str(outflow.amount_units),
                "amount_usdc": format(units_to_usdc(outflow.amount_units), "f"),
                "destinations": [
                    {"owner": owner, "amount_units": str(units)} for owner, units in outflow.destinations
                ],
                "matched_action_id": result.matched_action_id,
                "anomalies": [a.rule_id for a in result.anomalies],
            },
            agent_id=agent_id,
        )
        if not result.anomalies:
            return

        self._journal.emit(
            "anomaly",
            {
                "signature": outflow.signature,
                "wallet": outflow.wallet,
                "rules": [a.rule_id for a in result.anomalies],
                "reasons": [a.reason for a in result.anomalies],
            },
            agent_id=agent_id,
        )
        if agent_id in self._frozen_by_us:
            return
        self._frozen_by_us.add(agent_id)
        await self._freeze(agent_id, outflow.signature, result.anomalies[0].reason)

    def _expected(self, agent_id: str, now) -> list[ExpectedSpend]:
        """Allowed x402 payments not yet seen onchain, oldest first.

        Only x402 payments leave the hot wallet; vault transfers leave the vault.
        The decision is recorded before the executor runs, so a payment can never
        reach the chain before it appears here.
        """
        already = self._audit.matched_action_ids(agent_id)
        not_released = REFUSED | {ActionStatus.PENDING_APPROVAL}
        return [
            ExpectedSpend(
                action_id=record.action_id,
                destination=record.destination,
                amount_units=usdc_to_units(Decimal(record.action_json["amount"])),
            )
            for record in self._audit.actions(agent_id, since=now - MATCH_WINDOW)
            if record.action_type == "x402_payment"
            and record.status not in not_released
            and record.action_id not in already
        ]

    async def _freeze(self, agent_id: str, trigger_signature: str, reason: str) -> None:
        self._gate.set_frozen(agent_id, True)
        if self._freezer is None:
            onchain: dict[str, Any] = {"status": "not_configured"}
        else:
            try:
                onchain = {
                    "status": "confirmed",
                    "signature": await self._freezer.freeze(agent_id, reason),
                }
            except Exception as exc:  # report every failure; never pretend it froze
                onchain = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        self._journal.emit(
            "freeze",
            {
                "reason": reason,
                "trigger_signature": trigger_signature,
                "gate_frozen": True,
                "onchain": onchain,
            },
            agent_id=agent_id,
        )
