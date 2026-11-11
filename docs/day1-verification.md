# Day-1 verification report

Run 2026-11-11. Everything below was checked against the live service, not taken from docs alone, unless marked otherwise.

**Outcome: all four dependencies work on Solana devnet. No Arc fallback.**

| # | Check | Result | Evidence |
|---|---|---|---|
| 1 | Gemini Prediction Markets public API | Works | `GET https://api.gemini.com/v1/prediction-markets/events?status=active` → HTTP 200, 1000 active events. BTC 5-minute contracts present (`GEMI-BTC05M<yymmddhhmm>-UP`), `quantityMinimum` `0.01`, `priceIncrement` `0.01`. |
| 1b | Gemini sandbox for prediction markets | Partly verified | `https://api.sandbox.gemini.com` serves PM events (HTTP 200, ~90 active, Crypto + Commodities). Order placement is **not yet verified** — it needs a sandbox API key (first task of M3). |
| 2 | QuickNode Streams on Solana devnet | Supported (docs) | Supported-chains table lists Solana Devnet with the `Block` and `Programs + Logs` datasets. Free plan follows the chain tip only. Webhooks carry `X-QN-Nonce`, `X-QN-Timestamp`, `X-QN-Signature` (HMAC-SHA256 over `nonce + timestamp + payload`). Live stream is created in M5. |
| 3 | x402 V2 facilitator, Solana devnet, `exact` | Works | `GET https://x402.org/facilitator/supported` → `{"x402Version":2,"scheme":"exact","network":"solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"}`, fee payer `CKPKJWNdJEqa81x7CkZ14BVPiY6y16Sxs7owznqtWYp5`. No API key. Backup: Coinbase CDP facilitator (needs CDP keys). |
| 4 | Devnet USDC | Confirmed onchain | `getAccountInfo` on devnet: mint `4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU`, 6 decimals, SPL Token program. Faucet: https://faucet.circle.com (20 USDC per address per 2 h). |

## Things that differ from what a tutorial would tell you

- **Sandbox order books are empty.** Sandbox contracts return `prices: {"buy": {}, "sell": {}}`. Sandbox orders can be placed but will rest unfilled. The agent therefore reads prices from the public production API and sends orders to the sandbox. The UI and README label this `SANDBOX`.
- **Sandbox `search` parameter returns HTTP 503.** `?search=BTC` works on production and fails on sandbox; `?category=crypto` works on both. `GeminiMarketsClient.find_events` filters by ticker prefix client-side so it behaves the same on both.
- **Production and sandbox list the same upcoming 5-minute symbols.** `python -m scripts.print_markets --sandbox-parity` showed every upcoming `GEMI-BTC05M…-UP` contract present in both. The agent only trades symbols that exist in the sandbox.
- **X API has no free tier.** Pay-per-use, roughly $0.005 per post read (third-party reporting; confirm in the X developer console before buying credits). Live X is a stretch goal; the demo uses the recorded feed.
- **Anchor needs WSL on Windows.** The dev machine has Node 24 and Python 3.12 natively; Rust, the Solana CLI and Anchor are installed in WSL Ubuntu 24.04 in M2.

## x402 design consequence

The `exact` SVM scheme verifies a specific transaction shape (a plain `TransferChecked` signed by the payer). A transfer made by the vault program through a CPI would not pass that verification, so x402 payments are made from the agent's hot wallet, and the hot wallet is funded only through the vault's capped `top_up` instruction. The onchain caps still bound what an agent can ever spend.

## Sources

- Gemini: https://developer.gemini.com/prediction-markets/getting-started, https://developer.gemini.com/prediction-markets-spec/trading
- QuickNode: https://www.quicknode.com/docs/streams/supported-chains, https://www.quicknode.com/docs/streams/data-sources/solana, https://www.quicknode.com/guides/quicknode-products/streams/validating-incoming-streams-webhook-messages
- x402: https://solana.com/docs/payments/agentic-payments/x402, https://docs.x402.org/getting-started/quickstart-for-sellers.md, https://docs.cdp.coinbase.com/x402/network-support.md
- Circle faucet: https://faucet.circle.com/
- Anchor: https://www.anchor-lang.com/docs/installation
