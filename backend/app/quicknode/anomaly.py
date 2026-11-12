"""Turn a stream delivery into USDC outflows and decide which are anomalies.

Pure functions: no clock, no network, no database.

Payload contract (what our stream filter emits; the balances are copied verbatim
from Solana's transaction meta):

    {"transactions": [
        {"signature": "...", "slot": 1, "blockTime": 1700000000, "err": null,
         "preTokenBalances":  [{"owner": "...", "mint": "...", "uiTokenAmount": {"amount": "1000"}}],
         "postTokenBalances": [...]}
    ]}

A list of such batches is accepted too. Anything else is an error: we do not guess.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Collection, Sequence

from app.chain.units import units_to_usdc

UNEXPECTED_OUTFLOW = "UNEXPECTED_OUTFLOW"
UNKNOWN_DESTINATION = "UNKNOWN_DESTINATION"
OUTFLOW_BURST = "OUTFLOW_BURST"


class PayloadError(ValueError):
    pass


@dataclass(frozen=True)
class ObservedTx:
    signature: str
    slot: int | None
    block_time: datetime | None
    failed: bool
    # ((owner, mint), post - pre) summed over that owner's token accounts
    deltas: tuple[tuple[tuple[str, str], int], ...]


@dataclass(frozen=True)
class Outflow:
    signature: str
    wallet: str
    amount_units: int
    # Every owner whose balance of the mint rose in this transaction, sorted.
    destinations: tuple[tuple[str, int], ...]
    at: datetime | None


@dataclass(frozen=True)
class ExpectedSpend:
    """An allowed payment that has not yet been seen onchain."""

    action_id: str
    destination: str
    amount_units: int


@dataclass(frozen=True)
class Anomaly:
    rule_id: str
    reason: str


@dataclass(frozen=True)
class Assessment:
    matched_action_id: str | None
    anomalies: tuple[Anomaly, ...]


# -------------------------------------------------------------------- parsing


def parse_payload(payload: Any) -> list[ObservedTx]:
    batches = payload if isinstance(payload, list) else [payload]
    parsed: list[ObservedTx] = []
    for batch in batches:
        if not isinstance(batch, dict) or not isinstance(batch.get("transactions"), list):
            raise PayloadError('expected {"transactions": [...]} from the stream filter')
        parsed.extend(_parse_tx(raw) for raw in batch["transactions"])
    return parsed


def _parse_tx(raw: Any) -> ObservedTx:
    if not isinstance(raw, dict):
        raise PayloadError("a transaction must be an object")
    signature = raw.get("signature")
    if not isinstance(signature, str) or not signature:
        raise PayloadError("transaction has no signature")
    slot = raw.get("slot")
    if slot is not None and (isinstance(slot, bool) or not isinstance(slot, int)):
        raise PayloadError(f"{signature}: slot must be an integer")
    block_time = raw.get("blockTime")
    if block_time is not None and (isinstance(block_time, bool) or not isinstance(block_time, int)):
        raise PayloadError(f"{signature}: blockTime must be an integer")

    deltas: dict[tuple[str, str], int] = defaultdict(int)
    for field, sign in (("preTokenBalances", -1), ("postTokenBalances", 1)):
        balances = raw.get(field, [])
        if not isinstance(balances, list):
            raise PayloadError(f"{signature}: {field} must be a list")
        for entry in balances:
            owner, mint, amount = _parse_balance(signature, field, entry)
            deltas[(owner, mint)] += sign * amount

    return ObservedTx(
        signature=signature,
        slot=slot,
        block_time=datetime.fromtimestamp(block_time, timezone.utc) if block_time is not None else None,
        failed=raw.get("err") is not None,
        deltas=tuple(sorted(deltas.items())),
    )


def _parse_balance(signature: str, field: str, entry: Any) -> tuple[str, str, int]:
    if not isinstance(entry, dict):
        raise PayloadError(f"{signature}: {field} entry must be an object")
    owner, mint = entry.get("owner"), entry.get("mint")
    amount = (entry.get("uiTokenAmount") or {}).get("amount") if isinstance(
        entry.get("uiTokenAmount"), dict
    ) else None
    if not isinstance(owner, str) or not isinstance(mint, str) or not owner or not mint:
        raise PayloadError(f"{signature}: {field} entry needs owner and mint")
    if not isinstance(amount, str) or not amount.isascii() or not amount.isdigit():
        raise PayloadError(f"{signature}: {field} amount must be a string of base units")
    return owner, mint, int(amount)


# ------------------------------------------------------------------- outflows


def usdc_outflows(tx: ObservedTx, *, mint: str, watched: Collection[str]) -> list[Outflow]:
    """USDC that left a watched wallet in this transaction."""
    if tx.failed:
        return []
    net = {owner: delta for (owner, token), delta in tx.deltas if token == mint and delta != 0}
    receivers = tuple(sorted((owner, delta) for owner, delta in net.items() if delta > 0))
    return [
        Outflow(
            signature=tx.signature,
            wallet=owner,
            amount_units=-delta,
            destinations=tuple(r for r in receivers if r[0] != owner),
            at=tx.block_time,
        )
        for owner, delta in sorted(net.items())
        if delta < 0 and owner in watched
    ]


# ----------------------------------------------------------------- assessment


def _short(address: str) -> str:
    return f"{address[:4]}…{address[-4:]}" if len(address) > 10 else address


def assess(
    outflow: Outflow,
    *,
    expected: Sequence[ExpectedSpend],
    allowed_destinations: Collection[str],
    recent_outflows: int,
    max_outflows_per_minute: int,
) -> Assessment:
    """`expected` is oldest first; `recent_outflows` counts the last 60 s before this one."""
    amount = f"${units_to_usdc(outflow.amount_units)} USDC"
    anomalies: list[Anomaly] = []

    # A match must account for all the money that left, not just part of it.
    matched = next(
        (
            spend.action_id
            for spend in expected
            if spend.amount_units == outflow.amount_units
            and (spend.destination, spend.amount_units) in outflow.destinations
        ),
        None,
    )
    if matched is None:
        anomalies.append(
            Anomaly(
                UNEXPECTED_OUTFLOW,
                f"{amount} left wallet {_short(outflow.wallet)} and no allowed action accounts for it.",
            )
        )

    strangers = [dest for dest, _ in outflow.destinations if dest not in allowed_destinations]
    if strangers:
        anomalies.append(
            Anomaly(
                UNKNOWN_DESTINATION,
                f"{amount} left wallet {_short(outflow.wallet)} toward "
                f"{', '.join(_short(s) for s in strangers)}, which is not in the mandate.",
            )
        )

    if recent_outflows + 1 > max_outflows_per_minute:
        anomalies.append(
            Anomaly(
                OUTFLOW_BURST,
                f"{recent_outflows + 1} outflows from wallet {_short(outflow.wallet)} within a "
                f"minute; the limit is {max_outflows_per_minute}.",
            )
        )

    return Assessment(matched_action_id=matched, anomalies=tuple(anomalies))
