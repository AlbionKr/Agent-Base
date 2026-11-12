"""Sentinel: stream delivery -> outflows -> anomaly -> freeze."""

from __future__ import annotations

import asyncio

import pytest

from app.gate import NotExecutable, UnknownAgent
from app.policy import engine
from app.policy.models import Verdict
from app.quicknode.anomaly import PayloadError
from app.quicknode.sentinel import Sentinel
from gate_fixtures import AGENT, World
from policy_fixtures import ATTACKER, NEWS_SELLER, NEWS_URL, NOW, addr

USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
HOT = addr(10)


class RecordingFreezer:
    """Stands in for the guardian's onchain freeze_agent transaction."""

    def __init__(self, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    async def freeze(self, agent_id: str, reason: str) -> str:
        self.calls.append((agent_id, reason))
        if self.fail:
            raise RuntimeError("devnet RPC unreachable")
        return "5FreezeSig"


def bal(owner: str, amount: int, index: int = 1) -> dict:
    return {"accountIndex": index, "mint": USDC, "owner": owner, "uiTokenAmount": {"amount": str(amount)}}


def payment_tx(amount: int, to: str, signature: str = "sig-1") -> dict:
    return {
        "signature": signature,
        "slot": 1,
        "blockTime": int(NOW.timestamp()),
        "err": None,
        "preTokenBalances": [bal(HOT, 100_000_000, 1), bal(to, 0, 2)],
        "postTokenBalances": [bal(HOT, 100_000_000 - amount, 1), bal(to, amount, 2)],
    }


def batch(*txs: dict) -> dict:
    return {"transactions": list(txs)}


def setup(freezer=None) -> tuple[World, Sentinel]:
    w = World()
    sentinel = Sentinel(journal=w.journal, gate=w.gate, usdc_mint=USDC, freezer=freezer)
    sentinel.watch(AGENT, HOT)
    return w, sentinel


def allow_news_payment(w: World, amount: str = "0.001") -> str:
    proposal = w.gate.propose(
        AGENT, {"type": "x402_payment", "url": NEWS_URL, "payee": NEWS_SELLER, "amount": amount}
    )
    assert proposal.decision.verdict is Verdict.ALLOW
    return proposal.action_id


def handle(sentinel: Sentinel, payload) -> None:
    asyncio.run(sentinel.handle(payload))


def test_only_registered_agents_can_be_watched():
    w, sentinel = setup()
    with pytest.raises(UnknownAgent):
        sentinel.watch("nobody", addr(11))
    assert sentinel.watched() == {HOT: AGENT}


def test_an_allowed_payment_seen_onchain_is_matched_and_nothing_is_frozen():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)
    action_id = allow_news_payment(w)

    handle(sentinel, batch(payment_tx(1000, NEWS_SELLER)))

    observed = w.last("outflow_observed").payload
    assert observed == {
        "signature": "sig-1",
        "wallet": HOT,
        "amount_units": "1000",
        "amount_usdc": "0.001",
        "destinations": [{"owner": NEWS_SELLER, "amount_units": "1000"}],
        "matched_action_id": action_id,
        "anomalies": [],
    }
    assert "anomaly" not in w.kinds() and "freeze" not in w.kinds()
    assert freezer.calls == []
    assert w.gate.agents()[0]["frozen"] is False


def test_an_out_of_band_transfer_freezes_the_agent_onchain():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)
    earlier = allow_news_payment(w)

    handle(sentinel, batch(payment_tx(50_000, ATTACKER, signature="sig-drain")))

    anomaly = w.last("anomaly")
    assert anomaly.agent_id == AGENT
    assert anomaly.payload["signature"] == "sig-drain"
    assert anomaly.payload["rules"] == ["UNEXPECTED_OUTFLOW", "UNKNOWN_DESTINATION"]
    assert len(anomaly.payload["reasons"]) == 2

    assert [agent for agent, _ in freezer.calls] == [AGENT]
    freeze = w.last("freeze").payload
    assert freeze["trigger_signature"] == "sig-drain"
    assert freeze["gate_frozen"] is True
    assert freeze["onchain"] == {"status": "confirmed", "signature": "5FreezeSig"}

    # From here on the gate refuses the agent, including what it allowed earlier.
    w.clock.advance(5)
    after = w.gate.propose(AGENT, {"type": "transfer", "payee": NEWS_SELLER, "amount": "0.01"})
    assert after.decision.rules_fired[0] == engine.AGENT_FROZEN
    with pytest.raises(NotExecutable):
        w.gate.claim(earlier)


