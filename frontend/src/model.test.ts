import { describe as suite, expect, it } from "vitest";
import { describe, headline, timeline } from "./describe";
import { emptyWorld, reduceAll, usd, type Ev } from "./model";

let nextId = 0;
function ev(kind: string, payload: Record<string, any> = {}, agent_id: string | null = "a1"): Ev {
  nextId += 1;
  return { id: nextId, at: new Date(1_800_000_000_000 + nextId * 1000).toISOString(), kind, agent_id, payload };
}

const registered = () =>
  ev("agent_registered", { agent_name: "BTC Trader", mandate: { max_per_action_usd: "1.00", max_per_day_usd: "5.00", approval_threshold_usd: "0.50" } });

suite("money taken from protected agents", () => {
  it("counts an outflow that no allowed action explains", () => {
    const world = reduceAll(emptyWorld, [registered(), ev("outflow_observed", { amount_units: "50000", matched_action_id: null })]);
    expect(world.protectedLostUnits).toBe(50000);
  });

  it("does not count an outflow the gate released", () => {
    const world = reduceAll(emptyWorld, [registered(), ev("outflow_observed", { amount_units: "100000", matched_action_id: "act-1" })]);
    expect(world.protectedLostUnits).toBe(0);
  });

  it("adds up several unexplained outflows exactly", () => {
    const world = reduceAll(emptyWorld, [
      ev("outflow_observed", { amount_units: "1", matched_action_id: null }),
      ev("outflow_observed", { amount_units: "50000", matched_action_id: null }),
    ]);
    expect(world.protectedLostUnits).toBe(50001);
  });
});

suite("demo chapters", () => {
  it("records each chapter with its place in the script", () => {
    const world = reduceAll(emptyWorld, [
      ev("demo_chapter", { number: 1, total: 7, title: "Place a protected agent" }, null),
      ev("demo_chapter", { number: 2, total: 7, title: "A normal small trade" }, null),
    ]);
    expect(world.chapters.map((c) => [c.number, c.total, c.title])).toEqual([
      [1, 7, "Place a protected agent"],
      [2, 7, "A normal small trade"],
    ]);
  });

  it("starts over when a new run reaches chapter 1 again", () => {
    const world = reduceAll(emptyWorld, [
      ev("demo_chapter", { number: 1, total: 7, title: "Place a protected agent" }, null),
      ev("demo_chapter", { number: 2, total: 7, title: "A normal small trade" }, null),
      ev("demo_chapter", { number: 1, total: 7, title: "Place a protected agent" }, null),
    ]);
    expect(world.chapters).toHaveLength(1);
  });
});

suite("describing events", () => {
  it("tags a denied transfer as blocked and names the amount", () => {
    const events = [registered(), ev("decision", { verdict: "DENY", rules_fired: ["TAINT_NEW_PAYEE"], reason: "The payee came from a post.", action: { type: "transfer", amount: "2", payee: "CH1GUPkqXFG4pfuNHz8iYzU2qMnGyyuw3nNJCexo7YbB" } })];
    const world = reduceAll(emptyWorld, events);
    const line = describe(events[1], world)!;
    expect(line.tag).toBe("Blocked");
    expect(line.tone).toBe("bad");
    expect(line.title).toContain("$2.00");
    expect(line.detail).toContain("The payee came from a post.");
  });

  it("tags an action that needs a person", () => {
    const events = [registered(), ev("decision", { verdict: "REQUIRE_APPROVAL", reason: "Above the threshold.", action: { type: "gemini_order", side: "buy", quantity: "1.8", outcome: "yes", symbol: "GEMI-X", price: "0.50" } })];
    const line = describe(events[1], reduceAll(emptyWorld, events))!;
    expect(line.tag).toBe("Needs you");
    expect(line.tone).toBe("wait");
  });

  it("says nothing for bookkeeping events", () => {
    const e = ev("balances", {});
    expect(describe(e, reduceAll(emptyWorld, [e]))).toBeNull();
  });
});

