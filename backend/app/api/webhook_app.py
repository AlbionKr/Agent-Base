"""Webhook receiver: the only thing that is ever put behind a public tunnel.

It runs on its own port and serves exactly one route. A delivery is processed
only after its QuickNode signature, timestamp and nonce check out, and the most
it can cause is a freeze.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.api.services import Services
from app.quicknode.anomaly import PayloadError
from app.quicknode.verify import MAX_BODY_BYTES, WebhookRejected, decode_body, verify_webhook

REJECTION_LOG_INTERVAL = timedelta(seconds=10)


class _RejectionLog:
    """Logs refused deliveries without letting a flood of them fill the audit log."""

    def __init__(self, services: Services):
        self._services = services
        self._last: datetime | None = None
        self._suppressed = 0

    def record(self, rejection: WebhookRejected) -> None:
        now = self._services.clock()
        if self._last is not None and now - self._last < REJECTION_LOG_INTERVAL:
            self._suppressed += 1
            return
        self._services.journal.emit(
            "webhook_rejected",
            {
                "code": rejection.code,
                "status": rejection.status,
                "suppressed_since_last": self._suppressed,
            },
        )
        self._last = now
        self._suppressed = 0


async def _read_capped(request: Request, max_bytes: int) -> bytes:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > max_bytes:
        raise WebhookRejected("body_too_large", f"body exceeds {max_bytes} bytes", 413)
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > max_bytes:
            raise WebhookRejected("body_too_large", f"body exceeds {max_bytes} bytes", 413)
        chunks.append(chunk)
    return b"".join(chunks)


def create_webhook_app(services: Services, *, max_body_bytes: int = MAX_BODY_BYTES) -> FastAPI:
    app = FastAPI(title="Agent Base webhook receiver", docs_url=None, redoc_url=None, openapi_url=None)
    rejections = _RejectionLog(services)

    @app.get("/health")
    async def health():
        return {"ok": True, "service": "webhook"}

    @app.post("/webhooks/quicknode")
    async def quicknode(request: Request):
        try:
            wire = await _read_capped(request, max_body_bytes)
            body = decode_body(wire, request.headers.get("content-encoding"), max_body_bytes)
            verify_webhook(
                secret=services.webhook_secret,
                nonce=request.headers.get("x-qn-nonce"),
                timestamp=request.headers.get("x-qn-timestamp"),
                signature=request.headers.get("x-qn-signature"),
                payload=body,
                now=services.clock(),
                nonces=services.nonces,
            )
        except WebhookRejected as rejection:
            rejections.record(rejection)
            return JSONResponse({"error": rejection.code}, status_code=rejection.status)

        # Authentic from here on.
        try:
            payload = json.loads(body)
        except (ValueError, UnicodeDecodeError):
            services.journal.emit("webhook_unparsed", {"error": "signed body is not valid JSON"})
            return JSONResponse({"error": "invalid_json"}, status_code=400)
        try:
            await services.sentinel.handle(payload)
        except PayloadError as exc:
            services.journal.emit("webhook_unparsed", {"error": str(exc)})
            return JSONResponse({"error": "unexpected_payload"}, status_code=422)
        return {"ok": True}

    return app
