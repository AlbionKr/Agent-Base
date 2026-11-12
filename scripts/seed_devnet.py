"""Seed devnet: vault, deposit, one agent, and a first capped top-up.

    python -m scripts.seed_devnet

Idempotent: every step checks onchain state first and is skipped if already
done. Needs the program deployed, devnet SOL in the owner wallet and devnet
USDC (https://faucet.circle.com) in the owner's token account.
"""

from __future__ import annotations

import asyncio
import sys
import time
from decimal import Decimal

from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from spl.token.instructions import create_idempotent_associated_token_account

from app.chain import vault_ix
from app.chain.client import (
    ChainError,
    SolanaClient,
    explorer_address,
    explorer_tx,
    load_keypair,
    save_new_keypair,
)
from app.chain.units import units_to_usdc, usdc_to_units
from app.config import REPO_ROOT, load_chain_settings

AGENT_ID = 1
AGENT_KEY_PATH = REPO_ROOT / ".keys" / "agent-1.json"

VAULT_TARGET = usdc_to_units(Decimal("10"))
PER_TX_CAP = usdc_to_units(Decimal("1"))
DAILY_CAP = usdc_to_units(Decimal("5"))
FIRST_TOP_UP = usdc_to_units(Decimal("0.10"))
SESSION_DAYS = 9  # covers the hackathon
AGENT_FEE_LAMPORTS = 20_000_000  # 0.02 SOL so the session key can pay tx fees
MIN_OWNER_LAMPORTS = 50_000_000


def step(text: str) -> None:
    print(f"\n== {text}")


async def main() -> int:
    settings = load_chain_settings()
    program_id = Pubkey.from_string(settings.vault_program_id)
    mint = Pubkey.from_string(settings.usdc_mint)
    owner = load_keypair(settings.owner_keypair_path)
    guardian = load_keypair(settings.guardian_keypair_path)

    vault = vault_ix.vault_address(program_id, owner.pubkey())
    vault_ata = vault_ix.associated_token_address(vault, mint)
    owner_ata = vault_ix.associated_token_address(owner.pubkey(), mint)
    allowance = vault_ix.allowance_address(program_id, vault, AGENT_ID)

    print(f"rpc       {settings.rpc_url}")
    print(f"program   {program_id}")
    print(f"owner     {owner.pubkey()}")
    print(f"guardian  {guardian.pubkey()}")
    print(f"vault     {vault}")

    async with SolanaClient(settings.rpc_url) as chain:
        if await chain.account_data(program_id) is None:
            print("\nThe vault program is not deployed at this address.", file=sys.stderr)
            return 1
        lamports = await chain.sol_balance(owner.pubkey())
        if lamports < MIN_OWNER_LAMPORTS:
            print(
                f"\nOwner has {lamports / 1e9:.4f} SOL; needs at least "
                f"{MIN_OWNER_LAMPORTS / 1e9} SOL. Get devnet SOL at https://faucet.solana.com",
                file=sys.stderr,
            )
            return 1

        step("vault")
        if await chain.account_data(vault) is None:
            sig = await chain.send(
                [vault_ix.initialize_vault(program_id, owner.pubkey(), guardian.pubkey(), mint)],
                owner,
            )
            print(f"initialized  {explorer_tx(sig)}")
        else:
            print("already initialized")

        step("deposit")
        vault_balance = await chain.token_balance(vault_ata) or 0
        if vault_balance < VAULT_TARGET:
            needed = VAULT_TARGET - vault_balance
            owner_balance = await chain.token_balance(owner_ata) or 0
            if owner_balance < needed:
                print(
                    f"Owner holds {units_to_usdc(owner_balance)} USDC but {units_to_usdc(needed)} "
                    f"is needed.\nSend devnet USDC to {owner.pubkey()} at https://faucet.circle.com "
                    "(network: Solana Devnet), then run this again.",
                    file=sys.stderr,
                )
                return 1
            sig = await chain.send(
                [vault_ix.deposit(program_id, owner.pubkey(), owner.pubkey(), mint, needed)],
                owner,
            )
            print(f"deposited {units_to_usdc(needed)} USDC  {explorer_tx(sig)}")
        else:
            print(f"vault already holds {units_to_usdc(vault_balance)} USDC")

        step("agent session key")
        if AGENT_KEY_PATH.exists():
            agent = load_keypair(AGENT_KEY_PATH)
        else:
            agent = save_new_keypair(AGENT_KEY_PATH)
        agent_ata = vault_ix.associated_token_address(agent.pubkey(), mint)
        print(f"agent     {agent.pubkey()}")

        setup = []
        if await chain.sol_balance(agent.pubkey()) < AGENT_FEE_LAMPORTS // 2:
            setup.append(
                transfer(
                    TransferParams(
                        from_pubkey=owner.pubkey(),
                        to_pubkey=agent.pubkey(),
                        lamports=AGENT_FEE_LAMPORTS,
                    )
                )
            )
        if await chain.account_data(agent_ata) is None:
            setup.append(
                create_idempotent_associated_token_account(owner.pubkey(), agent.pubkey(), mint)
            )
        if setup:
            sig = await chain.send(setup, owner)
            print(f"funded fees / created token account  {explorer_tx(sig)}")

        step("agent allowance")
        if await chain.account_data(allowance) is None:
            policy = vault_ix.AgentPolicy(
                expires_at=int(time.time()) + SESSION_DAYS * 86_400,
                per_tx_cap=PER_TX_CAP,
                daily_cap=DAILY_CAP,
                allowlist=(),
                # Placeholder until M3 compiles a real mandate and calls update_policy.
                mandate_hash=bytes(32),
            )
            sig = await chain.send(
                [vault_ix.create_agent(program_id, owner.pubkey(), AGENT_ID, agent.pubkey(), policy)],
                owner,
            )
            print(f"created  {explorer_tx(sig)}")
        else:
            print("already exists")

        step("top_up within caps")
        sig = await chain.send(
            [vault_ix.top_up(program_id, owner.pubkey(), agent.pubkey(), AGENT_ID, mint, FIRST_TOP_UP)],
            agent,
        )
        print(f"top_up {units_to_usdc(FIRST_TOP_UP)} USDC  {explorer_tx(sig)}")

        step("top_up over the per-tx cap (must be rejected onchain)")
        try:
            await chain.send(
                [vault_ix.top_up(program_id, owner.pubkey(), agent.pubkey(), AGENT_ID, mint, PER_TX_CAP + 1)],
                agent,
            )
        except ChainError as exc:
            print(f"rejected as expected: {exc}")
        else:
            print("ERROR: an over-cap top_up was accepted", file=sys.stderr)
            return 1

        step("state")
        state = vault_ix.AgentAllowanceState.decode(await chain.account_data(allowance))
        print(f"spent today   {units_to_usdc(state.spent_today)} / {units_to_usdc(state.daily_cap)} USDC")
        print(f"per-tx cap    {units_to_usdc(state.per_tx_cap)} USDC")
        print(f"agent wallet  {units_to_usdc(await chain.token_balance(agent_ata) or 0)} USDC")
        print(f"vault         {units_to_usdc(await chain.token_balance(vault_ata) or 0)} USDC")
        print(f"program       {explorer_address(program_id)}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
