"""Print live Gemini prediction markets (M1 smoke test).

    python -m scripts.print_markets                      # BTC 5-minute contracts
    python -m scripts.print_markets --prefix ETH05M
    python -m scripts.print_markets --sandbox-parity     # mark symbols the sandbox also lists

Market data is read from the public production API (real prices, no auth, no
orders). Orders in this project go to the sandbox only; `--sandbox-parity`
shows which symbols exist there too.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

from app.gemini.markets import PRODUCTION_URL, SANDBOX_URL, Event, GeminiMarketsClient


def fmt(value: Decimal | None) -> str:
    return "-" if value is None else str(value)


def minutes_left(expiry: datetime | None) -> str:
    if expiry is None:
        return "-"
    seconds = (expiry - datetime.now(timezone.utc)).total_seconds()
    return f"{seconds / 60:.1f}m"


async def run(args: argparse.Namespace) -> int:
    async with GeminiMarketsClient(args.base_url) as client:
        events: list[Event] = await client.find_events(
            ticker_prefix=args.prefix, category=args.category
        )

    sandbox_symbols: set[str] | None = None
    if args.sandbox_parity:
        async with GeminiMarketsClient(SANDBOX_URL) as sandbox:
            sandbox_events = await sandbox.find_events(
                ticker_prefix=args.prefix, category=args.category
            )
        sandbox_symbols = {
            c.instrument_symbol for e in sandbox_events for c in e.contracts
        }

    print(f"source: {args.base_url}  prefix: {args.prefix}  events: {len(events)}")
    if not events:
        print("no matching active events", file=sys.stderr)
        return 1

    header = f"{'instrumentSymbol':<34} {'expires in':>10} {'bid':>6} {'ask':>6} {'last':>6} {'state':<8}"
    if sandbox_symbols is not None:
        header += " sandbox"
    print(header)
    print("-" * len(header))

    shown = 0
    for event in events:
        for contract in event.contracts:
            if shown >= args.limit:
                break
            row = (
                f"{contract.instrument_symbol:<34} "
                f"{minutes_left(contract.expiry_date or event.expiry_date):>10} "
                f"{fmt(contract.prices.best_bid):>6} "
                f"{fmt(contract.prices.best_ask):>6} "
                f"{fmt(contract.prices.last_trade_price):>6} "
                f"{(contract.market_state or '-'):<8}"
            )
            if sandbox_symbols is not None:
                row += " yes" if contract.instrument_symbol in sandbox_symbols else " no"
            print(row)
            shown += 1

    first = events[0]
    print(f"\nexample event: {first.ticker} - {first.title!r} ({first.category})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prefix", default="BTC05M", help="event ticker prefix")
    parser.add_argument("--category", default="crypto")
    parser.add_argument("--limit", type=int, default=15, help="max rows")
    parser.add_argument(
        "--base-url",
        default=os.environ.get("GEMINI_MARKET_DATA_URL", PRODUCTION_URL),
    )
    parser.add_argument("--sandbox-parity", action="store_true")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    sys.exit(main())
