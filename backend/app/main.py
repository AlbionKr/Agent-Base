"""Run the backend:  python -m app.main

Two listeners in one process, both on 127.0.0.1:

    BACKEND_PORT (8000)  operator API — agents, proposals, approvals, event stream
    WEBHOOK_PORT (8001)  POST /webhooks/quicknode only — the one port a tunnel may expose
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.api.api_app import create_api_app
from app.api.services import Services, build_services
from app.api.webhook_app import create_webhook_app
from app.audit import AuditLog
from app.config import BackendSettings, load_backend_settings

log = logging.getLogger("agentbase")

HOST = "127.0.0.1"


async def expire_approvals_forever(services: Services, interval_seconds: float = 1.0) -> None:
    """Silence means no: turn overdue approval requests into denials."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            services.gate.expire_approvals()
        except Exception:
            log.exception("expiring approvals failed")


async def serve(settings: BackendSettings) -> None:
    audit = AuditLog(settings.audit_db_path)
    services = build_services(
        audit=audit,
        trusted_owner=settings.owner_address,
        usdc_mint=settings.usdc_mint,
        webhook_secret=settings.quicknode_security_token,
        allowed_origins=settings.frontend_origins,
        approval_timeout_seconds=settings.approval_timeout_seconds,
    )
    # Approvals live in memory. Any left pending by a previous run are denied.
    expired = audit.expire_pending_approvals()
    services.journal.emit(
        "backend_started",
        {
            "expired_actions": expired,
            "webhook_token_configured": bool(settings.quicknode_security_token),
            # The guardian's onchain freeze is wired in M5, once the vault is deployed.
            "onchain_freeze": "not_configured",
        },
    )

    # proxy_headers=False: uvicorn would otherwise rewrite the client address from
    # X-Forwarded-For when the peer is 127.0.0.1 (exactly what a tunnel looks like).
    # The guard must always see the real socket peer.
    servers = [
        uvicorn.Server(
            uvicorn.Config(app, host=HOST, port=port, proxy_headers=False, server_header=False)
        )
        for app, port in (
            (create_api_app(services), settings.api_port),
            (create_webhook_app(services), settings.webhook_port),
        )
    ]
    ticker = asyncio.create_task(expire_approvals_forever(services))
    try:
        await asyncio.gather(*(server.serve() for server in servers))
    finally:
        ticker.cancel()
        audit.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_backend_settings()
    except RuntimeError as exc:
        raise SystemExit(f"backend: {exc}") from None
    log.info("operator API     http://%s:%d  (this machine only)", HOST, settings.api_port)
    log.info("webhook receiver http://%s:%d/webhooks/quicknode", HOST, settings.webhook_port)
    log.info("audit log        %s", settings.audit_db_path)
    if not settings.quicknode_security_token:
        log.warning("QUICKNODE_STREAM_SECURITY_TOKEN is not set: every webhook will be refused")
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