def test_an_allowed_action_covers_one_outflow_only():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)
    allow_news_payment(w)

    handle(sentinel, batch(payment_tx(1000, NEWS_SELLER, signature="sig-1")))
    handle(sentinel, batch(payment_tx(1000, NEWS_SELLER, signature="sig-2")))

    assert w.last("anomaly").payload["signature"] == "sig-2"
    assert w.last("anomaly").payload["rules"] == ["UNEXPECTED_OUTFLOW"]
    assert len(freezer.calls) == 1


def test_a_redelivered_transaction_is_processed_once():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)
    allow_news_payment(w)

    handle(sentinel, batch(payment_tx(1000, NEWS_SELLER)))
    handle(sentinel, batch(payment_tx(1000, NEWS_SELLER)))

    assert w.kinds().count("outflow_observed") == 1
    assert "anomaly" not in w.kinds()


def test_a_denied_or_unapproved_action_does_not_excuse_an_outflow():
    w, sentinel = setup(RecordingFreezer())
    denied = w.gate.propose(
        AGENT, {"type": "x402_payment", "url": NEWS_URL, "payee": ATTACKER, "amount": "0.05"}
    )
    assert denied.decision.verdict is Verdict.DENY

    handle(sentinel, batch(payment_tx(50_000, ATTACKER)))

    assert "UNEXPECTED_OUTFLOW" in w.last("anomaly").payload["rules"]


def test_without_a_guardian_the_gate_still_freezes_and_the_event_says_so():
    w, sentinel = setup(freezer=None)

    handle(sentinel, batch(payment_tx(50_000, ATTACKER)))

    freeze = w.last("freeze").payload
    assert freeze["gate_frozen"] is True
    assert freeze["onchain"] == {"status": "not_configured"}
    assert w.gate.agents()[0]["frozen"] is True


def test_a_failed_onchain_freeze_is_reported_not_hidden():
    w, sentinel = setup(RecordingFreezer(fail=True))

    handle(sentinel, batch(payment_tx(50_000, ATTACKER)))

    freeze = w.last("freeze").payload
    assert freeze["onchain"]["status"] == "failed"
    assert "devnet RPC unreachable" in freeze["onchain"]["error"]
    assert w.gate.agents()[0]["frozen"] is True


def test_an_already_frozen_agent_is_not_frozen_again():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)

    handle(sentinel, batch(payment_tx(50_000, ATTACKER, signature="a")))
    handle(sentinel, batch(payment_tx(50_000, ATTACKER, signature="b")))

    assert w.kinds().count("anomaly") == 2
    assert w.kinds().count("freeze") == 1
    assert len(freezer.calls) == 1


def test_transactions_that_do_not_touch_a_watched_wallet_are_ignored():
    w, sentinel = setup(RecordingFreezer())
    stranger = payment_tx(1000, NEWS_SELLER)
    for entry in stranger["preTokenBalances"] + stranger["postTokenBalances"]:
        if entry["owner"] == HOT:
            entry["owner"] = addr(77)
    before = w.audit.last_id()

    handle(sentinel, batch(stranger))

    assert w.audit.last_id() == before


def test_a_malformed_delivery_raises_and_freezes_nothing():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)
    with pytest.raises(PayloadError):
        handle(sentinel, {"unexpected": True})
    assert freezer.calls == []


def test_whole_dollar_amounts_are_written_plainly_not_in_exponent_form():
    w, sentinel = setup(RecordingFreezer())
    tx = payment_tx(10_000_000, ATTACKER)
    tx["preTokenBalances"][0]["uiTokenAmount"]["amount"] = "10000000"
    tx["postTokenBalances"][0]["uiTokenAmount"]["amount"] = "0"

    handle(sentinel, batch(tx))

    assert w.last("outflow_observed").payload["amount_usdc"] == "10"


def test_a_payment_still_waiting_for_approval_does_not_excuse_an_outflow():
    w, sentinel = setup(RecordingFreezer())
    pending = w.gate.propose(
        AGENT, {"type": "x402_payment", "url": NEWS_URL, "payee": NEWS_SELLER, "amount": "0.60"}
    )
    assert pending.decision.verdict is Verdict.REQUIRE_APPROVAL

    handle(sentinel, batch(payment_tx(600_000, NEWS_SELLER)))

    assert w.last("anomaly").payload["rules"] == ["UNEXPECTED_OUTFLOW"]
    assert w.last("outflow_observed").payload["matched_action_id"] is None


def test_after_the_owner_unfreezes_a_new_anomaly_freezes_again():
    freezer = RecordingFreezer()
    w, sentinel = setup(freezer)
    handle(sentinel, batch(payment_tx(50_000, ATTACKER, signature="a")))
    w.gate.set_frozen(AGENT, False)
    sentinel.clear(AGENT)

    handle(sentinel, batch(payment_tx(50_000, ATTACKER, signature="b")))

    assert len(freezer.calls) == 2
    assert w.gate.agents()[0]["frozen"] is True
