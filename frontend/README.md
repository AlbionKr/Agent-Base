# Frontend

React + Vite, one HTML canvas for the space scene, Framer Motion for interface motion. Two views over one data source: the backend's audit-log event stream (`GET /events/stream`).

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

Runs the tests for the event reducer, the event wording, the map's nodes and the detail panel's selectors (`src/*.test.ts`), and the design rules (`src/design.test.ts`): type, spacing and radius on fixed scales, colours only from the tokens, no uppercase or tracked-out labels, monospace only for machine data, short transitions, no placeholder copy. The rules and where they come from are in [docs/design-notes.md](../docs/design-notes.md).

## Nothing is drawn without an event

`src/model.ts` reduces audit events into the state both views render. `src/describe.ts` turns one event into one sentence. `src/nodes.ts` derives the things on the map and `src/detail.ts` what a detail panel lists. Reloading the page replays the log and rebuilds the same picture; history is drawn, not re-animated.

The stars, the nebula and the slow orbits are decoration. Everything that carries meaning is an event: every flow between nodes, every status, every number and every notice.

## The Base view: a command centre

Dark, with the vault as a glowing core and everything else in orbit around it.

- **Top bar**: name, the two views, the demo stepper (seven steps joined by a line that fills as the script goes), and the current step's title. The stepper appears only while `scripts/demo.py` runs (`demo_chapter` events).
- **Left rail**: the run in one sentence (`verdict()`), then three figures and four counts.
  - *With the guard*: USDC that left a protected hot wallet with no allowed action behind it (the sum of `outflow_observed` events without a matching action).
  - *Without it*: USDC the unprotected agent sent to the attacker (`naive_action` executed).
  - *Still at risk today*: what is left of the onchain daily allowance plus the hot wallet balance (`balances`).
  - Allowed, blocked (policy denials plus calls the vault program refused), asked a person, frozen.
- **Centre**: the scene.
- **Right rail**: a pending approval (voice or buttons) when there is one, then the event log, newest first, grouped by demo step, each entry with its audit event id and transaction link, and the legend.
- **Notices** over the bottom of the scene: the latest event that is more than routine, with the one before it stacked above until it ages out.
- **Bottom strip**: connection state and what is not fully real in this run (for example `LOCALNET`, `RECORDED FEED`, `SANDBOX`, `SCRIPTED` proposals). Those labels come from the backend's `/config` and from the events themselves.

### The scene

| On screen | What it is |
|---|---|
| Glowing core with a balance | the vault and its USDC balance (`balances`); it pulses when the balance changes |
| Nodes on the inner ring | protected agents. The segmented ring around each is today's onchain spend against its daily cap. A thin line ties each to the vault |
| Stations on the outer ring | allowed payees, the news stand (x402), Gemini markets |
| Red diamond outside the rings | the attacker wallet and what it holds |
| Dashed node outside the rings | the unprotected agent: no policy engine, no vault |

| Event | What moves |
|---|---|
| `decision` DENY | a red strike flies from the news stand (if the block cites content the agent read) or from the attacker, and stops at a shield flash in front of the agent |
| `decision` REQUIRE_APPROVAL, `approval_requested` | a violet pulse on the agent and a "Waiting for your answer" tag; the approval card appears |
| `approval_resolved` | a green or violet ring on the agent |
| `execution` ok | amber motes: vault to agent for a top-up, agent to the news stand for an x402 payment, vault to payees for a transfer |
| `context` | a dashed cyan beam from the source to the agent that read it |
| `chain_refusal` (demo script) | a red strike at the vault that stops at its shield |
| `anomaly` | red motes from the agent to the attacker |
| `freeze` | an icy ring; the agent desaturates, gets a lock and a "Frozen onchain" tag, and glides to rest beside the vault |
| `naive_action` executed | red motes from the unprotected agent to the attacker, a red flash and a "Lost" tag |

Outcome colours are always paired with a word and a shape: green (allowed, done), red (attack blocked, or money lost), violet (a person decides), ice blue (frozen), amber (money; also "allowed but not run", the main button and the focus ring). Everything else is neutral, links included.

### Detail panels

Every node is a button. Clicking one (or Enter on it) opens a panel that grows out of the node: budget, policy, activity, payments and recent reads for an agent; program and allowances for the vault; the posts bought for the news stand; what was tried, stopped and taken for the attacker; what happened to the unprotected agent. Esc, the X or a click outside closes it and returns the focus to the node. Focus stays inside the panel while it is open.

### Motion

One `requestAnimationFrame` loop in `src/space/engine.ts`. It stops while the tab is hidden. With `prefers-reduced-motion` the scene is drawn once and stands still: no orbits, flows or pulses, and the statuses, numbers, notices and log still update.

## Dashboard view

A sidebar of topics, one page each, so nothing competes on one long scroll. The page is kept in the address (`#/overview`, `#/agents/<id>`, ...), so the back button and shared links work.

- **Overview**: what needs you first (a pending approval, or a frozen agent with an unfreeze button; a calm line when nothing does), then money today (a ring of the day's spend against all agents' daily caps, with the vault balance, what is still at risk and what was taken with and without the guard), the agents as cards with the ones that need you first, decisions over time, recent activity, and one card each for the red team, the paid news and what is live in this run, each with a link to its page.
- **Agents**: one row per agent: status, spend against its cap, most at risk, decisions, last activity. Each row opens the agent's own page: budget, what a stolen key could take now, its decisions over time, the limits it signed, its activity, payments and reads, with run-a-cycle and unfreeze.
- **Red team**: the scorecard as one bar, what an unprotected agent would lose next to what got out, and the cases filterable by outcome.
- **Paid news**: every post bought over x402, attack posts marked with the decision that cited them.
- **Audit log**: every event, filterable by outcome, with Explorer links.
- **This run**: what is live and what is not, the program and vault accounts, and the policy engine's own numbers.

"Decisions over time" splits the run into equal stretches and stacks allowed, asked and blocked in each (a call the vault program refused counts as blocked, as in the counts on the Base view). Hover a column for its counts. The helpers behind these pages are in `src/fleet.ts` and tested in `src/fleet.test.ts`.

## Reviewing one demo step

`http://127.0.0.1:5173/?step=4` replays the audit log up to the end of demo step 4 and stops there. The bottom strip says so. This is how each step was screenshotted and its numbers checked against the raw log.

## Not verified

The voice approval button calls ElevenLabs through `@elevenlabs/client`. It has not been run against ElevenLabs (no API key yet); the approve and deny buttons have.
