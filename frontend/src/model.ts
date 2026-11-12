// Everything the UI shows is derived from audit-log events. No event, no pixel.

export const API = (import.meta.env.VITE_API_URL as string | undefined) ?? "http://127.0.0.1:8787";

export type Ev = {
  id: number;
  at: string;
  kind: string;
  agent_id: string | null;
  payload: Record<string, any>;
};

export type AgentView = {
  id: string;
  name: string;
  frozen: boolean;
  maxPerAction: string;
  maxPerDay: string;
  approvalThreshold: string;
  markets: string[];
  payees: string[];
  hotWallet?: string;
  proposer?: string;
};

export type ApprovalView = {
  approval_id: string;
  action_id: string;
  agent_id: string;
  summary: string;
  amount_usd: string;
  deadline: string;
  status: "pending" | "approved" | "denied" | "expired";
  resolved_via?: string | null;
};

export type NewsView = {
  eventId: number;
  agentId: string;
  source: string;
  itemId: string;
  text: string;
  blockedBy: number[]; // decision event ids that cited this item
};

export type Balances = {
  vault_usdc: string | null;
  agents: Record<
    string,
    {
      hot_wallet: string;
      hot_wallet_usdc: string;
      daily_cap_usdc?: string;
      spent_today_usdc?: string;
      per_tx_cap_usdc?: string;
      frozen_onchain?: boolean;
      max_loss_bound_usdc?: string;
      allowance?: string;
    }
  >;
  naive: { wallet: string; usdc: string } | null;
  attacker_usdc: string | null;
};

export type World = {
  lastId: number;
  events: Ev[]; // newest last, capped
  agents: Record<string, AgentView>;
  approvals: Record<string, ApprovalView>;
  news: NewsView[];
  balances: Balances | null;
  naiveLost: number;
  naiveProposer?: string;
  counts: { allow: number; deny: number; approval: number; freezes: number };
};

export const emptyWorld: World = {
  lastId: 0,
  events: [],
  agents: {},
  approvals: {},
  news: [],
  balances: null,
  naiveLost: 0,
  counts: { allow: 0, deny: 0, approval: 0, freezes: 0 },
};

const MAX_EVENTS = 600;
const MAX_NEWS = 80;

export function reduce(world: World, ev: Ev): World {
  if (ev.id <= world.lastId) return world;
  const w: World = {
    ...world,
    lastId: ev.id,
    events: world.events.length >= MAX_EVENTS ? [...world.events.slice(-MAX_EVENTS + 1), ev] : [...world.events, ev],
  };
  const p = ev.payload;
  const agentId = ev.agent_id ?? "";

  switch (ev.kind) {
    case "agent_registered": {
      const m = p.mandate ?? {};
      w.agents = {
        ...w.agents,
        [agentId]: {
          ...(w.agents[agentId] ?? { frozen: false }),
          id: agentId,
          name: p.agent_name ?? agentId,
          maxPerAction: m.max_per_action_usd ?? "?",
          maxPerDay: m.max_per_day_usd ?? "?",
          approvalThreshold: m.approval_threshold_usd ?? "?",
          markets: m.allowed_markets ?? [],
          payees: m.allowed_payees ?? [],
        },
      };
      break;
    }
    case "agent_provisioned":
      if (w.agents[agentId]) w.agents = { ...w.agents, [agentId]: { ...w.agents[agentId], hotWallet: p.hot_wallet } };
      break;
    case "cycle_started":
      if (w.agents[agentId]) w.agents = { ...w.agents, [agentId]: { ...w.agents[agentId], proposer: p.proposer } };
      break;
    case "naive_cycle":
      w.naiveProposer = p.proposer;
      break;
    case "context":
      w.news = [
        ...w.news.slice(-MAX_NEWS + 1),
        { eventId: ev.id, agentId, source: p.source, itemId: p.item_id, text: p.text, blockedBy: [] },
      ];
      break;
    case "decision": {
      const counts = { ...w.counts };
      if (p.verdict === "ALLOW") counts.allow++;
      else if (p.verdict === "DENY") counts.deny++;
      else counts.approval++;
      w.counts = counts;
      const cited = new Set<string>((p.taint ?? []).map((t: any) => t.item_id));
      if (cited.size && p.verdict !== "ALLOW") {
        w.news = w.news.map((n) =>
          n.agentId === agentId && cited.has(n.itemId) ? { ...n, blockedBy: [...n.blockedBy, ev.id] } : n,
        );
      }
      break;
    }
    case "approval_requested":
      w.approvals = { ...w.approvals, [p.approval_id]: { ...(p as ApprovalView), status: "pending" } };
      break;
    case "approval_resolved":
      w.approvals = { ...w.approvals, [p.approval_id]: p as ApprovalView };
      break;
    case "freeze":
      w.counts = { ...w.counts, freezes: w.counts.freezes + 1 };
      if (w.agents[agentId]) w.agents = { ...w.agents, [agentId]: { ...w.agents[agentId], frozen: true } };
      break;
    case "unfreeze":
      if (w.agents[agentId]) w.agents = { ...w.agents, [agentId]: { ...w.agents[agentId], frozen: false } };
      break;
    case "balances":
      w.balances = p as Balances;
      break;
    case "naive_action":
      if (p.executed) w.naiveLost = w.naiveLost + Number(p.amount_usd ?? 0);
      break;
  }
  return w;
}

export function reduceAll(world: World, events: Ev[]): World {
  return events.reduce(reduce, world);
}

export function pendingApprovals(world: World): ApprovalView[] {
  return Object.values(world.approvals)
    .filter((a) => a.status === "pending")
    .sort((a, b) => a.deadline.localeCompare(b.deadline));
}

export function short(address: string | undefined | null): string {
  if (!address) return "";
  return address.length > 12 ? `${address.slice(0, 4)}…${address.slice(-4)}` : address;
}

export function usd(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === "") return "–";
  const n = Number(value);
  if (!Number.isFinite(n)) return String(value);
  // Sub-cent prices matter here (news costs $0.001).
  return `$${n < 0.01 && n > 0 ? n.toFixed(3) : n.toFixed(2)}`;
}

export async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API}${path}`);
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}

export async function postJson<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`${API}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(data.error ?? data.detail?.[0]?.msg ?? `HTTP ${response.status}`);
  return data;
}

export type Config = {
  labels: Record<string, string>;
  cluster: string;
  explorer_cluster_param: string;
  program_id: string;
  vault: string | null;
  attacker: string | null;
  can_create_agents: boolean;
  approval_timeout_seconds: number;
};

export function explorerTx(config: Config | null, signature: string): string {
  return `https://explorer.solana.com/tx/${signature}?cluster=${config?.explorer_cluster_param ?? "devnet"}`;
}

export function explorerAddress(config: Config | null, address: string): string {
  return `https://explorer.solana.com/address/${address}?cluster=${config?.explorer_cluster_param ?? "devnet"}`;
}
