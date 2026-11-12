# Agent Base (working name)

A security layer for AI agents that hold money, shown as a tower-defense base: your vault is the core, each agent is a structure around it, your policies are the defenses, and real attacks visibly hit and bounce off.

**The AI proposes, the policy engine decides, the chain enforces.** Even if the agent and the policy engine are both compromised, the maximum loss is that agent's onchain allowance.

UBC 2026 Hackathon — Agentic Payments & AI track · Best use of Solana · Gemini Prediction Markets, QuickNode Streams and ElevenLabs bounties.

> This README is a work in progress. The full submission README (architecture, threat model, red-team results, stream config) lands in M8.

## Status

| Milestone | Scope | State |
|---|---|---|
| M1 | Repo scaffold, Day-1 verification, Gemini market data | done |
| M2 | Vault program (Anchor) + tests, deployed to devnet | program + 26 tests done; **devnet deploy pending** (needs faucet funds) |
| M3 | Policy engine + tests, agent loop, Gemini sandbox orders | policy engine + mandate + taint ledger done; agent loop and orders need API keys |
| M4 | x402 paid news, taint tracking, Naive Agent | not started |
| M5 | QuickNode stream → webhook → anomaly → freeze | not started |
| M6 | ElevenLabs voice approval, dashboard, red-team suite | not started |
| M7 | Base view (PixiJS), demo script | not started |
| M8 | README, docs, fixes | not started |

Day-1 dependency checks: [docs/day1-verification.md](docs/day1-verification.md).

## What is mocked, recorded or sandboxed

Nothing is silently faked. Current and planned exceptions, each labeled in the UI:

- **Gemini orders: `SANDBOX`.** Market data is real (public production API). Orders go to Gemini's sandbox, whose books are empty, so orders rest unfilled.
- **News feed: `RECORDED FEED`** (from M4). A replayed feed with planted attack posts; live X is a stretch goal.

## Layout

```
programs/vault   Anchor program (Rust)                                   M2
backend          FastAPI: policy engine, agent runtime, executor, hooks  M1+
x402-news        Express x402 paid news endpoint (TypeScript)            M4
frontend         React + Vite + PixiJS                                   M6–M7
redteam          attack cases + harness                                  M6
scripts          demo scenario, deploy, seed, tooling
docs             verification report, architecture, threat model
```

## Setup (what works today)

Requires Python 3.12+ and git.

```bash
git config core.hooksPath .githooks
```

```bash
python -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -e "backend[dev]"
```

(On macOS/Linux the interpreter is `.venv/bin/python`.)

Copy `.env.example` to `.env`. Nothing in M1 needs a credential.

Run the tests:

```bash
.venv/Scripts/python -m pytest
```

Print live BTC 5-minute prediction markets from Gemini:

```bash
.venv/Scripts/python -m scripts.print_markets --sandbox-parity
```

## Vault program (Solana devnet)

Program ID: `8en8vCL4DR8e3Y739FSgKtLzFvTwiMvD8GVgnKGTBV2x` (not yet deployed).

An agent's session key can move at most `per_tx_cap` per transaction and `daily_cap` per rolling 24 hours, only to its own wallet (`top_up`) or to an allowlisted payee (`pay`), only until `expires_at`, and only while neither the agent nor the vault is frozen. The guardian (backend key) can freeze and nothing else; only the owner can unfreeze, change policy, rotate keys or withdraw.

Building needs Rust, the Solana CLI and Anchor 1.1.2. On Windows these run in WSL; `scripts/wsl_run.sh` runs any command from the repo root with that toolchain on the path.

Install the toolchain (once, inside WSL or Linux):

```bash
bash scripts/setup_wsl_toolchain.sh
```

Create local devnet keypairs in `.keys/` (prints public keys only):

```bash
bash scripts/wsl_run.sh bash scripts/setup_keys.sh
```

Build and run the 26 security tests (LiteSVM, in-process, clock-controlled):

```bash
bash scripts/wsl_run.sh anchor build
```

```bash
bash scripts/wsl_run.sh cargo test -p vault
```

Deploy to devnet (owner wallet needs devnet SOL from https://faucet.solana.com):

```bash
bash scripts/wsl_run.sh bash scripts/deploy_devnet.sh
```

Seed a vault, one agent and a first capped top-up (owner needs devnet USDC from https://faucet.circle.com):

```bash
.venv/Scripts/python -m scripts.seed_devnet
```

From a Windows shell, prefix the `bash scripts/...` commands with `wsl -d Ubuntu --` and use the `/mnt/c/...` path to the repo.

The Python client in `backend/app/chain/vault_ix.py` builds instructions by hand; `backend/tests/test_vault_ix.py` checks every builder and decoder against the Anchor-generated IDL so the two cannot drift.

## Secrets

Secrets live in `.env`; keypairs live in `.keys/`. Both are gitignored. The pre-commit hook (`scripts/check_secrets.py`) refuses commits that contain API keys, private keys, Solana keypair JSON, or those paths.
