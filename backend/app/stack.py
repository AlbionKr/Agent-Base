"""Composition root: builds everything the backend runs, from the environment.

What is configured is used; what is not is reported as not configured, in
`labels`, in /config and in the UI. Nothing is substituted silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

from solders.pubkey import Pubkey

from app.agent.proposer import AnthropicProposer, Proposer, ScriptedProposer
from app.agent.runtime import MarketSource, NaiveAgent, NewsPlan, ProtectedAgent
from app.agent.x402_pay import X402Payer
from app.api.services import Services, build_services
from app.audit import AuditLog
from app.chain.client import SolanaClient, load_keypair
from app.chain.ops import (
    GuardianFreezer,
    PlainWallet,
    Provisioner,
    SessionKeys,
    VaultRef,
    VaultSpender,
    cluster_name,
    explorer_cluster_param,
    onchain_agent_id,
)
from app.chain.units import units_to_usdc
from app.config import REPO_ROOT, BackendSettings, ChainSettings, load_chain_settings
from app.executor import Executor, Handler, vault_transfer_handler
from app.gemini.markets import GeminiMarketsClient
from app.gemini.trading import GeminiTrader
from app.policy.mandate import Mandate, SignedMandate
from app.quicknode.local_relay import LocalStreamRelay

log = logging.getLogger("agentbase")

NO_PROPOSER_LABEL = "NO PROPOSER (no LLM key configured)"


def _env(name: str, default: str = "") -> str:
    return os.environ.get(name, "").strip() or default


def _usd(units: int | None) -> str | None:
    return None if units is None else format(units_to_usdc(units), "f")


def gemini_market_source(base_url: str, patterns: tuple[str, ...], limit: int = 6) -> MarketSource | None:
    """Live public market data for the mandate's markets (e.g. GEMI-BTC05M* -> ticker prefix BTC05M)."""
    prefixes = [p[len("GEMI-"):].rstrip("*") for p in patterns if p.startswith("GEMI-") and p.endswith("*")]
    if not prefixes:
        return None

    async def fetch() -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        async with GeminiMarketsClient(base_url) as client:
            for prefix in prefixes:
                for event in await client.find_events(ticker_prefix=prefix):
                    for contract in event.contracts:
                        markets.append(
                            {
                                "symbol": contract.instrument_symbol,
                                "title": event.title,
                                "description": contract.description_text[:400],
                                "best_bid": None if contract.prices.best_bid is None else str(contract.prices.best_bid),
                                "best_ask": None if contract.prices.best_ask is None else str(contract.prices.best_ask),
                                "expires": contract.expiry_date.isoformat() if contract.expiry_date else None,
                            }
                        )
                        if len(markets) >= limit:
                            return markets
        return markets

    return fetch


