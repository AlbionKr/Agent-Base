"""Vault program: addresses, instruction builders and account decoders.

Pure functions, no network. The byte layouts here mirror `programs/vault` and
are checked against the Anchor-generated IDL in `tests/test_vault_ix.py`, so a
change to the program that this file does not follow fails the test suite.
"""

from __future__ import annotations

import hashlib
import struct
from dataclasses import dataclass

from solders.instruction import AccountMeta, Instruction
from solders.pubkey import Pubkey

TOKEN_PROGRAM_ID = Pubkey.from_string("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA")
ASSOCIATED_TOKEN_PROGRAM_ID = Pubkey.from_string(
    "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
)
SYSTEM_PROGRAM_ID = Pubkey.from_string("11111111111111111111111111111111")

VAULT_SEED = b"vault"
AGENT_SEED = b"agent"
MAX_ALLOWLIST = 8


def _ix_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"global:{name}".encode()).digest()[:8]


def _account_discriminator(name: str) -> bytes:
    return hashlib.sha256(f"account:{name}".encode()).digest()[:8]


def _u64(value: int) -> bytes:
    return struct.pack("<Q", value)


def _i64(value: int) -> bytes:
    return struct.pack("<q", value)


# ------------------------------------------------------------------ addresses


def vault_address(program_id: Pubkey, owner: Pubkey) -> Pubkey:
    return Pubkey.find_program_address([VAULT_SEED, bytes(owner)], program_id)[0]


def allowance_address(program_id: Pubkey, vault: Pubkey, agent_id: int) -> Pubkey:
    return Pubkey.find_program_address(
        [AGENT_SEED, bytes(vault), _u64(agent_id)], program_id
    )[0]


def associated_token_address(owner: Pubkey, mint: Pubkey) -> Pubkey:
    return Pubkey.find_program_address(
        [bytes(owner), bytes(TOKEN_PROGRAM_ID), bytes(mint)],
        ASSOCIATED_TOKEN_PROGRAM_ID,
    )[0]


# --------------------------------------------------------------------- policy


@dataclass(frozen=True)
class AgentPolicy:
    """Onchain limits for one agent. Amounts are USDC base units (6 decimals)."""

    expires_at: int
    per_tx_cap: int
    daily_cap: int
    allowlist: tuple[Pubkey, ...]
    mandate_hash: bytes

    def __post_init__(self) -> None:
        if len(self.mandate_hash) != 32:
            raise ValueError("mandate_hash must be exactly 32 bytes")
        if len(self.allowlist) > MAX_ALLOWLIST:
            raise ValueError(f"allowlist holds at most {MAX_ALLOWLIST} payees")
        if self.per_tx_cap > self.daily_cap:
            raise ValueError("per_tx_cap cannot exceed daily_cap")

    def encode(self) -> bytes:
        return (
            _i64(self.expires_at)
            + _u64(self.per_tx_cap)
            + _u64(self.daily_cap)
            + struct.pack("<I", len(self.allowlist))
            + b"".join(bytes(p) for p in self.allowlist)
            + self.mandate_hash
        )


# --------------------------------------------------------------- instructions


def _meta(pubkey: Pubkey, *, signer: bool = False, writable: bool = False) -> AccountMeta:
    return AccountMeta(pubkey, is_signer=signer, is_writable=writable)


def initialize_vault(
    program_id: Pubkey, owner: Pubkey, guardian: Pubkey, usdc_mint: Pubkey
) -> Instruction:
    vault = vault_address(program_id, owner)
    return Instruction(
        program_id,
        _ix_discriminator("initialize_vault") + bytes(guardian),
        [
            _meta(owner, signer=True, writable=True),
            _meta(vault, writable=True),
            _meta(usdc_mint),
            _meta(associated_token_address(vault, usdc_mint), writable=True),
            _meta(TOKEN_PROGRAM_ID),
            _meta(ASSOCIATED_TOKEN_PROGRAM_ID),
            _meta(SYSTEM_PROGRAM_ID),
        ],
    )


