"""Thin async Solana RPC wrapper: send-and-confirm, balances, account data."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence

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
    def __init__(self, rpc_url: str):
        self.rpc_url = rpc_url
        self.rpc = AsyncClient(rpc_url, commitment=Confirmed)

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
        latest = (await self.rpc.get_latest_blockhash()).value
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
            signature = (await self.rpc.send_transaction(tx)).value
        except Exception as exc:  # preflight simulation failed
            raise ChainError(_describe(exc), _logs_of(exc)) from exc

        statuses = await self.rpc.confirm_transaction(
            signature,
            commitment=Confirmed,
            last_valid_block_height=latest.last_valid_block_height,
        )
        status = statuses.value[0] if statuses.value else None
        if status is None:
            raise ChainError(f"transaction {signature} was not confirmed")
        if status.err is not None:
            raise ChainError(f"transaction {signature} failed: {status.err}")
        return str(signature)

    async def account_data(self, address: Pubkey) -> bytes | None:
        value = (await self.rpc.get_account_info(address)).value
        return None if value is None else bytes(value.data)

    async def sol_balance(self, address: Pubkey) -> int:
        """Lamports."""
        return (await self.rpc.get_balance(address)).value

    async def token_balance(self, token_account: Pubkey) -> int | None:
        """Base units, or None if the token account does not exist."""
        if await self.account_data(token_account) is None:
            return None
        return int((await self.rpc.get_token_account_balance(token_account)).value.amount)


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
