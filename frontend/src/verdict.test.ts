import { expect, it } from "vitest";
import { verdict } from "./describe";
import { emptyWorld, reduceAll, type Ev } from "./model";

let nextId = 0;
function ev(kind: string, payload: Record<string, any> = {}, agent_id: string | null = "a1"): Ev {
  nextId += 1;
  return { id: nextId, at: new Date(1_800_000_000_000 + nextId * 1000).toISOString(), kind, agent_id, payload };
}

const registered = () => ev("agent_registered", { agent_name: "BTC Trader", mandate: {} });
const drain = () => ev("naive_action", { executed: true, amount_usd: "2", payee: "CH1GUPkqXFG4pfuNHz8iYzU2qMnGyyuw3nNJCexo7YbB" }, "naive-agent");
const leak = () => ev("outflow_observed", { amount_units: "50000", matched_action_id: null });

it("says there is no agent before one is placed", () => {
  expect(verdict(emptyWorld).lead).toBe("No agent is placed yet.");
});

it("says nothing has been taken while that is true", () => {
  expect(verdict(reduceAll(emptyWorld, [registered()])).lead).toBe("Nothing has been taken.");
});

it("sets the unprotected loss against a guarded agent that lost nothing", () => {
  const v = verdict(reduceAll(emptyWorld, [registered(), drain()]));
  expect(v.lead).toBe("Attackers took $2.00 without the guard");
  expect(v.turn).toBe("and nothing with it.");
});

it("names what left the guarded agent too, once something has", () => {
  const v = verdict(reduceAll(emptyWorld, [registered(), drain(), leak()]));
  expect(v.turn).toBe("and $0.05 with it.");
});

it("reports a guarded loss on its own when there is no unprotected agent to compare", () => {
  const v = verdict(reduceAll(emptyWorld, [registered(), leak()]));
  expect(v.lead).toBe("Attackers took $0.05 from the guarded agent.");
});