@dataclass
class Stack:
    settings: BackendSettings
    chain_settings: ChainSettings
    services: Services
    executor: Executor
    labels: dict[str, str]
    chain: SolanaClient | None = None
    ref: VaultRef | None = None
    keys: SessionKeys | None = None
    spender: VaultSpender | None = None
    provisioner: Provisioner | None = None
    relay: LocalStreamRelay | None = None
    naive: NaiveAgent | None = None
    naive_wallet: PlainWallet | None = None
    attacker: str = ""
    agents: dict[str, ProtectedAgent] = field(default_factory=dict)
    hot_wallets: dict[str, Pubkey] = field(default_factory=dict)
    _last_balances: dict | None = None

    # ------------------------------------------------------------ agents

    def default_proposer(self, profile: str = "standard") -> Proposer:
        if _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN"):
            return AnthropicProposer(model=_env("LLM_MODEL", "claude-opus-5-5"), profile=profile)
        idle = ScriptedProposer(lambda context: [])
        idle.label = NO_PROPOSER_LABEL
        return idle

    def _news_plan(self, mandate: Mandate) -> NewsPlan | None:
        url, payee = _env("X402_NEWS_URL"), _env("X402_PAY_TO")
        if not url or not payee or payee not in mandate.allowed_payees:
            return None
        price = Decimal(_env("X402_NEWS_PRICE", "$0.001").lstrip("$"))
        return NewsPlan(url=f"{url.rstrip('/')}/news?topic=btc", payee=payee, price=price)

    def _attach(self, signed: SignedMandate, proposer: Proposer | None) -> ProtectedAgent:
        mandate = signed.mandate
        gate = self.services.gate
        gate.register_agent(signed)
        if self.keys is not None and self.keys.path(mandate.agent_id).exists():
            wallet = self.keys.load(mandate.agent_id).pubkey()
            self.hot_wallets[mandate.agent_id] = wallet
            self.services.sentinel.watch(mandate.agent_id, str(wallet))
        agent = ProtectedAgent(
            agent_id=mandate.agent_id,
            gate=gate,
            executor=self.executor,
            proposer=proposer or self.default_proposer(),
            journal=self.services.journal,
            news=self._news_plan(mandate),
            markets=gemini_market_source(_env("GEMINI_MARKET_DATA_URL", "https://api.gemini.com"), mandate.allowed_markets),
        )
        self.agents[mandate.agent_id] = agent
        return agent

    async def create_agent(self, mandate: Mandate, proposer: Proposer | None = None) -> ProtectedAgent:
        """Sign the mandate, create the onchain allowance and session key, start watching the wallet."""
        if self.provisioner is None:
            raise RuntimeError("cannot create agents: the chain or the owner key is not available here")
        provisioned = await self.provisioner.create_agent(mandate)
        agent = self._attach(provisioned.signed_mandate, proposer)
        self.services.journal.emit(
            "agent_provisioned",
            {
                "hot_wallet": str(provisioned.hot_wallet),
                "allowance": str(provisioned.allowance),
                "onchain_agent_id": str(onchain_agent_id(mandate.agent_id)),
                "signatures": list(provisioned.signatures),
            },
            agent_id=mandate.agent_id,
        )
        await self.emit_balances()
        return agent

    async def restore(self) -> list[str]:
        """After a restart: re-register agents from the audit log and re-read freeze state from the chain."""
        latest: dict[str, dict] = {}
        for event in self.services.audit.events(kind="agent_registered", limit=100_000):
            latest[event.agent_id] = event.payload
        restored = []
        for agent_id, payload in latest.items():
            try:
                signed = SignedMandate(
                    mandate=Mandate(**payload["mandate"]), signer=payload["signer"], signature=payload["signature"]
                )
                self._attach(signed, None)
            except Exception as exc:  # expired or unverifiable mandates stay unregistered
                log.warning("agent %s not restored: %s", agent_id, exc)
                continue
            if self.spender is not None:
                state = await self.spender.state(agent_id)
                if state is not None and state.frozen:
                    self.services.gate.set_frozen(agent_id, True)
            restored.append(agent_id)
        return restored

    async def unfreeze(self, agent_id: str) -> str:
        if self.provisioner is None:
            raise RuntimeError("cannot unfreeze: the owner key is not available here")
        signature = await self.provisioner.unfreeze(agent_id)
        self.services.gate.set_frozen(agent_id, False)
        self.services.sentinel.clear(agent_id)
        self.services.journal.emit("unfreeze", {"signature": signature, "by": "owner"}, agent_id=agent_id)
        return signature

    # ------------------------------------------------------------- state

    async def state(self) -> dict[str, Any]:
        """Balances and onchain allowances, read from the chain right now."""
        out: dict[str, Any] = {"vault_usdc": None, "agents": {}, "naive": None, "attacker_usdc": None}
        if self.chain is None or self.ref is None:
            return out
        chain, ref = self.chain, self.ref
        out["vault_usdc"] = _usd(await chain.token_balance(ref.vault_token_account))
        for agent_id, wallet in self.hot_wallets.items():
            hot = await chain.token_balance(ref.token_account(wallet)) or 0
            allowance = await self.spender.state(agent_id) if self.spender else None
            entry: dict[str, Any] = {"hot_wallet": str(wallet), "hot_wallet_usdc": _usd(hot)}
            if allowance is not None:
                remaining = max(allowance.daily_cap - allowance.spent_today, 0)
                entry.update(
                    {
                        "allowance": str(ref.allowance(agent_id)),
                        "per_tx_cap_usdc": _usd(allowance.per_tx_cap),
                        "daily_cap_usdc": _usd(allowance.daily_cap),
                        "spent_today_usdc": _usd(allowance.spent_today),
                        "frozen_onchain": allowance.frozen,
                        # What a full compromise of this agent could still take today: what is
                        # left of the daily allowance (nothing once frozen) plus the hot wallet.
                        "max_loss_bound_usdc": _usd(hot if allowance.frozen else remaining + hot),
                    }
                )
            out["agents"][agent_id] = entry
        if self.naive_wallet is not None:
            out["naive"] = {
                "wallet": str(self.naive_wallet.address),
                "usdc": _usd(await self.naive_wallet.balance()),
            }
        if self.attacker:
            out["attacker_usdc"] = _usd(await chain.token_balance(ref.token_account(self.attacker)) or 0)
        return out

    async def emit_balances(self) -> None:
        state = await self.state()
        if state != self._last_balances:
            self._last_balances = state
            self.services.journal.emit("balances", state)

    def config(self) -> dict[str, Any]:
        ref = self.ref
        return {
            "labels": self.labels,
            "cluster": cluster_name(self.chain_settings.rpc_url),
            "explorer_cluster_param": explorer_cluster_param(self.chain_settings.rpc_url),
            "program_id": self.chain_settings.vault_program_id,
            "usdc_mint": self.chain_settings.usdc_mint,
            "vault": str(ref.vault) if ref else None,
            "vault_owner": self.settings.owner_address,
            "attacker": self.attacker or None,
            "can_create_agents": self.provisioner is not None,
            "approval_timeout_seconds": self.settings.approval_timeout_seconds,
        }

    # ---------------------------------------------------------- lifecycle

    async def run_background(self) -> None:
        """Balance watcher and (on localnet) the stream relay. Runs until cancelled."""

        async def balances() -> None:
            while True:
                try:
                    await self.emit_balances()
                except Exception:
                    log.exception("reading balances failed")
                await asyncio.sleep(2.0)

        tasks = [balances()]
        if self.relay is not None:
            tasks.append(self.relay.run_forever())
        await asyncio.gather(*tasks)

    async def close(self) -> None:
        if self.chain is not None:
            await self.chain.rpc.close()


