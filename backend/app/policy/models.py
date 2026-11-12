"""Policy engine data types: actions the agent may propose, and the decision."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from app.decimals import StrictDecimal


class Verdict(str, Enum):
    ALLOW = "ALLOW"
    REQUIRE_APPROVAL = "REQUIRE_APPROVAL"
    DENY = "DENY"

    @property
    def severity(self) -> int:
        return {"ALLOW": 0, "REQUIRE_APPROVAL": 1, "DENY": 2}[self.value]


# ---------------------------------------------------------------- actions


class _Action(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str = ""
    # Free text from the LLM. Logged for humans; never read by any rule.
    rationale: str = ""


class X402PaymentAction(_Action):
    """Pay for an HTTP resource (USDC, x402 `exact` scheme)."""

    type: Literal["x402_payment"] = "x402_payment"
    url: str
    payee: str
    amount: StrictDecimal


class GeminiOrderAction(_Action):
    """Limit order on a Gemini prediction market."""

    type: Literal["gemini_order"] = "gemini_order"
    symbol: str
    side: Literal["buy", "sell"]
    outcome: Literal["yes", "no"]
    quantity: StrictDecimal
    price: StrictDecimal


class TransferAction(_Action):
    """Direct USDC transfer from the vault to a payee."""

    type: Literal["transfer"] = "transfer"
    payee: str
    amount: StrictDecimal


Action = Annotated[
    Union[X402PaymentAction, GeminiOrderAction, TransferAction],
    Field(discriminator="type"),
]
ACTION_ADAPTER: TypeAdapter[Action] = TypeAdapter(Action)
ACTION_TYPES = ("x402_payment", "gemini_order", "transfer")


def parse_action(raw: dict) -> Action:
    """Validate an LLM-proposed action. Raises pydantic.ValidationError."""
    return ACTION_ADAPTER.validate_python(raw)


def amount_usd(action: Action) -> Decimal:
    """Money at risk if the action executes. USDC is counted 1:1 as USD."""
    if isinstance(action, GeminiOrderAction):
        return action.quantity * action.price
    return action.amount


def destination(action: Action) -> str:
    """Where the money goes: a payee address, or a market symbol."""
    if isinstance(action, GeminiOrderAction):
        return action.symbol
    return action.payee


# ------------------------------------------------------- policy and state


@dataclass(frozen=True)
class Policy:
    """Trusted configuration, compiled from a user-signed mandate."""

    agent_id: str
    owner: str
    allowed_action_types: frozenset[str]
    # Exact instrument symbols, or prefixes ending in "*".
    allowed_markets: tuple[str, ...]
    allowed_payees: frozenset[str]
    # Origins, e.g. "http://127.0.0.1:4021".
    allowed_endpoints: frozenset[str]
    max_per_action_usd: Decimal
    max_per_day_usd: Decimal
    approval_threshold_usd: Decimal
    max_actions_per_minute: int
    expires_at: datetime
    mandate_hash: bytes
    max_new_destinations_per_window: int = 3
    slow_drain_window_seconds: int = 600

    def market_allowed(self, symbol: str) -> bool:
        for pattern in self.allowed_markets:
            if pattern.endswith("*"):
                if symbol.startswith(pattern[:-1]):
                    return True
            elif symbol == pattern:
                return True
        return False


@dataclass(frozen=True)
class PastAction:
    """One earlier proposal by this agent, as recorded in the audit log."""

    at: datetime
    action_type: str
    destination: str
    amount_usd: Decimal
    verdict: Verdict
    executed: bool


@dataclass(frozen=True)
class PolicyState:
    """Everything time-dependent, passed in so `evaluate` stays a pure function."""

    now: datetime
    history: tuple[PastAction, ...] = ()


# --------------------------------------------------------------- decision


class TaintFinding(BaseModel):
    model_config = ConfigDict(frozen=True)

    param: str
    value: str
    source: str
    item_id: str
    via: str


class RuleHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule_id: str
    verdict: Verdict
    reason: str


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True)

    verdict: Verdict
    rules_fired: tuple[str, ...]
    reason: str
    hits: tuple[RuleHit, ...] = ()
    taint: tuple[TaintFinding, ...] = ()
