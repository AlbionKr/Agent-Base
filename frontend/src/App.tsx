import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApprovalCard } from "./Approval";
import { Dashboard } from "./Dashboard";
import { headline, timeline } from "./describe";
import { API, emptyWorld, getJson, pendingApprovals, postJson, reduceAll, unitsToUsd, usd, type Config, type Ev, type World } from "./model";
import { Scene } from "./scene";
import { Glyph, Tag, Tx } from "./ui";

type Connection = "connecting" | "live" | "offline";

/** Events older than this when they arrive are history: drawn, not animated. */
const LIVE_WINDOW_MS = 8000;

function useWorld(onLive: (ev: Ev) => void) {
  const [world, dispatch] = useReducer(reduceAll, emptyWorld);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [config, setConfig] = useState<Config | null>(null);
  const live = useRef(onLive);
  live.current = onLive;

  useEffect(() => {
    let buffer: Ev[] = [];
    let frame = 0;
    const flush = () => {
      frame = 0;
      const batch = buffer;
      buffer = [];
      dispatch(batch);
      batch.forEach((ev) => {
        if (Date.now() - Date.parse(ev.at) < LIVE_WINDOW_MS) live.current(ev);
      });
    };
    // The browser reconnects by itself and sends Last-Event-ID, so nothing is missed.
    const source = new EventSource(`${API}/events/stream`);
    source.onopen = () => {
      setConnection("live");
      getJson<Config>("/config").then(setConfig).catch(() => setConfig(null));
    };
    source.onerror = () => setConnection("offline");
    source.onmessage = (message) => {
      buffer.push(JSON.parse(message.data) as Ev);
      if (!frame) frame = requestAnimationFrame(flush);
    };
    return () => {
      source.close();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return { world, connection, config };
}

function BaseMap({ world, config, sceneRef }: { world: World; config: Config | null; sceneRef: React.MutableRefObject<Scene | null> }) {
  const host = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const scene = new Scene();
    sceneRef.current = scene;
    scene.mount(host.current!);
    return () => {
      sceneRef.current = null;
      scene.destroy();
    };
  }, [sceneRef]);
  useEffect(() => {
    sceneRef.current?.setWorld(world, config);
  }, [world, config, sceneRef]);
  return <div className="map" ref={host} role="img" aria-label="Map of the vault, its agents and the attacks against them" />;
}

/** The one comparison the whole product is about: what attackers took with the guard, and without it. */
function Scoreline({ world }: { world: World }) {
  const agents = Object.values(world.agents);
  const bounds = agents.map((a) => world.balances?.agents[a.id]?.max_loss_bound_usdc).filter((b): b is string => b != null);
  const bound = bounds.reduce((sum, b) => sum + Number(b), 0);
  const hasNaive = world.balances?.naive != null || world.naiveLost > 0;
  const guardedLost = unitsToUsd(world.protectedLostUnits);

  return (
    <section className="score" aria-label="What attackers took">
      <h2>Taken by attackers</h2>
      <div className="sides">
        <div className="side guarded">
          <p className="figure">{usd(guardedLost)}</p>
          <p className="whose">with the guard{agents.length > 0 && <span> ({agents.map((a) => a.name).join(", ")})</span>}</p>
          <p className="context">
            {agents.length === 0
              ? "No protected agent yet."
              : bounds.length > 0
                ? `The vault program lets at most ${usd(bound)} more leave today, even with a stolen key.`
                : "The vault program caps what can leave, even with a stolen key."}
          </p>
        </div>
        <div className={world.naiveLost > 0 ? "side bare hit" : "side bare"}>
          <p className="figure">{hasNaive ? usd(world.naiveLost) : "–"}</p>
          <p className="whose">without it{hasNaive && <span> (unprotected agent)</span>}</p>
          <p className="context">
            {hasNaive ? `It held ${usd(world.naiveHeld)} and read the same posts. Nothing checks its payments.` : "No unprotected agent in this run."}
          </p>
        </div>
      </div>
      <ul className="tally" aria-label="Outcomes so far">
        <li className="ok"><Glyph tone="ok" /><b>{world.counts.allow}</b> allowed</li>
        <li className="bad"><Glyph tone="bad" /><b>{world.counts.deny + world.counts.refusedOnchain}</b> blocked</li>
        <li className="wait"><Glyph tone="wait" /><b>{world.counts.approval}</b> asked a person</li>
        <li className="lock"><Glyph tone="lock" /><b>{world.counts.freezes}</b> frozen</li>
      </ul>
    </section>
  );
}

/** Where the demo script is. Drawn only from the script's own chapter events. */
function Rail({ world }: { world: World }) {
  const current = world.chapters[world.chapters.length - 1];
  if (!current) return null;
  return (
    <div className="rail">
      <ol aria-hidden="true">
        {Array.from({ length: current.total }, (_, i) => {
          const seen = world.chapters.find((c) => c.number === i + 1);
          return <li key={i} className={i + 1 === current.number ? "here" : seen ? "done" : ""} title={seen?.title}>{i + 1}</li>;
        })}
      </ol>
      <p>
        <span>Demo step {current.number} of {current.total}</span> {current.title}
      </p>
    </div>
  );
}

function Legend() {
  return (
    <ul className="legend" aria-label="What the moving shapes mean">
      <li><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><circle cx="7" cy="7" r="5" fill="var(--brass)" stroke="var(--ink)" strokeWidth="1.5" /></svg>money moving</li>
      <li><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M7 1l6 6-6 6-6-6z" fill="var(--attack)" stroke="var(--ink)" strokeWidth="1.5" /></svg>attack</li>
      <li><svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><rect x="3" y="1.5" width="8" height="11" fill="var(--paper)" stroke="var(--ink)" strokeWidth="1.5" /></svg>something an agent read</li>
    </ul>
  );
}

/** The latest thing that mattered, in type large enough to read from the back of a room. */
function Now({ world }: { world: World }) {
  const top = headline(world);
  if (!top) {
    return (
      <div className="now plain">
        <p className="say">Nothing to report yet.</p>
        <p className="why">The latest allowed, blocked or frozen action shows here.</p>
      </div>
    );
  }
  return (
    <div className={`now ${top.line.tone}`} key={top.ev.id} aria-live="polite">
      <Tag tone={top.line.tone}>{top.line.tag}</Tag>
      <p className="say">{top.line.title}</p>
      {top.line.detail && <p className="why">{top.line.detail}</p>}
    </div>
  );
}

function RunStatus({ config, world }: { config: Config | null; world: World }) {
  if (!config) return null;
  const l = config.labels;
  const proposers = new Set(Object.values(world.agents).map((a) => a.proposer).filter(Boolean) as string[]);
  const items: [string, string, boolean][] = [
    ["Chain", l.cluster ?? "?", l.cluster !== "DEVNET"],
    ["News", l.news_feed ?? "?", l.news_feed !== "LIVE FEED"],
    ["Gemini orders", l.gemini_orders ?? "?", true],
    ["Stream", l.stream ?? "?", l.stream !== "QuickNode Streams"],
    ...[...proposers].map((p): [string, string, boolean] => ["Proposals", p, !p.startsWith("LLM")]),
  ];
  const caveats = items.filter(([, , caveat]) => caveat).length;
  return (
    <footer className="run">
      <span className="lead">{caveats > 0 ? "Not fully live in this run" : "This run"}</span>
      <ul aria-label="What is real and what is not in this run">
        {items.map(([name, value, caveat]) => (
          <li key={name + value} className={caveat ? "caveat" : ""}>
            <span>{name}</span> {value}
          </li>
        ))}
      </ul>
    </footer>
  );
}

function PlaceAgent({ onClose }: { onClose: () => void }) {
  const [form, setForm] = useState({ agent_name: "BTC Trader", max_per_action_usd: "1.00", max_per_day_usd: "5.00", approval_threshold_usd: "0.50", markets: "GEMI-BTC05M*" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const set = (key: keyof typeof form) => (e: React.ChangeEvent<HTMLInputElement>) => setForm({ ...form, [key]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError("");
    try {
      const { markets, ...rest } = form;
      await postJson("/owner/agents", { ...rest, allowed_markets: markets.split(",").map((m) => m.trim()).filter(Boolean) });
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" onClick={onClose}>
      <form className="dialog" onSubmit={submit} onClick={(e) => e.stopPropagation()}>
        <h2>Place an agent</h2>
        <p className="quiet">
          Signs a mandate with the owner key on this machine, creates a session key, and creates the agent's spending
          allowance in the vault program. The caps below are enforced onchain.
        </p>
        <label>Name<input value={form.agent_name} onChange={set("agent_name")} required maxLength={40} /></label>
        <label>Markets it may trade<input value={form.markets} onChange={set("markets")} /></label>
        <div className="row">
          <label>Most per action (USD)<input value={form.max_per_action_usd} onChange={set("max_per_action_usd")} inputMode="decimal" required /></label>
          <label>Most per day (USD)<input value={form.max_per_day_usd} onChange={set("max_per_day_usd")} inputMode="decimal" required /></label>
          <label>Ask me above (USD)<input value={form.approval_threshold_usd} onChange={set("approval_threshold_usd")} inputMode="decimal" required /></label>
        </div>
        {error && <p className="error" role="alert">Not created: {error}</p>}
        <div className="actions">
          <button type="button" className="ghost" onClick={onClose}>Cancel</button>
          <button type="submit" disabled={busy}>{busy ? "Creating onchain…" : "Create agent"}</button>
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const sceneRef = useRef<Scene | null>(null);
  const { world, connection, config } = useWorld((ev) => sceneRef.current?.play(ev));
  const [view, setView] = useState<"base" | "dashboard">("base");
  const [placing, setPlacing] = useState(false);
  const pending = pendingApprovals(world);
  const groups = useMemo(() => timeline(world, 40), [world]);

  return (
    <div className="app">
      <header className="top">
        <div className="brand">
          <h1>Agent Base</h1>
          <p>A guard for AI agents that hold money. The AI proposes, the policy engine decides, the chain enforces.</p>
        </div>
        <nav aria-label="Views">
          <button className={view === "base" ? "tab on" : "tab"} onClick={() => setView("base")}>Base</button>
          <button className={view === "dashboard" ? "tab on" : "tab"} onClick={() => setView("dashboard")}>Dashboard</button>
        </nav>
        <span className={`conn ${connection}`}>{connection === "live" ? "Live" : connection === "connecting" ? "Connecting…" : "Backend not reachable"}</span>
        <button onClick={() => setPlacing(true)} disabled={!config?.can_create_agents} title={config?.can_create_agents ? "" : "Needs the chain and the owner key"}>
          Place an agent
        </button>
      </header>

      {connection === "offline" && world.lastId === 0 && (
        <p className="banner">
          The backend is not answering at {API}. Start it with <code>python -m scripts.demo</code> or <code>python -m app.main</code>.
        </p>
      )}

      <main className={view}>
        <section className="stage" hidden={view !== "base"}>
          <div className="field">
            <Scoreline world={world} />
            <div className="strip">
              <Rail world={world} />
              <Legend />
            </div>
            <BaseMap world={world} config={config} sceneRef={sceneRef} />
            <Now world={world} />
          </div>
          <aside className="watch" aria-label="Live activity">
            {pending.map((approval) => (
              <ApprovalCard
                key={approval.approval_id}
                approval={approval}
                world={world}
                voiceReady={config?.labels.voice === "ElevenLabs Agents"}
                timeoutSeconds={config?.approval_timeout_seconds ?? 60}
              />
            ))}
            <h2>Timeline</h2>
            {groups.length === 0 && <p className="quiet">Nothing yet. Every line here will be one entry of the audit log.</p>}
            {groups.map((group, i) => (
              <section key={group.chapter?.eventId ?? `pre-${i}`} className="chapter">
                {group.chapter && (
                  <h3>
                    <span>{group.chapter.number}</span>
                    {group.chapter.title}
                  </h3>
                )}
                <ol className="feed">
                  {group.items.map(({ ev, line }) => (
                    <li key={ev.id} className={line.tone}>
                      <div className="row">
                        <Tag tone={line.tone}>{line.tag}</Tag>
                        <time dateTime={ev.at} title={`audit event #${ev.id}`}>{new Date(ev.at).toLocaleTimeString()}</time>
                      </div>
                      <p className="what">{line.title}</p>
                      {line.detail && <p className="why">{line.detail}</p>}
                      <p className="meta">
                        audit event #{ev.id} <Tx config={config} signature={line.signature} />
                      </p>
                    </li>
                  ))}
                </ol>
              </section>
            ))}
          </aside>
        </section>
        {view === "dashboard" && <Dashboard world={world} config={config} />}
      </main>

      <RunStatus config={config} world={world} />
      {placing && <PlaceAgent onClose={() => setPlacing(false)} />}
    </div>
  );
}