def deposit(
    program_id: Pubkey,
    vault_owner: Pubkey,
    depositor: Pubkey,
    usdc_mint: Pubkey,
    amount: int,
) -> Instruction:
    vault = vault_address(program_id, vault_owner)
    return Instruction(
        program_id,
        _ix_discriminator("deposit") + _u64(amount),
        [
            _meta(depositor, signer=True),
            _meta(vault),
            _meta(associated_token_address(depositor, usdc_mint), writable=True),
            _meta(associated_token_address(vault, usdc_mint), writable=True),
            _meta(usdc_mint),
            _meta(TOKEN_PROGRAM_ID),
        ],
    )


def create_agent(
    program_id: Pubkey,
    owner: Pubkey,
    agent_id: int,
    session_key: Pubkey,
    policy: AgentPolicy,
) -> Instruction:
    vault = vault_address(program_id, owner)
    return Instruction(
        program_id,
        _ix_discriminator("create_agent")
        + _u64(agent_id)
        + bytes(session_key)
        + policy.encode(),
        [
            _meta(owner, signer=True, writable=True),
            _meta(vault),
            _meta(allowance_address(program_id, vault, agent_id), writable=True),
            _meta(SYSTEM_PROGRAM_ID),
        ],
    )


def _spend(
    name: str,
    program_id: Pubkey,
    vault_owner: Pubkey,
    agent: Pubkey,
    agent_id: int,
    destination_token_account: Pubkey,
    usdc_mint: Pubkey,
    amount: int,
) -> Instruction:
    vault = vault_address(program_id, vault_owner)
    return Instruction(
        program_id,
        _ix_discriminator(name) + _u64(amount),
        [
            _meta(agent, signer=True),
            _meta(vault),
            _meta(allowance_address(program_id, vault, agent_id), writable=True),
            _meta(associated_token_address(vault, usdc_mint), writable=True),
            _meta(destination_token_account, writable=True),
            _meta(usdc_mint),
            _meta(TOKEN_PROGRAM_ID),
        ],
    )


def top_up(
    program_id: Pubkey,
    vault_owner: Pubkey,
    agent: Pubkey,
    agent_id: int,
    usdc_mint: Pubkey,
    amount: int,
) -> Instruction:
    """Vault -> the agent's own associated token account."""
    return _spend(
        "top_up",
        program_id,
        vault_owner,
        agent,
        agent_id,
        associated_token_address(agent, usdc_mint),
        usdc_mint,
        amount,
    )


def pay(
    program_id: Pubkey,
    vault_owner: Pubkey,
    agent: Pubkey,
    agent_id: int,
    payee_token_account: Pubkey,
    usdc_mint: Pubkey,
    amount: int,
) -> Instruction:
    """Vault -> a payee. The token account's owner must be on the allowlist."""
    return _spend(
        "pay",
        program_id,
        vault_owner,
        agent,
        agent_id,
        payee_token_account,
        usdc_mint,
        amount,
    )


def freeze_agent(
    program_id: Pubkey, vault_owner: Pubkey, authority: Pubkey, agent_id: int
) -> Instruction:
    """`authority` is the owner or the guardian."""
    vault = vault_address(program_id, vault_owner)
    return Instruction(
        program_id,
        _ix_discriminator("freeze_agent"),
        [
            _meta(authority, signer=True),
            _meta(vault),
            _meta(allowance_address(program_id, vault, agent_id), writable=True),
        ],
    )


def freeze_vault(program_id: Pubkey, vault_owner: Pubkey, authority: Pubkey) -> Instruction:
    return Instruction(
        program_id,
        _ix_discriminator("freeze_vault"),
        [
            _meta(authority, signer=True),
            _meta(vault_address(program_id, vault_owner), writable=True),
        ],
    )


def _owner_agent_action(
    name: str, program_id: Pubkey, owner: Pubkey, agent_id: int, args: bytes = b""
) -> Instruction:
    vault = vault_address(program_id, owner)
    return Instruction(
        program_id,
        _ix_discriminator(name) + args,
        [
            _meta(owner, signer=True),
            _meta(vault),
            _meta(allowance_address(program_id, vault, agent_id), writable=True),
        ],
    )


def unfreeze_agent(program_id: Pubkey, owner: Pubkey, agent_id: int) -> Instruction:
    return _owner_agent_action("unfreeze_agent", program_id, owner, agent_id)


