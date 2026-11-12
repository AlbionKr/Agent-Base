# Backend

Python 3.12, FastAPI. One process, two listeners, both bound to `127.0.0.1`.

| Port | What | Who may reach it |
|---|---|---|
| `BACKEND_PORT` (8787) | Operator API: agents, proposals, approvals, audit log, live event stream | This machine only |
| `WEBHOOK_PORT` (8788) | `POST /webhooks/quicknode` and nothing else | The one port a tunnel may point at |

```bash
.venv/Scripts/python -m app.main
```

Needs `OWNER_ADDRESS` in `.env` (the vault owner's public address). See `.env.example` for the rest.

## How a decision is made

```
LLM output ──► Gate.propose ──► parse (strict) ──► policy engine (pure) ──► recorded
                                                         │
                         ALLOW ── claim() once ──► executor
              REQUIRE_APPROVAL ── human says yes within 60 s ──► claim() once ──► executor
                          DENY ── nothing runs
```

- **`app/policy/`** — the deterministic engine. No clock, network or LLM inside it. 15 rules, each with an ID and a plain-English reason.
- **`app/gate.py`** — the only door. It assigns the action id (whatever the LLM put there is discarded), builds the policy state from the audit log, records the decision, and releases an action to the executor **once**, only if it was allowed or approved, and only while the agent is not frozen. The executor never sees raw LLM output.
- **`app/approvals.py`** — approval queue. An answer at or after the deadline is a timeout, even if it says yes. Approvals do not survive a restart: anything left pending is denied at startup.
- **`app/audit.py`** — SQLite. `events` is the append-only log the UI reads; `actions` holds each proposal's status. Money is stored as decimal strings; floats are refused.
- **`app/events.py`** — `Journal.emit` writes to the audit log, then publishes the stored event. The SSE stream replays from `Last-Event-ID` and then follows live, so a reconnecting client misses nothing and sees nothing twice.

Budget accounting fails closed: an allowed action reserves its amount immediately, a pending approval reserves its amount, and a failed execution still counts as spent (it may have moved money). Only refused actions give their amount back.

## Operator API

| Route | Purpose |
|---|---|
| `GET /health` | liveness |
| `GET /agents` · `POST /agents` | list agents · register one from an owner-signed mandate |
| `POST /agents/{id}/context` | register untrusted content in the agent's Context Ledger |
| `POST /agents/{id}/proposals` | propose an action, get the decision |
| `GET /approvals` · `POST /approvals/{id}/decision` | pending approvals · approve or deny |
| `GET /events` · `GET /events/stream` | audit log · the same log as Server-Sent Events |

There is no route to execute anything. Over HTTP you can only ask for a decision.

The API can register agents and approve spending, so binding to loopback is not treated as enough (`app/api/guard.py`). Every request is refused unless the socket peer is loopback, it carries no proxy or tunnel headers, its `Host` is a loopback name (DNS rebinding), and its `Origin`, if any, is the frontend (`FRONTEND_ORIGINS`; blocks cross-site requests from other pages in the browser). uvicorn's `X-Forwarded-For` handling is switched off so the guard always sees the real peer.

## QuickNode webhook

`app/quicknode/verify.py` checks every delivery, in this order:

1. **Signature** — `hex(HMAC-SHA256(token, nonce + timestamp + payload))` over the un-gzipped body, compared in constant time.
2. **Timestamp** — within 5 minutes of our clock.
3. **Nonce** — not seen before. Remembered only after the signature passed, so forgeries cannot fill the cache or burn a nonce.

With no token configured, every delivery is refused. Bodies are capped at 5 MB, before and after decompression. Refused deliveries are logged at most once per 10 s with a count of the rest.

An authentic delivery goes to `app/quicknode/sentinel.py`, which computes USDC that left each watched hot wallet (from the transaction's pre/post token balances) and applies three rules (`app/quicknode/anomaly.py`):

| Rule | Fires when |
|---|---|
| `UNEXPECTED_OUTFLOW` | no allowed, not-yet-seen x402 payment accounts for the full amount and its destination |
| `UNKNOWN_DESTINATION` | money went to an address outside the mandate's payee list |
| `OUTFLOW_BURST` | more outflows in 60 s than the mandate's actions-per-minute limit |

Any anomaly freezes the agent in the gate at once (no new decisions, no claims) and then asks the guardian for the onchain `freeze_agent`. The `freeze` event says which of the two actually happened.

**Not done yet:** the guardian's onchain freeze transaction is not wired (needs the deployed vault; M5), so today the `freeze` event reports `onchain: not_configured`. The stream itself and its filter are created in M5; the payload contract at the top of `anomaly.py` is what that filter must emit, and it has not been checked against a live QuickNode delivery.

Forging the webhook cannot move money. The most it can cause is a freeze.

## Tests

```bash
.venv/Scripts/python -m pytest
```
