"""Who signs what onchain. Three roles, three classes, three different keys.

    Provisioner      owner key      creates agents, unfreezes         (operator's machine)
    VaultSpender     session keys   top_up / pay, within the caps     (the agent's hot wallets)
    GuardianFreezer  guardian key   freeze_agent, and nothing else    (the backend's brake)

The backend's anomaly path only ever holds the guardian key. A guardian can
freeze; it can never unfreeze, spend, or change a policy.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.system_program import TransferParams, transfer
from spl.token.constants import TOKEN_PROGRAM_ID
from spl.token.instructions import create_idempotent_associated_token_account, transfer_checked
from spl.token.models import TransferCheckedParams

from app.chain import vault_ix
from app.chain.client import SolanaClient, load_keypair, save_new_keypair
from app.policy.mandate import Mandate, SignedMandate, compile_mandate, onchain_policy, sign_mandate

AGENT_FEE_LAMPORTS = 20_000_000  # 0.02 SOL so a session key can pay transaction fees


def onchain_agent_id(agent_id: str) -> int:
    """The vault program keys allowances by u64; mandates name agents by string."""
    return int.from_bytes(hashlib.sha256(agent_id.encode("utf-8")).digest()[:8], "little")


def cluster_name(rpc_url: str) -> str:
    host = urlsplit(rpc_url).hostname or ""
    if host in {"127.0.0.1", "localhost"}:
        return "localnet"
    for name in ("devnet", "testnet"):
        if name in host:
            return name
    return "mainnet-beta" if "mainnet" in host else "custom"


def explorer_cluster_param(rpc_url: str) -> str:
    """The `cluster=` query value Solana Explorer needs for this RPC."""
    name = cluster_name(rpc_url)
    if name in {"devnet", "testnet", "mainnet-beta"}:
        return name
    return f"custom&customUrl={quote(rpc_url, safe='')}"


@dataclass(frozen=True)
class VaultRef:
    program_id: Pubkey
    usdc_mint: Pubkey
    owner: Pubkey

    @property
    def vault(self) -> Pubkey:
        return vault_ix.vault_address(self.program_id, self.owner)

    @property
    def vault_token_account(self) -> Pubkey:
        return vault_ix.associated_token_address(self.vault, self.usdc_mint)

    def allowance(self, agent_id: str) -> Pubkey:
        return vault_ix.allowance_address(self.program_id, self.vault, onchain_agent_id(agent_id))

    def token_account(self, owner: Pubkey | str) -> Pubkey:
        owner = Pubkey.from_string(owner) if isinstance(owner, str) else owner
        return vault_ix.associated_token_address(owner, self.usdc_mint)


class SessionKeys:
    """Agent session keys on disk (.keys/agents/<agent_id>.json, gitignored)."""

    def __init__(self, directory: Path):
        self._dir = directory

    def path(self, agent_id: str) -> Path:
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in agent_id)
        return self._dir / f"{safe}.json"

    def load(self, agent_id: str) -> Keypair:
        return load_keypair(self.path(agent_id))

    def load_or_create(self, agent_id: str) -> Keypair:
        path = self.path(agent_id)
        return load_keypair(path) if path.exists() else save_new_keypair(path)


@dataclass(frozen=True)
class ProvisionedAgent:
    signed_mandate: SignedMandate
    hot_wallet: Pubkey
    allowance: Pubkey
    signatures: tuple[str, ...]


class Provisioner:
    """Owner-side setup. Uses the owner's key, so it lives with the operator, not the agent."""

    def __init__(self, chain: SolanaClient, ref: VaultRef, owner: Keypair, keys: SessionKeys):
        if owner.pubkey() != ref.owner:
            raise ValueError("owner keypair does not match the vault owner")
        self._chain, self._ref, self._owner, self._keys = chain, ref, owner, keys

    async def create_agent(self, mandate: Mandate) -> ProvisionedAgent:
        """Sign the mandate, create the session key and the onchain allowance. Idempotent."""
        ref = self._ref
        signed = sign_mandate(mandate, self._owner)
        policy = compile_mandate(signed, trusted_owner=str(ref.owner))
        session = self._keys.load_or_create(mandate.agent_id)
        numeric_id = onchain_agent_id(mandate.agent_id)
        allowance = ref.allowance(mandate.agent_id)
        signatures: list[str] = []

        setup = []
        if await self._chain.sol_balance(session.pubkey()) < AGENT_FEE_LAMPORTS // 2:
            setup.append(
                transfer(
                    TransferParams(
                        from_pubkey=ref.owner, to_pubkey=session.pubkey(), lamports=AGENT_FEE_LAMPORTS
                    )
                )
            )
        # Token accounts for the hot wallet and every allowlisted payee, so `pay` has a destination.
        for holder in (session.pubkey(), *(Pubkey.from_string(p) for p in sorted(policy.allowed_payees))):
            if await self._chain.account_data(ref.token_account(holder)) is None:
                setup.append(create_idempotent_associated_token_account(ref.owner, holder, ref.usdc_mint))
        if setup:
            signatures.append(await self._chain.send(setup, self._owner))

        chain_policy = onchain_policy(policy)
        existing = await self._chain.account_data(allowance)
        if existing is None:
            ix = vault_ix.create_agent(ref.program_id, ref.owner, numeric_id, session.pubkey(), chain_policy)
        else:
            ix = vault_ix.update_policy(ref.program_id, ref.owner, numeric_id, chain_policy)
        signatures.append(await self._chain.send([ix], self._owner))
        return ProvisionedAgent(signed, session.pubkey(), allowance, tuple(signatures))

    async def unfreeze(self, agent_id: str) -> str:
        ix = vault_ix.unfreeze_agent(self._ref.program_id, self._ref.owner, onchain_agent_id(agent_id))
        return await self._chain.send([ix], self._owner)


