"""QuickNode webhook authentication: HMAC, freshness, replay."""

from __future__ import annotations

import gzip
import hashlib
import hmac
from datetime import datetime, timedelta, timezone

import pytest

from app.quicknode.verify import (
    NonceCache,
    WebhookRejected,
    decode_body,
    expected_signature,
    verify_webhook,
)

SECRET = "qn-test-security-token"  # secretscan:allow (test fixture, not a real token)
NOW = datetime(2026, 11, 16, 12, 0, 0, tzinfo=timezone.utc)
TS = str(int(NOW.timestamp()))
BODY = b'{"transactions":[]}'


def signed(body: bytes = BODY, nonce: str = "nonce-1", timestamp: str = TS, secret: str = SECRET) -> dict:
    return dict(
        secret=SECRET,
        nonce=nonce,
        timestamp=timestamp,
        signature=expected_signature(secret, nonce, timestamp, body),
        payload=body,
        now=NOW,
        nonces=NonceCache(),
    )


def rejected(**kwargs) -> str:
    with pytest.raises(WebhookRejected) as info:
        verify_webhook(**kwargs)
    return info.value.code


def test_signature_is_hex_hmac_sha256_over_nonce_timestamp_payload():
    # Written out independently of the implementation, per QuickNode's guide.
    message = b"nonce-1" + TS.encode() + BODY
    independent = hmac.new(SECRET.encode(), message, hashlib.sha256).hexdigest()
    assert expected_signature(SECRET, "nonce-1", TS, BODY) == independent


def test_a_correctly_signed_fresh_message_is_accepted():
    verify_webhook(**signed())


def test_an_unsigned_message_is_rejected():
    assert rejected(**{**signed(), "signature": None}) == "missing_headers"
    assert rejected(**{**signed(), "nonce": ""}) == "missing_headers"
    assert rejected(**{**signed(), "timestamp": None}) == "missing_headers"


def test_a_message_signed_with_another_secret_is_rejected():
    assert rejected(**signed(secret="some-other-token")) == "bad_signature"


@pytest.mark.parametrize(
    "tamper",
    [
        {"payload": b'{"transactions":[{"signature":"forged"}]}'},
        {"nonce": "nonce-2"},
        {"timestamp": str(int(NOW.timestamp()) + 1)},
        {"signature": "00" * 32},
        {"signature": "not-hex"},
        {"signature": "é" * 64},
    ],
)
def test_changing_any_signed_part_breaks_the_signature(tamper):
    assert rejected(**{**signed(), **tamper}) == "bad_signature"


def test_without_a_configured_secret_everything_is_rejected():
    kwargs = signed(secret="")
    kwargs["secret"] = ""
    assert rejected(**kwargs) == "not_configured"


@pytest.mark.parametrize("offset", [-301, 301, -86400])
def test_a_validly_signed_but_stale_or_future_message_is_rejected(offset):
    timestamp = str(int(NOW.timestamp()) + offset)
    assert rejected(**signed(timestamp=timestamp)) == "stale_timestamp"


@pytest.mark.parametrize("offset", [-300, 0, 300])
def test_timestamps_within_five_minutes_are_accepted(offset):
    verify_webhook(**signed(timestamp=str(int(NOW.timestamp()) + offset)))


def test_millisecond_timestamps_are_understood():
    verify_webhook(**signed(timestamp=str(int(NOW.timestamp() * 1000))))


@pytest.mark.parametrize("timestamp", ["yesterday", "12.5", "-5", " "])
def test_unparseable_timestamps_are_rejected_even_when_signed(timestamp):
    assert rejected(**signed(timestamp=timestamp)) in {"bad_timestamp", "missing_headers"}


def test_a_replayed_message_is_rejected():
    kwargs = signed()
    verify_webhook(**kwargs)
    assert rejected(**kwargs) == "replayed_nonce"


def test_a_forged_message_cannot_burn_a_nonce():
    nonces = NonceCache()
    forged = {**signed(), "signature": "00" * 32, "nonces": nonces}
    assert rejected(**forged) == "bad_signature"
    # The genuine message with the same nonce still goes through afterwards.
    verify_webhook(**{**signed(), "nonces": nonces})


def test_nonces_are_forgotten_only_after_they_can_no_longer_be_fresh():
    nonces = NonceCache(ttl_seconds=700)
    assert nonces.add("n", NOW) is True
    assert nonces.add("n", NOW + timedelta(seconds=699)) is False
    assert nonces.add("n", NOW + timedelta(seconds=1401)) is True
    assert len(nonces) == 1


def test_gzip_bodies_are_decoded_before_verification():
    assert decode_body(gzip.compress(BODY), "gzip") == BODY
    assert decode_body(BODY, None) == BODY
    assert decode_body(BODY, "identity") == BODY


def test_a_gzip_bomb_or_garbage_is_refused():
    bomb = gzip.compress(b"0" * 200_000)
    with pytest.raises(WebhookRejected) as info:
        decode_body(bomb, "gzip", max_bytes=100_000)
    assert info.value.code == "body_too_large"
    with pytest.raises(WebhookRejected) as info:
        decode_body(b"not gzip", "gzip")
    assert info.value.code == "bad_encoding"
    with pytest.raises(WebhookRejected) as info:
        decode_body(BODY, "br")
    assert info.value.code == "bad_encoding"
    with pytest.raises(WebhookRejected) as info:
        decode_body(b"0" * 11, None, max_bytes=10)
    assert info.value.code == "body_too_large"
