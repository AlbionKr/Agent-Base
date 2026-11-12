/** The wall around an agent has one segment per family of rules. */
export const FAMILIES = ["mandate", "allowlists", "spending caps", "rate limit", "content tracking", "approval"];

/** Which wall segment a policy rule belongs to. */
export function ruleFamily(rule: string): number {
  if (rule.startsWith("TAINT") || rule === "PAYEE_LOOKALIKE") return 4;
  if (rule.endsWith("NOT_ALLOWLISTED") || rule.endsWith("NOT_ALLOWED")) return 1;
  if (rule === "PER_ACTION_CAP" || rule === "DAILY_BUDGET" || rule === "SLOW_DRAIN") return 2;
  if (rule === "RATE_LIMIT") return 3;
  if (rule === "APPROVAL_THRESHOLD") return 5;
  return 0;
}