class VaultSpender:
    """Agent-side spending. Every call is checked again by the vault program."""

    def __init__(self, chain: SolanaClient, ref: VaultRef, keys: SessionKeys):
        self._chain, self._ref, self._keys = chain, ref, keys

    def hot_wallet(self, agent_id: str) -> Pubkey:
        return self._keys.load(agent_id).pubkey()

    async def pay(self, agent_id: str, payee: str, amount_units: int) -> str:
        session = self._keys.load(agent_id)
        ref = self._ref
        ix = vault_ix.pay(
            ref.program_id, ref.owner, session.pubkey(), onchain_agent_id(agent_id),
            ref.token_account(payee), ref.usdc_mint, amount_units,
        )
        return await self._chain.send([ix], session)

    async def top_up(self, agent_id: str, amount_units: int) -> str:
        session = self._keys.load(agent_id)
        ref = self._ref
        ix = vault_ix.top_up(
            ref.program_id, ref.owner, session.pubkey(), onchain_agent_id(agent_id),
            ref.usdc_mint, amount_units,
        )
        return await self._chain.send([ix], session)

    async def state(self, agent_id: str) -> vault_ix.AgentAllowanceState | None:
        data = await self._chain.account_data(self._ref.allowance(agent_id))
        return None if data is None else vault_ix.AgentAllowanceState.decode(data)


class GuardianFreezer:
    """Implements the Sentinel's `Freezer`: one instruction, one key, freeze only."""

    def __init__(self, chain: SolanaClient, ref: VaultRef, guardian: Keypair):
        self._chain, self._ref, self._guardian = chain, ref, guardian

    async def freeze(self, agent_id: str, reason: str) -> str:
        ix = vault_ix.freeze_agent(
            self._ref.program_id, self._ref.owner, self._guardian.pubkey(), onchain_agent_id(agent_id)
        )
        # freeze_agent is idempotent onchain: freezing a frozen agent succeeds.
        return await self._chain.send([ix], self._guardian)


class PlainWallet:
    """An ordinary wallet with no vault and no caps: what the Naive Agent has."""

    def __init__(self, chain: SolanaClient, ref: VaultRef, keypair: Keypair):
        self._chain, self._ref, self._keypair = chain, ref, keypair

    @property
    def address(self) -> Pubkey:
        return self._keypair.pubkey()

    async def balance(self) -> int:
        return await self._chain.token_balance(self._ref.token_account(self.address)) or 0

    async def send(self, payee: str, amount_units: int) -> str:
        """Plain SPL transfer. If asked for more than it holds, it sends everything it has."""
        destination = Pubkey.from_string(payee)
        amount = min(amount_units, await self.balance())
        if amount <= 0:
            raise ValueError("wallet is empty")
        return await self._chain.send(
            [
                create_idempotent_associated_token_account(self.address, destination, self._ref.usdc_mint),
                transfer_checked(
                    TransferCheckedParams(
                        program_id=TOKEN_PROGRAM_ID,
                        source=self._ref.token_account(self.address),
                        mint=self._ref.usdc_mint,
                        dest=self._ref.token_account(destination),
                        owner=self.address,
                        amount=amount,
                        decimals=6,
                    )
                ),
            ],
            self._keypair,
        )
