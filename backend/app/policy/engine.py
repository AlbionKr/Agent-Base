"""The policy engine: `evaluate(action, policy, state, ledger) -> Decision`.

A pure function. Same inputs, same decision, every time: no clock (time comes
in through `state.now`), no randomness, no network, no LLM. Every rule has an
ID and a plain-English reason; the decision lists every rule that fired.

The strictest verdict wins: DENY > REQUIRE_APPROVAL > ALLOW.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from typing import Callable, Iterable
from urllib.parse import urlsplit

from solders.pubkey import Pubkey

from app.policy.ledger import LedgerSnapshot, TaintMatch
from app.policy.models import (
    Action,
    Decision,
    GeminiOrderAction,
    Policy,
    PolicyState,
    RuleHit,
    TaintFinding,
    TransferAction,
    Verdict,
    X402PaymentAction,
    amount_usd,
    destination,
)

# Rule IDs, in evaluation order.
AGENT_FROZEN = "AGENT_FROZEN"
INPUT_INVALID = "INPUT_INVALID"
MANDATE_EXPIRED = "MANDATE_EXPIRED"
TAINT_NEW_PAYEE = "TAINT_NEW_PAYEE"
PAYEE_LOOKALIKE = "PAYEE_LOOKALIKE"
MANDATE_ACTION_TYPE = "MANDATE_ACTION_TYPE"
MARKET_NOT_ALLOWED = "MARKET_NOT_ALLOWED"
PAYEE_NOT_ALLOWLISTED = "PAYEE_NOT_ALLOWLISTED"
ENDPOINT_NOT_ALLOWED = "ENDPOINT_NOT_ALLOWED"
PER_ACTION_CAP = "PER_ACTION_CAP"
DAILY_BUDGET = "DAILY_BUDGET"
RATE_LIMIT = "RATE_LIMIT"
SLOW_DRAIN = "SLOW_DRAIN"
TAINT_PARAM = "TAINT_PARAM"
APPROVAL_THRESHOLD = "APPROVAL_THRESHOLD"

GEMINI_MIN_PRICE = Decimal("0.01")
GEMINI_MAX_PRICE = Decimal("0.99")
DAY = timedelta(hours=24)
MINUTE = timedelta(seconds=60)


class _Eval:
    """Scratchpad for one evaluation."""

    def __init__(self, action: Action, policy: Policy, state: PolicyState, ledger: LedgerSnapshot):
        self.action = action
        self.policy = policy
        self.state = state
        self.ledger = ledger
        self.amount = amount_usd(action)
        self.destination = destination(action)
        self.hits: list[RuleHit] = []
        self.taint: list[TaintFinding] = []

    def hit(self, rule_id: str, verdict: Verdict, reason: str) -> None:
        self.hits.append(RuleHit(rule_id=rule_id, verdict=verdict, reason=reason))

    def tainted(self, param: str, value: str, match: TaintMatch) -> None:
        self.taint.append(
            TaintFinding(
                param=param, value=value, source=match.source, item_id=match.item_id, via=match.via
            )
        )


def _short(address: str) -> str:
    return address if len(address) <= 12 else f"{address[:4]}…{address[-4:]}"


def _usd(value: Decimal) -> str:
    return f"${value.quantize(Decimal('0.01')) if value == value.quantize(Decimal('0.01')) else value}"


def _is_solana_address(value: str) -> bool:
    try:
        Pubkey.from_string(value)
    except Exception:
        return False
    return True


def _origin(url: str) -> str | None:
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    default = {"http": 80, "https": 443}[parts.scheme]
    try:
        port = parts.port
    except ValueError:
        return None
    host = parts.hostname.lower()
    return f"{parts.scheme}://{host}" if port in (None, default) else f"{parts.scheme}://{host}:{port}"


def _levenshtein(a: str, b: str, limit: int) -> int:
    """Edit distance, capped: returns `limit + 1` once it is clear the cap is exceeded."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        if min(current) > limit:
            return limit + 1
        previous = current
    return previous[-1]


# ------------------------------------------------------------------- rules


def _rule_frozen(e: _Eval) -> None:
    if e.state.frozen:
        e.hit(
            AGENT_FROZEN,
            Verdict.DENY,
            "This agent is frozen; nothing executes until the owner unfreezes it.",
        )


def _rule_input(e: _Eval) -> None:
    a = e.action
    if isinstance(a, GeminiOrderAction):
        if a.quantity <= 0:
            e.hit(INPUT_INVALID, Verdict.DENY, f"Order quantity must be positive, got {a.quantity}.")
        if not (GEMINI_MIN_PRICE <= a.price <= GEMINI_MAX_PRICE):
            e.hit(
                INPUT_INVALID,
                Verdict.DENY,
                f"Order price {a.price} is outside the valid range $0.01–$0.99.",
            )
        if not a.symbol.strip():
            e.hit(INPUT_INVALID, Verdict.DENY, "Order has no market symbol.")
        return
    if a.amount <= 0:
        e.hit(INPUT_INVALID, Verdict.DENY, f"Amount must be positive, got {a.amount}.")
    if not _is_solana_address(a.payee):
        e.hit(INPUT_INVALID, Verdict.DENY, f"Payee {a.payee!r} is not a valid Solana address.")
    if isinstance(a, X402PaymentAction) and _origin(a.url) is None:
        e.hit(INPUT_INVALID, Verdict.DENY, f"Payment URL {a.url!r} is not a valid http(s) URL.")


