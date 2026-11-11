"""Tests for the Gemini Prediction Markets discovery client.

The fixture is a real (trimmed) response from
GET https://api.gemini.com/v1/prediction-markets/events captured on 2026-11-11.
"""

import asyncio
import json
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from app.gemini.markets import (
    Contract,
    EventsPage,
    GeminiAPIError,
    GeminiMarketsClient,
    flatten_rich_text,
)

FIXTURE = Path(__file__).parent / "fixtures" / "events_page.json"


def load_raw() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"), parse_float=Decimal)


def client_with(handler) -> GeminiMarketsClient:
    return GeminiMarketsClient(
        "https://example.invalid", transport=httpx.MockTransport(handler)
    )


def test_parses_real_page():
    raw = load_raw()
    page = EventsPage.from_api(raw)

    assert page.limit == 2 and page.offset == 0
    assert page.total == raw["pagination"]["total"]
    assert [e.ticker for e in page.events] == [e["ticker"] for e in raw["data"]]

    five = page.events[0]
    assert five.category == "Crypto"
    assert five.expiry_date is not None and five.expiry_date.tzinfo is not None


def test_instrument_symbol_is_passed_through_untouched():
    raw = load_raw()
    page = EventsPage.from_api(raw)
    for event, raw_event in zip(page.events, raw["data"]):
        for contract, raw_contract in zip(event.contracts, raw_event["contracts"]):
            assert contract.instrument_symbol == raw_contract["instrumentSymbol"]


def test_prices_and_increments_are_decimals_not_floats():
    raw = load_raw()
    contract = EventsPage.from_api(raw).events[0].contracts[0]
    raw_prices = raw["data"][0]["contracts"][0]["prices"]

    assert isinstance(contract.prices.best_bid, Decimal)
    assert contract.prices.best_bid == Decimal(raw_prices["bestBid"])
    assert contract.prices.best_ask == Decimal(raw_prices["bestAsk"])
    assert contract.prices.buy_yes == Decimal(raw_prices["buy"]["yes"])
    assert contract.prices.sell_no == Decimal(raw_prices["sell"]["no"])
    assert contract.quantity_minimum == Decimal("0.01")
    assert contract.price_increment == Decimal("0.01")
    # Exactness: 0.1 + 0.2 style drift must be impossible.
    assert str(contract.prices.best_bid) == raw_prices["bestBid"]


def test_empty_order_book_parses_to_none():
    """The sandbox returns prices == {"buy": {}, "sell": {}}."""
    raw = load_raw()["data"][0]["contracts"][0]
    raw["prices"] = {"buy": {}, "sell": {}}
    contract = Contract.model_validate(raw)
    assert contract.prices.best_bid is None
    assert contract.prices.best_ask is None
    assert contract.prices.buy_yes is None


@pytest.mark.parametrize("bad", [0.62, True])
def test_float_and_bool_prices_are_rejected(bad):
    raw = load_raw()["data"][0]["contracts"][0]
    raw["prices"]["bestBid"] = bad
    with pytest.raises(ValidationError):
        Contract.model_validate(raw)


def test_garbage_price_string_is_rejected():
    raw = load_raw()["data"][0]["contracts"][0]
    raw["prices"]["bestAsk"] = "0.6.2"
    with pytest.raises(ValidationError):
        Contract.model_validate(raw)


def test_description_is_flattened_to_plain_text():
    raw = load_raw()["data"][0]["contracts"][0]
    contract = Contract.model_validate(raw)
    assert "resolve" in contract.description_text
    assert "{" not in contract.description_text


def test_flatten_rich_text_handles_nesting_and_junk():
    doc = {
        "nodeType": "document",
        "content": [
            {"nodeType": "paragraph", "content": [{"nodeType": "text", "value": "a"}]},
            {"nodeType": "text", "value": "b"},
        ],
    }
    assert flatten_rich_text(doc) == "a b"
    assert flatten_rich_text(None) == ""
    assert flatten_rich_text("plain") == "plain"


def test_list_events_sends_query_and_parses():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["params"] = dict(request.url.params)
        return httpx.Response(200, content=FIXTURE.read_bytes())

    async def run():
        async with client_with(handler) as client:
            return await client.list_events(category="crypto", limit=2)

    page = asyncio.run(run())
    assert seen["path"] == "/v1/prediction-markets/events"
    assert seen["params"] == {
        "status": "active",
        "category": "crypto",
        "limit": "2",
        "offset": "0",
    }
    assert len(page.events) == 2


def test_json_numbers_from_the_wire_never_become_floats():
    """If Gemini ever sends a bare JSON number, it must still arrive exact."""
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body = json.dumps(raw).replace('"bestBid": "0.97"', '"bestBid": 0.97', 1)
    assert '"bestBid": 0.97' in body

    def handler(request):
        return httpx.Response(200, content=body.encode())

    async def run():
        async with client_with(handler) as client:
            return await client.list_events()

    page = asyncio.run(run())
    assert page.events[0].contracts[0].prices.best_bid == Decimal("0.97")


def test_non_200_raises_with_status_code():
    def handler(request):
        return httpx.Response(503, text="Service Unavailable")

    async def run():
        async with client_with(handler) as client:
            await client.list_events()

    with pytest.raises(GeminiAPIError) as exc:
        asyncio.run(run())
    assert exc.value.status_code == 503


def test_find_events_filters_by_ticker_prefix_across_pages():
    raw = json.loads(FIXTURE.read_text(encoding="utf-8"))
    pages = [
        {"data": [raw["data"][1]], "pagination": {"limit": 1, "offset": 0, "total": 2}},
        {"data": [raw["data"][0]], "pagination": {"limit": 1, "offset": 1, "total": 2}},
    ]
    offsets = []

    def handler(request):
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        return httpx.Response(200, json=pages[offset])

    async def run():
        async with client_with(handler) as client:
            return await client.find_events(ticker_prefix="BTC05M", page_size=1)

    events = asyncio.run(run())
    assert offsets == [0, 1]
    assert [e.ticker for e in events] == [raw["data"][0]["ticker"]]
    assert all(e.ticker.startswith("BTC05M") for e in events)
