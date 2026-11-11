# Agent Base (working name)

A security layer for AI agents that hold money, shown as a tower-defense base: your vault is the core, each agent is a structure around it, your policies are the defenses, and real attacks visibly hit and bounce off.

**The AI proposes, the policy engine decides, the chain enforces.** Even if the agent and the policy engine are both compromised, the maximum loss is that agent's onchain allowance.

UBC 2026 Hackathon — Agentic Payments & AI track · Best use of Solana · Gemini Prediction Markets, QuickNode Streams and ElevenLabs bounties.

> This README is a work in progress. The full submission README (architecture, threat model, red-team results, stream config) lands in M8.

## Status

| Milestone | Scope | State |
|---|---|---|
| M1 | Repo scaffold, Day-1 verification, Gemini market data | done |
| M2 | Vault program (Anchor) + tests, deployed to devnet | not started |
| M3 | Policy engine + tests, agent loop, Gemini sandbox orders | not started |
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

## Secrets

Secrets live in `.env`; keypairs live in `.keys/`. Both are gitignored. The pre-commit hook (`scripts/check_secrets.py`) refuses commits that contain API keys, private keys, Solana keypair JSON, or those paths.