def _rule_mandate_expired(e: _Eval) -> None:
    if e.state.now >= e.policy.expires_at:
        e.hit(
            MANDATE_EXPIRED,
            Verdict.DENY,
            f"The mandate expired at {e.policy.expires_at.isoformat()}; the user must sign a new one.",
        )


def _rule_taint_new_payee(e: _Eval) -> None:
    a = e.action
    if isinstance(a, GeminiOrderAction) or a.payee in e.policy.allowed_payees:
        return
    match = e.ledger.find_text(a.payee)
    if match:
        e.tainted("payee", a.payee, match)
        e.hit(
            TAINT_NEW_PAYEE,
            Verdict.DENY,
            f"Payee {_short(a.payee)} came from {match.describe()} and is not on the "
            "allowlist. The agent was told where to send money by content it read.",
        )


def _rule_lookalike(e: _Eval) -> None:
    a = e.action
    if isinstance(a, GeminiOrderAction) or a.payee in e.policy.allowed_payees:
        return
    for trusted in sorted(e.policy.allowed_payees):
        same_ends = a.payee[:4] == trusted[:4] and a.payee[-4:] == trusted[-4:]
        if same_ends or a.payee.lower() == trusted.lower() or _levenshtein(a.payee, trusted, 2) <= 2:
            e.hit(
                PAYEE_LOOKALIKE,
                Verdict.DENY,
                f"Payee {_short(a.payee)} imitates allowlisted payee {_short(trusted)} but is a "
                "different address. This is the pattern of an address-poisoning attack.",
            )
            return


def _rule_action_type(e: _Eval) -> None:
    if e.action.type not in e.policy.allowed_action_types:
        e.hit(
            MANDATE_ACTION_TYPE,
            Verdict.DENY,
            f"The mandate does not permit {e.action.type} actions.",
        )


def _rule_market(e: _Eval) -> None:
    a = e.action
    if isinstance(a, GeminiOrderAction) and not e.policy.market_allowed(a.symbol):
        e.hit(
            MARKET_NOT_ALLOWED,
            Verdict.DENY,
            f"Market {a.symbol} is outside the mandate (allowed: "
            f"{', '.join(e.policy.allowed_markets) or 'none'}).",
        )


def _rule_payee_allowlist(e: _Eval) -> None:
    a = e.action
    if isinstance(a, GeminiOrderAction) or a.payee in e.policy.allowed_payees:
        return
    e.hit(
        PAYEE_NOT_ALLOWLISTED,
        Verdict.DENY,
        f"Payee {_short(a.payee)} is not on the allowlist.",
    )


def _rule_endpoint(e: _Eval) -> None:
    a = e.action
    if not isinstance(a, X402PaymentAction):
        return
    origin = _origin(a.url)
    if origin is not None and origin not in e.policy.allowed_endpoints:
        e.hit(
            ENDPOINT_NOT_ALLOWED,
            Verdict.DENY,
            f"Paid endpoint {origin} is not an approved service.",
        )


def _rule_per_action_cap(e: _Eval) -> None:
    if e.amount > e.policy.max_per_action_usd:
        e.hit(
            PER_ACTION_CAP,
            Verdict.DENY,
            f"{_usd(e.amount)} exceeds the per-action cap of {_usd(e.policy.max_per_action_usd)}.",
        )


def _rule_daily_budget(e: _Eval) -> None:
    since = e.state.now - DAY
    spent = sum(
        (p.amount_usd for p in e.state.history if p.executed and since < p.at <= e.state.now),
        Decimal(0),
    )
    if spent + e.amount > e.policy.max_per_day_usd:
        e.hit(
            DAILY_BUDGET,
            Verdict.DENY,
            f"{_usd(e.amount)} would bring the last 24h to {_usd(spent + e.amount)}, over the "
            f"daily budget of {_usd(e.policy.max_per_day_usd)} ({_usd(spent)} already spent).",
        )


def _rule_rate_limit(e: _Eval) -> None:
    since = e.state.now - MINUTE
    recent = sum(1 for p in e.state.history if since < p.at <= e.state.now)
    if recent >= e.policy.max_actions_per_minute:
        e.hit(
            RATE_LIMIT,
            Verdict.DENY,
            f"{recent} actions proposed in the last minute; the limit is "
            f"{e.policy.max_actions_per_minute}.",
        )


