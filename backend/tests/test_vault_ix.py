"""The Python vault client must match the deployed program byte for byte.

`backend/app/chain/idl/vault.json` is the IDL Anchor generates from the Rust
source (copied by scripts/deploy_devnet.sh). These tests compare every
hand-written instruction builder and account decoder against it, so the two
sides cannot drift apart silently.
"""

import dataclasses
import json
import struct
from pathlib import Path

import pytest
from solders.pubkey import Pubkey

from app.chain import vault_ix as v

IDL_PATH = Path(v.__file__).parent / "idl" / "vault.json"
IDL = json.loads(IDL_PATH.read_text(encoding="utf-8"))

PROGRAM = Pubkey.from_string(IDL["address"])
OWNER, GUARDIAN, AGENT, MINT, PAYEE_ATA, NEW_KEY = (Pubkey.new_unique() for _ in range(6))
ALLOWLIST = (Pubkey.new_unique(), Pubkey.new_unique())
POLICY = v.AgentPolicy(
    expires_at=1_900_000_000,
    per_tx_cap=1_000_000,
    daily_cap=5_000_000,
    allowlist=ALLOWLIST,
    mandate_hash=bytes(range(32)),
)

BUILT = {
    "initialize_vault": v.initialize_vault(PROGRAM, OWNER, GUARDIAN, MINT),
    "deposit": v.deposit(PROGRAM, OWNER, OWNER, MINT, 7),
    "create_agent": v.create_agent(PROGRAM, OWNER, 1, AGENT, POLICY),
    "top_up": v.top_up(PROGRAM, OWNER, AGENT, 1, MINT, 7),
    "pay": v.pay(PROGRAM, OWNER, AGENT, 1, PAYEE_ATA, MINT, 7),
    "freeze_agent": v.freeze_agent(PROGRAM, OWNER, GUARDIAN, 1),
    "freeze_vault": v.freeze_vault(PROGRAM, OWNER, GUARDIAN),
    "unfreeze_agent": v.unfreeze_agent(PROGRAM, OWNER, 1),
    "unfreeze_vault": v.unfreeze_vault(PROGRAM, OWNER),
    "update_policy": v.update_policy(PROGRAM, OWNER, 1, POLICY),
    "rotate_session_key": v.rotate_session_key(PROGRAM, OWNER, 1, NEW_KEY, 1_900_000_000),
    "withdraw": v.withdraw(PROGRAM, OWNER, MINT, 7),
}

FIXED_SIZES = {"u64": 8, "i64": 8, "pubkey": 32, "bool": 1, "u8": 1}
POLICY_SIZE = 8 + 8 + 8 + 4 + 32 * len(ALLOWLIST) + 32


def idl_instruction(name: str) -> dict:
    return next(ix for ix in IDL["instructions"] if ix["name"] == name)


def arg_size(arg_type) -> int:
    if isinstance(arg_type, str):
        return FIXED_SIZES[arg_type]
    assert arg_type == {"defined": {"name": "AgentPolicy"}}, arg_type
    return POLICY_SIZE


def test_every_program_instruction_has_a_builder():
    assert set(BUILT) == {ix["name"] for ix in IDL["instructions"]}


@pytest.mark.parametrize("name", sorted(BUILT))
def test_builder_matches_idl(name):
    built, spec = BUILT[name], idl_instruction(name)

    assert built.program_id == PROGRAM
    assert bytes(built.data[:8]) == bytes(spec["discriminator"])
    assert len(built.data) == 8 + sum(arg_size(a["type"]) for a in spec["args"])

    assert len(built.accounts) == len(spec["accounts"])
    for meta, account in zip(built.accounts, spec["accounts"]):
        label = f"{name}.{account['name']}"
        assert meta.is_signer == account.get("signer", False), label
        assert meta.is_writable == account.get("writable", False), label
        if "address" in account:
            assert str(meta.pubkey) == account["address"], label


