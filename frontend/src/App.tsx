import { AnimatePresence, MotionConfig, motion } from "framer-motion";
import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ApprovalCard } from "./Approval";
import { Dashboard } from "./Dashboard";
import { describe, timeline, verdict, type Entry } from "./describe";
import { Drawer, type Box } from "./Drawer";
import { API, emptyWorld, getJson, pendingApprovals, postJson, reduceAll, unitsToUsd, usd, type Config, type Ev, type World } from "./model";
import { Space, type SpaceHandle } from "./space/Space";
import { Glyph, Num, Tx } from "./ui";

type Connection = "connecting" | "live" | "offline";

/** For reviewing one demo step at a time: 0 means follow the log live. */
const REPLAY_STEP = Number(new URLSearchParams(window.location.search).get("step")) || 0;

/** Events older than this when they arrive are history: drawn, not animated. */
const LIVE_WINDOW_MS = 8000;

/** How long a notice stays once a newer one has arrived. The newest always stays. */
const TOAST_MS = 9000;

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
    let stopped = false;
    source.onmessage = (message) => {
      if (stopped) return;
      const ev = JSON.parse(message.data) as Ev;
      // ?step=N replays the audit log up to the end of demo step N and stops there.
      if (REPLAY_STEP && ev.kind === "demo_chapter" && ev.payload.number > REPLAY_STEP) {
        stopped = true;
        source.close();
        return;
      }
      buffer.push(ev);
      if (!frame) frame = requestAnimationFrame(flush);
    };
    return () => {
      source.close();
      if (frame) cancelAnimationFrame(frame);
    };
  }, []);

  return { world, connection, config };
}

/** Where the demo script is: a row of steps joined by a line that fills as it goes. */
function Stepper({ world }: { world: World }) {
  const current = world.chapters[world.chapters.length - 1];
  if (!current) return <span />;
  const progress = current.total > 1 ? (current.number - 1) / (current.total - 1) : 1;
  return (
    <ol className="stepper" style={{ "--progress": progress } as React.CSSProperties} aria-label={`Demo step ${current.number} of ${current.total}`}>
      {Array.from({ length: current.total }, (_, i) => {
        const seen = world.chapters.find((c) => c.number === i + 1);
        return (
          <li key={i} className={i + 1 === current.number ? "here" : seen ? "done" : ""} title={seen?.title} aria-current={i + 1 === current.number ? "step" : undefined}>
            {i + 1}
          </li>
        );
      })}
    </ol>
  );
}

function StepTitle({ world }: { world: World }) {
  const current = world.chapters[world.chapters.length - 1];
  if (!current) return <span />;
  return (
    <p className="steptitle" data-stat="step" title={current.title}>
      <span className="hud">Demo step {current.number} of {current.total}</span>
      <b>{current.title}</b>
    </p>
  );
}

/** The run in three figures and four counts. */
function Stats({ world, canPlace, onPlace }: { world: World; canPlace: boolean; onPlace: () => void }) {
  const agents = Object.values(world.agents);
  const bounds = agents.map((a) => world.balances?.agents[a.id]?.max_loss_bound_usdc).filter((b): b is string => b != null);
  const bound = bounds.reduce((sum, b) => sum + Number(b), 0);
  const hasNaive = world.balances?.naive != null || world.naiveLost > 0;
  const says = verdict(world);
  return (
    <aside className="rail left panel" aria-label="What attackers took, and what stopped them">
      <p className="says" data-stat="says">
        {says.lead} <em>{says.turn}</em>
      </p>

      <div className="stat hero safe">
        <span className="hud">With the guard</span>
        <strong data-stat="guard"><Num value={unitsToUsd(world.protectedLostUnits)} format={usd} /></strong>
        <span className="note">taken from guarded agents</span>
      </div>
      <div className={world.naiveLost > 0 ? "stat danger" : "stat"}>
        <span className="hud">Without it</span>
        <strong data-stat="without">{hasNaive ? <Num value={world.naiveLost} format={usd} /> : "–"}</strong>
        <span className="note">taken from the unprotected agent</span>
      </div>
      <div className="stat" title="What is left of the onchain daily allowance plus the hot wallet balance">
        <span className="hud">Still at risk today</span>
        <strong data-stat="risk">{bounds.length > 0 ? <Num value={bound} format={usd} /> : "–"}</strong>
        <span className="note">the most the chain would still let out</span>
      </div>

      <ul className="counts" aria-label="Outcomes so far">
        <li className="ok"><Glyph tone="ok" /><span className="hud">Allowed</span><strong data-stat="allowed"><Num value={world.counts.allow} /></strong></li>
        <li className="bad"><Glyph tone="bad" /><span className="hud">Blocked</span><strong data-stat="blocked"><Num value={world.counts.deny + world.counts.refusedOnchain} /></strong></li>
        <li className="wait"><Glyph tone="wait" /><span className="hud">Asked a person</span><strong data-stat="asked"><Num value={world.counts.approval} /></strong></li>
        <li className="lock"><Glyph tone="lock" /><span className="hud">Frozen</span><strong data-stat="frozen"><Num value={world.counts.freezes} /></strong></li>
      </ul>

      <p className="creed">The AI proposes.<br />The policy engine decides.<br />The chain enforces.</p>
      <button className="cta" onClick={onPlace} disabled={!canPlace} title={canPlace ? "" : "Needs the chain and the owner key"}>
        Place an agent
      </button>
    </aside>
  );
}

