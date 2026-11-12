"""Reading stream payloads and judging outflows. Pure functions, no I/O."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.quicknode.anomaly import (
    OUTFLOW_BURST,
    UNEXPECTED_OUTFLOW,
    UNKNOWN_DESTINATION,
    ExpectedSpend,
    Outflow,
    PayloadError,
    assess,
    parse_payload,
    usdc_outflows,
)
from policy_fixtures import ATTACKER, NEWS_SELLER, PARTNER, addr

USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
OTHER_MINT = addr(90)
HOT = addr(10)
NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)


def bal(owner: str, amount: int, mint: str = USDC, index: int = 1) -> dict:
    """One entry of a Solana pre/postTokenBalances array."""
    return {
        "accountIndex": index,
        "mint": mint,
        "owner": owner,
        "uiTokenAmount": {"amount": str(amount), "decimals": 6, "uiAmountString": "ignored"},
    }


def tx(pre: list[dict], post: list[dict], *, signature: str = "sig-1", err=None) -> dict:
    return {
        "signature": signature,
        "slot": 400_000_000,
        "blockTime": int(NOW.timestamp()),
        "err": err,
        "preTokenBalances": pre,
        "postTokenBalances": post,
    }


def payment(amount: int = 1000, to: str = NEWS_SELLER, **kw) -> dict:
    return tx(
        [bal(HOT, 100_000, index=1), bal(to, 5_000, index=2)],
        [bal(HOT, 100_000 - amount, index=1), bal(to, 5_000 + amount, index=2)],
        **kw,
    )


# -------------------------------------------------------------------- parsing


def test_a_batch_is_parsed_into_transactions():
    [parsed] = parse_payload({"transactions": [payment()]})
    assert parsed.signature == "sig-1"
    assert parsed.slot == 400_000_000
    assert parsed.block_time == NOW
    assert parsed.failed is False


def test_a_list_of_batches_is_flattened():
    parsed = parse_payload(
        [{"transactions": [payment(signature="a")]}, {"transactions": [payment(signature="b")]}]
    )
    assert [t.signature for t in parsed] == ["a", "b"]


def test_an_empty_batch_is_fine():
    assert parse_payload({"transactions": []}) == []


@pytest.mark.parametrize(
    "bad",
    [
        None,
        "text",
        {"blocks": []},
        {"transactions": "nope"},
        {"transactions": [{"slot": 1}]},  # no signature
        {"transactions": [{**payment(), "preTokenBalances": "x"}]},
        {"transactions": [tx([{"owner": HOT, "mint": USDC}], [])]},  # no amount
        {"transactions": [tx([bal(HOT, 1)], [{**bal(HOT, 1), "uiTokenAmount": {"amount": "1.5"}}])]},
    ],
)
def test_a_payload_in_the_wrong_shape_is_an_error_not_a_guess(bad):
    with pytest.raises(PayloadError):
        parse_payload(bad)


# ------------------------------------------------------------------- outflows


def outflows(raw_tx: dict, watched=(HOT,)) -> list[Outflow]:
    [parsed] = parse_payload({"transactions": [raw_tx]})
    return usdc_outflows(parsed, mint=USDC, watched=frozenset(watched))


def test_a_payment_from_a_watched_wallet_is_an_outflow_to_the_receiver():
    [out] = outflows(payment(1000))
    assert out == Outflow(
        signature="sig-1", wallet=HOT, amount_units=1000, destinations=((NEWS_SELLER, 1000),), at=NOW
    )


def test_a_token_account_created_in_the_transaction_counts_from_zero():
    raw = tx([bal(HOT, 5_000_000)], [bal(HOT, 0), bal(ATTACKER, 5_000_000, index=3)])
    [out] = outflows(raw)
    assert out.amount_units == 5_000_000
    assert out.destinations == ((ATTACKER, 5_000_000),)


def test_a_token_account_closed_in_the_transaction_counts_down_to_zero():
    raw = tx([bal(HOT, 700)], [bal(ATTACKER, 700, index=2)])
    [out] = outflows(raw)
    assert (out.amount_units, out.destinations) == (700, ((ATTACKER, 700),))


def test_split_payments_list_every_receiver():
    raw = tx(
        [bal(HOT, 3000)],
        [bal(HOT, 0), bal(NEWS_SELLER, 1000, index=2), bal(ATTACKER, 2000, index=3)],
    )
    [out] = outflows(raw)
    assert out.amount_units == 3000
    assert out.destinations == tuple(sorted([(NEWS_SELLER, 1000), (ATTACKER, 2000)]))


def test_inflows_other_mints_other_wallets_and_failed_transactions_are_not_outflows():
    top_up = tx([bal(HOT, 0)], [bal(HOT, 100_000)])
    other_mint = tx([bal(HOT, 9, OTHER_MINT)], [bal(HOT, 0, OTHER_MINT), bal(ATTACKER, 9, OTHER_MINT)])
    stranger = tx([bal(PARTNER, 50)], [bal(PARTNER, 0), bal(ATTACKER, 50, index=2)])
    failed = payment(1000, err={"InstructionError": [0, "Custom"]})
    for raw in (top_up, other_mint, stranger, failed):
        assert outflows(raw) == []


def test_two_token_accounts_of_one_owner_are_netted():
    raw = tx(
        [bal(HOT, 1000, index=1), bal(HOT, 0, index=2)],
        [bal(HOT, 0, index=1), bal(HOT, 1000, index=2)],
    )
    assert outflows(raw) == []


# ----------------------------------------------------------------- assessment


def flow(amount: int = 1000, to: str = NEWS_SELLER, **kw) -> Outflow:
    return Outflow(
        signature=kw.get("signature", "sig-1"),
        wallet=HOT,
        amount_units=amount,
        destinations=kw.get("destinations", ((to, amount),)),
        at=NOW,
    )


def expect(action_id: str = "act-1", amount: int = 1000, to: str = NEWS_SELLER) -> ExpectedSpend:
    return ExpectedSpend(action_id=action_id, destination=to, amount_units=amount)


def judge(outflow: Outflow, expected=(), recent: int = 0, limit: int = 6):
    return assess(
        outflow,
        expected=tuple(expected),
        allowed_destinations=frozenset({NEWS_SELLER, PARTNER}),
        recent_outflows=recent,
        max_outflows_per_minute=limit,
    )


def test_an_outflow_that_matches_an_allowed_payment_is_normal():
    result = judge(flow(), [expect("act-7")])
    assert result.matched_action_id == "act-7"
    assert result.anomalies == ()


def test_an_outflow_nobody_allowed_is_an_anomaly():
    result = judge(flow(), [])
    assert result.matched_action_id is None
    assert [a.rule_id for a in result.anomalies] == [UNEXPECTED_OUTFLOW]
    assert "no allowed action" in result.anomalies[0].reason


@pytest.mark.parametrize(
    "expected",
    [
        [expect(amount=999)],  # different amount
        [expect(to=PARTNER)],  # different payee
    ],
)
def test_a_near_miss_does_not_match(expected):
    assert UNEXPECTED_OUTFLOW in [a.rule_id for a in judge(flow(), expected).anomalies]


def test_an_allowed_payment_does_not_cover_extra_money_leaving_in_the_same_transaction():
    leak = flow(3000, destinations=tuple(sorted([(NEWS_SELLER, 1000), (ATTACKER, 2000)])))
    result = judge(leak, [expect(amount=1000)])
    assert {a.rule_id for a in result.anomalies} == {UNEXPECTED_OUTFLOW, UNKNOWN_DESTINATION}
    assert result.matched_action_id is None


def test_the_oldest_matching_allowed_action_is_consumed_first():
    result = judge(flow(), [expect("act-1"), expect("act-2")])
    assert result.matched_action_id == "act-1"


def test_money_to_an_address_outside_the_mandate_is_an_anomaly_even_if_expected():
    result = judge(flow(to=ATTACKER), [expect(to=ATTACKER)])
    assert [a.rule_id for a in result.anomalies] == [UNKNOWN_DESTINATION]
    assert ATTACKER[:4] in result.anomalies[0].reason


def test_a_burst_of_outflows_is_an_anomaly():
    assert judge(flow(), [expect()], recent=5, limit=6).anomalies == ()
    burst = judge(flow(), [expect()], recent=6, limit=6)
    assert [a.rule_id for a in burst.anomalies] == [OUTFLOW_BURST]