def _rule_slow_drain(e: _Eval) -> None:
    """Many small payments to destinations the agent has never used before."""
    window_start = e.state.now - timedelta(seconds=e.policy.slow_drain_window_seconds)
    executed = [p for p in e.state.history if p.executed and p.at <= e.state.now]
    established = {p.destination for p in executed if p.at <= window_start}
    fresh = {p.destination for p in executed if p.at > window_start} - established
    if e.destination in established:
        return
    fresh.add(e.destination)
    if len(fresh) > e.policy.max_new_destinations_per_window:
        minutes = e.policy.slow_drain_window_seconds // 60
        e.hit(
            SLOW_DRAIN,
            Verdict.DENY,
            f"This would be the {len(fresh)}th new destination in {minutes} minutes (limit "
            f"{e.policy.max_new_destinations_per_window}). Many small payments to new "
            "destinations is the pattern of a slow drain.",
        )


def _rule_taint_param(e: _Eval) -> None:
    """Parameters that trace back to untrusted content and are not trusted config."""
    a = e.action
    findings: list[tuple[str, str, TaintMatch]] = []

    if isinstance(a, GeminiOrderAction):
        if not e.policy.market_allowed(a.symbol):
            match = e.ledger.find_text(a.symbol)
            if match:
                findings.append(("symbol", a.symbol, match))
        for param, value in (("quantity", a.quantity), ("notional", e.amount)):
            match = e.ledger.find_amount(value)
            if match:
                findings.append((param, str(value), match))
                break
    else:
        match = e.ledger.find_amount(a.amount)
        if match:
            findings.append(("amount", str(a.amount), match))
        if isinstance(a, X402PaymentAction):
            origin = _origin(a.url)
            if origin is not None and origin not in e.policy.allowed_endpoints:
                host = urlsplit(a.url).hostname or ""
                match = e.ledger.find_text(a.url) or e.ledger.find_text(host)
                if match:
                    findings.append(("url", a.url, match))

    for param, value, match in findings:
        e.tainted(param, value, match)
        e.hit(
            TAINT_PARAM,
            Verdict.REQUIRE_APPROVAL,
            f"The {param} ({value}) matches {match.describe()}. A human must confirm the "
            "agent is not following instructions from content it read.",
        )


def _rule_approval_threshold(e: _Eval) -> None:
    if e.amount > e.policy.approval_threshold_usd:
        e.hit(
            APPROVAL_THRESHOLD,
            Verdict.REQUIRE_APPROVAL,
            f"{_usd(e.amount)} is above the approval threshold of "
            f"{_usd(e.policy.approval_threshold_usd)}.",
        )


RULES: tuple[tuple[str, Callable[[_Eval], None]], ...] = (
    (AGENT_FROZEN, _rule_frozen),
    (INPUT_INVALID, _rule_input),
    (MANDATE_EXPIRED, _rule_mandate_expired),
    (TAINT_NEW_PAYEE, _rule_taint_new_payee),
    (PAYEE_LOOKALIKE, _rule_lookalike),
    (MANDATE_ACTION_TYPE, _rule_action_type),
    (MARKET_NOT_ALLOWED, _rule_market),
    (PAYEE_NOT_ALLOWLISTED, _rule_payee_allowlist),
    (ENDPOINT_NOT_ALLOWED, _rule_endpoint),
    (PER_ACTION_CAP, _rule_per_action_cap),
    (DAILY_BUDGET, _rule_daily_budget),
    (RATE_LIMIT, _rule_rate_limit),
    (SLOW_DRAIN, _rule_slow_drain),
    (TAINT_PARAM, _rule_taint_param),
    (APPROVAL_THRESHOLD, _rule_approval_threshold),
)
RULE_IDS: tuple[str, ...] = tuple(rule_id for rule_id, _ in RULES)


def _unique(values: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


def _summary(action: Action, amount: Decimal) -> str:
    if isinstance(action, GeminiOrderAction):
        return (
            f"{action.side} {action.quantity} {action.outcome.upper()} on {action.symbol} "
            f"at ${action.price} ({_usd(amount)})"
        )
    if isinstance(action, TransferAction):
        return f"transfer {_usd(amount)} to {_short(action.payee)}"
    return f"pay {_usd(amount)} to {_origin(action.url) or action.url}"


def describe_action(action: Action) -> str:
    """One line a human can approve or refuse, built only from the validated action."""
    return _summary(action, amount_usd(action))


def evaluate(
    action: Action,
    policy: Policy,
    state: PolicyState,
    ledger: LedgerSnapshot = LedgerSnapshot(),
) -> Decision:
    """Decide whether `action` may execute. Deterministic; see module docstring."""
    e = _Eval(action, policy, state, ledger)
    for _, rule in RULES:
        rule(e)

    if not e.hits:
        return Decision(
            verdict=Verdict.ALLOW,
            rules_fired=(),
            reason=f"Within the mandate: {_summary(action, e.amount)}.",
        )

    verdict = max((h.verdict for h in e.hits), key=lambda v: v.severity)
    decisive = [h for h in e.hits if h.verdict is verdict]
    return Decision(
        verdict=verdict,
        rules_fired=_unique(h.rule_id for h in e.hits),
        reason=decisive[0].reason,
        hits=tuple(e.hits),
        taint=tuple(e.taint),
    )
