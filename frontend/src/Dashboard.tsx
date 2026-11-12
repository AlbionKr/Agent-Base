import { useEffect, useState } from "react";
import { describe, type Tone } from "./describe";
import { explorerAddress, getJson, postJson, short, usd, type Config, type World } from "./model";
import { Glyph, Tag, Tx } from "./ui";

type Metrics = { decisions: number; blocked: number; blocked_citing_untrusted_content: number; by_verdict: Record<string, number>; policy_latency_us: { p50: number | null; p95: number | null; max: number | null }; freezes: number; anomalies: number };
type Scorecard = { generated_at: string; mode_note: string; summary: Record<string, any>; protected_agent: Record<string, any>; results: any[] };

/** Red-team outcomes, in the order they are drawn. */
const OUTCOMES: { key: string; name: string; tone: Tone }[] = [
  { key: "blocked", name: "Blocked", tone: "ok" },
  { key: "held_for_human", name: "Held for a person", tone: "wait" },
  { key: "partially_blocked", name: "Partly blocked", tone: "warn" },
  { key: "not_blocked", name: "Not blocked", tone: "bad" },
];

function Tile({ label, value, note }: { label: string; value: string | number; note?: string }) {
  return (
    <div className="tile">
      <dt>{label}</dt>
      <dd>{value}</dd>
      {note && <p>{note}</p>}
    </div>
  );
}

