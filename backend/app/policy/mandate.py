"""User-signed mandates and their compilation into a Policy.

A mandate is the user's instruction to one agent, in JSON: what it may do, with
whom, how much, and until when. The owner signs its canonical bytes; the sha256
of those bytes is stored onchain in the agent's allowance (`mandate_hash`), so
anyone can check that the policy the backend enforces is the one the user signed.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.signature import Signature

from app.chain import vault_ix
from app.chain.units import usdc_to_units
from app.decimals import StrictDecimal
from app.policy.models import Policy

ActionType = Literal["x402_payment", "gemini_order", "transfer"]


class MandateError(ValueError):
    """The mandate is malformed, unsigned, or signed by the wrong key."""


class Mandate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Literal[1] = 1
    agent_id: str = Field(min_length=1)
    agent_name: str = Field(min_length=1)
    allowed_action_types: tuple[ActionType, ...]
    # Exact Gemini instrument symbols, or prefixes ending in "*".
    allowed_markets: tuple[str, ...] = ()
    allowed_payees: tuple[str, ...] = ()
    # Origins of paid endpoints, e.g. "http://127.0.0.1:4021".
    allowed_endpoints: tuple[str, ...] = ()
    max_per_action_usd: StrictDecimal
    max_per_day_usd: StrictDecimal
    approval_threshold_usd: StrictDecimal
    max_actions_per_minute: int = Field(gt=0)
    expires_at: datetime

    @field_validator("max_per_action_usd", "max_per_day_usd", "approval_threshold_usd")
    @classmethod
    def _positive(cls, value):
        if value <= 0:
            raise ValueError("must be greater than zero")
        return value

    @field_validator("expires_at")
    @classmethod
    def _aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("expires_at must include a timezone")
        return value

    @field_validator("allowed_payees")
    @classmethod
    def _valid_payees(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > vault_ix.MAX_ALLOWLIST:
            raise ValueError(f"at most {vault_ix.MAX_ALLOWLIST} payees (onchain limit)")
        for payee in value:
            try:
                Pubkey.from_string(payee)
            except Exception as exc:
                raise ValueError(f"not a Solana address: {payee!r}") from exc
        return value

    def canonical_bytes(self) -> bytes:
        """The exact bytes that are signed and hashed. Stable across runs."""
        payload = self.model_dump(mode="json")
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("ascii")

    def hash(self) -> bytes:
        return hashlib.sha256(self.canonical_bytes()).digest()


class SignedMandate(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    mandate: Mandate
    signer: str
    signature: str


def sign_mandate(mandate: Mandate, owner: Keypair) -> SignedMandate:
    signature = owner.sign_message(mandate.canonical_bytes())
    return SignedMandate(mandate=mandate, signer=str(owner.pubkey()), signature=str(signature))


def compile_mandate(signed: SignedMandate, *, trusted_owner: str) -> Policy:
    """Verify the signature and turn the mandate into enforceable policy.

    `trusted_owner` comes from local configuration (the vault owner's address),
    never from the mandate itself.
    """
    if signed.signer != trusted_owner:
        raise MandateError(
            f"mandate is signed by {signed.signer}, but the vault owner is {trusted_owner}"
        )
    try:
        signature = Signature.from_string(signed.signature)
        valid = signature.verify(Pubkey.from_string(signed.signer), signed.mandate.canonical_bytes())
    except Exception as exc:
        raise MandateError(f"mandate signature is malformed: {exc}") from exc
    if not valid:
        raise MandateError("mandate signature does not match its contents")

    m = signed.mandate
    if m.max_per_action_usd > m.max_per_day_usd:
        raise MandateError("max_per_action_usd cannot exceed max_per_day_usd")
    return Policy(
        agent_id=m.agent_id,
        owner=trusted_owner,
        allowed_action_types=frozenset(m.allowed_action_types),
        allowed_markets=tuple(m.allowed_markets),
        allowed_payees=frozenset(m.allowed_payees),
        allowed_endpoints=frozenset(e.rstrip("/").lower() for e in m.allowed_endpoints),
        max_per_action_usd=m.max_per_action_usd,
        max_per_day_usd=m.max_per_day_usd,
        approval_threshold_usd=m.approval_threshold_usd,
        max_actions_per_minute=m.max_actions_per_minute,
        expires_at=m.expires_at,
        mandate_hash=m.hash(),
    )


def onchain_policy(policy: Policy) -> vault_ix.AgentPolicy:
    """The subset of the policy the vault program enforces by itself."""
    return vault_ix.AgentPolicy(
        expires_at=int(policy.expires_at.timestamp()),
        per_tx_cap=usdc_to_units(policy.max_per_action_usd),
        daily_cap=usdc_to_units(policy.max_per_day_usd),
        allowlist=tuple(Pubkey.from_string(p) for p in sorted(policy.allowed_payees)),
        mandate_hash=policy.mandate_hash,
    )
