"""Run the backend:  python -m app.main

Two listeners in one process, both on 127.0.0.1:

    BACKEND_PORT (8787)  operator API — agents, proposals, approvals, event stream
    WEBHOOK_PORT (8788)  POST /webhooks/quicknode only — the one port a tunnel may expose
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from app.api.api_app import create_api_app
from app.api.services import Services
from app.api.webhook_app import create_webhook_app
from app.config import BackendSettings, load_backend_settings
from app.stack import Stack, build_stack

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


async def start(settings: BackendSettings) -> tuple[Stack, list[uvicorn.Server], list[asyncio.Task]]:
    """Build the stack and start serving. Returns once both listeners are up."""
    stack = await build_stack(settings)
    services = stack.services
    # Approvals live in memory. Any left pending by a previous run are denied.
    expired = services.audit.expire_pending_approvals()
    restored = await stack.restore()
    services.journal.emit(
        "backend_started", {"expired_actions": expired, "restored_agents": restored, "labels": stack.labels}
    )
    for name, value in stack.labels.items():
        log.info("%-20s %s", name, value)

    # proxy_headers=False: uvicorn would otherwise rewrite the client address from
    # X-Forwarded-For when the peer is 127.0.0.1 (exactly what a tunnel looks like).
    # The guard must always see the real socket peer.
    servers = [
        uvicorn.Server(
            uvicorn.Config(
                app, host=HOST, port=port, proxy_headers=False, server_header=False, log_level="warning"
            )
        )
        for app, port in (
            (create_api_app(services, stack), settings.api_port),
            (create_webhook_app(services), settings.webhook_port),
        )
    ]
    tasks = [asyncio.create_task(server.serve()) for server in servers]
    tasks.append(asyncio.create_task(expire_approvals_forever(services)))
    tasks.append(asyncio.create_task(stack.run_background()))
    while not all(server.started for server in servers):
        if any(task.done() for task in tasks[:2]):
            raise RuntimeError("a listener failed to start (is the port already in use?)")
        await asyncio.sleep(0.05)
    return stack, servers, tasks


async def stop(stack: Stack, servers: list[uvicorn.Server], tasks: list[asyncio.Task]) -> None:
    for server in servers:
        server.should_exit = True
    await asyncio.gather(*tasks[:2], return_exceptions=True)
    for task in tasks[2:]:
        task.cancel()
    await asyncio.gather(*tasks[2:], return_exceptions=True)
    await stack.close()
    stack.services.audit.close()


async def serve(settings: BackendSettings) -> None:
    stack, servers, tasks = await start(settings)
    try:
        await asyncio.gather(*tasks[:2])
    finally:
        await stop(stack, servers, tasks)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        settings = load_backend_settings()
    except RuntimeError as exc:
        raise SystemExit(f"backend: {exc}") from None
    log.info("operator API     http://%s:%d  (this machine only)", HOST, settings.api_port)
    log.info("webhook receiver http://%s:%d/webhooks/quicknode", HOST, settings.webhook_port)
    log.info("audit log        %s", settings.audit_db_path)
    asyncio.run(serve(settings))


if __name__ == "__main__":
    main()
