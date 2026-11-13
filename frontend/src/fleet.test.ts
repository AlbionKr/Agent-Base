import { describe as suite, expect, it } from "vitest";
import { activitySeries, agentSummaries, attention, moneySummary, runParts } from "./fleet";
import { emptyWorld, reduceAll, type Config, type Ev } from "./model";

let nextId = 0;
const T0 = 1_800_000_000_000;
function ev(kind: string, payload: Record<string, any> = {}, agent_id: string | null = "a1", atSeconds?: number): Ev {
  nextId += 1;
  return { id: nextId, at: new Date(T0 + (atSeconds ?? nextId) * 1000).toISOString(), kind, agent_id, payload };
}
const registered = (id: string, name: string) =>
  ev("agent_registered", { agent_name: name, mandate: { max_per_action_usd: "1.00", max_per_day_usd: "5.00", approval_threshold_usd: "0.50" } }, id);
const decision = (verdict: string, id = "a1", at?: number) => ev("decision", { verdict, action: { type: "transfer", amount: "0.1", payee: "x" } }, id, at);
const balances = (agents: Record<string, any>) => ev("balances", { vault_usdc: "9.5", agents, naive: { wallet: "w", usdc: "0" }, attacker_usdc: "2.05" }, null);

suite("agent summaries", () => {
  it("counts each agent's own decisions, with onchain refusals as blocks", () => {
    const w = reduceAll(emptyWorld, [
      registered("a1", "BTC Trader"),
      registered("a2", "ETH Trader"),
      decision("ALLOW"),
      decision("ALLOW"),
      decision("DENY"),
      decision("REQUIRE_APPROVAL"),
      ev("chain_refusal", { call: "top_up", error: "PerTxCapExceeded" }),
      decision("ALLOW", "a2"),
    ]);
    const [a1] = agentSummaries(w).filter((a) => a.id === "a1");
    expect(a1.counts).toEqual({ allow: 2, deny: 2, approval: 1 });
    expect(agentSummaries(w).find((a) => a.id === "a2")?.counts).toEqual({ allow: 1, deny: 0, approval: 0 });
  });

  it("puts what needs you first: waiting, then frozen, then the rest", () => {
    const w = reduceAll(emptyWorld, [
      registered("calm", "Calm"),
      registered("cold", "Cold"),
      registered("asks", "Asks"),
      ev("freeze", { reason: "outflow" }, "cold"),
      ev("approval_requested", { approval_id: "ap1", action_id: "x", agent_id: "asks", summary: "buy", amount_usd: "0.9", deadline: "2030-01-01T00:00:00Z" }, "asks"),
    ]);
    expect(agentSummaries(w).map((a) => [a.name, a.status])).toEqual([
      ["Asks", "waiting"],
      ["Cold", "frozen"],
      ["Calm", "idle"],
    ]);
  });

  it("reads spend, cap and the most at risk from the latest balances, as numbers", () => {
    const w = reduceAll(emptyWorld, [
      registered("a1", "BTC Trader"),
      decision("ALLOW"),
      balances({ a1: { hot_wallet: "h", hot_wallet_usdc: "0.046", daily_cap_usdc: "5", spent_today_usdc: "0.3", max_loss_bound_usdc: "4.746", frozen_onchain: false } }),
    ]);
    const [a] = agentSummaries(w);
    expect(a).toMatchObject({ status: "active", spent: 0.3, cap: 5, atRisk: 4.746, hotWallet: 0.046 });
  });

  it("treats frozen onchain as frozen even before the gate hears of it", () => {
    const w = reduceAll(emptyWorld, [registered("a1", "BTC Trader"), balances({ a1: { hot_wallet: "h", hot_wallet_usdc: "0", frozen_onchain: true } })]);
    expect(agentSummaries(w)[0].status).toBe("frozen");
  });
});

suite("activity over time", () => {
  it("splits the run into equal buckets and counts each outcome in its bucket", () => {
    const w = reduceAll(emptyWorld, [
      registered("a1", "BTC Trader"),
      decision("ALLOW", "a1", 0),
      decision("DENY", "a1", 10),
      decision("REQUIRE_APPROVAL", "a1", 50),
      decision("ALLOW", "a1", 100),
    ]);
    const s = activitySeries(w, null, 4);
    expect(s.buckets.map((b) => [b.allow, b.deny, b.approval])).toEqual([[1, 1, 0], [0, 0, 0], [0, 0, 1], [1, 0, 0]]);
    expect(s.total).toBe(4);
  });

  it("can follow one agent only", () => {
    const w = reduceAll(emptyWorld, [registered("a1", "A"), registered("a2", "B"), decision("ALLOW", "a1", 0), decision("DENY", "a2", 5), decision("ALLOW", "a1", 9)]);
    expect(activitySeries(w, "a2", 2).total).toBe(1);
    expect(activitySeries(w, "a1", 2).total).toBe(2);
  });

  it("has no buckets when nothing was decided", () => {
    expect(activitySeries(reduceAll(emptyWorld, [registered("a1", "A")]), null, 12).buckets).toEqual([]);
  });
});

suite("what needs you", () => {
  it("lists pending approvals and frozen agents, nothing else", () => {
    const w = reduceAll(emptyWorld, [
      registered("a1", "BTC Trader"),
      registered("a2", "Other"),
      ev("freeze", { reason: "money left that nothing allowed" }, "a1"),
      ev("approval_requested", { approval_id: "ap1", action_id: "x", agent_id: "a2", summary: "buy 1.8 YES at $0.50", amount_usd: "0.9", deadline: "2030-01-01T00:00:00Z" }, "a2"),
    ]);
    expect(attention(w).map((n) => [n.kind, n.agentId])).toEqual([
      ["approval", "a2"],
      ["frozen", "a1"],
    ]);
  });
});

suite("what is live in this run", () => {
  it("marks every part that is not the real thing, and Gemini orders always (sandbox at best)", () => {
    const w = reduceAll(emptyWorld, [registered("a1", "A"), ev("cycle_started", { proposer: "SCRIPTED (no LLM)" })]);
    const config = { labels: { cluster: "DEVNET", news_feed: "RECORDED FEED", gemini_orders: "SANDBOX", stream: "QuickNode Streams" } } as unknown as Config;
    expect(runParts(config, w).map((p) => [p.name, p.caveat])).toEqual([
      ["Chain", false],
      ["News", true],
      ["Gemini orders", true],
      ["Stream", false],
      ["Proposals", true],
    ]);
    expect(runParts(null, w)).toEqual([]);
  });
});

suite("money", () => {
  it("adds up the day's spend, caps and what is still at risk across agents", () => {
    const w = reduceAll(emptyWorld, [
      registered("a1", "A"),
      registered("a2", "B"),
      balances({
        a1: { hot_wallet: "h", hot_wallet_usdc: "0.05", daily_cap_usdc: "5", spent_today_usdc: "0.3", max_loss_bound_usdc: "4.75" },
        a2: { hot_wallet: "h2", hot_wallet_usdc: "0", daily_cap_usdc: "5", spent_today_usdc: "0", max_loss_bound_usdc: "5" },
      }),
    ]);
    expect(moneySummary(w)).toMatchObject({ vault: 9.5, spent: 0.3, cap: 10, atRisk: 9.75 });
  });
});
