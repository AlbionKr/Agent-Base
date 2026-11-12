"""The `layer: chain` cases, run for real against the configured cluster.

A stolen agent session key ignores the policy engine and calls the vault program
directly. The program must refuse. Needs the vault deployed and the owner funded
(devnet, or the local validator from scripts/localnet_validator.sh).
"""

from __future__ import annotations

import re

from solders.pubkey import Pubkey
from spl.token.instructions import create_idempotent_associated_token_account

from app.chain import vault_ix
from app.chain.client import ChainError, SolanaClient, load_keypair
from app.chain.ops import Provisioner, SessionKeys, VaultRef, cluster_name, onchain_agent_id
from app.chain.units import usdc_to_units
from app.config import REPO_ROOT, load_chain_settings
from app.policy.mandate import Mandate

_ERROR_CODE = re.compile(r"Error Code: (\w+)")


def _error_name(exc: ChainError) -> str:
    match = _ERROR_CODE.search(str(exc)) or _ERROR_CODE.search("\n".join(exc.logs))
    return match.group(1) if match else str(exc)[:200]


async def run_chain_cases(cases: list[dict], mandate_fields: dict, attacker: str) -> dict[str, dict]:
    """Returns {case id: result} in the scorecard's shape. Raises if the cluster is not usable."""
    settings = load_chain_settings()
    owner = load_keypair(settings.owner_keypair_path)
    ref = VaultRef(
        Pubkey.from_string(settings.vault_program_id), Pubkey.from_string(settings.usdc_mint), owner.pubkey()
    )
    keys = SessionKeys(REPO_ROOT / ".keys" / "agents")
    mandate = Mandate(**mandate_fields)
    agent_id = mandate.agent_id
    cluster = cluster_name(settings.rpc_url)
    per_tx_cap = usdc_to_units(mandate.max_per_action_usd)

    async with SolanaClient(settings.rpc_url) as chain:
        if await chain.account_data(ref.vault) is None:
            raise RuntimeError(f"no vault for owner {owner.pubkey()} on {cluster}; run scripts.seed_devnet first")
        await Provisioner(chain, ref, owner, keys).create_agent(mandate)
        session = keys.load(agent_id)
        attacker_account = ref.token_account(attacker)
        await chain.send(
            [create_idempotent_associated_token_account(owner.pubkey(), Pubkey.from_string(attacker), ref.usdc_mint)],
            owner,
        )
        numeric_id = onchain_agent_id(agent_id)
        attacks = {
            "top_up_over_per_tx_cap": vault_ix.top_up(
                ref.program_id, ref.owner, session.pubkey(), numeric_id, ref.usdc_mint, per_tx_cap + 1
            ),
            "pay_non_allowlisted": vault_ix.pay(
                ref.program_id, ref.owner, session.pubkey(), numeric_id, attacker_account, ref.usdc_mint, 100_000
            ),
        }

        results: dict[str, dict] = {}
        for case in cases:
            vault_before = await chain.token_balance(ref.vault_token_account)
            try:
                signature = await chain.send([attacks[case["chain"]["attack"]]], session)
                error = None
            except ChainError as exc:
                signature, error = None, _error_name(exc)
            vault_after = await chain.token_balance(ref.vault_token_account)
            reverted = error is not None and vault_after == vault_before
            results[case["id"]] = {
                "status": "run",
                "matches_expected": error == case["expected_error"],
                "cluster": cluster,
                "protected": {
                    "outcome": "blocked" if reverted else "not_blocked",
                    "executed_usd": "0" if reverted else "unknown",
                    "onchain_error": error,
                    "signature": signature,
                    "note": f"the vault program rejected the transaction on {cluster}; vault balance unchanged"
                    if reverted
                    else "THE TRANSACTION WAS NOT REJECTED",
                },
                "naive": {"outcome": "not_applicable", "would_lose_usd": "0", "note": "the naive agent has no vault"},
            }
        return results
