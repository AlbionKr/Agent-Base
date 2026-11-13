import { describe as suite, expect, it } from "vitest";
import { agentActivity, agentPayments, agentReads, naiveStory, threatStory } from "./detail";
import { emptyWorld, reduceAll, type Config, type Ev } from "./model";
import { sceneNodes } from "./nodes";

const ATTACKER = "CH1GUPkqXFG4pfuNHz8iYzU2qMnGyyuw3nNJCexo7YbB";
const config = { labels: { news_feed: "RECORDED FEED", gemini_orders: "NOT CONFIGURED (no sandbox API key)" }, attacker: ATTACKER } as unknown as Config;

let nextId = 0;
function ev(kind: string, payload: Record<string, any> = {}, agent_id: string | null = "a1"): Ev {
  nextId += 1;
  return { id: nextId, at: new Date(1_800_000_000_000 + nextId * 1000).toISOString(), kind, agent_id, payload };
}

const registered = (id = "a1", name = "BTC Trader") =>
  ev("agent_registered", { agent_name: name, mandate: { max_per_action_usd: "1.00", max_per_day_usd: "5.00", approval_threshold_usd: "0.50", allowed_payees: ["87ZM"] } }, id);
const denied = (payee = ATTACKER, taint: any[] = [{ item_id: "inj-1" }]) =>
  ev("decision", { verdict: "DENY", rules_fired: ["TAINT_NEW_PAYEE"], reason: "Payee came from a post.", taint, action: { type: "transfer", amount: "2", payee } });
const paid = () => ev("execution", { ok: true, detail: { via: "x402.exact", price_units: "1000", response: { items: [1], label: "RECORDED FEED" }, signature: "sig" } });
const read = (itemId: string, agent = "a1") => ev("context", { source: "x402-news/x", item_id: itemId, text: `post ${itemId}` }, agent);
const drain = () => ev("naive_action", { executed: true, amount_usd: "2", payee: ATTACKER }, "naive-agent");
const balances = (agents: Record<string, any> = {}, naive: string | null = "2") =>
  ev("balances", { vault_usdc: "9.8", agents, naive: naive === null ? null : { wallet: "w", usdc: naive }, attacker_usdc: "0" }, null);

suite("an agent's own history", () => {
  it("lists only that agent's events, newest first", () => {
    const events = [registered("a1"), registered("a2", "Other"), denied(), paid()];
    const mine = agentActivity(reduceAll(emptyWorld, events), "a1", 40);
    expect(mine.map((e) => e.ev.kind)).toEqual(["execution", "decision", "agent_registered"]);
  });

  it("lists what it paid for", () => {
    const events = [registered(), denied(), paid()];
    const payments = agentPayments(reduceAll(emptyWorld, events), "a1");
    expect(payments).toHaveLength(1);
    expect(payments[0].line.title).toContain("paid $0.001");
  });

  it("lists what it read once each, newest first", () => {
    const events = [registered(), read("001"), read("002"), read("001"), read("900", "a2")];
    const reads = agentReads(reduceAll(emptyWorld, events), "a1", 8);
    expect(reads.map((r) => r.itemId)).toEqual(["001", "002"]);
  });
});

suite("the attacker's story", () => {
  it("separates what was tried and stopped from what was taken", () => {
    const events = [
      registered(),
      denied(),
      ev("chain_refusal", { call: "top_up", amount_usdc: "1.000001", error: "PerTxCapExceeded" }),
      drain(),
      ev("anomaly", { reasons: ["0.05 USDC left"], rules: ["UNEXPECTED_OUTFLOW"] }),
    ];
    const story = threatStory(reduceAll(emptyWorld, events), ATTACKER);
    expect(story.stopped.map((e) => e.ev.kind)).toEqual(["chain_refusal", "decision"]);
    expect(story.taken.map((e) => e.ev.kind)).toEqual(["anomaly", "naive_action"]);
  });

  it("leaves out a block that had nothing to do with an attack", () => {
    const events = [registered(), ev("decision", { verdict: "DENY", rules_fired: ["AGENT_FROZEN"], reason: "Frozen.", action: { type: "x402_payment", amount: "0.001" } })];
    expect(threatStory(reduceAll(emptyWorld, events), ATTACKER).stopped).toHaveLength(0);
  });
});

suite("the unprotected agent's story", () => {
  it("is what it did, newest first", () => {
    const events = [registered(), drain(), ev("naive_action", { executed: false, error: "no funds" }, "naive-agent")];
    expect(naiveStory(reduceAll(emptyWorld, events)).map((e) => e.line.tag)).toEqual(["Note", "Stolen"]);
  });
});

suite("the nodes on the map", () => {
  const ids = (events: Ev[]) => sceneNodes(reduceAll(emptyWorld, events), config).map((n) => n.id);

  it("always has the vault, the three services and the attacker", () => {
    expect(ids([])).toEqual(["vault", "station:payees", "station:news", "station:markets", "attacker"]);
  });

  it("adds each agent, and the unprotected agent once it exists", () => {
    expect(ids([registered(), balances()])).toEqual(["vault", "agent:a1", "station:payees", "station:news", "station:markets", "attacker", "naive"]);
  });

  it("shows an agent's onchain spend against its daily cap", () => {
    const events = [registered(), balances({ a1: { hot_wallet: "h", hot_wallet_usdc: "0.1", daily_cap_usdc: "5", spent_today_usdc: "0.2" } })];
    const agent = sceneNodes(reduceAll(emptyWorld, events), config).find((n) => n.id === "agent:a1")!;
    expect(agent.budget).toEqual({ spent: 0.2, cap: 5 });
    expect(agent.sub).toBe("$0.20 of $5.00 spent today");
    expect(agent.status).toBe("active");
  });

  it("marks a frozen agent, and says where it is frozen", () => {
    const events = [registered(), ev("freeze", { reason: "drain", onchain: { status: "confirmed" } }), balances({ a1: { hot_wallet: "h", hot_wallet_usdc: "0", frozen_onchain: true } })];
    const agent = sceneNodes(reduceAll(emptyWorld, events), config).find((n) => n.id === "agent:a1")!;
    expect(agent.status).toBe("frozen");
    expect(agent.tag).toBe("Frozen onchain");
  });

  it("marks an agent that is waiting for a person", () => {
    const events = [registered(), ev("approval_requested", { approval_id: "ap1", action_id: "x", agent_id: "a1", summary: "buy", amount_usd: "0.9", deadline: "2030-01-01T00:00:00Z" })];
    const agent = sceneNodes(reduceAll(emptyWorld, events), config).find((n) => n.id === "agent:a1")!;
    expect(agent.status).toBe("waiting");
    expect(agent.tag).toBe("Waiting for your answer");
  });

  it("marks the unprotected agent as having lost money", () => {
    const naive = sceneNodes(reduceAll(emptyWorld, [registered(), balances(), drain()]), config).find((n) => n.id === "naive")!;
    expect(naive.status).toBe("lost");
    expect(naive.tag).toBe("Lost $2.00");
  });

  it("carries the run's labels on the services", () => {
    const nodes = sceneNodes(reduceAll(emptyWorld, []), config);
    expect(nodes.find((n) => n.id === "station:news")!.sub).toContain("RECORDED FEED");
    expect(nodes.find((n) => n.id === "station:markets")!.sub).toBe("orders are not sent: no sandbox key");
  });
});