function Legend() {
  return (
    <ul className="legend" aria-label="What the moving shapes mean">
      <li><svg width="16" height="10" viewBox="0 0 16 10" aria-hidden="true"><circle cx="11" cy="5" r="3.2" fill="var(--accent)" /><circle cx="4.5" cy="5" r="2" fill="var(--accent)" opacity="0.5" /></svg>money moving</li>
      <li><svg width="16" height="10" viewBox="0 0 16 10" aria-hidden="true"><path d="M11 1l4 4-4 4-4-4z" fill="var(--danger)" /><path d="M1 5h6" stroke="var(--danger)" strokeWidth="1.6" opacity="0.6" /></svg>attack</li>
      <li><svg width="16" height="10" viewBox="0 0 16 10" aria-hidden="true"><path d="M1 5h14" stroke="var(--read)" strokeWidth="1.6" strokeDasharray="3 2.5" /></svg>something an agent read</li>
      <li><svg width="16" height="10" viewBox="0 0 16 10" aria-hidden="true"><circle cx="8" cy="5" r="4" fill="none" stroke="var(--frozen)" strokeWidth="1.6" /></svg>attack stopped</li>
    </ul>
  );
}

function LogItem({ entry, config }: { entry: Entry; config: Config | null }) {
  const { ev, line } = entry;
  return (
    <li className={line.tone}>
      <div className="logrow">
        <span className="dot" aria-hidden="true" />
        <span className="logtag">{line.tag}</span>
        <time dateTime={ev.at}>{new Date(ev.at).toLocaleTimeString()}</time>
      </div>
      <p className="what">{line.title}</p>
      {line.detail && <p className="why">{line.detail}</p>}
      <p className="meta">
        audit event #{ev.id} <Tx config={config} signature={line.signature} />
      </p>
    </li>
  );
}

/** The latest events that are more than routine, newest first. */
function useNotices(world: World): Entry[] {
  const notable = useMemo(() => {
    const out: Entry[] = [];
    for (let i = world.events.length - 1; i >= 0 && out.length < 2; i--) {
      const line = describe(world.events[i], world);
      if (line && line.tone !== "plain") out.push({ ev: world.events[i], line });
    }
    return out;
  }, [world]);
  const [, tick] = useState(0);
  const now = Date.now();
  const shown = notable.filter((entry, i) => i === 0 || now - Date.parse(entry.ev.at) < TOAST_MS);
  useEffect(() => {
    if (shown.length <= 1) return;
    const timer = setTimeout(() => tick((n) => n + 1), 1000);
    return () => clearTimeout(timer);
  });
  return shown;
}

function Notices({ world }: { world: World }) {
  const shown = useNotices(world);
  return (
    <div className="toasts" aria-live="polite">
      {shown.length === 0 && (
        <div className="toast plain">
          <p className="say">Nothing to report yet.</p>
          <p className="why">The latest allowed, blocked or frozen action shows here.</p>
        </div>
      )}
      <AnimatePresence initial={false} mode="popLayout">
        {shown.map(({ ev, line }, i) => (
          <motion.div
            key={ev.id}
            layout
            className={i === 0 ? `toast ${line.tone}` : `toast older ${line.tone}`}
            data-stat={i === 0 ? "toast" : undefined}
            initial={{ opacity: 0, y: 36, scale: 0.97 }}
            animate={{ opacity: i === 0 ? 1 : 0.6, y: 0, scale: i === 0 ? 1 : 0.96 }}
            exit={{ opacity: 0, y: -12, scale: 0.94 }}
            transition={{ type: "spring", stiffness: 420, damping: 34, opacity: { duration: 0.2, delay: i === 0 ? 0.12 : 0 } }}
          >
            <div className="logrow">
              <span className="dot" aria-hidden="true" />
              <span className="logtag">{line.tag}</span>
              <span className="meta">audit event #{ev.id}</span>
            </div>
            <p className="say">{line.title}</p>
            {line.detail && i === 0 && <p className="why">{line.detail}</p>}
          </motion.div>
        ))}
      </AnimatePresence>
    </div>
  );
}

