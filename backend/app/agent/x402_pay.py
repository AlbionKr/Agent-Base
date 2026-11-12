"""Pay for an x402 resource from the agent's hot wallet.

The gate approved "pay AMOUNT to PAYEE for URL". The server then states its own
price in the 402 challenge. Before anything is signed, `choose_offer` checks the
challenge against the approval: same payee, same asset, same network, `exact`
scheme, and a price no higher than approved. A server (or a tampered response)
cannot make the agent pay someone else or pay more.

If the hot wallet is short, it is topped up from the vault first. That top-up is
an onchain `top_up`, so the vault's caps apply to it.
"""

from __future__ import annotations

from typing import Any, Sequence

import httpx

from app.chain.client import SolanaClient
from app.chain.ops import SessionKeys, VaultRef, VaultSpender
from app.chain.units import usdc_to_units
from app.policy.models import Action, X402PaymentAction

MIN_TOP_UP_UNITS = 100_000  # top up 0.10 USDC at a time so most payments need no vault call


class PaymentRefused(Exception):
    pass


def _field(offer: Any, name: str) -> Any:
    return offer.get(name) if isinstance(offer, dict) else getattr(offer, name, None)


def choose_offer(
    offers: Sequence[Any], *, payee: str, approved_units: int, network: str, asset: str
) -> int:
    """Index of the offer that matches the approval. Raises PaymentRefused with the reason."""
    reasons: list[str] = []
    for index, candidate in enumerate(offers):
        amount = str(_field(candidate, "amount") or "")
        if _field(candidate, "scheme") != "exact":
            reasons.append(f"offer {index}: scheme {_field(candidate, 'scheme')!r} is not exact")
        elif _field(candidate, "network") != network:
            reasons.append(f"offer {index}: network {_field(candidate, 'network')!r} is not {network}")
        elif _field(candidate, "asset") != asset:
            reasons.append(f"offer {index}: asset {_field(candidate, 'asset')!r} is not USDC ({asset})")
        elif _field(candidate, "pay_to") != payee:
            reasons.append(
                f"offer {index}: payee {_field(candidate, 'pay_to')!r} is not the approved payee {payee}"
            )
        elif not (amount.isascii() and amount.isdigit()):
            reasons.append(f"offer {index}: amount {amount!r} is not a whole number of base units")
        elif int(amount) > approved_units:
            reasons.append(f"offer {index}: asks {amount} units, more than the {approved_units} approved")
        else:
            return index
    raise PaymentRefused("; ".join(reasons) or "the server offered no way to pay")


class X402Payer:
    """Executor handler for `x402_payment` actions."""

    def __init__(
        self,
        *,
        chain: SolanaClient,
        ref: VaultRef,
        keys: SessionKeys,
        spender: VaultSpender,
        network: str,
        rpc_url: str,
    ):
        self._chain, self._ref, self._keys, self._spender = chain, ref, keys, spender
        self._network, self._rpc_url = network, rpc_url

    async def __call__(self, agent_id: str, action: Action) -> dict[str, Any]:
        from x402 import x402Client
        from x402.http.x402_http_client import x402HTTPClient
        from x402.mechanisms.svm.exact.register import register_exact_svm_client
        from x402.mechanisms.svm.signers import KeypairSigner

        assert isinstance(action, X402PaymentAction)
        approved_units = usdc_to_units(action.amount)
        session = self._keys.load(agent_id)
        client = register_exact_svm_client(
            x402Client(), KeypairSigner(session), networks=self._network, rpc_url=self._rpc_url
        )
        http_x402 = x402HTTPClient(client)

        async with httpx.AsyncClient(timeout=30) as http:
            challenge = await http.get(action.url)
            if challenge.status_code != 402:
                raise PaymentRefused(
                    f"expected a 402 payment challenge from {action.url}, got HTTP {challenge.status_code}"
                )
            headers = {name.upper(): value for name, value in challenge.headers.items()}
            required = http_x402.get_payment_required_response(headers.get, None)
            index = choose_offer(
                required.accepts,
                payee=action.payee,
                approved_units=approved_units,
                network=self._network,
                asset=str(self._ref.usdc_mint),
            )
            offer = required.accepts[index]
            price_units = int(offer.amount)

            detail: dict[str, Any] = {"via": "x402.exact", "price_units": str(price_units)}
            balance = await self._chain.token_balance(self._ref.token_account(session.pubkey())) or 0
            if balance < price_units:
                state = await self._spender.state(agent_id)
                top_up = max(price_units - balance, MIN_TOP_UP_UNITS)
                if state is not None:
                    top_up = min(top_up, state.per_tx_cap)
                detail["top_up_signature"] = await self._spender.top_up(agent_id, top_up)
                detail["top_up_units"] = str(top_up)

            # Sign only the offer that passed the check.
            payload = await client.create_payment_payload(required.model_copy(update={"accepts": [offer]}))
            paid = await http.get(action.url, headers=http_x402.encode_payment_signature_header(payload))
            if paid.status_code != 200:
                raise PaymentRefused(f"payment was not accepted: HTTP {paid.status_code} {paid.text[:200]}")
            paid_headers = {name.upper(): value for name, value in paid.headers.items()}
            settlement = http_x402.get_payment_settle_response(paid_headers.get)
            detail["signature"] = getattr(settlement, "transaction", None)
            detail["payer"] = str(session.pubkey())
            detail["response"] = paid.json()
            return detail
