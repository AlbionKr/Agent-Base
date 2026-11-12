"""Operator API: agents, proposals, approvals, the audit log and its live stream.

Loopback only (see guard.py). A thin wrapper: every decision is made by
`Gate.propose`, which calls the pure policy engine.

There is deliberately no route to claim or execute an action. The executor runs
in this process and talks to the gate directly; over HTTP you can only ask for
a decision.
"""

from __future__ import annotations

import json
from typing import Literal

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field

from app.api.guard import LocalOperatorOnly
from app.api.services import Services
from app.approvals import AlreadyResolved, UnknownApproval
from app.events import sse_frames
from app.gate import UnknownAgent
from app.policy.mandate import MandateError, SignedMandate


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=20_000)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "deny"]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_api_app(services: Services) -> FastAPI:
    app = FastAPI(title="Agent Base operator API", docs_url=None, redoc_url=None, openapi_url=None)
    # Added first = innermost. The guard is added last so it sees every request first.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(services.allowed_origins),
        allow_methods=["GET", "POST"],
        allow_headers=["content-type", "last-event-id"],
    )
    app.add_middleware(LocalOperatorOnly, allowed_origins=services.allowed_origins)

    gate = services.gate

    @app.exception_handler(UnknownAgent)
    async def unknown_agent(_: Request, exc: UnknownAgent):
        return _error(404, f"unknown agent {exc.args[0]!r}")

    @app.get("/health")
    async def health():
        return {
            "ok": True,
            "service": "api",
            "agents": len(gate.agents()),
            "last_event_id": services.audit.last_id(),
        }

    # ------------------------------------------------------------- agents

    @app.get("/agents")
    async def list_agents():
        return {"agents": gate.agents()}

    @app.post("/agents", status_code=201)
    async def register_agent(signed: SignedMandate):
        try:
            policy = gate.register_agent(signed)
        except MandateError as exc:
            return _error(400, str(exc))
        return {"agent_id": policy.agent_id, "mandate_hash": policy.mandate_hash.hex()}

    @app.post("/agents/{agent_id}/context", status_code=201)
    async def add_context(agent_id: str, item: ContextItem):
        gate.observe(agent_id, item.source, item.item_id, item.text)
        return {"ok": True}

    @app.post("/agents/{agent_id}/proposals")
    async def propose(agent_id: str, request: Request):
        try:
            raw = json.loads(await request.body())
        except (ValueError, UnicodeDecodeError):
            return _error(400, "body is not valid JSON")
        # Anything that parsed goes to the gate, which validates and records it.
        return gate.propose(agent_id, raw).to_json()

    # ----------------------------------------------------------- approvals

    @app.get("/approvals")
    async def pending_approvals():
        return {"pending": [approval.to_json() for approval in gate.pending_approvals()]}

    @app.post("/approvals/{approval_id}/decision")
    async def decide_approval(approval_id: str, body: ApprovalDecision):
        try:
            resolved = gate.resolve_approval(
                approval_id, approve=body.decision == "approve", via="dashboard"
            )
        except UnknownApproval:
            return _error(404, f"unknown approval {approval_id!r}")
        except AlreadyResolved as exc:
            return _error(409, str(exc))
        return resolved.to_json()

    # -------------------------------------------------------------- events

    @app.get("/events")
    async def list_events(
        after: int = Query(0, ge=0),
        limit: int = Query(200, ge=1, le=1000),
        kind: str | None = None,
        agent_id: str | None = None,
    ):
        events = services.audit.events(after_id=after, limit=limit, kind=kind, agent_id=agent_id)
        return {"events": [event.to_json() for event in events]}

    @app.get("/events/stream")
    async def stream_events(request: Request, after: int = Query(0, ge=0)):
        """Server-Sent Events. Reconnects resume from Last-Event-ID, so nothing is missed."""
        last_seen = request.headers.get("last-event-id", "")
        start = int(last_seen) if last_seen.isascii() and last_seen.isdigit() else after
        return StreamingResponse(
            sse_frames(services.audit, services.bus, after_id=start),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
