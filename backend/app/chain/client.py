"""Thin async Solana RPC wrapper: send-and-confirm, balances, account data."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from solana.exceptions import SolanaRpcException
from solana.rpc.async_api import AsyncClient
from solana.rpc.commitment import Confirmed
from solders.instruction import Instruction
from solders.keypair import Keypair
from solders.message import MessageV0
from solders.pubkey import Pubkey
from solders.transaction import VersionedTransaction


class ChainError(RuntimeError):
    """A transaction was rejected. `logs` holds the program logs when available."""

    def __init__(self, message: str, logs: Sequence[str] = ()):
        self.logs = list(logs)
        super().__init__(message)


class RpcUnavailable(RuntimeError):
    """The RPC did not answer (rate limit, network). Deliberately NOT a ChainError:
    "the chain refused this" must never be inferred from a request that got no answer."""


# Public RPCs rate-limit. Waits between retries of a call that got no answer.
RETRY_DELAYS = (0.5, 1.5, 4.0)


def load_keypair(path: str | Path) -> Keypair:
    return Keypair.from_bytes(bytes(json.loads(Path(path).read_text())))


def save_new_keypair(path: str | Path) -> Keypair:
    """Create a keypair file (Solana CLI format). Refuses to overwrite."""
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    keypair = Keypair()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(list(bytes(keypair))))
    return keypair


def explorer_tx(signature: str, cluster: str = "devnet") -> str:
    return f"https://explorer.solana.com/tx/{signature}?cluster={cluster}"


def explorer_address(address: Pubkey | str, cluster: str = "devnet") -> str:
    return f"https://explorer.solana.com/address/{address}?cluster={cluster}"


class SolanaClient:
    def __init__(self, rpc_url: str, retry_delays: Sequence[float] = RETRY_DELAYS):
        self.rpc_url = rpc_url
        self.rpc = AsyncClient(rpc_url, commitment=Confirmed)
        self._retry_delays = tuple(retry_delays)

    async def _call(self, what: str, call: Callable[[], Awaitable[Any]]) -> Any:
        """Run one RPC call, retrying only when the RPC gave no answer. An answer that
        says no (a rejected transaction) is raised straight through, untouched."""
        for delay in (*self._retry_delays, None):
            try:
                return await call()
            except SolanaRpcException as exc:
                if delay is None:
                    raise RpcUnavailable(f"{what}: no answer from {self.rpc_url} ({_describe(exc)})") from exc
                await asyncio.sleep(delay)

    async def __aenter__(self) -> "SolanaClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.rpc.close()

    async def send(
        self,
        instructions: Sequence[Instruction],
        payer: Keypair,
        signers: Sequence[Keypair] = (),
    ) -> str:
        """Sign, send and confirm. Returns the signature. Raises ChainError on failure."""
        latest = (await self._call("get_latest_blockhash", self.rpc.get_latest_blockhash)).value
        message = MessageV0.try_compile(
            payer.pubkey(), list(instructions), [], latest.blockhash
        )
        by_pubkey = {kp.pubkey(): kp for kp in (payer, *signers)}
        required = message.account_keys[: message.header.num_required_signatures]
        try:
            ordered = [by_pubkey[key] for key in required]
        except KeyError as exc:
            raise ChainError(f"missing signer for {exc.args[0]}") from exc
        tx = VersionedTransaction(message, ordered)

        try:
            # the same signed transaction each time, so a retry cannot send it twice
            signature = (await self._call("send_transaction", lambda: self.rpc.send_transaction(tx))).value
        except RpcUnavailable:
            raise
        except Exception as exc:  # preflight simulation failed
            raise ChainError(_describe(exc), _logs_of(exc)) from exc

        statuses = await self._call(
            "confirm_transaction",
            lambda: self.rpc.confirm_transaction(
                signature,
                commitment=Confirmed,
                last_valid_block_height=latest.last_valid_block_height,
            ),
        )
        status = statuses.value[0] if statuses.value else None
        if status is None:
            raise ChainError(f"transaction {signature} was not confirmed")
        if status.err is not None:
            raise ChainError(f"transaction {signature} failed: {status.err}")
        return str(signature)

    async def account_data(self, address: Pubkey) -> bytes | None:
        value = (await self._call("get_account_info", lambda: self.rpc.get_account_info(address))).value
        return None if value is None else bytes(value.data)

    async def sol_balance(self, address: Pubkey) -> int:
        """Lamports."""
        return (await self._call("get_balance", lambda: self.rpc.get_balance(address))).value

    async def token_balance(self, token_account: Pubkey) -> int | None:
        """Base units, or None if the token account does not exist."""
        if await self.account_data(token_account) is None:
            return None
        return int(
            (await self._call("get_token_account_balance", lambda: self.rpc.get_token_account_balance(token_account))).value.amount
        )


def _logs_of(exc: Exception) -> list[str]:
    for arg in getattr(exc, "args", ()):
        data = getattr(arg, "data", None)
        logs = getattr(data, "logs", None)
        if logs:
            return list(logs)
    return []


def _describe(exc: Exception) -> str:
    logs = _logs_of(exc)
    for line in logs:
        if "Error Code:" in line or "Error Message:" in line:
            return line.replace("Program log: ", "")
    for arg in getattr(exc, "args", ()):
        message = getattr(arg, "message", None)
        if message:
            return str(message)
    return str(exc) or type(exc).__name__
