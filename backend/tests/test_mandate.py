"""Signed mandates: canonical bytes, signature checks, compilation."""

from datetime import timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from solders.pubkey import Pubkey

from app.policy.engine import evaluate
from app.policy.mandate import (
    Mandate,
    MandateError,
    SignedMandate,
    compile_mandate,
    onchain_policy,
    sign_mandate,
)
from app.policy.models import Verdict
from policy_fixtures import (
    NEWS_ORIGIN,
    NEWS_SELLER,
    NOW,
    OWNER,
    PARTNER,
    addr,
    key,
    order,
    state,
    transfer,
)

TRUSTED_OWNER = str(OWNER.pubkey())


def make_mandate(**overrides) -> Mandate:
    base = dict(
        agent_id="btc-trader",
        agent_name="BTC Trader",
        allowed_action_types=("x402_payment", "gemini_order", "transfer"),
        allowed_markets=("GEMI-BTC05M*",),
        allowed_payees=(NEWS_SELLER, PARTNER),
        allowed_endpoints=(NEWS_ORIGIN + "/",),
        max_per_action_usd="1.00",
        max_per_day_usd="5.00",
        approval_threshold_usd="0.50",
        max_actions_per_minute=6,
        expires_at=NOW + timedelta(days=7),
    )
    base.update(overrides)
    return Mandate(**base)


def test_canonical_bytes_are_stable_and_order_independent():
    a = make_mandate()
    b = Mandate(**dict(reversed(list(a.model_dump().items()))))
    assert a.canonical_bytes() == b.canonical_bytes()
    assert a.hash() == b.hash() and len(a.hash()) == 32
    text = a.canonical_bytes().decode()
    assert '"max_per_action_usd":"1.00"' in text  # money stays a string
    assert '", "' not in text and '": ' not in text  # compact separators


def test_any_change_changes_the_hash():
    base = make_mandate().hash()
    assert make_mandate(max_per_day_usd="5.01").hash() != base
    assert make_mandate(allowed_payees=(NEWS_SELLER,)).hash() != base
    assert make_mandate(expires_at=NOW + timedelta(days=8)).hash() != base


def test_signed_mandate_compiles_to_policy():
    policy = compile_mandate(sign_mandate(make_mandate(), OWNER), trusted_owner=TRUSTED_OWNER)
    assert policy.owner == TRUSTED_OWNER
    assert policy.allowed_payees == frozenset({NEWS_SELLER, PARTNER})
    assert policy.allowed_endpoints == frozenset({NEWS_ORIGIN})  # trailing slash dropped
    assert policy.max_per_action_usd == Decimal("1.00")
    assert policy.mandate_hash == make_mandate().hash()

    # The compiled policy is what the engine enforces.
    assert evaluate(order("1", "0.40"), policy, state()).verdict is Verdict.ALLOW
    assert evaluate(order("3", "0.40"), policy, state()).verdict is Verdict.DENY  # $1.20 > $1
    assert evaluate(transfer("0.75"), policy, state()).verdict is Verdict.REQUIRE_APPROVAL


def test_mandate_signed_by_someone_else_is_rejected():
    forged = sign_mandate(make_mandate(), key(66))
    with pytest.raises(MandateError, match="vault owner"):
        compile_mandate(forged, trusted_owner=TRUSTED_OWNER)


def test_claiming_to_be_the_owner_without_their_signature_is_rejected():
    forged = sign_mandate(make_mandate(), key(66))
    spoofed = SignedMandate(mandate=forged.mandate, signer=TRUSTED_OWNER, signature=forged.signature)
    with pytest.raises(MandateError, match="does not match"):
        compile_mandate(spoofed, trusted_owner=TRUSTED_OWNER)


def test_editing_a_signed_mandate_invalidates_it():
    signed = sign_mandate(make_mandate(), OWNER)
    tampered = SignedMandate(
        mandate=make_mandate(max_per_day_usd="5000", allowed_payees=(addr(66),)),
        signer=signed.signer,
        signature=signed.signature,
    )
    with pytest.raises(MandateError, match="does not match"):
        compile_mandate(tampered, trusted_owner=TRUSTED_OWNER)


def test_garbage_signature_is_rejected():
    signed = sign_mandate(make_mandate(), OWNER)
    broken = SignedMandate(mandate=signed.mandate, signer=signed.signer, signature="not-a-signature")
    with pytest.raises(MandateError):
        compile_mandate(broken, trusted_owner=TRUSTED_OWNER)


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_per_action_usd": 1.5},  # float
        {"max_per_action_usd": "0"},
        {"max_per_day_usd": "-5"},
        {"max_actions_per_minute": 0},
        {"expires_at": NOW.replace(tzinfo=None)},
        {"allowed_payees": ("not-an-address",)},
        {"allowed_payees": tuple(addr(n) for n in range(10, 19))},  # 9 > onchain limit of 8
        {"allowed_action_types": ("drain_vault",)},
        {"surprise_field": True},
        {"version": 2},
    ],
)
def test_malformed_mandates_are_rejected(overrides):
    with pytest.raises(ValidationError):
        make_mandate(**overrides)


def test_per_action_cap_above_daily_budget_is_rejected_at_compile():
    signed = sign_mandate(make_mandate(max_per_action_usd="6", max_per_day_usd="5"), OWNER)
    with pytest.raises(MandateError, match="cannot exceed"):
        compile_mandate(signed, trusted_owner=TRUSTED_OWNER)


def test_onchain_policy_mirrors_the_mandate():
    policy = compile_mandate(sign_mandate(make_mandate(), OWNER), trusted_owner=TRUSTED_OWNER)
    chain = onchain_policy(policy)
    assert chain.per_tx_cap == 1_000_000
    assert chain.daily_cap == 5_000_000
    assert chain.expires_at == int((NOW + timedelta(days=7)).timestamp())
    assert set(chain.allowlist) == {Pubkey.from_string(NEWS_SELLER), Pubkey.from_string(PARTNER)}
    assert chain.mandate_hash == policy.mandate_hash
    assert len(chain.encode()) == 8 + 8 + 8 + 4 + 2 * 32 + 32
