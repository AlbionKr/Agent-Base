# Frontend

React + Vite + PixiJS. Two views over one data source: the backend's audit-log event stream (`GET /events/stream`).

```bash
npm install
```

```bash
npm run dev
```

Opens on http://127.0.0.1:5173 and talks to the backend at http://127.0.0.1:8787 (override with `VITE_API_URL`). The backend only accepts browser requests from the origins in `FRONTEND_ORIGINS`.

## Nothing is drawn without an event

`src/model.ts` reduces audit events into the state both views render. `src/scene.ts` plays one animation per live event, and every popup shows the audit event id it came from. Reloading the page replays the log and rebuilds the same picture; history is drawn, not re-animated.

| Event | Base view |
|---|---|
| `agent_registered`, `agent_provisioned` | a structure appears, with its wall of rules and the vault's onchain ring |
| `decision` DENY | a red bolt flies in (from the news stand if the block cites content the agent read, else from the attacker), bounces off the wall, and a card shows the rule and reason |
| `decision` REQUIRE_APPROVAL, `approval_requested` | a violet ring around the structure; the approval card with its countdown |
| `approval_resolved` | the ring closes; a card says approved, denied or no answer |
| `execution` ok | coins travel: vault to hot wallet for a top-up, hot wallet to the news stand for an x402 payment, vault to payees for a transfer |
| `context` | a sheet of paper travels from the news stand to the agent |
| `anomaly`, `freeze` | a coin leaves toward the attacker, then the structure greys out and locks; the card says whether the freeze is onchain |
| `naive_action` executed | coins pour from the naive agent's tent to the attacker |
| `balances` | the numbers on the vault, wallets and attacker |

The top bar lists what is not fully real in the current run (for example `LOCALNET`, `RECORDED FEED`, `SANDBOX`, `SCRIPTED` proposals). Those labels come from the backend's `/config` and from the events themselves.

## Views

- **Base**: the map, the pending approval (voice or buttons), and a plain-language feed of the latest audit entries.
- **Dashboard**: agents with their onchain allowance and most-at-risk figure, counters and policy decision time, the paid news with posts that caused a block highlighted, the red-team scorecard, and the audit log with Explorer links.

## Not verified

The voice approval button calls ElevenLabs through `@elevenlabs/client`. It has not been run against ElevenLabs (no API key yet); the approve and deny buttons have.
