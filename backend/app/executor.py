"""Executor: the only code that moves money, and it only runs what the gate releases.

It never receives an action from the LLM. It receives an action *id*, asks the
gate to `claim` it (which works once, and only for allowed or approved actions
of an agent that is not frozen), runs the matching handler, and reports back.
"""

from __future__ import annotations

from typing import Any, Awaitable, Callable, Mapping

from app.audit import AuditEvent
from app.chain.ops import VaultSpender
from app.chain.units import usdc_to_units
from app.gate import Gate
from app.policy.models import Action, TransferAction

# (agent_id, validated action) -> JSON detail for the audit log. Raise on failure.
Handler = Callable[[str, Action], Awaitable[dict[str, Any]]]


class Executor:
    def __init__(self, gate: Gate, handlers: Mapping[str, Handler]):
        self._gate = gate
        self._handlers = dict(handlers)

    async def execute(self, action_id: str) -> AuditEvent:
        """Raises NotExecutable if the gate will not release the action."""
        agent_id, action = self._gate.claim_for(action_id)
        handler = self._handlers.get(action.type)
        if handler is None:
            return self._gate.report_execution(
                action_id,
                ok=False,
                detail={"error": f"no executor is configured for {action.type} actions"},
            )
        try:
            detail = await handler(agent_id, action)
        except Exception as exc:  # every failure is recorded, none is swallowed
            return self._gate.report_execution(
                action_id, ok=False, detail={"error": f"{type(exc).__name__}: {exc}"[:500]}
            )
        return self._gate.report_execution(action_id, ok=True, detail=detail)


def vault_transfer_handler(spender: VaultSpender) -> Handler:
    """`transfer` actions: the vault program pays the payee directly (`pay`).

    The program re-checks the cap, the daily budget, the allowlist, expiry and
    freeze state, so this cannot exceed the onchain allowance whatever the
    policy engine said.
    """

    async def handle(agent_id: str, action: Action) -> dict[str, Any]:
        assert isinstance(action, TransferAction)
        signature = await spender.pay(agent_id, action.payee, usdc_to_units(action.amount))
        return {"signature": signature, "via": "vault.pay", "payee": action.payee}

    return handle
