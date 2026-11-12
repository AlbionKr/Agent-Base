import { expect, it } from "vitest";
import { FAMILIES, ruleFamily } from "./rules";

it("puts every policy rule on the wall segment that names its job", () => {
  const family = (rule: string) => FAMILIES[ruleFamily(rule)];
  expect(family("TAINT_NEW_PAYEE")).toBe("content tracking");
  expect(family("TAINT_PARAM")).toBe("content tracking");
  expect(family("PAYEE_LOOKALIKE")).toBe("content tracking");
  expect(family("PAYEE_NOT_ALLOWLISTED")).toBe("allowlists");
  expect(family("MARKET_NOT_ALLOWED")).toBe("allowlists");
  expect(family("ENDPOINT_NOT_ALLOWED")).toBe("allowlists");
  expect(family("PER_ACTION_CAP")).toBe("spending caps");
  expect(family("DAILY_BUDGET")).toBe("spending caps");
  expect(family("SLOW_DRAIN")).toBe("spending caps");
  expect(family("RATE_LIMIT")).toBe("rate limit");
  expect(family("APPROVAL_THRESHOLD")).toBe("approval");
  expect(family("MANDATE_EXPIRED")).toBe("mandate");
  expect(family("AGENT_FROZEN")).toBe("mandate");
});
