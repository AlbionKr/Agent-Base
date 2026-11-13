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

The screen is built to be read in a few seconds. A dark sidebar holds the name, the two views and the list of what is not fully live in this run. The content sits on a light sheet beside it, as a few rounded cards:

1. **The dark panel at the top** says the state of the run in one sentence (`verdict()` in `src/describe.ts`), for example "Attackers took $2.00 without the guard, and $0.05 with it." Under it are the numbers behind the sentence:
   - *With the guard*: USDC that left a protected hot wallet with no allowed action behind it (the sum of `outflow_observed` events without a matching action).
   - *Without it*: USDC the unprotected agent sent to the attacker (`naive_action` executed).
   - *Still at risk today*: what is left of the onchain daily allowance plus the hot wallet balance (`balances`).
   - Counts: allowed, blocked (policy denials plus calls the vault program refused), asked a person, frozen.
   While `scripts/demo.py` runs, the top line of the panel shows which chapter of the scenario is on screen (`demo_chapter`).
2. **The map.**
3. **The headline** under the map: the latest event that is more than routine, in large type, with its outcome as a labelled tag.

A pending approval appears above the Timeline as a solid violet card: it is the one thing on the screen that needs the person.

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

The sidebar lists, on every view, what is not fully real in the current run (for example `LOCALNET`, `RECORDED FEED`, `SANDBOX`, `SCRIPTED` proposals). Those labels come from the backend's `/config` and from the events themselves.

## Views

- **Base**: the dark summary panel, the map, the headline, the pending approval (voice or buttons), and the Timeline.
- **Dashboard**: the run in five numbers; agents with their onchain allowance and most-at-risk figure; the red-team scorecard as one bar (blocked, held for a person, partly blocked, not blocked), with what an unprotected agent would have lost next to what got out, and the cases filterable by outcome; the paid news with posts that caused a block marked; and the audit log with Explorer links.

## Not verified

The voice approval button calls ElevenLabs through `@elevenlabs/client`. It has not been run against ElevenLabs (no API key yet); the approve and deny buttons have.
