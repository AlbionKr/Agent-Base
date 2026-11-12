"""Authenticate QuickNode Streams webhooks.

QuickNode signs each delivery with the stream's security token:

    X-QN-Signature = hex( HMAC-SHA256( token, nonce + timestamp + payload ) )

where `payload` is the decoded (un-gzipped) body. See
https://www.quicknode.com/guides/quicknode-products/streams/validating-incoming-streams-webhook-messages

Three checks, in this order:
1. signature  — proves QuickNode sent it and nothing was changed
2. timestamp  — within five minutes of our clock
3. nonce      — not seen before

The nonce is remembered only after the signature passed, so a forged request can
neither fill the cache nor burn a nonce the genuine message will use.

Even a perfectly forged webhook cannot move money: the only thing this path can
trigger is the guardian, and the guardian can only freeze.
"""

from __future__ import annotations

import hashlib
import hmac
import zlib
from datetime import datetime, timedelta

MAX_SKEW_SECONDS = 300
MAX_BODY_BYTES = 5 * 1024 * 1024


class WebhookRejected(Exception):
    def __init__(self, code: str, message: str, status: int = 401):
        super().__init__(message)
        self.code = code
        self.status = status


class NonceCache:
    """Remembers nonces for longer than a message can stay fresh."""

    def __init__(self, ttl_seconds: int = 2 * MAX_SKEW_SECONDS + 60):
        self._ttl = timedelta(seconds=ttl_seconds)
        self._seen: dict[str, datetime] = {}

    def __len__(self) -> int:
        return len(self._seen)

    def add(self, nonce: str, now: datetime) -> bool:
        """True if the nonce is new. A known nonce keeps its original expiry."""
        cutoff = now - self._ttl
        for old in [n for n, at in self._seen.items() if at < cutoff]:
            del self._seen[old]
        if nonce in self._seen:
            return False
        self._seen[nonce] = now
        return True


def expected_signature(secret: str, nonce: str, timestamp: str, payload: bytes) -> str:
    message = nonce.encode("utf-8") + timestamp.encode("utf-8") + payload
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def decode_body(body: bytes, content_encoding: str | None, max_bytes: int = MAX_BODY_BYTES) -> bytes:
    """Return the bytes QuickNode signed. Bounded, so a gzip bomb cannot exhaust memory."""
    encoding = (content_encoding or "identity").strip().lower()
    if encoding == "identity":
        decoded = body
    elif encoding == "gzip":
        try:
            inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
            decoded = inflater.decompress(body, max_bytes + 1)
        except zlib.error as exc:
            raise WebhookRejected("bad_encoding", f"body is not valid gzip: {exc}", 400) from exc
    else:
        raise WebhookRejected("bad_encoding", f"unsupported Content-Encoding {encoding!r}", 400)
    if len(decoded) > max_bytes:
        raise WebhookRejected("body_too_large", f"body exceeds {max_bytes} bytes", 413)
    return decoded


def _parse_timestamp(value: str) -> int | None:
    if not value.isascii() or not value.isdigit():
        return None
    seconds = int(value)
    # QuickNode's guide does not pin the unit; accept epoch milliseconds too.
    return seconds // 1000 if seconds > 10**11 else seconds


def verify_webhook(
    *,
    secret: str,
    nonce: str | None,
    timestamp: str | None,
    signature: str | None,
    payload: bytes,
    now: datetime,
    nonces: NonceCache,
    max_skew_seconds: int = MAX_SKEW_SECONDS,
) -> None:
    """Raise WebhookRejected unless the delivery is authentic, fresh and new."""
    if not secret:
        raise WebhookRejected(
            "not_configured", "QUICKNODE_STREAM_SECURITY_TOKEN is not set; refusing all webhooks", 503
        )
    nonce, timestamp, signature = (
        (value or "").strip() for value in (nonce, timestamp, signature)
    )
    if not (nonce and timestamp and signature):
        raise WebhookRejected("missing_headers", "X-QN-Nonce, X-QN-Timestamp and X-QN-Signature are required")

    expected = expected_signature(secret, nonce, timestamp, payload)
    if not hmac.compare_digest(expected.encode("ascii"), signature.lower().encode("utf-8")):
        raise WebhookRejected("bad_signature", "signature does not match")

    seconds = _parse_timestamp(timestamp)
    if seconds is None:
        raise WebhookRejected("bad_timestamp", "timestamp is not an epoch number")
    if abs(now.timestamp() - seconds) > max_skew_seconds:
        raise WebhookRejected("stale_timestamp", f"timestamp is more than {max_skew_seconds}s from now")

    if not nonces.add(nonce, now):
        raise WebhookRejected("replayed_nonce", "this nonce was already used")
