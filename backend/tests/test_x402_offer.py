"""The payer signs only an offer that matches what the gate approved."""

from __future__ import annotations

import pytest

from app.agent.x402_pay import PaymentRefused, choose_offer
from policy_fixtures import ATTACKER, NEWS_SELLER

USDC = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"
DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"


def offer(**overrides) -> dict:
    base = {"scheme": "exact", "network": DEVNET, "asset": USDC, "amount": "1000", "pay_to": NEWS_SELLER}
    return {**base, **overrides}


def pick(offers, approved_units=1000, payee=NEWS_SELLER):
    return choose_offer(offers, payee=payee, approved_units=approved_units, network=DEVNET, asset=USDC)


def test_the_matching_offer_is_chosen():
    assert pick([offer()]) == 0
    assert pick([offer(network="eip155:8453"), offer()]) == 1


def test_a_cheaper_price_than_approved_is_fine():
    assert pick([offer(amount="500")]) == 0


@pytest.mark.parametrize(
    "bad, word",
    [
        (offer(amount="1001"), "more than"),
        (offer(pay_to=ATTACKER), "payee"),
        (offer(asset=NEWS_SELLER), "asset"),
        (offer(network="solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"), "network"),
        (offer(scheme="upto"), "scheme"),
        (offer(amount="-5"), "amount"),
        (offer(amount="1e3"), "amount"),
    ],
)
def test_an_offer_that_differs_from_what_was_approved_is_refused(bad, word):
    with pytest.raises(PaymentRefused) as info:
        pick([bad])
    assert word in str(info.value)


def test_no_offers_is_refused():
    with pytest.raises(PaymentRefused):
        pick([])
