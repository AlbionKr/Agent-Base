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

from datetime import timedelta
from typing import TYPE_CHECKING

from app.api.guard import LocalOperatorOnly
from app.api.services import Services
from app.approvals import AlreadyResolved, UnknownApproval
from app.events import sse_frames
from app.gate import UnknownAgent
from app.decimals import StrictDecimal
from app.policy.mandate import Mandate, MandateError, SignedMandate

if TYPE_CHECKING:
    from app.stack import Stack


class ContextItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1, max_length=64)
    item_id: str = Field(min_length=1, max_length=128)
    text: str = Field(max_length=20_000)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["approve", "deny"]


class CreateAgent(BaseModel):
    """The "place a structure" form. The owner key on this machine signs the mandate."""

    model_config = ConfigDict(extra="forbid")

    agent_name: str = Field(min_length=1, max_length=40)
    allowed_markets: list[str] = Field(default_factory=lambda: ["GEMI-BTC05M*"], max_length=8)
    max_per_action_usd: StrictDecimal
    max_per_day_usd: StrictDecimal
    approval_threshold_usd: StrictDecimal
    max_actions_per_minute: int = Field(default=6, ge=1, le=60)
    days_valid: int = Field(default=7, ge=1, le=30)


def _slug(name: str) -> str:
    slug = "".join(c.lower() if c.isalnum() else "-" for c in name).strip("-")
    return "-".join(part for part in slug.split("-") if part) or "agent"


def _percentile(sorted_values: list[int], fraction: float) -> int | None:
    if not sorted_values:
        return None
    return sorted_values[min(len(sorted_values) - 1, int(fraction * len(sorted_values)))]


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


def create_api_app(services: Services, stack: "Stack | None" = None) -> FastAPI:
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

    # ------------------------------------------------------------- metrics

    @app.get("/metrics")
    async def metrics():
        decisions = services.audit.events(kind="decision", limit=100_000)
        latencies = sorted(e.payload["latency_us"] for e in decisions)
        by_verdict = {"ALLOW": 0, "REQUIRE_APPROVAL": 0, "DENY": 0}
        for event in decisions:
            by_verdict[event.payload["verdict"]] += 1
        return {
            "decisions": len(decisions),
            "by_verdict": by_verdict,
            "blocked": by_verdict["DENY"],
            "blocked_citing_untrusted_content": sum(
                1 for e in decisions if e.payload["verdict"] == "DENY" and e.payload["taint"]
            ),
            "policy_latency_us": {
                "p50": _percentile(latencies, 0.50),
                "p95": _percentile(latencies, 0.95),
                "max": latencies[-1] if latencies else None,
            },
            "freezes": len(services.audit.events(kind="freeze", limit=100_000)),
            "anomalies": len(services.audit.events(kind="anomaly", limit=100_000)),
        }

    @app.get("/redteam/scorecard")
    async def redteam_scorecard():
        from app.stack import read_scorecard

        scorecard = read_scorecard()
        if scorecard is None:
            return _error(404, "no scorecard yet: run python -m redteam.harness")
        return scorecard

    if stack is None:
        return app

    # ------------------------------------- routes that need the full stack

    @app.get("/config")
    async def config():
        return stack.config()

    @app.get("/state")
    async def state():
        return await stack.state()

    @app.post("/owner/agents", status_code=201)
    async def create_agent(body: CreateAgent):
        if stack.provisioner is None:
            return _error(503, "agents cannot be created here: the chain or the owner key is not available")
        from app.stack import _env

        agent_id = _slug(body.agent_name)
        if any(a["agent_id"] == agent_id for a in gate.agents()):
            return _error(409, f"an agent named {agent_id!r} already exists")
        payee, news_url = _env("X402_PAY_TO"), _env("X402_NEWS_URL")
        try:
            mandate = Mandate(
                agent_id=agent_id,
                agent_name=body.agent_name,
                allowed_action_types=("x402_payment", "gemini_order", "transfer"),
                allowed_markets=tuple(body.allowed_markets),
                allowed_payees=(payee,) if payee else (),
                allowed_endpoints=(news_url,) if news_url else (),
                max_per_action_usd=body.max_per_action_usd,
                max_per_day_usd=body.max_per_day_usd,
                approval_threshold_usd=body.approval_threshold_usd,
                max_actions_per_minute=body.max_actions_per_minute,
                expires_at=services.clock() + timedelta(days=body.days_valid),
            )
            await stack.create_agent(mandate)
        except (MandateError, ValueError) as exc:
            return _error(400, str(exc))
        except Exception as exc:  # chain errors: report them, the agent was not created
            return _error(502, f"{type(exc).__name__}: {exc}"[:400])
        return {"agent_id": agent_id, **(await stack.state())["agents"].get(agent_id, {})}

    @app.post("/owner/agents/{agent_id}/unfreeze")
    async def unfreeze(agent_id: str):
        gate.policy(agent_id)  # 404 if unknown
        try:
            signature = await stack.unfreeze(agent_id)
        except Exception as exc:
            return _error(502, f"{type(exc).__name__}: {exc}"[:400])
        return {"agent_id": agent_id, "signature": signature}

    @app.post("/agents/{agent_id}/cycle")
    async def run_cycle(agent_id: str):
        agent = stack.agents.get(agent_id)
        if agent is None:
            raise UnknownAgent(agent_id)
        return await agent.cycle()

    return app
