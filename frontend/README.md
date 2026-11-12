# Frontend

React + Vite + PixiJS. Two views over one data source: the backend's audit-log event stream (`GET /events/stream`).

```bash
npm install
```

```bash
npm run dev
```

Opens on http://127.0.0.1:5173 and talks to the backend at http://127.0.0.1:8787 (override with `VITE_API_URL`). The backend only accepts browser requests from the origins in `FRONTEND_ORIGINS`.

```bash
npm test
```

Runs the tests for the event reducer and the event wording (`src/model.test.ts`, `src/rules.test.ts`).

## Nothing is drawn without an event

`src/model.ts` reduces audit events into the state both views render. `src/describe.ts` turns one event into one sentence. `src/scene.ts` plays one animation per live event, and every popup shows the audit event id it came from. Reloading the page replays the log and rebuilds the same picture; history is drawn, not re-animated.

## What the Base view shows first

The screen is built to be read in a few seconds, top to bottom:

1. **One sentence** in the top bar that says what the product is.
2. **The scoreboard**, "Taken by attackers": two figures side by side.
   - *With the guard*: USDC that left a protected hot wallet with no allowed action behind it (the sum of `outflow_observed` events without a matching action), and the onchain limit on further loss today (`balances`).
   - *Without it*: USDC the unprotected agent sent to the attacker (`naive_action` executed).
   - Counters: allowed, blocked (policy denials plus calls the vault program refused), sent to a person, frozen.
3. **The step rail**, only while `scripts/demo.py` runs: which chapter of the scenario is on screen (`demo_chapter`).
4. **The map.**
5. **The headline**: the latest event that is more than routine, in large type, with its outcome as a labelled tag.

The Timeline on the right lists the same events, newest first, grouped under the demo chapter they belong to. Each entry carries its audit event id and, where there is one, a link to the transaction.

Outcome colours are always paired with a shape and a word, never used alone: green tick (allowed, done), red diamond (attack blocked, or money lost), violet clock (a person decides), dark padlock (frozen), brass dash (allowed but not run, for example with no Gemini key).

| Event | Base view |
|---|---|
| `agent_registered`, `agent_provisioned` | a structure appears, with its wall of rules, the vault's onchain ring, and a meter of today's onchain allowance |
| `decision` DENY | a red diamond flies in (from the news stand if the block cites content the agent read, else from the attacker), bounces off the wall, the wall segment for that family of rules lights up, and a card shows the rule and reason |
| `decision` REQUIRE_APPROVAL, `approval_requested` | a violet ring and a "Waiting for your answer" plate on the structure; the approval card with its countdown |
| `approval_resolved` | the ring closes; a card says approved, denied or no answer |
| `execution` ok | coins travel: vault to hot wallet for a top-up, hot wallet to the news stand for an x402 payment, vault to payees for a transfer |
| `context` | a sheet of paper travels from the news stand to the agent |
| `chain_refusal` (demo script) | a red diamond flies at the vault and bounces off; the card shows the vault program's error |
| `anomaly`, `freeze` | a coin leaves toward the attacker, then the structure greys out, locks, and gets a "Frozen onchain" plate; the card says whether the freeze is onchain |
| `naive_action` executed | coins pour from the unprotected agent's tent to the attacker; the tent turns red with a "Lost" plate |
| `balances` | the numbers on the vault, wallets, meters and attacker |

The bar at the bottom of every view lists what is not fully real in the current run (for example `LOCALNET`, `RECORDED FEED`, `SANDBOX`, `SCRIPTED` proposals). Those labels come from the backend's `/config` and from the events themselves.

## Views

- **Base**: the scoreboard, the map, the headline, the pending approval (voice or buttons), and the Timeline.
- **Dashboard**: the run in five numbers; agents with their onchain allowance and most-at-risk figure; the red-team scorecard as one bar (blocked, held for a person, partly blocked, not blocked), with what an unprotected agent would have lost next to what got out, and the cases filterable by outcome; the paid news with posts that caused a block marked; and the audit log with Explorer links.

## Not verified

The voice approval button calls ElevenLabs through `@elevenlabs/client`. It has not been run against ElevenLabs (no API key yet); the approve and deny buttons have.
