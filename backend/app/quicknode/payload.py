"""The stream payload contract, in one place.

`stream_item` projects a Solana transaction (the shape `getTransaction` and
`getBlock` return with encoding "json") to what our QuickNode filter emits and
`anomaly.parse_payload` reads. The JavaScript filter in `quicknode/filter.js`
does the same projection; `scripts/local_stream_relay.py` uses this one.
"""

from __future__ import annotations

from typing import Any


def stream_item(tx: dict[str, Any], *, slot: int | None = None, block_time: int | None = None) -> dict:
    """`tx` is {"transaction": {"signatures": [...]}, "meta": {...}} (plus slot/blockTime if present)."""
    meta = tx.get("meta") or {}
    return {
        "signature": tx["transaction"]["signatures"][0],
        "slot": tx.get("slot", slot),
        "blockTime": tx.get("blockTime", block_time),
        "err": meta.get("err"),
        "preTokenBalances": meta.get("preTokenBalances") or [],
        "postTokenBalances": meta.get("postTokenBalances") or [],
    }


def stream_batch(items: list[dict]) -> dict:
    return {"transactions": items}
