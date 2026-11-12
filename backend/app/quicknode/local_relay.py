"""LOCAL RELAY: a stand-in for the QuickNode stream when running on a local validator.

QuickNode Streams cannot reach a validator on this machine. This relay watches
the same token accounts over RPC, builds the same payload the QuickNode filter
emits (`payload.stream_item`), signs it the way QuickNode signs (nonce +
timestamp + body, HMAC-SHA256) and POSTs it to the real webhook endpoint, so the
full verification and anomaly path runs.

It is not QuickNode. The UI and /config label it `LOCAL RELAY (not QuickNode)`,
and it refuses to run against any RPC that is not on this machine.
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from typing import Callable, Iterable
from urllib.parse import urlsplit

import httpx
from solders.pubkey import Pubkey
from solders.signature import Signature

from app.chain.client import SolanaClient
from app.quicknode.payload import stream_batch, stream_item
from app.quicknode.verify import expected_signature

log = logging.getLogger("agentbase.relay")


class LocalStreamRelay:
    def __init__(
        self,
        *,
        chain: SolanaClient,
        token_accounts: Callable[[], Iterable[Pubkey]],
        webhook_url: str,
        secret: str,
        interval_seconds: float = 1.0,
    ):
        if urlsplit(chain.rpc_url).hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("the local relay only runs against a local validator")
        self._chain = chain
        self._accounts = token_accounts
        self._url = webhook_url
        self._secret = secret
        self._interval = interval_seconds
        self._seen: set[str] = set()
        self._primed: set[Pubkey] = set()

    async def poll_once(self, http: httpx.AsyncClient) -> int:
        """Deliver transactions not seen before. Returns how many were delivered."""
        items = []
        for account in list(self._accounts()):
            recent = (await self._chain.rpc.get_signatures_for_address(account, limit=25)).value
            fresh = [entry for entry in reversed(recent) if str(entry.signature) not in self._seen]
            self._seen.update(str(entry.signature) for entry in recent)
            if account not in self._primed:
                # History from before the relay started watching is not replayed.
                self._primed.add(account)
                continue
            for entry in fresh:
                item = await self._fetch(entry.signature)
                if item is not None:
                    items.append(item)
        if items:
            await self._deliver(http, items)
        return len(items)

    async def _fetch(self, signature: Signature) -> dict | None:
        response = await self._chain.rpc.get_transaction(
            signature, encoding="json", max_supported_transaction_version=0
        )
        result = json.loads(response.to_json()).get("result")
        if not result:
            return None
        return stream_item(
            {
                "transaction": result["transaction"],
                "meta": result["meta"],
                "slot": result.get("slot"),
                "blockTime": result.get("blockTime"),
            }
        )

    async def _deliver(self, http: httpx.AsyncClient, items: list[dict]) -> None:
        body = json.dumps(stream_batch(items)).encode()
        nonce, timestamp = secrets.token_hex(16), str(int(time.time()))
        response = await http.post(
            self._url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "X-QN-Nonce": nonce,
                "X-QN-Timestamp": timestamp,
                "X-QN-Signature": expected_signature(self._secret, nonce, timestamp, body),
            },
        )
        if response.status_code != 200:
            log.warning("webhook refused the relay's delivery: %s %s", response.status_code, response.text[:200])

    async def run_forever(self) -> None:
        async with httpx.AsyncClient(timeout=10) as http:
            while True:
                try:
                    await self.poll_once(http)
                except Exception:
                    log.exception("local relay poll failed")
                await asyncio.sleep(self._interval)
