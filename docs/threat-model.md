# Threat model

## What we are protecting

USDC held for AI agents. The goal is a hard, stated bound on loss, not perfect prevention.

## What we assume can be compromised

| Component | Assumed attacker capability | Loss bound |
|---|---|---|
| Content the agent reads (posts, news, API responses, market text, token metadata) | fully attacker-controlled | none gets through the policy engine as a new payee; other tainted parameters need a human |
| The LLM | says whatever the attacker wants | the mandate's caps; see the red-team results |
| The x402 news server | returns anything, asks any price | the payee and maximum price fixed at approval |
| The agent's session key | stolen; attacker signs transactions directly | hot wallet balance + remaining daily allowance, until the stream freezes it |
| The policy engine and backend | fully compromised | the same onchain bound: the vault program does not trust the backend |
| The guardian key | stolen | can freeze (denial of service), cannot move funds or unfreeze |
| The stream webhook | attacker can send requests to it | rejected without the stream's secret; with it, at most a freeze |
| The voice agent's model | talked into calling approve | refused without the user's own spoken yes |

Not defended against: a stolen owner key (the owner can withdraw; that is what owning the vault means), a malicious vault program upgrade (upgrade authority is the owner), and loss on Gemini itself (offchain funds; bounded by the policy engine and a hard notional cap in code, and zero in this build because orders go to the sandbox).

## Attacks, OWASP LLM Top 10 mapping, and the layer that stops each

OWASP Top 10 for LLM Applications (2025): LLM01 Prompt Injection, LLM05 Improper Output Handling, LLM06 Excessive Agency, LLM10 Unbounded Consumption.

| # | Attack | OWASP | First layer that stops it | Backstop |
|---|---|---|---|---|
| 1 | Direct instruction in a post: send funds to X | LLM01 | policy: `TAINT_NEW_PAYEE` (payee came from content, not on allowlist) | `PAYEE_NOT_ALLOWLISTED`; vault allowlist |
| 2 | Instruction encoded in base64, hex, Morse, invisible Unicode | LLM01 | Context Ledger decodes, then rule 1 | vault allowlist |
| 3 | Content dictates an amount or order size | LLM01 | policy: `TAINT_PARAM`, human must confirm | caps |
| 4 | Roleplay jailbreak ("no limits") | LLM01 | policy: `PER_ACTION_CAP`, `DAILY_BUDGET` | vault caps |
| 5 | "Approval is pre-granted" | LLM01, LLM06 | policy: `APPROVAL_THRESHOLD`; the model cannot waive it | 60 s timeout is a denial |
| 6 | Indirect injection inside a paid x402 response | LLM01 | same as 1 and 3: paid content is untrusted content | |
| 7 | Redirect to another paid endpoint | LLM01 | policy: `ENDPOINT_NOT_ALLOWED` | payer refuses a challenge that differs from the approval |
| 8 | Received token or NFT claims to grant admin rights | LLM01, LLM06 | there is no action that changes policy or unfreezes; unknown action types are `INPUT_INVALID` | `update_policy` and `unfreeze` need the owner's signature onchain |
| 9 | Address poisoning with a lookalike address | LLM06 | policy: `PAYEE_LOOKALIKE` | vault allowlist |
| 10 | Slow drain: many small payments to new destinations | LLM06 | policy: `SLOW_DRAIN` | vault daily cap |
| 11 | Rate abuse | LLM10 | policy: `RATE_LIMIT` | vault daily cap |
| 12 | Spend the whole budget on an allowlisted payee | LLM06 | policy: `DAILY_BUDGET` | vault daily cap. This is the bound, not a failure |
| 13 | Market outside the mandate | LLM06 | policy: `MARKET_NOT_ALLOWED` | |
| 14 | Malformed action, float money, negative amount | LLM05 | gate: strict schema, `INPUT_INVALID` | |
| 15 | Executing an action twice, or a denied one | LLM06 | gate: `claim` is single-use and status-checked | |
| 16 | Stolen session key calls `top_up` or `pay` directly | LLM06 | vault program: caps, allowlist, expiry | stream: `UNEXPECTED_OUTFLOW`, guardian freeze |
| 17 | Stolen session key drains the hot wallet | LLM06 | bounded by the hot wallet balance | stream, then freeze stops refills |
| 18 | x402 server asks for more, or another payee, after approval | LLM06 | payer: `choose_offer` | caps |
| 19 | Forged or replayed stream webhook | n/a | HMAC, timestamp, nonce | worst case is a freeze |
| 20 | Voice model talked into approving | LLM01, LLM06 | backend requires the user's own spoken yes | tools carry no parameters |
| 21 | Web page in the operator's browser calls the local API | n/a | `Origin`, `Host` and proxy-header checks on the operator API | |

## Known gaps

- **A fully compromised agent can spend its daily budget on allowlisted payees and allowed markets.** That is by design; the budget is the bound. Keep allowlists short and budgets small.
- **The first few steps of a slow drain or a burst go through** before the rule fires (see RT-17 and RT-18 in the red-team results).
- **Taint tracking is about parameters, not intent.** An attacker who persuades the model to do something harmful using only allowlisted payees, allowed markets and amounts that never appeared in content is limited by the caps, not by taint rules.
- **Amounts match exactly.** An instruction to send "half of 0.80" is not traced to 0.40.
- **The stream reacts after the fact.** A stolen session key can take what is in the hot wallet before the freeze lands. Top-ups are small (0.10 USDC) for that reason.
- **Agent wallets are listed in the stream filter**, so a newly created agent is not watched until the filter is regenerated.
- **Red-team results assume a fully compromised model** and do not measure how often a real model is fooled. No LLM-in-the-loop run has been done.
- **Approvals do not survive a backend restart**; pending ones are denied at startup.
