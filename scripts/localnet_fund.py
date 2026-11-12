"""Fund the local validator's wallets: SOL by airdrop, test USDC by minting.

    SOLANA_RPC_URL=http://127.0.0.1:8899 python -m scripts.localnet_fund

LOCALNET ONLY. Refuses to run against any RPC that is not on this machine.
The "USDC" minted here is a local stand-in (see scripts/localnet_prepare.py).
"""

from __future__ import annotations

import asyncio
import sys
from decimal import Decimal
from urllib.parse import urlsplit

from solders.pubkey import Pubkey
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import create_idempotent_associated_token_account, mint_to
from spl.token.models import MintToParams

from app.chain import vault_ix
from app.chain.client import SolanaClient, load_keypair
from app.chain.units import units_to_usdc, usdc_to_units
from app.config import REPO_ROOT, load_chain_settings

SOL = 1_000_000_000
OWNER_USDC = usdc_to_units(Decimal("1000"))
EXTRA_KEYS = ("naive-agent", "attacker", "news-seller", "local-facilitator")


async def airdrop(chain: SolanaClient, address: Pubkey, lamports: int) -> None:
    if await chain.sol_balance(address) >= lamports // 2:
        return
    signature = (await chain.rpc.request_airdrop(address, lamports)).value
    await chain.rpc.confirm_transaction(signature)


async def main() -> int:
    settings = load_chain_settings()
    if urlsplit(settings.rpc_url).hostname not in {"127.0.0.1", "localhost"}:
        print(f"refusing: {settings.rpc_url} is not a local validator", file=sys.stderr)
        return 1
    mint = Pubkey.from_string(settings.usdc_mint)
    owner = load_keypair(settings.owner_keypair_path)
    guardian = load_keypair(settings.guardian_keypair_path)

    async with SolanaClient(settings.rpc_url) as chain:
        await airdrop(chain, owner.pubkey(), 100 * SOL)
        await airdrop(chain, guardian.pubkey(), 5 * SOL)
        for name in EXTRA_KEYS:
            path = REPO_ROOT / ".keys" / f"{name}.json"
            if path.exists():
                await airdrop(chain, load_keypair(path).pubkey(), 2 * SOL)

        owner_ata = vault_ix.associated_token_address(owner.pubkey(), mint)
        balance = await chain.token_balance(owner_ata) or 0
        if balance < OWNER_USDC:
            await chain.send(
                [
                    create_idempotent_associated_token_account(owner.pubkey(), owner.pubkey(), mint),
                    mint_to(
                        MintToParams(
                            program_id=TOKEN_PROGRAM_ID,
                            mint=mint,
                            dest=owner_ata,
                            mint_authority=owner.pubkey(),
                            amount=OWNER_USDC - balance,
                        )
                    ),
                ],
                owner,
            )
        print(f"owner     {owner.pubkey()}")
        print(f"  SOL     {await chain.sol_balance(owner.pubkey()) / SOL:.2f}")
        print(f"  USDC    {units_to_usdc(await chain.token_balance(owner_ata) or 0)} (LOCALNET stand-in)")
        print(f"guardian  {guardian.pubkey()}  SOL {await chain.sol_balance(guardian.pubkey()) / SOL:.2f}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
