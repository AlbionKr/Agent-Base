// One audit event as a sentence a person can read at a glance.

import { short, usd, type Chapter, type Ev, type World } from "./model";

/** ok: went through. bad: an attack or a loss. wait: a person decides. warn: did not run. lock: frozen. */
export type Tone = "ok" | "bad" | "wait" | "warn" | "lock" | "plain";

export type Line = { tag: string; title: string; detail?: string; tone: Tone; signature?: string };

export function actionText(a: Record<string, any>): string {
  if (a.type === "gemini_order") return `${a.side} ${a.quantity} ${String(a.outcome).toUpperCase()} at $${a.price}`;
  if (a.type === "transfer") return `send ${usd(a.amount)} to ${short(a.payee)}`;
  return `pay ${usd(a.amount)} for news`;
}

/** What a completed action did: the headline and the detail under it. */
function executed(who: string, detail: Record<string, any> = {}): { title: string; detail: string } {
  if (detail.via === "x402.exact") {
    const top = detail.top_up_units ? ` The vault topped up its wallet by ${usd(Number(detail.top_up_units) / 1e6)} first.` : "";
    return {
      title: `${who} paid ${usd(Number(detail.price_units) / 1e6)} for news over x402`,
      detail: `${detail.response?.items?.length ?? 0} posts returned (${detail.response?.label ?? "feed"}).${top}`,
    };
  }
  if (detail.via === "vault.pay") return { title: `The vault paid ${short(detail.payee)} for ${who}`, detail: "" };
  if (detail.venue) return { title: `${who}'s order was ${detail.status ?? "accepted"} on ${detail.venue}`, detail: "" };
  return { title: `${who}: carried out`, detail: "" };
}

