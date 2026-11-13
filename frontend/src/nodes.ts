// The things drawn on the map, derived from the same state as everything else.

import { short, usd, type Config, type World } from "./model";

export type NodeKind = "vault" | "agent" | "station" | "attacker" | "naive";
export type NodeStatus = "active" | "waiting" | "frozen" | "lost" | "hostile" | "idle";

export type SceneNode = {
  /** "vault", "agent:<agent id>", "station:payees|news|markets", "attacker" or "naive". */
  id: string;
  kind: NodeKind;
  label: string;
  /** The line under the name. */
  sub: string;
  status: NodeStatus;
  /** A status worth a tag of its own, such as "Frozen onchain" or "Lost $2.00". */
  tag?: string;
  /** Agents only: today's onchain spend against the daily cap. */
  budget?: { spent: number; cap: number };
};

export function sceneNodes(world: World, config: Config | null): SceneNode[] {
  const labels = config?.labels ?? {};
  const nodes: SceneNode[] = [
    {
      id: "vault",
      kind: "vault",
      label: "Vault",
      sub: world.balances?.vault_usdc != null ? `${usd(world.balances.vault_usdc)} in the vault` : "no chain",
      status: "active",
    },
  ];

  for (const agent of Object.values(world.agents)) {
    const bal = world.balances?.agents[agent.id];
    const frozen = agent.frozen || bal?.frozen_onchain === true;
    const waiting = Object.values(world.approvals).some((a) => a.agent_id === agent.id && a.status === "pending");
    const cap = Number(bal?.daily_cap_usdc ?? agent.maxPerDay);
    const spent = Number(bal?.spent_today_usdc ?? 0);
    nodes.push({
      id: `agent:${agent.id}`,
      kind: "agent",
      label: agent.name,
      sub: bal?.spent_today_usdc != null ? `${usd(spent)} of ${usd(cap)} spent today` : `up to ${usd(agent.maxPerDay)} a day`,
      status: frozen ? "frozen" : waiting ? "waiting" : "active",
      tag: frozen ? (bal?.frozen_onchain ? "Frozen onchain" : "Frozen in the gate") : waiting ? "Waiting for your answer" : undefined,
      budget: Number.isFinite(cap) && cap > 0 ? { spent, cap } : undefined,
    });
  }

  nodes.push(
    { id: "station:payees", kind: "station", label: "Allowed payees", sub: "the only addresses the vault will pay", status: "idle" },
    { id: "station:news", kind: "station", label: "News stand", sub: `${usd("0.001")} a call over x402, ${labels.news_feed ?? "feed"}`, status: "idle" },
    {
      id: "station:markets",
      kind: "station",
      label: "Gemini markets",
      sub: labels.gemini_orders?.startsWith("SANDBOX") ? "orders go to the SANDBOX" : "orders are not sent: no sandbox key",
      status: "idle",
    },
    {
      id: "attacker",
      kind: "attacker",
      label: "Attacker",
      sub: world.balances?.attacker_usdc != null ? `holds ${usd(world.balances.attacker_usdc)}, ${short(config?.attacker)}` : short(config?.attacker),
      status: "hostile",
    },
  );

  if (world.balances?.naive || world.naiveLost > 0) {
    const lost = world.naiveLost > 0;
    nodes.push({
      id: "naive",
      kind: "naive",
      label: "Unprotected agent",
      sub: lost ? `holds ${usd(world.balances?.naive?.usdc)} now` : `no policy engine, no vault, holds ${usd(world.balances?.naive?.usdc)}`,
      status: lost ? "lost" : "idle",
      tag: lost ? `Lost ${usd(world.naiveLost)}` : undefined,
    });
  }
  return nodes;
}
