// The dashboard: one page per topic, an overview that leads with what needs you,
// and a page per agent. Every figure is read from audit events or the backend's own reports.

import { useEffect, useMemo, useRef, useState } from "react";
import { ApprovalCard } from "./Approval";
import { ActivityChart, ChartLegend, Ring } from "./charts";
import { describe, type Entry, type Tone } from "./describe";
import { agentActivity, agentPayments, agentReads } from "./detail";
import { Address, Events, Facts, Posts } from "./Drawer";
import { activitySeries, agentSummaries, attention, moneySummary, runParts, type AgentSummary } from "./fleet";
import { Icon, type IconName } from "./icons";
import { explorerAddress, getJson, postJson, short, usd, type Config, type World } from "./model";
import { Glyph, Num, Tag, Tx } from "./ui";

type Metrics = { decisions: number; blocked: number; blocked_citing_untrusted_content: number; by_verdict: Record<string, number>; policy_latency_us: { p50: number | null; p95: number | null; max: number | null }; freezes: number; anomalies: number };
type Scorecard = { generated_at: string; mode_note: string; summary: Record<string, any>; protected_agent: Record<string, any>; results: any[] };

type PageName = "overview" | "agents" | "agent" | "redteam" | "news" | "log" | "setup";
type Page = { name: PageName; id?: string };

const PAGES: { name: PageName; label: string; icon: IconName }[] = [
  { name: "overview", label: "Overview", icon: "overview" },
  { name: "agents", label: "Agents", icon: "agents" },
  { name: "redteam", label: "Red team", icon: "shield" },
  { name: "news", label: "Paid news", icon: "news" },
  { name: "log", label: "Audit log", icon: "log" },
  { name: "setup", label: "This run", icon: "setup" },
];

/** Red-team outcomes, in the order they are drawn. */
const OUTCOMES: { key: string; name: string; tone: Tone }[] = [
  { key: "blocked", name: "Blocked", tone: "ok" },
  { key: "held_for_human", name: "Held for a person", tone: "wait" },
  { key: "partially_blocked", name: "Partly blocked", tone: "warn" },
  { key: "not_blocked", name: "Not blocked", tone: "bad" },
];