function sentence(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

/** Null for events that are only bookkeeping. */
export function describe(ev: Ev, world: World): Line | null {
  const p = ev.payload;
  const who = (ev.agent_id && world.agents[ev.agent_id]?.name) || ev.agent_id || "";
  switch (ev.kind) {
    case "agent_registered":
      return {
        tag: "Placed",
        title: `${p.agent_name} placed`,
        detail: `Limits signed by the owner: ${usd(p.mandate?.max_per_action_usd)} per action, ${usd(p.mandate?.max_per_day_usd)} per day, asks you above ${usd(p.mandate?.approval_threshold_usd)}.`,
        tone: "plain",
      };
    case "agent_provisioned":
      return { tag: "Onchain", title: `${who}'s allowance created in the vault`, detail: `Hot wallet ${short(p.hot_wallet)}.`, tone: "plain", signature: p.signatures?.at(-1) };
    case "decision": {
      const what = p.action ? actionText(p.action) : "do something malformed";
      const where = p.action?.symbol ? ` Market ${p.action.symbol}.` : "";
      if (p.verdict === "ALLOW") return { tag: "Allowed", title: `${who} may ${what}`, detail: `Passed every rule in ${p.latency_us / 1000} ms.${where}`, tone: "ok" };
      if (p.verdict === "DENY") {
        const rules = (p.rules_fired ?? []).join(", ");
        return { tag: "Blocked", title: `${who} tried to ${what}`, detail: `${p.reason ?? ""}${rules ? ` (${rules})` : ""}`, tone: "bad" };
      }
      return { tag: "Needs you", title: `${who} wants to ${what}`, detail: `${p.reason ?? ""}${where}`, tone: "wait" };
    }
    case "execution":
      return p.ok
        ? { tag: "Done", ...executed(who, p.detail), tone: "ok", signature: p.detail?.signature }
        : { tag: "Not run", title: `${who}: allowed, but it did not run`, detail: String(p.detail?.error ?? ""), tone: "warn" };
    case "approval_resolved":
      return p.status === "approved"
        ? { tag: "Approved", title: `Approved by ${p.resolved_via}`, detail: p.summary, tone: "ok" }
        : { tag: "Denied", title: p.status === "expired" ? "No answer in time, so the answer is no" : `Denied by ${p.resolved_via}`, detail: p.summary, tone: "wait" };
    case "voice_mismatch":
      return { tag: "Refused", title: "Voice approval refused", detail: "The voice agent reported an approval the person did not say.", tone: "bad" };
    case "outflow_observed":
      return p.matched_action_id
        ? { tag: "Seen onchain", title: `${usd(p.amount_usdc)} left ${who}'s wallet, as allowed`, tone: "plain", signature: p.signature }
        : null;
    case "anomaly":
      return { tag: "Alarm", title: `Money left ${who}'s wallet that nothing allowed`, detail: (p.reasons ?? []).join(" "), tone: "bad", signature: p.signature };
    case "freeze":
      return {
        tag: "Frozen",
        title: p.onchain?.status === "confirmed" ? `${who} frozen onchain by the guardian` : `${who} frozen in the gate only (onchain: ${p.onchain?.status})`,
        detail: p.reason,
        tone: "lock",
        signature: p.onchain?.signature,
      };
    case "chain_refusal":
      return {
        tag: "Blocked onchain",
        title: `The vault program refused a ${String(p.call ?? "call").replace(/_/g, "-")}${p.amount_usdc ? ` of $${p.amount_usdc}` : ""}`,
        detail: `${p.note ? `${p.note} ` : ""}Onchain error: ${p.error}.`,
        tone: "bad",
        signature: p.signature,
      };
    case "unfreeze":
      return { tag: "Unfrozen", title: `${who} unfrozen by the owner`, tone: "plain", signature: p.signature };
    case "naive_action":
      return p.executed
        ? { tag: "Stolen", title: `The unprotected agent sent ${usd(p.amount_usd)} to ${short(p.payee)}`, detail: "Nothing checked the payment.", tone: "bad", signature: p.signature }
        : { tag: "Note", title: "The unprotected agent could not act", detail: p.error, tone: "plain" };
    case "demo_step":
      return { tag: "Script", title: sentence(String(p.title ?? "")), detail: p.detail, tone: "plain", signature: p.signature };
    case "cycle_note":
      return { tag: "Note", title: `${who}: ${p.note}`, tone: "plain" };
    case "webhook_rejected":
      return { tag: "Refused", title: `Stream delivery refused (${p.code})`, detail: p.suppressed_since_last ? `${p.suppressed_since_last} more since the last report.` : undefined, tone: "bad" };
    default:
      return null;
  }
}

export type Entry = { ev: Ev; line: Line };
export type Group = { chapter: Chapter | null; items: Entry[] };

/** The last `limit` readable events, newest first, under the demo chapter they belong to. */
export function timeline(world: World, limit: number): Group[] {
  const entries: Entry[] = [];
  for (const ev of world.events) {
    const line = describe(ev, world);
    if (line) entries.push({ ev, line });
  }
  const groups: Group[] = [];
  for (const entry of entries.slice(-limit).reverse()) {
    let chapter: Chapter | null = null;
    for (const c of world.chapters) if (c.eventId < entry.ev.id) chapter = c;
    const last = groups[groups.length - 1];
    if (last && last.chapter === chapter) last.items.push(entry);
    else groups.push({ chapter, items: [entry] });
  }
  return groups;
}

/** The latest event that is more than routine: what the screen should say in large type. */
export function headline(world: World): Entry | null {
  for (let i = world.events.length - 1; i >= 0; i--) {
    const line = describe(world.events[i], world);
    if (line && line.tone !== "plain") return { ev: world.events[i], line };
  }
  return null;
}

/** The state of the run as one sentence in two parts, for the large headline. */
export function verdict(world: World): { lead: string; turn: string } {
  if (Object.keys(world.agents).length === 0) {
    return { lead: "No agent is placed yet.", turn: "Place one to put the guard up." };
  }
  const guarded = world.protectedLostUnits / 1_000_000;
  if (world.naiveLost > 0) {
    return {
      lead: `Attackers took ${usd(world.naiveLost)} without the guard`,
      turn: guarded > 0 ? `and ${usd(guarded)} with it.` : "and nothing with it.",
    };
  }
  if (guarded > 0) {
    return { lead: `Attackers took ${usd(guarded)} from the guarded agent.`, turn: "The onchain allowance capped the rest." };
  }
  return { lead: "Nothing has been taken.", turn: "Every action is checked before money moves." };
}
