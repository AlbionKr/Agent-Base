import { useEffect, useState } from "react";
import { explorerAddress, explorerTx, getJson, postJson, short, usd, type Config, type Ev, type World } from "./model";

type Line = { title: string; detail?: string; tone: "ok" | "bad" | "wait" | "plain"; signature?: string };

/** One audit event as a sentence, or null for events that are only bookkeeping. */
export function describe(ev: Ev, world: World): Line | null {
  const p = ev.payload;
  const who = (ev.agent_id && world.agents[ev.agent_id]?.name) || ev.agent_id || "";
  switch (ev.kind) {
    case "agent_registered":
      return { title: `${p.agent_name} registered`, detail: `Mandate signed by the owner: up to ${usd(p.mandate?.max_per_action_usd)} per action, ${usd(p.mandate?.max_per_day_usd)} per day.`, tone: "plain" };
    case "agent_provisioned":
      return { title: `${who} got its onchain allowance`, detail: `Hot wallet ${short(p.hot_wallet)}.`, tone: "plain", signature: p.signatures?.at(-1) };
    case "decision": {
      const what = p.action ? actionText(p.action) : "a malformed action";
      if (p.verdict === "ALLOW") return { title: `${who}: allowed ${what}`, detail: `${p.latency_us / 1000} ms to decide.`, tone: "ok" };
      if (p.verdict === "DENY") return { title: `${who}: blocked ${what}`, detail: `${(p.rules_fired ?? []).join(", ")}. ${p.reason}`, tone: "bad" };
      return { title: `${who}: ${what} needs approval`, detail: p.reason, tone: "wait" };
    }
    case "execution":
      return p.ok
        ? { title: `${who}: done`, detail: executed(p.detail), tone: "ok", signature: p.detail?.signature }
        : { title: `${who}: execution failed`, detail: String(p.detail?.error ?? ""), tone: "bad" };
    case "approval_resolved":
      return p.status === "approved"
        ? { title: `Approved by ${p.resolved_via}`, detail: p.summary, tone: "ok" }
        : { title: p.status === "expired" ? "No answer in time: denied" : `Denied by ${p.resolved_via}`, detail: p.summary, tone: "wait" };
    case "voice_mismatch":
      return { title: "Voice approval refused", detail: "The voice agent reported an approval the user did not say.", tone: "bad" };
    case "outflow_observed":
      return p.matched_action_id
        ? { title: `Stream: ${usd(p.amount_usdc)} left ${who}'s wallet, as allowed`, tone: "plain", signature: p.signature }
        : null;
    case "anomaly":
      return { title: `Stream: unexpected money movement from ${who}`, detail: (p.reasons ?? []).join(" "), tone: "bad", signature: p.signature };
    case "freeze":
      return {
        title: p.onchain?.status === "confirmed" ? `${who} frozen onchain by the guardian` : `${who} frozen in the gate only (onchain: ${p.onchain?.status})`,
        detail: p.reason,
        tone: "bad",
        signature: p.onchain?.signature,
      };
    case "unfreeze":
      return { title: `${who} unfrozen by the owner`, tone: "plain", signature: p.signature };
    case "naive_action":
      return p.executed
        ? { title: `Naive agent sent ${usd(p.amount_usd)} to ${short(p.payee)}`, detail: "Nothing checked it.", tone: "bad", signature: p.signature }
        : { title: "Naive agent could not act", detail: p.error, tone: "plain" };
    case "demo_step":
      return { title: `Demo: ${p.title}`, detail: p.detail, tone: "plain", signature: p.signature };
    case "cycle_note":
      return { title: `${who}: ${p.note}`, tone: "plain" };
    case "webhook_rejected":
      return { title: `Webhook delivery refused (${p.code})`, detail: p.suppressed_since_last ? `${p.suppressed_since_last} more since the last report.` : undefined, tone: "bad" };
    default:
      return null;
  }
}

function actionText(a: Record<string, any>): string {
  if (a.type === "gemini_order") return `${a.side} ${a.quantity} ${String(a.outcome).toUpperCase()} on ${a.symbol} at $${a.price}`;
  if (a.type === "transfer") return `a ${usd(a.amount)} transfer to ${short(a.payee)}`;
  return `a ${usd(a.amount)} payment for news`;
}

function executed(detail: Record<string, any> = {}): string {
  if (detail.via === "x402.exact") {
    const top = detail.top_up_units ? ` after a ${usd(Number(detail.top_up_units) / 1e6)} top-up from the vault` : "";
    return `Paid ${usd(Number(detail.price_units) / 1e6)} over x402${top}; ${detail.response?.items?.length ?? 0} posts returned (${detail.response?.label ?? "feed"}).`;
  }
  if (detail.via === "vault.pay") return `The vault paid ${short(detail.payee)} directly.`;
  if (detail.venue) return `Order ${detail.status ?? "accepted"} on ${detail.venue}.`;
  return "";
}

