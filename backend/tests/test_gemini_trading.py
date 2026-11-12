"""Gemini order construction, signing and the hard cap (no network)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from decimal import Decimal

import pytest

from app.gemini.trading import (
    GeminiTrader,
    OrderRefused,
    auth_headers,
    check_order_cap,
    interpret,
    order_message,
)
from app.policy.models import GeminiOrderAction

SYMBOL = "GEMI-BTC05M2611141205-UP"


def order(quantity="2", price="0.40", **kw) -> GeminiOrderAction:
    return GeminiOrderAction(
        action_id="act-77", symbol=kw.get("symbol", SYMBOL), side=kw.get("side", "buy"),
        outcome=kw.get("outcome", "yes"), quantity=Decimal(quantity), price=Decimal(price),
    )


def test_auth_headers_follow_geminis_documented_scheme():
    headers = auth_headers("key-1", "secret-1", 1_800_000_000)
    payload = base64.b64encode(b"1800000000").decode()
    assert headers["X-GEMINI-APIKEY"] == "key-1"
    assert headers["X-GEMINI-NONCE"] == "1800000000"
    assert headers["X-GEMINI-PAYLOAD"] == payload
    assert headers["X-GEMINI-SIGNATURE"] == hmac.new(b"secret-1", payload.encode(), hashlib.sha384).hexdigest()


def test_the_order_message_keeps_the_symbol_and_decimal_strings_exactly():
    message = order_message(order("0.01", "0.07", symbol="GEMI-FEDJAN26-DN25", outcome="no", side="sell"), "r1")
    assert message == {
        "id": "r1",
        "method": "order.place",
        "params": {
            "symbol": "GEMI-FEDJAN26-DN25", "side": "SELL", "type": "LIMIT", "timeInForce": "GTC",
            "price": "0.07", "quantity": "0.01", "eventOutcome": "NO", "clientOrderId": "act-77",
        },
    }
    assert all(not isinstance(v, float) for v in message["params"].values())


def test_the_hard_cap_refuses_an_order_the_policy_might_have_allowed():
    assert check_order_cap(order("2", "0.50"), Decimal("1.00")) == Decimal("1.00")
    with pytest.raises(OrderRefused) as info:
        check_order_cap(order("2.01", "0.50"), Decimal("1.00"))
    assert "hard cap" in str(info.value)


def test_only_the_sandbox_is_allowed_and_keys_are_required():
    with pytest.raises(ValueError):
        GeminiTrader(api_key="k", api_secret="s", max_notional_usd=Decimal("1"), environment="live")
    with pytest.raises(ValueError):
        GeminiTrader(api_key="", api_secret="", max_notional_usd=Decimal("1"))


def test_incoming_messages_are_matched_to_our_order_only():
    assert interpret({"id": "other", "result": {}}, "r1", "act-77") is None
    assert interpret({"s": SYMBOL, "b": "0.4", "a": "0.5"}, "r1", "act-77") is None
    assert interpret({"id": "r1", "status": 200, "result": {"ok": True}}, "r1", "act-77")["accepted"] is True
    rejected = interpret({"id": "r1", "status": 400, "error": {"code": -2010, "msg": "bad price"}}, "r1", "act-77")
    assert rejected["accepted"] is False and "-2010" in rejected["error"]
    assert interpret({"c": "someone-else", "X": "NEW", "i": "1"}, "r1", "act-77") is None
    update = interpret({"c": "act-77", "X": "OPEN", "i": "7379"}, "r1", "act-77")
    assert update == {"accepted": True, "status": "OPEN", "order_id": "7379"}
    assert interpret({"c": "act-77", "X": "REJECTED"}, "r1", "act-77")["accepted"] is False
