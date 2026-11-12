import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApprovalCard } from "./Approval";
import { Dashboard, describe } from "./Dashboard";
import { API, emptyWorld, getJson, pendingApprovals, postJson, reduceAll, type Config, type Ev, type World } from "./model";
import { Scene } from "./scene";

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

function Badges({ config, world }: { config: Config | null; world: World }) {
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
  return (
    <ul className="badges" aria-label="What is real and what is not in this run">
      {items.map(([name, value, caveat]) => (
        <li key={name + value} className={caveat ? "badge caveat" : "badge"}>
          <span>{name}</span> {value}
        </li>
      ))}
    </ul>
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
  const recent = useMemo(() => world.events.filter((e) => describe(e, world) !== null).slice(-40).reverse(), [world]);

  return (
    <div className="app">
      <header>
        <h1>Agent Base</h1>
        <nav aria-label="Views">
          <button className={view === "base" ? "tab on" : "tab"} onClick={() => setView("base")}>Base</button>
          <button className={view === "dashboard" ? "tab on" : "tab"} onClick={() => setView("dashboard")}>Dashboard</button>
        </nav>
        <Badges config={config} world={world} />
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
          <BaseMap world={world} config={config} sceneRef={sceneRef} />
          <aside className="watch" aria-label="Live activity">
            {pending.map((approval) => (
              <ApprovalCard key={approval.approval_id} approval={approval} world={world} voiceReady={config?.labels.voice === "ElevenLabs Agents"} />
            ))}
            <h2>What just happened</h2>
            {recent.length === 0 && <p className="quiet">Nothing yet. Every line here will be one entry of the audit log.</p>}
            <ol className="feed">
              {recent.map((ev) => {
                const line = describe(ev, world)!;
                return (
                  <li key={ev.id} className={line.tone}>
                    <span className="what">{line.title}</span>
                    {line.detail && <span className="why">{line.detail}</span>}
                    <span className="meta">#{ev.id} at {new Date(ev.at).toLocaleTimeString()}</span>
                  </li>
                );
              })}
            </ol>
          </aside>
        </section>
        {view === "dashboard" && <Dashboard world={world} config={config} />}
      </main>

      {placing && <PlaceAgent onClose={() => setPlacing(false)} />}
    </div>
  );
}