function Tx({ config, signature }: { config: Config | null; signature?: string }) {
  if (!signature) return null;
  return <a href={explorerTx(config, signature)} target="_blank" rel="noreferrer">transaction {short(signature)}</a>;
}

type Metrics = { decisions: number; blocked: number; blocked_citing_untrusted_content: number; by_verdict: Record<string, number>; policy_latency_us: { p50: number | null; p95: number | null; max: number | null }; freezes: number; anomalies: number };
type Scorecard = { generated_at: string; mode_note: string; summary: Record<string, any>; protected_agent: Record<string, any>; results: any[] };

export function Dashboard({ world, config }: { world: World; config: Config | null }) {
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null | "missing">(null);
  const [note, setNote] = useState("");

  useEffect(() => {
    getJson<Metrics>("/metrics").then(setMetrics).catch(() => setMetrics(null));
  }, [world.counts, world.lastId > 0]);
  useEffect(() => {
    getJson<Scorecard>("/redteam/scorecard").then(setScorecard).catch(() => setScorecard("missing"));
  }, []);

  async function act(path: string, done: string) {
    setNote("");
    try {
      await postJson(path);
      setNote(done);
    } catch (err) {
      setNote((err as Error).message);
    }
  }

  const ms = (us: number | null | undefined) => (us == null ? "–" : `${(us / 1000).toFixed(2)} ms`);
  const agents = Object.values(world.agents);
  const log = world.events.slice(-150).reverse();
  // One row per post: the same post bought twice is one piece of content.
  const seen = new Map<string, (typeof world.news)[number]>();
  for (const item of world.news) {
    const key = `${item.agentId}/${item.source}/${item.itemId}`;
    const earlier = seen.get(key);
    seen.set(key, earlier ? { ...item, blockedBy: [...new Set([...earlier.blockedBy, ...item.blockedBy])] } : item);
  }
  const all = [...seen.values()];
  const news = all.filter((n) => n.source.startsWith("x402-news") || n.blockedBy.length > 0);
  const market = all.length - news.length;

  return (
    <div className="dash">
      <section>
        <h2>Agents</h2>
        {agents.length === 0 ? (
          <p className="quiet">No agents yet. Place one from the top bar.</p>
        ) : (
          <table>
            <thead>
              <tr><th>Agent</th><th>Per action</th><th>Per day</th><th>Spent today (onchain)</th><th>Most at risk today</th><th>Hot wallet</th><th>State</th><th></th></tr>
            </thead>
            <tbody>
              {agents.map((a) => {
                const b = world.balances?.agents[a.id];
                const frozen = a.frozen || b?.frozen_onchain;
                return (
                  <tr key={a.id}>
                    <td><strong>{a.name}</strong><br /><span className="quiet">{a.proposer ?? "has not run yet"}</span></td>
                    <td>{usd(a.maxPerAction)}</td>
                    <td>{usd(a.maxPerDay)}</td>
                    <td>{b?.spent_today_usdc != null ? `${usd(b.spent_today_usdc)} of ${usd(b.daily_cap_usdc)}` : "–"}</td>
                    <td>{usd(b?.max_loss_bound_usdc)}</td>
                    <td>{b ? <a href={explorerAddress(config, b.hot_wallet)} target="_blank" rel="noreferrer">{short(b.hot_wallet)}</a> : "–"} {b && <span className="quiet">{usd(b.hot_wallet_usdc)}</span>}</td>
                    <td className={frozen ? "bad" : ""}>{frozen ? (b?.frozen_onchain ? "Frozen onchain" : "Frozen in the gate") : "Active"}</td>
                    <td className="buttons">
                      <button className="ghost" onClick={() => act(`/agents/${a.id}/cycle`, `${a.name} ran one cycle.`)}>Run a cycle</button>
                      {frozen && <button className="ghost" onClick={() => act(`/owner/agents/${a.id}/unfreeze`, `${a.name} unfrozen.`)}>Unfreeze</button>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
        {note && <p className="quiet" role="status">{note}</p>}
        <p className="quiet">
          “Most at risk today” is what is left of the onchain daily allowance plus the hot wallet balance. The vault program enforces it even if
          the agent and the policy engine are both compromised.
        </p>
      </section>

      <section className="two">
        <div>
          <h2>Numbers</h2>
          <dl className="numbers">
            <div><dt>Decisions</dt><dd>{metrics?.decisions ?? "–"}</dd></div>
            <div><dt>Blocked</dt><dd>{metrics?.blocked ?? "–"}</dd></div>
            <div><dt>Blocked citing content the agent read</dt><dd>{metrics?.blocked_citing_untrusted_content ?? "–"}</dd></div>
            <div><dt>Sent to a human</dt><dd>{metrics?.by_verdict.REQUIRE_APPROVAL ?? "–"}</dd></div>
            <div><dt>Policy decision time, median</dt><dd>{ms(metrics?.policy_latency_us.p50)}</dd></div>
            <div><dt>Policy decision time, slowest</dt><dd>{ms(metrics?.policy_latency_us.max)}</dd></div>
            <div><dt>Stream anomalies</dt><dd>{metrics?.anomalies ?? "–"}</dd></div>
            <div><dt>Freezes</dt><dd>{metrics?.freezes ?? "–"}</dd></div>
          </dl>
        </div>
        <div>
          <h2>What is real in this run</h2>
          <dl className="labels">
            {Object.entries(config?.labels ?? {}).map(([k, v]) => (
              <div key={k}><dt>{k.replace(/_/g, " ")}</dt><dd>{v}</dd></div>
            ))}
            {config && <div><dt>vault program</dt><dd><a href={explorerAddress(config, config.program_id)} target="_blank" rel="noreferrer">{config.program_id}</a></dd></div>}
            {config?.vault && <div><dt>vault account</dt><dd><a href={explorerAddress(config, config.vault)} target="_blank" rel="noreferrer">{config.vault}</a></dd></div>}
          </dl>
        </div>
      </section>

      <section>
        <h2>News the agents paid for</h2>
        {market > 0 && (
          <p className="quiet">
            The agents also read {market} market descriptions from Gemini. Those are treated as untrusted in the same way and are listed here only if a decision cited them.
          </p>
        )}
        {news.length === 0 ? (
          <p className="quiet">No news bought yet. Everything an agent reads is listed here as untrusted content.</p>
        ) : (
          <ul className="news">
            {news.slice(-24).reverse().map((n) => (
              <li key={n.eventId} className={n.blockedBy.length ? "tainted" : ""}>
                <span className="src">{n.source} {n.itemId}</span>
                <span className="txt">{n.text.replace(/[\u{E0000}-\u{E007F}]/gu, "␣")}</span>
                {n.blockedBy.length > 0 && <span className="flag">Cited when blocking decision{n.blockedBy.length > 1 ? "s" : ""} #{n.blockedBy.join(", #")}</span>}
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2>Red-team results</h2>
        {scorecard === "missing" && <p className="quiet">No scorecard yet. Run <code>python -m redteam.harness</code>.</p>}
        {scorecard && scorecard !== "missing" && (
          <>
            <p className="quiet">
              {scorecard.summary.run} of {scorecard.summary.cases} cases run: {scorecard.summary.blocked} blocked, {scorecard.summary.held_for_human} held for a human,{" "}
              {scorecard.summary.partially_blocked} partly blocked, {scorecard.summary.not_blocked} not blocked. Most released in any one case:{" "}
              {usd(scorecard.summary.largest_single_case_executed_usd)} (onchain bound {usd(scorecard.protected_agent.max_loss_bound_usd)} a day). {scorecard.mode_note}
            </p>
            <table className="score">
              <thead><tr><th>Case</th><th>Attack</th><th>Protected agent</th><th>Got out</th><th>Naive agent would lose</th><th>Stopped by</th></tr></thead>
              <tbody>
                {scorecard.results.map((r) => {
                  const steps = r.protected.steps ?? [];
                  const rules = [...new Set(steps.flatMap((s: any) => s.rules_fired))].slice(0, 3).join(", ");
                  const by = r.layer === "chain" ? r.protected.onchain_error ? `vault program: ${r.protected.onchain_error} (${r.cluster})` : "not run" : r.layer === "webhook" ? "webhook signature checks" : rules || "–";
                  return (
                    <tr key={r.id} className={r.protected.outcome}>
                      <td>{r.id}</td>
                      <td>{r.title}<br /><span className="quiet">{r.category}, {r.owasp}</span></td>
                      <td>{String(r.protected.outcome).replace(/_/g, " ")}</td>
                      <td>{usd(r.protected.executed_usd)}</td>
                      <td>{r.naive.outcome === "not_applicable" ? "n/a" : usd(r.naive.would_lose_usd)}</td>
                      <td>{by}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </>
        )}
      </section>

      <section>
        <h2>Audit log</h2>
        <table className="log">
          <thead><tr><th>#</th><th>Time</th><th>Event</th><th>What happened</th></tr></thead>
          <tbody>
            {log.map((ev) => {
              const line = describe(ev, world);
              return (
                <tr key={ev.id} className={line?.tone ?? "plain"}>
                  <td>{ev.id}</td>
                  <td>{new Date(ev.at).toLocaleTimeString()}</td>
                  <td>{ev.kind.replace(/_/g, " ")}</td>
                  <td>
                    {line ? <>{line.title}{line.detail ? <span className="quiet"> {line.detail}</span> : null} <Tx config={config} signature={line.signature} /></> : <span className="quiet">{ev.agent_id ?? ""}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </section>
    </div>
  );
}
