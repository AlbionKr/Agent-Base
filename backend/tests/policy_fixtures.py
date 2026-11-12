"""Shared, fully deterministic fixtures for the policy tests."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from solders.keypair import Keypair

from app.policy.ledger import ContextLedger, LedgerSnapshot
from app.policy.models import (
    GeminiOrderAction,
    PastAction,
    Policy,
    PolicyState,
    TransferAction,
    Verdict,
    X402PaymentAction,
)

NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=timezone.utc)


def key(n: int) -> Keypair:
    return Keypair.from_seed(bytes([n]) * 32)


def addr(n: int) -> str:
    return str(key(n).pubkey())


OWNER = key(1)
NEWS_SELLER = addr(2)
PARTNER = addr(3)
ATTACKER = addr(66)
NEWS_ORIGIN = "http://127.0.0.1:4021"
NEWS_URL = f"{NEWS_ORIGIN}/news?topic=btc"
BTC_SYMBOL = "GEMI-BTC05M2611141205-UP"


def make_policy(**overrides) -> Policy:
    base = dict(
        agent_id="btc-trader",
        owner=str(OWNER.pubkey()),
        allowed_action_types=frozenset({"x402_payment", "gemini_order", "transfer"}),
        allowed_markets=("GEMI-BTC05M*", "GEMI-FED-DEC26-CUT25"),
        allowed_payees=frozenset({NEWS_SELLER, PARTNER}),
        allowed_endpoints=frozenset({NEWS_ORIGIN}),
        max_per_action_usd=Decimal("10"),
        max_per_day_usd=Decimal("25"),
        approval_threshold_usd=Decimal("5"),
        max_actions_per_minute=6,
        expires_at=NOW + timedelta(days=7),
        mandate_hash=bytes(32),
    )
    base.update(overrides)
    return Policy(**base)


def state(*history: PastAction, now: datetime = NOW) -> PolicyState:
    return PolicyState(now=now, history=tuple(history))


def past(
    seconds_ago: float,
    amount: str = "1",
    dest: str = NEWS_SELLER,
    *,
    executed: bool = True,
    verdict: Verdict = Verdict.ALLOW,
    action_type: str = "x402_payment",
) -> PastAction:
    return PastAction(
        at=NOW - timedelta(seconds=seconds_ago),
        action_type=action_type,
        destination=dest,
        amount_usd=Decimal(amount),
        verdict=verdict,
        executed=executed,
    )


def news_payment(amount: str = "0.001", payee: str = NEWS_SELLER, url: str = NEWS_URL):
    return X402PaymentAction(url=url, payee=payee, amount=Decimal(amount))


def order(quantity: str = "4", price: str = "0.50", symbol: str = BTC_SYMBOL, **kw):
    return GeminiOrderAction(
        symbol=symbol,
        side=kw.pop("side", "buy"),
        outcome=kw.pop("outcome", "yes"),
        quantity=Decimal(quantity),
        price=Decimal(price),
        **kw,
    )


def transfer(amount: str = "1", payee: str = PARTNER):
    return TransferAction(payee=payee, amount=Decimal(amount))


def ledger(*items: tuple[str, str, str]) -> LedgerSnapshot:
    """ledger(("x", "42", "text"), ...)"""
    book = ContextLedger()
    for source, item_id, text in items:
        book.register(source, item_id, text)
    return book.snapshot()
