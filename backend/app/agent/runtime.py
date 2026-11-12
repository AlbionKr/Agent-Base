"""Agent decision loops.

ProtectedAgent, one cycle:
    1. read the markets in its mandate                     (untrusted text -> Context Ledger)
    2. buy news over x402, as a proposal through the gate  (untrusted text -> Context Ledger)
    3. the proposer (LLM) suggests actions
    4. the gate decides each one: ALLOW -> executor, REQUIRE_APPROVAL -> waits for a human,
       DENY -> nothing happens
Every step is an audit event.

NaiveAgent: the same proposer, no gate, its own small wallet. Whatever it is told
to do, it does. It exists so a drain is real and visible next to the protected one.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Awaitable, Callable, Protocol

from pydantic import ValidationError

from app.agent.proposer import CycleContext, Proposer
from app.chain.units import usdc_to_units
from app.events import Journal
from app.executor import Executor
from app.gate import Gate
from app.policy.models import GeminiOrderAction, Verdict, amount_usd, parse_action

MarketSource = Callable[[], Awaitable[list[dict[str, Any]]]]


@dataclass(frozen=True)
class NewsPlan:
    """Where the agent buys news. Trusted configuration, still checked by the gate."""

    url: str
    payee: str
    price: Decimal


def _item_text(item: dict[str, Any]) -> str:
    return " ".join(str(item.get(key, "")) for key in ("author", "text", "url")).strip()


class ProtectedAgent:
    def __init__(
        self,
        *,
        agent_id: str,
        gate: Gate,
        executor: Executor,
        proposer: Proposer,
        journal: Journal,
        news: NewsPlan | None = None,
        markets: MarketSource | None = None,
    ):
        self.agent_id = agent_id
        self.proposer = proposer
        self._gate, self._executor, self._journal = gate, executor, journal
        self._news, self._markets = news, markets
        self._cycles = 0
        self.last_news: list[dict[str, Any]] = []

    async def cycle(self) -> dict[str, Any]:
        self._cycles += 1
        gate, agent_id = self._gate, self.agent_id
        policy = gate.policy(agent_id)
        self._journal.emit(
            "cycle_started", {"cycle": self._cycles, "proposer": self.proposer.label}, agent_id=agent_id
        )

        markets: list[dict[str, Any]] = []
        if self._markets is not None:
            try:
                markets = await self._markets()
            except Exception as exc:
                self._journal.emit(
                    "cycle_note", {"note": f"market data unavailable: {type(exc).__name__}: {exc}"[:300]},
                    agent_id=agent_id,
                )
            for market in markets:
                text = " ".join(str(market.get(k, "")) for k in ("title", "description")).strip()
                if text:
                    gate.observe(agent_id, "gemini-market", str(market.get("symbol", "?")), text)

        news = await self._buy_news()
        self.last_news = news

        context = CycleContext(
            agent_id=agent_id,
            agent_name=next(a["agent_name"] for a in gate.agents() if a["agent_id"] == agent_id),
            mandate={
                "allowed_action_types": sorted(policy.allowed_action_types),
                "allowed_markets": list(policy.allowed_markets),
                "allowed_payees": sorted(policy.allowed_payees),
                "allowed_endpoints": sorted(policy.allowed_endpoints),
                "max_per_action_usd": str(policy.max_per_action_usd),
                "max_per_day_usd": str(policy.max_per_day_usd),
                "approval_threshold_usd": str(policy.approval_threshold_usd),
            },
            markets=markets,
            news=news,
        )
        proposal = await self.proposer.propose(context)
        if proposal.note:
            self._journal.emit("cycle_note", {"note": proposal.note}, agent_id=agent_id)

        outcomes = [await self._submit(raw) for raw in proposal.actions]
        summary = {
            "cycle": self._cycles,
            "proposer": self.proposer.label,
            "rationale": proposal.rationale[:2000],
            "news_items": len(news),
            "proposed": len(outcomes),
            "outcomes": outcomes,
        }
        self._journal.emit("cycle_finished", summary, agent_id=agent_id)
        return summary

    async def _buy_news(self) -> list[dict[str, Any]]:
        if self._news is None:
            return []
        plan = self._news
        outcome = await self._submit(
            {"type": "x402_payment", "url": plan.url, "payee": plan.payee, "amount": str(plan.price)}
        )
        response = (outcome.get("detail") or {}).get("response") or {}
        items = response.get("items") if isinstance(response, dict) else None
        if not isinstance(items, list):
            return []
        news = [item for item in items if isinstance(item, dict)]
        for item in news:
            # Registered before the proposer sees it.
            self._gate.observe(
                self.agent_id, f"x402-news/{item.get('source', '?')}", str(item.get("id", "?")), _item_text(item)
            )
        return news

    async def _submit(self, raw: Any) -> dict[str, Any]:
        """Propose one action; execute it only if the gate allows it right now."""
        proposal = self._gate.propose(self.agent_id, raw)
        outcome: dict[str, Any] = {
            "action_id": proposal.action_id,
            "verdict": proposal.decision.verdict.value,
            "rules_fired": list(proposal.decision.rules_fired),
        }
        if proposal.decision.verdict is Verdict.ALLOW:
            event = await self._executor.execute(proposal.action_id)
            outcome["executed"] = event.payload["ok"]
            outcome["detail"] = event.payload["detail"]
        return outcome


# ----------------------------------------------------------------------- naive


class Wallet(Protocol):
    async def send(self, payee: str, amount_units: int) -> str: ...


class NaiveAgent:
    """No policy engine, no vault. Label everywhere: NAIVE."""

    label = "NAIVE (no policy engine)"

    def __init__(self, *, name: str, proposer: Proposer, journal: Journal, wallet: Wallet | None):
        self.name = name
        self.proposer = proposer
        self._journal = journal
        self._wallet = wallet

    async def cycle(self, news: list[dict[str, Any]], markets: list[dict[str, Any]] | None = None) -> dict:
        context = CycleContext(
            agent_id=self.name, agent_name=self.name, mandate={"note": "none: this agent has no mandate"},
            markets=markets or [], news=news,
        )
        proposal = await self.proposer.propose(context)
        results = [await self._do(raw) for raw in proposal.actions]
        summary = {"proposer": self.proposer.label, "proposed": len(results), "results": results}
        self._journal.emit("naive_cycle", summary, agent_id=self.name)
        return summary

    async def _do(self, raw: Any) -> dict[str, Any]:
        try:
            if not isinstance(raw, dict):
                raise TypeError("not an action")
            action = parse_action({k: v for k, v in raw.items() if k != "action_id"})
        except (ValidationError, TypeError) as exc:
            result = {"executed": False, "error": f"not an executable action: {type(exc).__name__}"}
        else:
            base = {"action": action.model_dump(mode="json"), "amount_usd": str(amount_usd(action))}
            if isinstance(action, GeminiOrderAction):
                result = {**base, "executed": False, "error": "the naive agent has no exchange account"}
            elif self._wallet is None:
                result = {**base, "executed": False, "error": "no wallet configured"}
            else:
                try:
                    signature = await self._wallet.send(action.payee, usdc_to_units(action.amount))
                    result = {**base, "executed": True, "signature": signature, "payee": action.payee}
                except Exception as exc:
                    result = {**base, "executed": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        self._journal.emit("naive_action", result, agent_id=self.name)
        return result
