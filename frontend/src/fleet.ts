// What the dashboard says about the agents as a group. All of it is read from audit events.

import { pendingApprovals, unitsToUsd, type ApprovalView, type Config, type World } from "./model";

export type AgentStatus = "waiting" | "frozen" | "active" | "idle";
export type Outcomes = { allow: number; deny: number; approval: number };

export type AgentSummary = {
  id: string;
  name: string;
  status: AgentStatus;
  proposer?: string;
  counts: Outcomes;
  spent: number | null;
  cap: number;
  perAction: number;
  threshold: number;
  atRisk: number | null;
  hotWallet: number | null;
  hotWalletAddress?: string;
  frozenOnchain: boolean;
  lastAt: string | null;
};

const ORDER: Record<AgentStatus, number> = { waiting: 0, frozen: 1, active: 2, idle: 3 };

function num(value: string | number | null | undefined): number | null {
  if (value == null || value === "") return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

/** The outcome an event counts as, if it is a decision or a call the vault refused. */
function outcomeOf(kind: string, payload: Record<string, any>): keyof Outcomes | null {
  if (kind === "chain_refusal") return "deny";
  if (kind !== "decision") return null;
  if (payload.verdict === "ALLOW") return "allow";
  if (payload.verdict === "DENY") return "deny";
  return "approval";
}

/** One line per agent, the ones that need you first. */
export function agentSummaries(world: World): AgentSummary[] {
  const counts = new Map<string, Outcomes>();
  const last = new Map<string, string>();
  for (const ev of world.events) {
    if (!ev.agent_id) continue;
    last.set(ev.agent_id, ev.at);
    const outcome = outcomeOf(ev.kind, ev.payload);
    if (!outcome) continue;
    const c = counts.get(ev.agent_id) ?? { allow: 0, deny: 0, approval: 0 };
    c[outcome] += 1;
    counts.set(ev.agent_id, c);
  }
  const waiting = new Set(pendingApprovals(world).map((a) => a.agent_id));

  return Object.values(world.agents)
    .map((a): AgentSummary => {
      const b = world.balances?.agents[a.id];
      const c = counts.get(a.id) ?? { allow: 0, deny: 0, approval: 0 };
      const frozenOnchain = b?.frozen_onchain === true;
      const status: AgentStatus = waiting.has(a.id) ? "waiting" : a.frozen || frozenOnchain ? "frozen" : c.allow + c.deny + c.approval > 0 || a.proposer ? "active" : "idle";
      return {
        id: a.id,
        name: a.name,
        status,
        proposer: a.proposer,
        counts: c,
        spent: num(b?.spent_today_usdc),
        cap: num(b?.daily_cap_usdc) ?? num(a.maxPerDay) ?? 0,
        perAction: num(b?.per_tx_cap_usdc) ?? num(a.maxPerAction) ?? 0,
        threshold: num(a.approvalThreshold) ?? 0,
        atRisk: num(b?.max_loss_bound_usdc),
        hotWallet: num(b?.hot_wallet_usdc),
        hotWalletAddress: b?.hot_wallet ?? a.hotWallet,
        frozenOnchain,
        lastAt: last.get(a.id) ?? null,
      };
    })
    .sort((x, y) => ORDER[x.status] - ORDER[y.status] || x.name.localeCompare(y.name));
}

export type Bucket = Outcomes & { from: number; to: number };
export type Series = { start: number; end: number; buckets: Bucket[]; total: number; peak: number };

/** Decisions over the run, split into `n` equal stretches of time. One agent, or all with `agentId` null. */
export function activitySeries(world: World, agentId: string | null, n: number): Series {
  const points: { t: number; outcome: keyof Outcomes }[] = [];
  for (const ev of world.events) {
    if (agentId != null && ev.agent_id !== agentId) continue;
    const outcome = outcomeOf(ev.kind, ev.payload);
    if (outcome) points.push({ t: Date.parse(ev.at), outcome });
  }
  if (points.length === 0) return { start: 0, end: 0, buckets: [], total: 0, peak: 0 };
  const start = Math.min(...points.map((p) => p.t));
  const end = Math.max(...points.map((p) => p.t));
  const span = Math.max(end - start, 1);
  const buckets: Bucket[] = Array.from({ length: n }, (_, i) => ({
    from: start + (span * i) / n,
    to: start + (span * (i + 1)) / n,
    allow: 0,
    deny: 0,
    approval: 0,
  }));
  for (const p of points) buckets[Math.min(n - 1, Math.floor(((p.t - start) / span) * n))][p.outcome] += 1;
  const peak = Math.max(...buckets.map((b) => b.allow + b.deny + b.approval));
  return { start, end, buckets, total: points.length, peak };
}

export type Attention =
  | { kind: "approval"; agentId: string; approval: ApprovalView }
  | { kind: "frozen"; agentId: string };

/** What is waiting on the owner: answers first, then frozen agents. */
export function attention(world: World): Attention[] {
  const out: Attention[] = pendingApprovals(world).map((approval) => ({ kind: "approval", agentId: approval.agent_id, approval }));
  for (const a of agentSummaries(world)) if (a.status === "frozen") out.push({ kind: "frozen", agentId: a.id });
  return out;
}

export type RunPart = { name: string; value: string; caveat: boolean };

/** Each part of the run and whether it is less than the real thing, from the backend's labels. */
export function runParts(config: Config | null, world: World): RunPart[] {
  if (!config) return [];
  const l = config.labels ?? {};
  const proposers = new Set(Object.values(world.agents).map((a) => a.proposer).filter(Boolean) as string[]);
  return [
    { name: "Chain", value: l.cluster ?? "?", caveat: l.cluster !== "DEVNET" },
    { name: "News", value: l.news_feed ?? "?", caveat: l.news_feed !== "LIVE FEED" },
    { name: "Gemini orders", value: l.gemini_orders ?? "?", caveat: true },
    { name: "Stream", value: l.stream ?? "?", caveat: l.stream !== "QuickNode Streams" },
    ...[...proposers].map((p) => ({ name: "Proposals", value: p, caveat: !p.startsWith("LLM") })),
  ];
}

export type Money ={ vault: number | null; spent: number; cap: number; atRisk: number | null; withGuard: number; without: number };

/** The day's money across all agents. */
export function moneySummary(world: World): Money {
  const agents = agentSummaries(world);
  const risks = agents.map((a) => a.atRisk).filter((r): r is number => r != null);
  return {
    vault: num(world.balances?.vault_usdc),
    spent: agents.reduce((sum, a) => sum + (a.spent ?? 0), 0),
    cap: agents.reduce((sum, a) => sum + a.cap, 0),
    atRisk: risks.length ? risks.reduce((sum, r) => sum + r, 0) : null,
    withGuard: unitsToUsd(world.protectedLostUnits),
    without: world.naiveLost,
  };
}
