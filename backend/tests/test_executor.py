"""Executor: runs only what the gate releases, and always reports what happened."""

from __future__ import annotations

import asyncio
from decimal import Decimal

import pytest

from app.audit import ActionStatus
from app.chain.ops import cluster_name, explorer_cluster_param, onchain_agent_id
from app.executor import Executor
from app.gate import NotExecutable
from gate_fixtures import AGENT, World
from policy_fixtures import ATTACKER, PARTNER


def transfer(amount="0.25", payee=PARTNER) -> dict:
    return {"type": "transfer", "payee": payee, "amount": amount}


class Recorder:
    def __init__(self, fail: bool = False):
        self.calls = []
        self.fail = fail

    async def __call__(self, agent_id, action):
        self.calls.append((agent_id, action))
        if self.fail:
            raise RuntimeError("PerTxCapExceeded")
        return {"signature": "5sig", "via": "vault.pay"}


def test_an_allowed_action_is_executed_once_and_logged():
    w = World()
    handler = Recorder()
    executor = Executor(w.gate, {"transfer": handler})
    proposal = w.gate.propose(AGENT, transfer())

    event = asyncio.run(executor.execute(proposal.action_id))

    assert [(agent, action.amount) for agent, action in handler.calls] == [(AGENT, Decimal("0.25"))]
    assert event.payload == {
        "action_id": proposal.action_id, "ok": True, "detail": {"signature": "5sig", "via": "vault.pay"},
    }
    assert w.audit.action(proposal.action_id).status is ActionStatus.EXECUTED
    with pytest.raises(NotExecutable):
        asyncio.run(executor.execute(proposal.action_id))
    assert len(handler.calls) == 1


def test_a_denied_action_never_reaches_a_handler():
    w = World()
    handler = Recorder()
    executor = Executor(w.gate, {"transfer": handler})
    denied = w.gate.propose(AGENT, transfer(payee=ATTACKER))

    with pytest.raises(NotExecutable):
        asyncio.run(executor.execute(denied.action_id))

    assert handler.calls == []


def test_a_failing_handler_is_reported_as_a_failed_execution():
    w = World()
    executor = Executor(w.gate, {"transfer": Recorder(fail=True)})
    proposal = w.gate.propose(AGENT, transfer())

    event = asyncio.run(executor.execute(proposal.action_id))

    assert event.payload["ok"] is False
    assert "PerTxCapExceeded" in event.payload["detail"]["error"]
    assert w.audit.action(proposal.action_id).status is ActionStatus.FAILED


def test_an_action_type_with_no_executor_fails_loudly_instead_of_pretending():
    w = World()
    executor = Executor(w.gate, {})
    proposal = w.gate.propose(AGENT, transfer())

    event = asyncio.run(executor.execute(proposal.action_id))

    assert event.payload["ok"] is False
    assert "no executor is configured" in event.payload["detail"]["error"]


def test_onchain_agent_ids_are_stable_u64s():
    assert onchain_agent_id("btc-trader") == onchain_agent_id("btc-trader")
    assert onchain_agent_id("btc-trader") != onchain_agent_id("btc-trader-2")
    assert 0 <= onchain_agent_id("x") < 2**64


def test_cluster_names_and_explorer_links():
    assert cluster_name("https://api.devnet.solana.com") == "devnet"
    assert cluster_name("http://127.0.0.1:8899") == "localnet"
    assert explorer_cluster_param("https://api.devnet.solana.com") == "devnet"
    assert explorer_cluster_param("http://127.0.0.1:8899") == "custom&customUrl=http%3A%2F%2F127.0.0.1%3A8899"
    assert cluster_name("https://example.solana-devnet.quiknode.pro/abc/") == "devnet"
