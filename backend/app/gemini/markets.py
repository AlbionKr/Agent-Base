"""Gemini Prediction Markets discovery (public REST, no auth).

Endpoint: GET /v1/prediction-markets/events

Rules this module enforces:
- `instrumentSymbol` is carried through exactly as Gemini returns it. It is the
  only identifier used later for WebSocket subscriptions and orders.
- Prices and quantities are decimal strings on the wire and `Decimal` in memory.
  A float can never enter: JSON numbers are parsed straight to `Decimal`, and a
  Python float handed to a model is a validation error.
- Market titles and descriptions are third-party text. `description_text` is
  UNTRUSTED content; the agent runtime registers it in the Context Ledger.
"""

from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic.alias_generators import to_camel

from app.decimals import OptionalDecimal

PRODUCTION_URL = "https://api.gemini.com"
SANDBOX_URL = "https://api.sandbox.gemini.com"
EVENTS_PATH = "/v1/prediction-markets/events"

# A 5-minute market that closes inside this margin is skipped: an order sent now
# would likely reach a market that is no longer open.
CLOSE_MARGIN_SECONDS = 120


def still_open(expiry: datetime | None, now: datetime, margin_seconds: int = CLOSE_MARGIN_SECONDS) -> bool:
    """True if a market with this expiry can still take an order (None: no expiry given)."""
    if expiry is None:
        return True
    return (expiry - now).total_seconds() > margin_seconds


def market_data_label(base_url: str) -> str:
    """What the UI calls the market data source. Orders only ever go to the sandbox,
    so pointing market data at the sandbox makes the agent see the books it trades on."""
    if base_url.rstrip("/") == SANDBOX_URL:
        return "SANDBOX market data (the books orders go to)"
    return "live public API (read-only)"


class GeminiAPIError(RuntimeError):
    """A non-200 response from Gemini. Never swallowed."""

    def __init__(self, status_code: int, body: str, url: str):
        self.status_code = status_code
        self.body = body
        self.url = url
        super().__init__(f"Gemini returned HTTP {status_code} for {url}: {body[:200]}")


DecimalStr = OptionalDecimal


def flatten_rich_text(node: Any) -> str:
    """Flatten Gemini's rich-text document tree into plain text."""
    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif isinstance(item, dict):
            value = item.get("value")
            if isinstance(value, str):
                parts.append(value)
            walk(item.get("content"))

    walk(node)
    return " ".join(" ".join(parts).split())


class _Model(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="ignore", frozen=True
    )


class Prices(_Model):
    buy_yes: DecimalStr = None
    buy_no: DecimalStr = None
    sell_yes: DecimalStr = None
    sell_no: DecimalStr = None
    best_bid: DecimalStr = None
    best_ask: DecimalStr = None
    last_trade_price: DecimalStr = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_sides(cls, data: Any) -> Any:
        if data is None:
            return {}
        if isinstance(data, dict) and ("buy" in data or "sell" in data):
            buy = data.get("buy") or {}
            sell = data.get("sell") or {}
            return {
                "buy_yes": buy.get("yes"),
                "buy_no": buy.get("no"),
                "sell_yes": sell.get("yes"),
                "sell_no": sell.get("no"),
                "best_bid": data.get("bestBid"),
                "best_ask": data.get("bestAsk"),
                "last_trade_price": data.get("lastTradePrice"),
            }
        return data


class Contract(_Model):
    id: str
    instrument_symbol: str
    label: str = ""
    ticker: str = ""
    status: str = ""
    market_state: str | None = None
    expiry_date: datetime | None = None
    prices: Prices = Field(default_factory=Prices)
    quantity_increment: DecimalStr = None
    quantity_minimum: DecimalStr = None
    price_increment: DecimalStr = None
    price_minimum: DecimalStr = None
    description_text: str = ""

    @model_validator(mode="before")
    @classmethod
    def _flatten_description(cls, data: Any) -> Any:
        if isinstance(data, dict) and "description" in data:
            data = {**data, "descriptionText": flatten_rich_text(data["description"])}
        return data


class Event(_Model):
    id: str
    ticker: str
    title: str = ""
    slug: str = ""
    type: str = ""
    category: str = ""
    status: str = ""
    expiry_date: datetime | None = None
    contracts: list[Contract] = Field(default_factory=list)


class EventsPage(_Model):
    events: list[Event]
    limit: int
    offset: int
    total: int

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "EventsPage":
        pagination = raw.get("pagination") or {}
        events = [Event.model_validate(item) for item in raw.get("data") or []]
        return cls(
            events=events,
            limit=pagination.get("limit", len(events)),
            offset=pagination.get("offset", 0),
            total=pagination.get("total", len(events)),
        )


class GeminiMarketsClient:
    """Async client for the public prediction-markets discovery endpoint."""

    def __init__(
        self,
        base_url: str = PRODUCTION_URL,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 15.0,
    ):
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, timeout=timeout
        )

    async def __aenter__(self) -> "GeminiMarketsClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def list_events(
        self,
        *,
        status: str = "active",
        category: str | None = None,
        search: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> EventsPage:
        params: dict[str, str | int] = {"status": status}
        if category:
            params["category"] = category
        if search:
            # Note: the sandbox answers 503 to `search` (observed 2026-11-11).
            # Use `find_events`, which filters client-side, for anything that
            # must work against both environments.
            params["search"] = search
        params["limit"] = limit
        params["offset"] = offset

        response = await self._http.get(EVENTS_PATH, params=params)
        if response.status_code != 200:
            raise GeminiAPIError(response.status_code, response.text, str(response.url))
        raw = json.loads(response.content, parse_float=Decimal)
        return EventsPage.from_api(raw)

    async def find_events(
        self,
        *,
        ticker_prefix: str,
        category: str | None = "crypto",
        status: str = "active",
        page_size: int = 200,
        max_pages: int = 10,
    ) -> list[Event]:
        """All events whose ticker starts with `ticker_prefix`, soonest first.

        Pages through the category and filters client-side so it behaves the
        same on production and sandbox.
        """
        matches: list[Event] = []
        offset = 0
        for _ in range(max_pages):
            page = await self.list_events(
                status=status, category=category, limit=page_size, offset=offset
            )
            matches.extend(e for e in page.events if e.ticker.startswith(ticker_prefix))
            offset += len(page.events)
            if not page.events or offset >= page.total:
                break
        matches.sort(key=lambda e: (e.expiry_date is None, e.expiry_date, e.ticker))
        return matches