function RunStatus({ config, world, connection }: { config: Config | null; world: World; connection: Connection }) {
  const l = config?.labels ?? {};
  const proposers = new Set(Object.values(world.agents).map((a) => a.proposer).filter(Boolean) as string[]);
  const items: [string, string, boolean][] = config
    ? [
        ["Chain", l.cluster ?? "?", l.cluster !== "DEVNET"],
        ["News", l.news_feed ?? "?", l.news_feed !== "LIVE FEED"],
        ["Gemini orders", l.gemini_orders ?? "?", true],
        ["Stream", l.stream ?? "?", l.stream !== "QuickNode Streams"],
        ...[...proposers].map((p): [string, string, boolean] => ["Proposals", p, !p.startsWith("LLM")]),
      ]
    : [];
  const caveats = items.filter(([, , caveat]) => caveat).length;
  return (
    <footer className="strip">
      <span className={`conn ${connection}`}>{connection === "live" ? "Live" : connection === "connecting" ? "Connecting…" : "Backend not reachable"}</span>
      {REPLAY_STEP > 0 && <span className="replay">Replay, stopped at the end of demo step {REPLAY_STEP}</span>}
      {items.length > 0 && <span className="lead">{caveats > 0 ? "Not fully live in this run" : "This run"}</span>}
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
          <button type="submit" className="cta" disabled={busy}>{busy ? "Creating onchain…" : "Create agent"}</button>
        </div>
      </form>
    </div>
  );
}

export default function App() {
  const space = useRef<SpaceHandle>(null);
  const { world, connection, config } = useWorld((ev) => space.current?.play(ev));
  const [view, setView] = useState<"base" | "dashboard">("base");
  const [placing, setPlacing] = useState(false);
  const [selected, setSelected] = useState<{ id: string; from: Box | null } | null>(null);
  const pending = pendingApprovals(world);
  const groups = useMemo(() => timeline(world, 40), [world]);

  function open(id: string) {
    const rect = space.current?.rectOf(id);
    setSelected({ id, from: rect ? { left: rect.left, top: rect.top, width: rect.width, height: rect.height } : null });
  }
  function close() {
    const id = selected?.id;
    setSelected(null);
    // hand the focus back to the node the panel came from
    if (id) requestAnimationFrame(() => space.current?.focusNode(id));
  }

  return (
    <MotionConfig reducedMotion="user">
      <div className="app">
        <header className="top">
          <div className="brand">
            <svg className="mark" width="26" height="26" viewBox="0 0 26 26" aria-hidden="true">
              <circle cx="13" cy="13" r="4.2" fill="var(--accent)" />
              <ellipse cx="13" cy="13" rx="11.5" ry="6" fill="none" stroke="var(--accent)" strokeWidth="1.2" opacity="0.7" transform="rotate(-24 13 13)" />
            </svg>
            <h1>Agent Base</h1>
            <nav aria-label="Views">
              <button className={view === "base" ? "tab on" : "tab"} onClick={() => setView("base")}>Base</button>
              <button className={view === "dashboard" ? "tab on" : "tab"} onClick={() => setView("dashboard")}>Dashboard</button>
            </nav>
          </div>
          <Stepper world={world} />
          <StepTitle world={world} />
        </header>

        {connection === "offline" && world.lastId === 0 && (
          <p className="banner">
            The backend is not answering at {API}. Start it with <code>python -m scripts.demo</code> or <code>python -m app.main</code>.
          </p>
        )}

        <main className={view}>
          <section className="stage" hidden={view !== "base"}>
            <Stats world={world} canPlace={!!config?.can_create_agents} onPlace={() => setPlacing(true)} />

            <div className="center">
              <Space ref={space} world={world} config={config} selected={selected?.id ?? null} onSelect={open} />
              <Notices world={world} />
            </div>

            <aside className="rail right" aria-label="Live activity">
              {pending.map((approval) => (
                <ApprovalCard
                  key={approval.approval_id}
                  approval={approval}
                  world={world}
                  voiceReady={config?.labels.voice === "ElevenLabs Agents"}
                  timeoutSeconds={config?.approval_timeout_seconds ?? 60}
                />
              ))}
              <div className="panel log-panel">
                <h2>Event log</h2>
                <div className="logscroll">
                  {groups.length === 0 && <p className="quiet">Nothing yet. Every line here will be one entry of the audit log.</p>}
                  {groups.map((group, i) => (
                    <section key={group.chapter?.eventId ?? `pre-${i}`} className="chapter">
                      {group.chapter && (
                        <h3>
                          <span>{group.chapter.number}</span>
                          {group.chapter.title}
                        </h3>
                      )}
                      <ol className="log">
                        {group.items.map((entry) => (
                          <LogItem key={entry.ev.id} entry={entry} config={config} />
                        ))}
                      </ol>
                    </section>
                  ))}
                </div>
                <Legend />
              </div>
            </aside>
          </section>
          {view === "dashboard" && <Dashboard world={world} config={config} />}
        </main>

        <RunStatus config={config} world={world} connection={connection} />

        <AnimatePresence>
          {selected && <Drawer key={selected.id} nodeId={selected.id} from={selected.from} world={world} config={config} onClose={close} />}
        </AnimatePresence>
        {placing && <PlaceAgent onClose={() => setPlacing(false)} />}
      </div>
    </MotionConfig>
  );
}