async def build_stack(settings: BackendSettings, *, audit: AuditLog | None = None) -> Stack:
    chain_settings = load_chain_settings()
    cluster = cluster_name(chain_settings.rpc_url)
    labels: dict[str, str] = {"cluster": cluster.upper()}
    if cluster == "localnet":
        labels["usdc"] = "LOCALNET stand-in (not Circle USDC)"

    # ---- chain
    chain: SolanaClient | None = SolanaClient(chain_settings.rpc_url)
    ref: VaultRef | None = VaultRef(
        Pubkey.from_string(chain_settings.vault_program_id),
        Pubkey.from_string(chain_settings.usdc_mint),
        Pubkey.from_string(settings.owner_address),
    )
    try:
        if await chain.account_data(ref.program_id) is None:
            raise RuntimeError("vault program is not deployed on this cluster")
        if await chain.account_data(ref.vault) is None:
            raise RuntimeError("vault is not initialized (run scripts.seed_devnet)")
        labels["chain"] = f"connected ({cluster})"
    except Exception as exc:
        labels["chain"] = f"UNAVAILABLE: {exc}"[:200]
        await chain.rpc.close()
        chain, ref = None, None

    keys = SessionKeys(REPO_ROOT / ".keys" / "agents")
    spender = VaultSpender(chain, ref, keys) if chain and ref else None
    freezer = None
    if chain and ref and chain_settings.guardian_keypair_path.exists():
        freezer = GuardianFreezer(chain, ref, load_keypair(chain_settings.guardian_keypair_path))
    labels["onchain_freeze"] = "guardian key loaded" if freezer else "NOT CONFIGURED"

    # ---- stream
    webhook_secret = settings.quicknode_security_token
    use_relay = chain is not None and cluster == "localnet"
    if use_relay:
        webhook_secret = webhook_secret or secrets.token_hex(24)  # this run only, never stored
        labels["stream"] = "LOCAL RELAY (not QuickNode)"
    else:
        labels["stream"] = "QuickNode Streams" if webhook_secret else "NOT CONFIGURED (no stream token)"

    services = build_services(
        audit=audit or AuditLog(settings.audit_db_path),
        trusted_owner=settings.owner_address,
        usdc_mint=chain_settings.usdc_mint,
        webhook_secret=webhook_secret,
        allowed_origins=settings.frontend_origins,
        freezer=freezer,
        approval_timeout_seconds=settings.approval_timeout_seconds,
    )

    # ---- executor handlers
    handlers: dict[str, Handler] = {}
    if chain and ref and spender:
        handlers["transfer"] = vault_transfer_handler(spender)
        handlers["x402_payment"] = X402Payer(
            chain=chain, ref=ref, keys=keys, spender=spender,
            network=chain_settings.network_caip2, rpc_url=chain_settings.rpc_url,
        )
    if _env("GEMINI_API_KEY") and _env("GEMINI_API_SECRET"):
        handlers["gemini_order"] = GeminiTrader(
            api_key=_env("GEMINI_API_KEY"),
            api_secret=_env("GEMINI_API_SECRET"),
            max_notional_usd=Decimal(_env("GEMINI_MAX_ORDER_NOTIONAL_USD", "1.00")),
            environment=_env("GEMINI_TRADING_ENV", "sandbox"),
        )
        labels["gemini_orders"] = "SANDBOX"
    else:
        labels["gemini_orders"] = "NOT CONFIGURED (no sandbox API key)"
    labels["gemini_market_data"] = "live public API (read-only)"
    labels["news_feed"] = "RECORDED FEED" if _env("FEED_MODE", "recorded") == "recorded" else "LIVE FEED"
    labels["x402_facilitator"] = _env("X402_FACILITATOR_URL", "https://x402.org/facilitator")
    labels["llm"] = (
        f"Claude ({_env('LLM_MODEL', 'claude-opus-5-5')})"
        if _env("ANTHROPIC_API_KEY") or _env("ANTHROPIC_AUTH_TOKEN")
        else "NOT CONFIGURED (no API key)"
    )
    labels["voice"] = "ElevenLabs Agents" if _env("ELEVENLABS_API_KEY") and _env("ELEVENLABS_AGENT_ID") else "NOT CONFIGURED (no ElevenLabs key)"

    executor = Executor(services.gate, handlers)

    def run_approved(action_id: str) -> None:
        asyncio.ensure_future(executor.execute(action_id))

    services.gate.on_approved = run_approved

    stack = Stack(
        settings=settings, chain_settings=chain_settings, services=services, executor=executor,
        labels=labels, chain=chain, ref=ref, keys=keys, spender=spender,
        attacker=_env("DEMO_ATTACKER_ADDRESS"),
    )

    # ---- owner-side provisioning (the operator's machine holds the owner key)
    if chain and ref and chain_settings.owner_keypair_path.exists():
        owner = load_keypair(chain_settings.owner_keypair_path)
        if str(owner.pubkey()) == settings.owner_address:
            stack.provisioner = Provisioner(chain, ref, owner, keys)

    # ---- naive agent's own wallet
    naive_key = REPO_ROOT / ".keys" / "naive-agent.json"
    if chain and ref and naive_key.exists():
        stack.naive_wallet = PlainWallet(chain, ref, load_keypair(naive_key))

    if use_relay and chain and ref:
        def watched_accounts():
            accounts = [ref.token_account(wallet) for wallet in stack.hot_wallets.values()]
            return accounts

        stack.relay = LocalStreamRelay(
            chain=chain,
            token_accounts=watched_accounts,
            webhook_url=f"http://127.0.0.1:{settings.webhook_port}/webhooks/quicknode",
            secret=webhook_secret,
        )
    return stack


def scorecard_path() -> Path:
    return REPO_ROOT / "redteam" / "scorecard.json"


def read_scorecard() -> dict | None:
    path = scorecard_path()
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