def test_pdas_use_the_program_seeds():
    vault = v.vault_address(PROGRAM, OWNER)
    built = BUILT["top_up"]
    assert built.accounts[1].pubkey == vault
    assert built.accounts[2].pubkey == v.allowance_address(PROGRAM, vault, 1)
    assert built.accounts[3].pubkey == v.associated_token_address(vault, MINT)
    # top_up can only target the agent's own token account.
    assert built.accounts[4].pubkey == v.associated_token_address(AGENT, MINT)
    assert BUILT["pay"].accounts[4].pubkey == PAYEE_ATA


def idl_struct_fields(name: str) -> list[str]:
    spec = next(t for t in IDL["types"] if t["name"] == name)
    return [field["name"] for field in spec["type"]["fields"]]


def idl_account_discriminator(name: str) -> bytes:
    return bytes(next(a for a in IDL["accounts"] if a["name"] == name)["discriminator"])


@pytest.mark.parametrize(
    "idl_name,state",
    [
        ("Vault", v.VaultState),
        ("AgentAllowance", v.AgentAllowanceState),
        ("AgentPolicy", v.AgentPolicy),
    ],
)
def test_field_order_matches_idl(idl_name, state):
    assert [f.name for f in dataclasses.fields(state)] == idl_struct_fields(idl_name)


def test_vault_decodes():
    raw = (
        idl_account_discriminator("Vault")
        + bytes(OWNER)
        + bytes(GUARDIAN)
        + bytes(MINT)
        + bytes(PAYEE_ATA)
        + b"\x01"
        + bytes([254])
    )
    state = v.VaultState.decode(raw)
    assert state == v.VaultState(OWNER, GUARDIAN, MINT, PAYEE_ATA, True, 254)


def test_allowance_decodes_with_trailing_padding():
    vault = v.vault_address(PROGRAM, OWNER)
    raw = (
        idl_account_discriminator("AgentAllowance")
        + bytes(vault)
        + struct.pack("<Q", 1)
        + bytes(AGENT)
        + struct.pack("<q", 1_900_000_000)
        + struct.pack("<Q", 1_000_000)
        + struct.pack("<Q", 5_000_000)
        + struct.pack("<Q", 250_000)
        + struct.pack("<q", 1_800_000_000)
        + struct.pack("<I", 2)
        + bytes(ALLOWLIST[0])
        + bytes(ALLOWLIST[1])
        + b"\x00"
        + bytes(range(32))
        + bytes([253])
        + bytes(6 * 32)  # unused space reserved for up to 8 payees
    )
    state = v.AgentAllowanceState.decode(raw)
    assert state.vault == vault
    assert state.agent_id == 1
    assert state.agent_session_key == AGENT
    assert state.expires_at == 1_900_000_000
    assert (state.per_tx_cap, state.daily_cap, state.spent_today) == (1_000_000, 5_000_000, 250_000)
    assert state.day_start == 1_800_000_000
    assert state.allowlist == ALLOWLIST
    assert state.frozen is False
    assert state.mandate_hash == bytes(range(32))
    assert state.bump == 253


def test_wrong_account_type_is_refused():
    with pytest.raises(ValueError):
        v.VaultState.decode(idl_account_discriminator("AgentAllowance") + bytes(200))
    with pytest.raises(ValueError):
        v.AgentAllowanceState.decode(idl_account_discriminator("Vault") + bytes(200))


def test_policy_refuses_invalid_values_before_they_reach_the_chain():
    with pytest.raises(ValueError):
        dataclasses.replace(POLICY, mandate_hash=b"short")
    with pytest.raises(ValueError):
        dataclasses.replace(POLICY, allowlist=tuple(Pubkey.new_unique() for _ in range(9)))
    with pytest.raises(ValueError):
        dataclasses.replace(POLICY, per_tx_cap=POLICY.daily_cap + 1)