function RedTeam({ scorecard }: { scorecard: Scorecard }) {
  const [filter, setFilter] = useState("all");
  const s = scorecard.summary;
  const parts = OUTCOMES.map((o) => ({ ...o, count: Number(s[o.key] ?? 0) }));
  const run = Number(s.run ?? 0);
  const naiveTotal = scorecard.results.reduce((sum, r) => sum + (r.naive.outcome === "not_applicable" ? 0 : Number(r.naive.would_lose_usd ?? 0)), 0);
  const gotOut = scorecard.results.reduce((sum, r) => sum + Number(r.protected.executed_usd ?? 0), 0);
  const widest = Math.max(naiveTotal, gotOut, 0.000001);
  const rows = scorecard.results.filter((r) => filter === "all" || r.protected.outcome === filter);

  return (
    <>
      <p className="verdict">
        {run} attacks run: {s.blocked} blocked, {s.held_for_human} held for a person, {s.partially_blocked} partly blocked,{" "}
        {Number(s.not_blocked) === 0 ? "none got through unchecked" : `${s.not_blocked} not blocked`}.
      </p>
      <div className="segbar" role="img" aria-label={parts.map((p) => `${p.name}: ${p.count} of ${run}`).join(". ")}>
        {parts.filter((p) => p.count > 0).map((p) => (
          <span key={p.key} className={p.tone} style={{ flexGrow: p.count }} title={`${p.name}: ${p.count} of ${run}`}>
            {p.count / run >= 0.06 ? p.count : ""}
          </span>
        ))}
      </div>
      <ul className="keys">
        {parts.map((p) => (
          <li key={p.key} className={p.tone}><i />{p.name}</li>
        ))}
      </ul>

      <div className="bars">
        <div>
          <span className="name">An unprotected agent would lose, across these cases</span>
          <span className="track"><i className="bad" style={{ "--share": naiveTotal / widest } as React.CSSProperties} /><b>{usd(naiveTotal)}</b></span>
        </div>
        <div>
          <span className="name">Got out of the protected agent</span>
          <span className="track"><i className="ok" style={{ "--share": gotOut / widest } as React.CSSProperties} /><b>{usd(gotOut)}</b></span>
        </div>
      </div>
      <p className="quiet">
        Most released in any one case: {usd(s.largest_single_case_executed_usd)}; the onchain bound is {usd(scorecard.protected_agent.max_loss_bound_usd)} a day. {scorecard.mode_note}
      </p>

      <div className="filters" role="group" aria-label="Show cases by outcome">
        <button className={filter === "all" ? "chip on" : "chip"} onClick={() => setFilter("all")}>All {scorecard.results.length}</button>
        {parts.filter((p) => p.count > 0).map((p) => (
          <button key={p.key} className={filter === p.key ? "chip on" : "chip"} onClick={() => setFilter(p.key)}>{p.name} {p.count}</button>
        ))}
      </div>
      <div className="scroll">
        <table className="score-table">
          <thead><tr><th>Case</th><th>Attack</th><th>Protected agent</th><th>Got out</th><th>Unprotected would lose</th><th>Stopped by</th></tr></thead>
          <tbody>
            {rows.map((r) => {
              const steps = r.protected.steps ?? [];
              const rules = [...new Set(steps.flatMap((step: any) => step.rules_fired))].slice(0, 3).join(", ");
              const by = r.layer === "chain" ? (r.protected.onchain_error ? `vault program: ${r.protected.onchain_error} (${r.cluster})` : "not run") : r.layer === "webhook" ? "webhook signature checks" : rules || "–";
              const outcome = OUTCOMES.find((o) => o.key === r.protected.outcome);
              return (
                <tr key={r.id}>
                  <td>{r.id}</td>
                  <td>{r.title}<br /><span className="quiet">{r.category}, {r.owasp}</span></td>
                  <td>{outcome ? <Tag tone={outcome.tone}>{outcome.name}</Tag> : String(r.protected.outcome).replace(/_/g, " ")}</td>
                  <td>{usd(r.protected.executed_usd)}</td>
                  <td>{r.naive.outcome === "not_applicable" ? "n/a" : usd(r.naive.would_lose_usd)}</td>
                  <td>{by}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

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
      <dl className="tiles" aria-label="This run in numbers">
        <Tile label="Decisions" value={metrics?.decisions ?? "–"} />
        <Tile label="Blocked" value={metrics?.blocked ?? "–"} note={metrics ? `${metrics.blocked_citing_untrusted_content} cited something the agent read` : undefined} />
        <Tile label="Sent to a person" value={metrics?.by_verdict.REQUIRE_APPROVAL ?? "–"} />
        <Tile label="Freezes" value={metrics?.freezes ?? "–"} note={metrics ? `${metrics.anomalies} stream alarm${metrics.anomalies === 1 ? "" : "s"}` : undefined} />
        <Tile label="Time to decide, median" value={ms(metrics?.policy_latency_us.p50)} note={metrics ? `slowest ${ms(metrics.policy_latency_us.max)}` : undefined} />
      </dl>

      <section>
        <h2>Agents</h2>
        {agents.length === 0 ? (
          <p className="quiet">No agents yet. Place one from the top bar.</p>
        ) : (
          <div className="scroll">
            <table>
              <thead>
                <tr><th>Agent</th><th>State</th><th>Spent today, onchain</th><th>Per action</th><th>Most at risk today</th><th>Hot wallet</th><th></th></tr>
              </thead>
              <tbody>
                {agents.map((a) => {
                  const b = world.balances?.agents[a.id];
                  const frozen = a.frozen || b?.frozen_onchain;
                  const cap = Number(b?.daily_cap_usdc ?? a.maxPerDay);
                  const spent = Number(b?.spent_today_usdc ?? 0);
                  return (
                    <tr key={a.id}>
                      <td><strong>{a.name}</strong><br /><span className="quiet">{a.proposer ?? "has not run yet"}</span></td>
                      <td>{frozen ? <Tag tone="lock">{b?.frozen_onchain ? "Frozen onchain" : "Frozen in the gate"}</Tag> : <Tag tone="ok">Active</Tag>}</td>
                      <td>
                        {b?.spent_today_usdc != null ? (
                          <>
                            <span className="meter" aria-hidden="true"><i style={{ width: `${cap > 0 ? Math.min(100, (spent / cap) * 100) : 0}%` }} /></span>
                            {usd(spent)} of {usd(cap)}
                          </>
                        ) : `up to ${usd(a.maxPerDay)}`}
                      </td>
                      <td>{usd(a.maxPerAction)}</td>
                      <td>{usd(b?.max_loss_bound_usdc)}</td>
                      <td>{b ? <a href={explorerAddress(config, b.hot_wallet)} target="_blank" rel="noreferrer">{short(b.hot_wallet)}</a> : "–"} {b && <span className="quiet">{usd(b.hot_wallet_usdc)}</span>}</td>
                      <td className="buttons">
                        <button className="ghost" onClick={() => act(`/agents/${a.id}/cycle`, `${a.name} ran one cycle.`)}>Run a cycle</button>
                        {frozen && <button className="ghost" onClick={() => act(`/owner/agents/${a.id}/unfreeze`, `${a.name} unfrozen.`)}>Unfreeze</button>}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
        {note && <p className="quiet" role="status">{note}</p>}
        <p className="quiet">
          “Most at risk today” is what is left of the onchain daily allowance plus the hot wallet balance. The vault program enforces it even if
          the agent and the policy engine are both compromised.
        </p>
      </section>

      <section>
        <h2>Red-team results</h2>
        {scorecard === "missing" && <p className="quiet">No scorecard yet. Run <code>python -m redteam.harness</code>.</p>}
        {scorecard && scorecard !== "missing" && <RedTeam scorecard={scorecard} />}
      </section>

      <section className="two">
        <div>
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
                  {n.blockedBy.length > 0 && <Tag tone="bad">Attack post</Tag>}
                  <span className="src">{n.source} {n.itemId}</span>
                  <span className="txt">{n.text.replace(/[\u{E0000}-\u{E007F}]/gu, "␣")}</span>
                  {n.blockedBy.length > 0 && <span className="flag">Cited when blocking decision{n.blockedBy.length > 1 ? "s" : ""} #{n.blockedBy.join(", #")}</span>}
                </li>
              ))}
            </ul>
          )}
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
        <h2>Audit log</h2>
        <div className="scroll">
          <table className="log">
            <thead><tr><th>#</th><th>Time</th><th>Event</th><th>What happened</th></tr></thead>
            <tbody>
              {log.map((ev) => {
                const line = describe(ev, world);
                return (
                  <tr key={ev.id}>
                    <td>{ev.id}</td>
                    <td>{new Date(ev.at).toLocaleTimeString()}</td>
                    <td>{line && line.tone !== "plain" ? <Tag tone={line.tone}>{line.tag}</Tag> : <span className="kind"><Glyph tone="plain" />{ev.kind.replace(/_/g, " ")}</span>}</td>
                    <td>
                      {line ? <>{line.title}{line.detail ? <span className="quiet"> {line.detail}</span> : null} <Tx config={config} signature={line.signature} /></> : <span className="quiet">{ev.payload?.title ?? ev.agent_id ?? ""}</span>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
