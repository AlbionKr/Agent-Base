// What a node's detail panel shows. All of it is read from audit events.

import { describe, type Entry } from "./describe";
import type { NewsView, World } from "./model";

function entries(world: World, keep: (kind: string, payload: Record<string, any>, agentId: string | null) => boolean): Entry[] {
  const out: Entry[] = [];
  for (let i = world.events.length - 1; i >= 0; i--) {
    const ev = world.events[i];
    if (!keep(ev.kind, ev.payload, ev.agent_id)) continue;
    const line = describe(ev, world);
    if (line) out.push({ ev, line });
  }
  return out;
}

/** One agent's readable events, newest first. */
export function agentActivity(world: World, agentId: string, limit: number): Entry[] {
  return entries(world, (_kind, _p, id) => id === agentId).slice(0, limit);
}

/** The payments and orders an agent actually carried out, newest first. */
export function agentPayments(world: World, agentId: string): Entry[] {
  return entries(world, (kind, p, id) => id === agentId && kind === "execution" && p.ok === true);
}

/** What an agent read, each item once, newest first. */
export function agentReads(world: World, agentId: string, limit: number): NewsView[] {
  const seen = new Set<string>();
  const out: NewsView[] = [];
  for (let i = world.news.length - 1; i >= 0 && out.length < limit; i--) {
    const item = world.news[i];
    const key = `${item.source}/${item.itemId}`;
    if (item.agentId !== agentId || seen.has(key)) continue;
    seen.add(key);
    out.push(item);
  }
  return out;
}

/** What attackers tried and were stopped at, and what they got away with. Newest first. */
export function threatStory(world: World, attacker: string | null | undefined): { stopped: Entry[]; taken: Entry[] } {
  const stopped = entries(world, (kind, p) => {
    if (kind === "chain_refusal") return true;
    if (kind !== "decision" || p.verdict !== "DENY") return false;
    // A block is part of the attack story if it cites content the agent read, or pays the attacker.
    return (p.taint ?? []).length > 0 || (attacker != null && p.action?.payee === attacker);
  });
  const taken = entries(world, (kind, p) => kind === "anomaly" || (kind === "naive_action" && p.executed === true));
  return { stopped, taken };
}

/** Everything the unprotected agent did, newest first. */
export function naiveStory(world: World): Entry[] {
  return entries(world, (kind) => kind === "naive_action");
}
