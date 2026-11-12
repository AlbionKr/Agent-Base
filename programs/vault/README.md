# Vault program

Anchor 1.x program for Solana devnet. Program ID `8en8vCL4DR8e3Y739FSgKtLzFvTwiMvD8GVgnKGTBV2x`.

The last line of defense: whatever happens offchain, an agent's session key is bounded by what this program enforces.

## Accounts

| Account | Seeds | Holds |
|---|---|---|
| `Vault` | `["vault", owner]` | owner, guardian, USDC mint, vault token account, `frozen` |
| `AgentAllowance` | `["agent", vault, agent_id (u64 LE)]` | session key, `expires_at`, `per_tx_cap`, `daily_cap`, `spent_today`, `day_start`, allowlist (max 8), `frozen`, `mandate_hash` |

## Instructions

| Instruction | Who | What it enforces |
|---|---|---|
| `initialize_vault(guardian)` | owner | creates the vault and its USDC token account |
| `deposit(amount)` | anyone | funds only flow in |
| `create_agent(agent_id, session_key, policy)` | owner | allowlist ≤ 8, expiry in the future, per-tx cap ≤ daily cap |
| `top_up(amount)` | agent session key | destination must be a token account owned by the session key; freeze, expiry, per-tx cap, rolling daily cap |
| `pay(amount)` | agent session key | destination owner must be on the allowlist; same checks and the same daily budget as `top_up` |
| `freeze_agent` / `freeze_vault` | owner or guardian | — |
| `unfreeze_agent` / `unfreeze_vault` | owner only | the guardian can never unfreeze |
| `update_policy(policy)` | owner only | same validation as `create_agent` |
| `rotate_session_key(new_key, new_expires_at)` | owner only | old key stops working immediately; daily budget is not reset |
| `withdraw(amount)` | owner only | works while frozen, so the owner can always recover funds |

`withdraw` is not in the original spec; it was added so the owner can recover funds and the demo can be reset.

## Tests

`tests/vault.rs` runs the compiled program in LiteSVM with the real SPL Token program and a controllable clock: 26 tests covering caps, the 24h window, the allowlist, expiry, freezing, role separation and wrong signers.
