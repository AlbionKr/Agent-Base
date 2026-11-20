"""Place prediction-market orders on Gemini over WebSocket (`order.place`).

SANDBOX ONLY. This build refuses to send an order anywhere but Gemini's sandbox,
which trades test funds. The UI labels them `SANDBOX`.

A hard cap on order notional is enforced here, in code, independently of the
policy engine: even an order the gate allowed is refused if quantity x price
exceeds GEMINI_MAX_ORDER_NOTIONAL_USD.

Protocol (developer.gemini.com/prediction-markets/getting-started):
  * auth headers on the handshake: nonce = unix seconds, payload = base64(nonce),
    signature = hex(HMAC-SHA384(secret, payload))
  * {"id", "method": "order.place", "params": {symbol, side, type, timeInForce,
    price, quantity, eventOutcome, clientOrderId}}
  * order updates arrive on the `orders@account` stream (X = status, i = order id)

Verified live on 2026-11-20: with an account-scoped sandbox key, `order.place` was
accepted and the order appeared as NEW/OPEN on the sandbox book (a $0.01 order on a
BTC monthly contract and a 0.5 @ $0.40 order on a BTC 5-minute contract).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any

from app.policy.models import Action, GeminiOrderAction

SANDBOX_WS = "wss://ws.sandbox.gemini.com"
ORDER_UPDATE_STATUSES = {"NEW", "OPEN", "FILLED", "PARTIALLY_FILLED", "REJECTED", "CANCELED", "CANCELLED"}


class OrderRefused(Exception):
    pass


def auth_headers(api_key: str, api_secret: str, unix_seconds: int) -> dict[str, str]:
    nonce = str(unix_seconds)
    payload = base64.b64encode(nonce.encode()).decode()
    signature = hmac.new(api_secret.encode(), payload.encode(), hashlib.sha384).hexdigest()
    return {
        "X-GEMINI-APIKEY": api_key,
        "X-GEMINI-NONCE": nonce,
        "X-GEMINI-PAYLOAD": payload,
        "X-GEMINI-SIGNATURE": signature,
    }


def check_order_cap(action: GeminiOrderAction, max_notional_usd: Decimal) -> Decimal:
    """Returns the notional. Raises OrderRefused above the hard cap."""
    notional = action.quantity * action.price
    if notional > max_notional_usd:
        raise OrderRefused(
            f"order notional ${notional} exceeds the hard cap of ${max_notional_usd} "
            "(GEMINI_MAX_ORDER_NOTIONAL_USD)"
        )
    return notional


def order_message(action: GeminiOrderAction, request_id: str) -> dict[str, Any]:
    """`symbol` is passed through exactly as Gemini returned it; numbers stay decimal strings."""
    return {
        "id": request_id,
        "method": "order.place",
        "params": {
            "symbol": action.symbol,
            "side": action.side.upper(),
            "type": "LIMIT",
            "timeInForce": "GTC",
            "price": str(action.price),
            "quantity": str(action.quantity),
            "eventOutcome": action.outcome.upper(),
            "clientOrderId": action.action_id,
        },
    }


def interpret(message: dict[str, Any], request_id: str, client_order_id: str) -> dict[str, Any] | None:
    """What one incoming message says about our order, or None if it is unrelated."""
    if message.get("id") == request_id:
        error = message.get("error")
        if error or (isinstance(message.get("status"), int) and message["status"] >= 400):
            return {"accepted": False, "error": json.dumps(error or message)[:500]}
        return {"accepted": True, "response": message.get("result", message)}
    if message.get("c") == client_order_id and message.get("X") in ORDER_UPDATE_STATUSES:
        accepted = message["X"] not in {"REJECTED"}
        return {"accepted": accepted, "status": message["X"], "order_id": message.get("i")}
    return None


class GeminiTrader:
    """Executor handler for `gemini_order` actions."""

    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        max_notional_usd: Decimal,
        environment: str = "sandbox",
        timeout_seconds: float = 10.0,
    ):
        if environment != "sandbox":
            raise ValueError("this build only trades on the Gemini sandbox (GEMINI_TRADING_ENV=sandbox)")
        if not api_key or not api_secret:
            raise ValueError("GEMINI_API_KEY and GEMINI_API_SECRET are required")
        self._key, self._secret = api_key, api_secret
        self._cap = max_notional_usd
        self._timeout = timeout_seconds

    async def __call__(self, agent_id: str, action: Action) -> dict[str, Any]:
        import time

        import websockets

        assert isinstance(action, GeminiOrderAction)
        notional = check_order_cap(action, self._cap)
        request_id = f"place-{action.action_id}"
        headers = auth_headers(self._key, self._secret, int(time.time()))

        detail: dict[str, Any] = {
            "venue": "gemini SANDBOX",
            "symbol": action.symbol,
            "client_order_id": action.action_id,
            "notional_usd": str(notional),
        }
        async with websockets.connect(SANDBOX_WS, additional_headers=headers) as socket:
            await socket.send(json.dumps({"id": "sub", "method": "subscribe", "params": ["orders@account"]}))
            await socket.send(json.dumps(order_message(action, request_id)))
            try:
                async with asyncio.timeout(self._timeout):
                    async for raw in socket:
                        message = json.loads(raw)
                        if not isinstance(message, dict):
                            continue
                        verdict = interpret(message, request_id, action.action_id)
                        if verdict is None:
                            continue
                        detail.update(verdict)
                        if not verdict["accepted"]:
                            raise OrderRefused(f"Gemini rejected the order: {verdict.get('error') or verdict}")
                        if "status" in verdict:  # an order event: the order exists on the sandbox book
                            return detail
            except TimeoutError:
                if detail.get("accepted"):
                    detail["note"] = "accepted; no order event within the timeout"
                    return detail
                raise OrderRefused("no response from Gemini within the timeout") from None
        raise OrderRefused("connection closed before Gemini answered")