suite("timeline", () => {
  it("groups events under their chapter, newest chapter and newest event first", () => {
    const events = [
      ev("demo_chapter", { number: 1, total: 2, title: "One" }, null),
      registered(),
      ev("demo_chapter", { number: 2, total: 2, title: "Two" }, null),
      ev("freeze", { reason: "drain", onchain: { status: "confirmed" } }),
      ev("unfreeze", {}),
    ];
    const groups = timeline(reduceAll(emptyWorld, events), 40);
    expect(groups.map((g) => g.chapter?.title)).toEqual(["Two", "One"]);
    expect(groups[0].items.map((i) => i.ev.kind)).toEqual(["unfreeze", "freeze"]);
    expect(groups[1].items.map((i) => i.ev.kind)).toEqual(["agent_registered"]);
  });

  it("keeps events from before any chapter in a group with no chapter", () => {
    const events = [registered()];
    const groups = timeline(reduceAll(emptyWorld, events), 40);
    expect(groups).toHaveLength(1);
    expect(groups[0].chapter).toBeNull();
  });
});

suite("headline", () => {
  it("is the latest event that is not routine", () => {
    const events = [registered(), ev("freeze", { reason: "drain", onchain: { status: "confirmed" } }), ev("demo_step", { title: "note" }, null)];
    const top = headline(reduceAll(emptyWorld, events));
    expect(top?.ev.kind).toBe("freeze");
  });

  it("is empty before anything notable happened", () => {
    expect(headline(reduceAll(emptyWorld, [registered()]))).toBeNull();
  });
});

suite("amounts", () => {
  it("keeps the third decimal when a sub-dollar amount has one", () => {
    expect(usd("0.046")).toBe("$0.046");
    expect(usd("0.001")).toBe("$0.001");
  });

  it("shows whole cents otherwise", () => {
    expect(usd("0.05")).toBe("$0.05");
    expect(usd("0.9")).toBe("$0.90");
    expect(usd(2)).toBe("$2.00");
    expect(usd("9.8")).toBe("$9.80");
  });
});

suite("what the unprotected agent held", () => {
  const balances = (naive: string) => ev("balances", { vault_usdc: "9.8", agents: {}, naive: { wallet: "w", usdc: naive }, attacker_usdc: "0" }, null);
  const drain = () => ev("naive_action", { executed: true, amount_usd: "2", payee: "CH1GUPkqXFG4pfuNHz8iYzU2qMnGyyuw3nNJCexo7YbB" }, "naive-agent");

  it("is not doubled while the balance is still the one from before the drain", () => {
    const world = reduceAll(emptyWorld, [balances("2"), drain()]);
    expect(world.naiveHeld).toBe(2);
  });

  it("stays the same once the emptied balance arrives", () => {
    const world = reduceAll(emptyWorld, [balances("2"), drain(), balances("0")]);
    expect(world.naiveHeld).toBe(2);
  });
});

suite("a direct call the vault program refused", () => {
  const refusal = () => ev("chain_refusal", { call: "top_up", amount_usdc: "1.000001", error: "PerTxCapExceeded", note: "A stolen agent key called the vault directly." });

  it("is counted", () => {
    expect(reduceAll(emptyWorld, [registered(), refusal()]).counts.refusedOnchain).toBe(1);
  });

  it("reads as blocked onchain, with the exact amount and the program's error", () => {
    const events = [registered(), refusal()];
    const line = describe(events[1], reduceAll(emptyWorld, events))!;
    expect(line.tag).toBe("Blocked onchain");
    expect(line.tone).toBe("bad");
    expect(line.title).toContain("$1.000001");
    expect(line.detail).toContain("PerTxCapExceeded");
  });
});

suite("a paid news call", () => {
  it("says what was paid in the title", () => {
    const events = [registered(), ev("execution", { ok: true, detail: { via: "x402.exact", price_units: "1000", response: { items: [1, 2, 3], label: "RECORDED FEED" }, signature: "sig" } })];
    const line = describe(events[1], reduceAll(emptyWorld, events))!;
    expect(line.title).toBe("BTC Trader paid $0.001 for news over x402");
    expect(line.detail).toContain("3 posts");
  });
});