// The page lives in the address, so the back button and a shared link both work.
function readHash(): Page {
  const [, name, id] = window.location.hash.replace(/^#/, "").split("/");
  if (name === "agents" && id) return { name: "agent", id: decodeURIComponent(id) };
  return PAGES.some((p) => p.name === name) ? { name: name as PageName } : { name: "overview" };
}
function hashOf(page: Page): string {
  return page.name === "agent" ? `#/agents/${encodeURIComponent(page.id ?? "")}` : `#/${page.name}`;
}

const STATUS: Record<AgentSummary["status"], { tone: Tone; text: (a: AgentSummary) => string }> = {
  waiting: { tone: "wait", text: () => "Waiting for you" },
  frozen: { tone: "lock", text: (a) => (a.frozenOnchain ? "Frozen onchain" : "Frozen in the gate") },
  active: { tone: "ok", text: () => "Active" },
  idle: { tone: "plain", text: () => "Not run yet" },
};

function StatusTag({ agent }: { agent: AgentSummary }) {
  const s = STATUS[agent.status];
  return <Tag tone={s.tone}>{s.text(agent)}</Tag>;
}

function ago(at: string | null): string {
  if (!at) return "no activity yet";
  const s = Math.max(0, Math.round((Date.now() - Date.parse(at)) / 1000));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(at).toLocaleDateString();
}

function Card({ title, action, className = "", children }: { title?: React.ReactNode; action?: React.ReactNode; className?: string; children: React.ReactNode }) {
  return (
    <section className={`card ${className}`}>
      {(title || action) && (
        <header className="cardhead">
          {title && <h2>{title}</h2>}
          {action}
        </header>
      )}
      {children}
    </section>
  );
}

function More({ to, go, children }: { to: Page; go: (p: Page) => void; children: React.ReactNode }) {
  return (
    <a className="more" href={hashOf(to)} onClick={(e) => { e.preventDefault(); go(to); }}>
      {children}
    </a>
  );
}

function AgentAvatar({ agent, size = 56 }: { agent: AgentSummary; size?: number }) {
  return (
    <span className={`avatar ${STATUS[agent.status].tone}`} style={{ width: size, height: size }}>
      <Ring value={agent.spent ?? 0} max={agent.cap} size={size} />
      <span className="avatarface"><Icon name={agent.status === "frozen" ? "lock" : "agent"} size={Math.round(size * 0.42)} /></span>
    </span>
  );
}

function Spend({ agent }: { agent: AgentSummary }) {
  const share = agent.cap > 0 && agent.spent != null ? Math.min(1, agent.spent / agent.cap) : 0;
  return (
    <div className="spend">
      <span className="meter" aria-hidden="true"><i style={{ width: `${share * 100}%` }} /></span>
      <span className="num">{agent.spent != null ? `${usd(agent.spent)} of ${usd(agent.cap)}` : `up to ${usd(agent.cap)}`}</span>
    </div>
  );
}

function Counts({ agent }: { agent: AgentSummary }) {
  return (
    <ul className="minicounts" aria-label="Decisions so far">
      <li className="ok" title="Allowed"><Glyph tone="ok" /><span className="num">{agent.counts.allow}</span><span className="sr">allowed</span></li>
      <li className="wait" title="Asked a person"><Glyph tone="wait" /><span className="num">{agent.counts.approval}</span><span className="sr">asked a person</span></li>
      <li className="bad" title="Blocked"><Glyph tone="bad" /><span className="num">{agent.counts.deny}</span><span className="sr">blocked</span></li>
    </ul>
  );
}

// ------------------------------------------------------------------ overview

function NeedsYou({ world, go, voiceReady, timeoutSeconds, onUnfreeze }: { world: World; go: (p: Page) => void; voiceReady: boolean; timeoutSeconds: number; onUnfreeze: (id: string, name: string) => void }) {
  const items = attention(world);
  const agents = Object.values(world.agents);
  if (items.length === 0) {
    return (
      <section className="calm">
        <span className="calmicon"><Icon name="shield" /></span>
        <div>
          <b>Nothing needs you right now.</b>
          <span>{agents.length === 0 ? "No agent is placed yet." : `${agents.length} agent${agents.length === 1 ? "" : "s"}, every action checked against the limits you signed.`}</span>
        </div>
      </section>
    );
  }
  return (
    <section className="needs" aria-label="Needs you">
      <h2>Needs you <span className="count">{items.length}</span></h2>
      <div className="needslist">
        {items.map((item) =>
          item.kind === "approval" ? (
            <ApprovalCard key={item.approval.approval_id} approval={item.approval} world={world} voiceReady={voiceReady} timeoutSeconds={timeoutSeconds} />
          ) : (
            <FrozenRow key={item.agentId} world={world} agentId={item.agentId} go={go} onUnfreeze={onUnfreeze} />
          ),
        )}
      </div>
    </section>
  );
}

function FrozenRow({ world, agentId, go, onUnfreeze }: { world: World; agentId: string; go: (p: Page) => void; onUnfreeze: (id: string, name: string) => void }) {
  const agent = world.agents[agentId];
  const freeze = [...world.events].reverse().find((e) => e.kind === "freeze" && e.agent_id === agentId);
  const onchain = world.balances?.agents[agentId]?.frozen_onchain === true;
  return (
    <div className="frozenrow lock">
      <span className="rowicon"><Icon name="lock" /></span>
      <div className="rowtext">
        <b>{agent?.name ?? agentId} is frozen {onchain ? "onchain" : "in the gate"}</b>
        <span>{freeze?.payload.reason ?? "The vault refuses it any more money until you unfreeze it."}</span>
      </div>
      <div className="rowactions">
        <button className="ghost" onClick={() => go({ name: "agent", id: agentId })}>Open</button>
        <button onClick={() => onUnfreeze(agentId, agent?.name ?? agentId)}>Unfreeze</button>
      </div>
    </div>
  );
}

function MoneyCard({ world }: { world: World }) {
  const m = moneySummary(world);
  return (
    <Card title="Money today" className="money">
      <div className="moneybody">
        <Ring value={m.spent} max={m.cap} size={156}>
          <strong className="num"><Num value={m.spent} format={usd} /></strong>
          <span>spent today</span>
          <span className="of">of {usd(m.cap)}</span>
        </Ring>
        <dl className="moneyrows">
          <div><dt><i className="sw accent" />In the vault</dt><dd className="num">{m.vault != null ? usd(m.vault) : "–"}</dd></div>
          <div><dt><i className="sw plain" />Still at risk today</dt><dd className="num">{m.atRisk != null ? usd(m.atRisk) : "–"}</dd></div>
          <div><dt><i className="sw ok" />Taken with the guard</dt><dd className="num">{usd(m.withGuard)}</dd></div>
          <div><dt><i className="sw bad" />Taken without it</dt><dd className="num">{usd(m.without)}</dd></div>
        </dl>
      </div>
    </Card>
  );
}

function AgentCards({ world, go, canPlace, onPlace }: { world: World; go: (p: Page) => void; canPlace: boolean; onPlace: () => void }) {
  const agents = agentSummaries(world);
  const strip = useRef<HTMLDivElement>(null);
  const [edges, setEdges] = useState({ start: true, end: true });
  const measure = () => {
    const el = strip.current;
    if (el) setEdges({ start: el.scrollLeft <= 2, end: el.scrollLeft + el.clientWidth >= el.scrollWidth - 2 });
  };
  useEffect(measure, [agents.length]);
  useEffect(() => {
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);
  const slide = (dir: number) => strip.current?.scrollBy({ left: dir * 260, behavior: "smooth" });

  return (
    <Card
      title={<>Agents <span className="count">{agents.length}</span></>}
      className="agentscard"
      action={
        <div className="cardtools">
          <button className="iconbtn" onClick={() => slide(-1)} disabled={edges.start} aria-label="Previous agents"><Icon name="left" size={18} /></button>
          <button className="iconbtn" onClick={() => slide(1)} disabled={edges.end} aria-label="More agents"><Icon name="right" size={18} /></button>
          <More to={{ name: "agents" }} go={go}>All agents</More>
        </div>
      }
    >
      <div className="cardstrip" ref={strip} onScroll={measure}>
        {agents.map((a, i) => (
          <button key={a.id} className={`agentcard ${STATUS[a.status].tone}${i === 0 && (a.status === "waiting" || a.status === "frozen") ? " flag" : ""}`} onClick={() => go({ name: "agent", id: a.id })}>
            <AgentAvatar agent={a} />
            <b className="name">{a.name}</b>
            <StatusTag agent={a} />
            <Spend agent={a} />
            <Counts agent={a} />
          </button>
        ))}
        {canPlace && (
          <button className="agentcard add" onClick={onPlace}>
            <span className="addicon"><Icon name="plus" /></span>
            <b className="name">Place an agent</b>
            <span className="quiet">Sign its limits; the vault creates its allowance onchain.</span>
          </button>
        )}
        {agents.length === 0 && !canPlace && <p className="quiet">No agents yet.</p>}
      </div>
    </Card>
  );
}

function Recent({ world, go, config }: { world: World; go: (p: Page) => void; config: Config | null }) {
  const notable = useMemo(() => {
    const out: Entry[] = [];
    for (let i = world.events.length - 1; i >= 0 && out.length < 4; i--) {
      const line = describe(world.events[i], world);
      if (line && line.tone !== "plain") out.push({ ev: world.events[i], line });
    }
    return out;
  }, [world]);
  return (
    <Card title="Recent activity" className="recent" action={<More to={{ name: "log" }} go={go}>Audit log</More>}>
      <div className="duo">
        <div className="duotile bad">
          <Glyph tone="bad" />
          <strong className="num"><Num value={world.counts.deny + world.counts.refusedOnchain} /></strong>
          <span>blocked</span>
        </div>
        <div className="duotile wait">
          <Glyph tone="wait" />
          <strong className="num"><Num value={world.counts.approval} /></strong>
          <span>asked a person</span>
        </div>
      </div>
      {notable.length === 0 ? (
        <p className="quiet">Nothing has happened yet.</p>
      ) : (
        <ol className="feed">
          {notable.map(({ ev, line }) => (
            <li key={ev.id} className={line.tone}>
              <span className="dot" aria-hidden="true" />
              <div>
                <p>{line.title}</p>
                <span className="meta">{line.tag}, {new Date(ev.at).toLocaleTimeString()} <Tx config={config} signature={line.signature} /></span>
              </div>
            </li>
          ))}
        </ol>
      )}
    </Card>
  );
}

function SegBar({ scorecard }: { scorecard: Scorecard }) {
  const s = scorecard.summary;
  const run = Number(s.run ?? 0);
  const parts = OUTCOMES.map((o) => ({ ...o, count: Number(s[o.key] ?? 0) }));
  return (
    <>
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
    </>
  );
}

function redTeamSentence(s: Record<string, any>): string {
  return `${s.run} attacks run: ${s.blocked} blocked, ${s.held_for_human} held for a person, ${s.partially_blocked} partly blocked, ${Number(s.not_blocked) === 0 ? "none got through unchecked" : `${s.not_blocked} not blocked`}.`;
}

function uniqueNews(world: World) {
  // One row per post: the same post bought twice is one piece of content.
  const seen = new Map<string, (typeof world.news)[number]>();
  for (const item of world.news) {
    const key = `${item.agentId}/${item.source}/${item.itemId}`;
    const earlier = seen.get(key);
    seen.set(key, earlier ? { ...item, blockedBy: [...new Set([...earlier.blockedBy, ...item.blockedBy])] } : item);
  }
  const all = [...seen.values()];
  const news = all.filter((n) => n.source.startsWith("x402-news") || n.blockedBy.length > 0);
  return { news, market: all.length - news.length };
}

function Topics({ world, config, scorecard, go }: { world: World; config: Config | null; scorecard: Scorecard | null | "missing"; go: (p: Page) => void }) {
  const { news } = uniqueNews(world);
  const attacks = news.filter((n) => n.blockedBy.length > 0);
  const parts = runParts(config, world);
  const live = parts.filter((p) => !p.caveat).length;
  return (
    <div className="topics">
      <Card title="Red team" action={<More to={{ name: "redteam" }} go={go}>All cases</More>}>
        {scorecard && scorecard !== "missing" ? (
          <>
            <p className="topicline">{redTeamSentence(scorecard.summary)}</p>
            <SegBar scorecard={scorecard} />
          </>
        ) : (
          <p className="quiet">{scorecard === "missing" ? "No scorecard yet. Run python -m redteam.harness." : "Loading the scorecard."}</p>
        )}
      </Card>
      <Card title="Paid news" action={<More to={{ name: "news" }} go={go}>All posts</More>}>
        <p className="topicline"><span className="num">{news.length}</span> posts bought over x402, <span className="num">{attacks.length}</span> caught as attack posts.</p>
        {attacks[0] ? (
          <div className="snippet bad"><Glyph tone="bad" /><p>{attacks[attacks.length - 1].text.replace(/[\u{E0000}-\u{E007F}]/gu, "␣")}</p></div>
        ) : (
          <p className="quiet">Everything an agent reads is treated as untrusted.</p>
        )}
      </Card>
      <Card title="This run" action={<More to={{ name: "setup" }} go={go}>Details</More>}>
        <p className="topicline"><span className="num">{live}</span> of <span className="num">{parts.length}</span> parts fully live.</p>
        <ul className="parts">
          {parts.map((p) => (
            <li key={p.name + p.value} className={p.caveat ? "warn" : "ok"}>
              <Glyph tone={p.caveat ? "warn" : "ok"} />
              <span>{p.name}</span>
              <span className="value">{p.value}</span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}

function Overview(props: PageProps) {
  const { world, config, scorecard, go } = props;
  const series = useMemo(() => activitySeries(world, null, 24), [world]);
  return (
    <div className="pagegrid">
      <NeedsYou world={world} go={go} voiceReady={props.voiceReady} timeoutSeconds={props.timeoutSeconds} onUnfreeze={props.unfreeze} />
      <div className="row money-agents">
        <MoneyCard world={world} />
        <AgentCards world={world} go={go} canPlace={props.canPlace} onPlace={props.onPlace} />
      </div>
      <div className="row chart-recent">
        <Card title="Decisions over time" action={<ChartLegend />}>
          <ActivityChart series={series} label="Decisions over time, all agents" height={280} />
        </Card>
        <Recent world={world} go={go} config={config} />
      </div>
      <Topics world={world} config={config} scorecard={scorecard} go={go} />
    </div>
  );
}

// -------------------------------------------------------------- other pages

function AgentsPage({ world, go, canPlace, onPlace }: PageProps) {
  const agents = agentSummaries(world);
  return (
    <div className="pagegrid">
      <Card title={<>All agents <span className="count">{agents.length}</span></>} action={canPlace ? <button className="small" onClick={onPlace}>Place an agent</button> : undefined}>
        {agents.length === 0 ? (
          <p className="quiet">No agents yet.</p>
        ) : (
          <ul className="agentrows">
            {agents.map((a) => (
              <li key={a.id}>
                <a href={hashOf({ name: "agent", id: a.id })} onClick={(e) => { e.preventDefault(); go({ name: "agent", id: a.id }); }}>
                  <AgentAvatar agent={a} size={44} />
                  <span className="who">
                    <b>{a.name}</b>
                    <span className="quiet">{a.proposer ?? "has not run yet"}</span>
                  </span>
                  <StatusTag agent={a} />
                  <span className="col"><span className="lbl">Spent today</span><Spend agent={a} /></span>
                  <span className="col"><span className="lbl">Most at risk</span><span className="num">{a.atRisk != null ? usd(a.atRisk) : "–"}</span></span>
                  <span className="col"><span className="lbl">Decisions</span><Counts agent={a} /></span>
                  <span className="col"><span className="lbl">Last activity</span><span>{ago(a.lastAt)}</span></span>
                  <span className="chev"><Icon name="right" size={18} /></span>
                </a>
              </li>
            ))}
          </ul>
        )}
        <p className="quiet foot">“Most at risk” is what is left of the onchain daily allowance plus the hot wallet balance. The vault program enforces it even if the agent and the policy engine are both compromised.</p>
      </Card>
    </div>
  );
}

function AgentPage({ world, config, go, id, runCycle, unfreeze }: PageProps & { id: string }) {
  const summary = agentSummaries(world).find((a) => a.id === id);
  const agent = world.agents[id];
  const series = useMemo(() => activitySeries(world, id, 16), [world, id]);
  if (!summary || !agent) {
    return (
      <Card title="Agent not found">
        <p className="quiet">No agent with this id is registered. <More to={{ name: "agents" }} go={go}>Back to all agents</More></p>
      </Card>
    );
  }
  const bal = world.balances?.agents[id];
  return (
    <div className="pagegrid">
      <header className="agenthead">
        <AgentAvatar agent={summary} size={72} />
        <div className="who">
          <h1>{summary.name}</h1>
          <div className="badges">
            <StatusTag agent={summary} />
            {bal && <Address value={bal.hot_wallet} config={config} />}
            <span className="quiet">{summary.proposer ? `Proposals: ${summary.proposer}` : "Has not run a cycle yet."}</span>
          </div>
        </div>
        <div className="rowactions">
          <button onClick={() => runCycle(id, summary.name)}><Icon name="play" size={16} />Run a cycle</button>
          {summary.status === "frozen" && <button onClick={() => unfreeze(id, summary.name)}><Icon name="unlock" size={16} />Unfreeze</button>}
        </div>
      </header>

      <div className="stats4">
        <div className="statcard"><span>Spent today</span><strong className="num">{summary.spent != null ? usd(summary.spent) : "–"}</strong><em>of {usd(summary.cap)}, counted onchain</em></div>
        <div className="statcard"><span>Most at risk today</span><strong className="num">{summary.atRisk != null ? usd(summary.atRisk) : "–"}</strong><em>allowance left plus hot wallet</em></div>
        <div className="statcard"><span>Hot wallet</span><strong className="num">{summary.hotWallet != null ? usd(summary.hotWallet) : "–"}</strong><em>what a stolen key could take now</em></div>
        <div className="statcard"><span>Decisions</span><strong className="num">{summary.counts.allow + summary.counts.approval + summary.counts.deny}</strong><Counts agent={summary} /></div>
      </div>

      <div className="row chart-recent">
        <Card title="Decisions over time" action={<ChartLegend />}>
          <ActivityChart series={series} label={`Decisions over time, ${summary.name}`} height={200} />
        </Card>
        <Card title="Limits it signed">
          <Facts
            rows={[
              ["Most per action", usd(agent.maxPerAction)],
              ["Most per day", usd(agent.maxPerDay)],
              ["Asks you above", usd(agent.approvalThreshold)],
              ["Also asks when", "an amount or market traces back to something it read"],
              ["Markets", agent.markets.length ? agent.markets.join(", ") : "none"],
              ["Payees", agent.payees.length ? agent.payees.map((p) => short(p)).join(", ") : "none"],
            ]}
          />
        </Card>
      </div>

      <div className="row thirds">
        <Card title="Activity"><div className="scrollbox"><Events items={agentActivity(world, id, 40)} config={config} empty="Nothing yet." /></div></Card>
        <Card title="Payments"><div className="scrollbox"><Events items={agentPayments(world, id)} config={config} empty="No payment has been carried out." /></div></Card>
        <Card title="What it read"><div className="scrollbox"><Posts items={agentReads(world, id, 12)} empty="It has not read anything yet." /></div></Card>
      </div>
    </div>
  );
}

function RedTeamPage({ scorecard }: PageProps) {
  const [filter, setFilter] = useState("all");
  if (!scorecard || scorecard === "missing") {
    return <Card title="Red team"><p className="quiet">{scorecard === "missing" ? <>No scorecard yet. Run <code>python -m redteam.harness</code>.</> : "Loading the scorecard."}</p></Card>;
  }
  const s = scorecard.summary;
  const naiveTotal = scorecard.results.reduce((sum, r) => sum + (r.naive.outcome === "not_applicable" ? 0 : Number(r.naive.would_lose_usd ?? 0)), 0);
  const gotOut = scorecard.results.reduce((sum, r) => sum + Number(r.protected.executed_usd ?? 0), 0);
  const widest = Math.max(naiveTotal, gotOut, 0.000001);
  const parts = OUTCOMES.map((o) => ({ ...o, count: Number(s[o.key] ?? 0) }));
  const rows = scorecard.results.filter((r) => filter === "all" || r.protected.outcome === filter);
  return (
    <div className="pagegrid">
      <Card>
        <p className="verdict">{redTeamSentence(s)}</p>
        <SegBar scorecard={scorecard} />
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
      </Card>
      <Card
        title="Cases"
        action={
          <div className="filters" role="group" aria-label="Show cases by outcome">
            <button className={filter === "all" ? "chip on" : "chip"} onClick={() => setFilter("all")}>All {scorecard.results.length}</button>
            {parts.filter((p) => p.count > 0).map((p) => (
              <button key={p.key} className={filter === p.key ? "chip on" : "chip"} onClick={() => setFilter(p.key)}>{p.name} {p.count}</button>
            ))}
          </div>
        }
      >
        <div className="scroll">
          <table>
            <thead><tr><th>Case</th><th>Attack</th><th>Protected agent</th><th>Got out</th><th>Unprotected would lose</th><th>Stopped by</th></tr></thead>
            <tbody>
              {rows.map((r) => {
                const steps = r.protected.steps ?? [];
                const rules = [...new Set(steps.flatMap((step: any) => step.rules_fired))].slice(0, 3).join(", ");
                const by = r.layer === "chain" ? (r.protected.onchain_error ? `vault program: ${r.protected.onchain_error} (${r.cluster})` : "not run") : r.layer === "webhook" ? "webhook signature checks" : rules || "–";
                const outcome = OUTCOMES.find((o) => o.key === r.protected.outcome);
                return (
                  <tr key={r.id}>
                    <td className="meta">{r.id}</td>
                    <td>{r.title}<br /><span className="quiet">{r.category}, {r.owasp}</span></td>
                    <td>{outcome ? <Tag tone={outcome.tone}>{outcome.name}</Tag> : String(r.protected.outcome).replace(/_/g, " ")}</td>
                    <td className="num">{usd(r.protected.executed_usd)}</td>
                    <td className="num">{r.naive.outcome === "not_applicable" ? "n/a" : usd(r.naive.would_lose_usd)}</td>
                    <td>{by}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Card>
    </div>
  );
}

function NewsPage({ world }: PageProps) {
  const { news, market } = uniqueNews(world);
  const [only, setOnly] = useState(false);
  const shown = (only ? news.filter((n) => n.blockedBy.length > 0) : news).slice(-60).reverse();
  return (
    <div className="pagegrid">
      <Card
        title={<>Posts the agents paid for <span className="count">{news.length}</span></>}
        action={
          <div className="filters" role="group" aria-label="Which posts">
            <button className={!only ? "chip on" : "chip"} onClick={() => setOnly(false)}>All</button>
            <button className={only ? "chip on" : "chip"} onClick={() => setOnly(true)}>Attack posts</button>
          </div>
        }
      >
        <p className="quiet intro">
          Bought at $0.001 a call over x402. Paying for a post does not make it safe: everything here is untrusted, and a post that a blocked decision cited is marked.
          {market > 0 && ` The agents also read ${market} market descriptions from Gemini, treated the same way.`}
        </p>
        {shown.length === 0 ? (
          <p className="quiet">No posts here yet.</p>
        ) : (
          <ul className="news">
            {shown.map((n) => (
              <li key={n.eventId} className={n.blockedBy.length ? "tainted" : ""}>
                {n.blockedBy.length > 0 && <Tag tone="bad">Attack post</Tag>}
                <span className="src">{n.source} {n.itemId}</span>
                <span className="txt">{n.text.replace(/[\u{E0000}-\u{E007F}]/gu, "␣")}</span>
                {n.blockedBy.length > 0 && <span className="flag">Cited when blocking decision{n.blockedBy.length > 1 ? "s" : ""} #{n.blockedBy.join(", #")}</span>}
              </li>
            ))}
          </ul>
        )}
      </Card>
    </div>
  );
}

const LOG_FILTERS: { key: Tone | "all"; name: string }[] = [
  { key: "all", name: "Everything" },
  { key: "ok", name: "Allowed and done" },
  { key: "wait", name: "Asked a person" },
  { key: "bad", name: "Blocked and alarms" },
  { key: "lock", name: "Frozen" },
];

function LogPage({ world, config }: PageProps) {
  const [filter, setFilter] = useState<Tone | "all">("all");
  const rows = world.events
    .slice()
    .reverse()
    .map((ev) => ({ ev, line: describe(ev, world) }))
    .filter(({ line }) => filter === "all" || line?.tone === filter)
    .slice(0, 200);
  return (
    <div className="pagegrid">
      <Card
        title="Every event, newest first"
        action={
          <div className="filters" role="group" aria-label="Show events by outcome">
            {LOG_FILTERS.map((f) => (
              <button key={f.key} className={filter === f.key ? "chip on" : "chip"} onClick={() => setFilter(f.key)}>{f.name}</button>
            ))}
          </div>
        }
      >
        <div className="scroll">
          <table>
            <thead><tr><th>#</th><th>Time</th><th>Event</th><th>What happened</th></tr></thead>
            <tbody>
              {rows.map(({ ev, line }) => (
                <tr key={ev.id}>
                  <td className="meta">{ev.id}</td>
                  <td className="meta when">{new Date(ev.at).toLocaleTimeString()}</td>
                  <td>{line && line.tone !== "plain" ? <Tag tone={line.tone}>{line.tag}</Tag> : <span className="kind"><Glyph tone="plain" />{line?.tag ?? ev.kind.replace(/_/g, " ")}</span>}</td>
                  <td>
                    {line ? (
                      <>
                        {line.title}
                        {line.detail ? <span className="quiet detail">{line.detail}</span> : null}
                        {line.signature && <span className="meta detail"><Tx config={config} signature={line.signature} /></span>}
                      </>
                    ) : (
                      <span className="quiet">{ev.payload?.title ?? "Bookkeeping: nothing to show on its own."}</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {rows.length === 0 && <p className="quiet">No events of this kind yet.</p>}
      </Card>
    </div>
  );
}

function SetupPage({ world, config, metrics }: PageProps) {
  const parts = runParts(config, world);
  const ms = (us: number | null | undefined) => (us == null ? "–" : `${(us / 1000).toFixed(2)} ms`);
  return (
    <div className="pagegrid">
      <Card title="What is real in this run">
        <ul className="parts big">
          {parts.map((p) => (
            <li key={p.name + p.value} className={p.caveat ? "warn" : "ok"}>
              <Glyph tone={p.caveat ? "warn" : "ok"} />
              <span>{p.name}</span>
              <span className="value">{p.value}</span>
              <span className="quiet">{p.caveat ? "not the real thing yet" : "live"}</span>
            </li>
          ))}
        </ul>
        <Facts
          rows={[
            ...Object.entries(config?.labels ?? {}).map(([k, v]): [string, React.ReactNode] => [k.charAt(0).toUpperCase() + k.slice(1).replace(/_/g, " "), v]),
            ...(config ? [["vault program", <a href={explorerAddress(config, config.program_id)} target="_blank" rel="noreferrer" className="meta">{config.program_id}</a>] as [string, React.ReactNode]] : []),
            ...(config?.vault ? [["vault account", <a href={explorerAddress(config, config.vault)} target="_blank" rel="noreferrer" className="meta">{config.vault}</a>] as [string, React.ReactNode]] : []),
          ]}
        />
      </Card>
      <Card title="The policy engine">
        <div className="stats4">
          <div className="statcard"><span>Decisions</span><strong className="num">{metrics?.decisions ?? "–"}</strong><em>since the backend started</em></div>
          <div className="statcard"><span>Blocked</span><strong className="num">{metrics?.blocked ?? "–"}</strong><em>{metrics ? `${metrics.blocked_citing_untrusted_content} cited something the agent read` : ""}</em></div>
          <div className="statcard"><span>Freezes</span><strong className="num">{metrics?.freezes ?? "–"}</strong><em>{metrics ? `${metrics.anomalies} stream alarm${metrics.anomalies === 1 ? "" : "s"}` : ""}</em></div>
          <div className="statcard"><span>Time to decide, median</span><strong className="num">{ms(metrics?.policy_latency_us.p50)}</strong><em>slowest {ms(metrics?.policy_latency_us.max)}</em></div>
        </div>
      </Card>
    </div>
  );
}

// -------------------------------------------------------------------- shell

type PageProps = {
  world: World;
  config: Config | null;
  metrics: Metrics | null;
  scorecard: Scorecard | null | "missing";
  go: (p: Page) => void;
  canPlace: boolean;
  onPlace: () => void;
  voiceReady: boolean;
  timeoutSeconds: number;
  runCycle: (id: string, name: string) => void;
  unfreeze: (id: string, name: string) => void;
};

type Props = { world: World; config: Config | null; canPlace: boolean; onPlace: () => void; voiceReady: boolean; timeoutSeconds: number };

export function Dashboard({ world, config, canPlace, onPlace, voiceReady, timeoutSeconds }: Props) {
  const [page, setPage] = useState<Page>(readHash);
  const [metrics, setMetrics] = useState<Metrics | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null | "missing">(null);
  const [note, setNote] = useState("");
  const content = useRef<HTMLDivElement>(null);

  useEffect(() => {
    getJson<Metrics>("/metrics").then(setMetrics).catch(() => setMetrics(null));
  }, [world.counts, world.lastId > 0]);
  useEffect(() => {
    getJson<Scorecard>("/redteam/scorecard").then(setScorecard).catch(() => setScorecard("missing"));
  }, []);
  useEffect(() => {
    const onHash = () => setPage(readHash());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  function go(next: Page) {
    if (hashOf(next) !== window.location.hash) window.history.pushState(null, "", hashOf(next));
    setPage(next);
    setNote("");
    content.current?.scrollTo({ top: 0 });
  }

  async function act(path: string, done: string) {
    setNote("");
    try {
      await postJson(path);
      setNote(done);
    } catch (err) {
      setNote((err as Error).message);
    }
  }

  const props: PageProps = {
    world,
    config,
    metrics,
    scorecard,
    go,
    canPlace,
    onPlace,
    voiceReady,
    timeoutSeconds,
    runCycle: (id, name) => act(`/agents/${id}/cycle`, `${name} ran one cycle.`),
    unfreeze: (id, name) => act(`/owner/agents/${id}/unfreeze`, `${name} unfrozen.`),
  };
  const agents = Object.values(world.agents);
  const needs = attention(world).length;
  const { news } = uniqueNews(world);
  const badge: Partial<Record<PageName, number>> = { overview: needs, agents: agents.length, news: news.length };
  const current = page.name === "agent" ? "agents" : page.name;
  const title = page.name === "agent" ? world.agents[page.id ?? ""]?.name ?? "Agent" : PAGES.find((p) => p.name === page.name)?.label;

  return (
    <div className="fleet">
      <nav className="side" aria-label="Dashboard pages">
        <ul>
          {PAGES.map((p) => (
            <li key={p.name}>
              <a href={hashOf({ name: p.name })} className={current === p.name ? "on" : ""} aria-current={current === p.name ? "page" : undefined} onClick={(e) => { e.preventDefault(); go({ name: p.name }); }}>
                <Icon name={p.icon} />
                <span>{p.label}</span>
                {badge[p.name] ? <span className={p.name === "overview" ? "badge alert" : "badge"}>{badge[p.name]}</span> : null}
              </a>
            </li>
          ))}
        </ul>
        {canPlace && <button className="cta" onClick={onPlace}><Icon name="plus" size={18} />Place an agent</button>}
      </nav>

      <div className="content" ref={content}>
        <header className="pagehead">
          {page.name === "agent" && (
            <p className="crumbs">
              <More to={{ name: "agents" }} go={go}>Agents</More>
              <span aria-hidden="true">/</span>
            </p>
          )}
          {page.name !== "agent" && <h1>{title}</h1>}
          {note && <p className="note" role="status">{note}</p>}
        </header>
        {page.name === "overview" && <Overview {...props} />}
        {page.name === "agents" && <AgentsPage {...props} />}
        {page.name === "agent" && <AgentPage {...props} id={page.id ?? ""} />}
        {page.name === "redteam" && <RedTeamPage {...props} />}
        {page.name === "news" && <NewsPage {...props} />}
        {page.name === "log" && <LogPage {...props} />}
        {page.name === "setup" && <SetupPage {...props} />}
      </div>
    </div>
  );
}