def update_policy(
    program_id: Pubkey, owner: Pubkey, agent_id: int, policy: AgentPolicy
) -> Instruction:
    return _owner_agent_action(
        "update_policy", program_id, owner, agent_id, policy.encode()
    )


def rotate_session_key(
    program_id: Pubkey,
    owner: Pubkey,
    agent_id: int,
    new_session_key: Pubkey,
    new_expires_at: int,
) -> Instruction:
    return _owner_agent_action(
        "rotate_session_key",
        program_id,
        owner,
        agent_id,
        bytes(new_session_key) + _i64(new_expires_at),
    )


def unfreeze_vault(program_id: Pubkey, owner: Pubkey) -> Instruction:
    return Instruction(
        program_id,
        _ix_discriminator("unfreeze_vault"),
        [
            _meta(owner, signer=True),
            _meta(vault_address(program_id, owner), writable=True),
        ],
    )


def withdraw(program_id: Pubkey, owner: Pubkey, usdc_mint: Pubkey, amount: int) -> Instruction:
    vault = vault_address(program_id, owner)
    return Instruction(
        program_id,
        _ix_discriminator("withdraw") + _u64(amount),
        [
            _meta(owner, signer=True),
            _meta(vault),
            _meta(associated_token_address(vault, usdc_mint), writable=True),
            _meta(associated_token_address(owner, usdc_mint), writable=True),
            _meta(usdc_mint),
            _meta(TOKEN_PROGRAM_ID),
        ],
    )


# ------------------------------------------------------------ account decoding


class _Reader:
    def __init__(self, data: bytes):
        self._data = data
        self._pos = 0

    def take(self, n: int) -> bytes:
        chunk = self._data[self._pos : self._pos + n]
        if len(chunk) != n:
            raise ValueError("account data is shorter than its layout")
        self._pos += n
        return chunk

    def pubkey(self) -> Pubkey:
        return Pubkey.from_bytes(self.take(32))

    def u64(self) -> int:
        return struct.unpack("<Q", self.take(8))[0]

    def i64(self) -> int:
        return struct.unpack("<q", self.take(8))[0]

    def u32(self) -> int:
        return struct.unpack("<I", self.take(4))[0]

    def boolean(self) -> bool:
        return self.take(1) != b"\x00"

    def u8(self) -> int:
        return self.take(1)[0]


@dataclass(frozen=True)
class VaultState:
    owner: Pubkey
    guardian: Pubkey
    usdc_mint: Pubkey
    usdc_vault_ata: Pubkey
    frozen: bool
    bump: int

    @classmethod
    def decode(cls, data: bytes) -> "VaultState":
        if data[:8] != _account_discriminator("Vault"):
            raise ValueError("not a Vault account")
        r = _Reader(data[8:])
        return cls(r.pubkey(), r.pubkey(), r.pubkey(), r.pubkey(), r.boolean(), r.u8())


@dataclass(frozen=True)
class AgentAllowanceState:
    vault: Pubkey
    agent_id: int
    agent_session_key: Pubkey
    expires_at: int
    per_tx_cap: int
    daily_cap: int
    spent_today: int
    day_start: int
    allowlist: tuple[Pubkey, ...]
    frozen: bool
    mandate_hash: bytes
    bump: int

    @classmethod
    def decode(cls, data: bytes) -> "AgentAllowanceState":
        if data[:8] != _account_discriminator("AgentAllowance"):
            raise ValueError("not an AgentAllowance account")
        r = _Reader(data[8:])
        vault = r.pubkey()
        agent_id = r.u64()
        session_key = r.pubkey()
        expires_at = r.i64()
        per_tx_cap = r.u64()
        daily_cap = r.u64()
        spent_today = r.u64()
        day_start = r.i64()
        count = r.u32()
        if count > MAX_ALLOWLIST:
            raise ValueError("allowlist longer than the program allows")
        allowlist = tuple(r.pubkey() for _ in range(count))
        return cls(
            vault,
            agent_id,
            session_key,
            expires_at,
            per_tx_cap,
            daily_cap,
            spent_today,
            day_start,
            allowlist,
            r.boolean(),
            r.take(32),
            r.u8(),
        )
