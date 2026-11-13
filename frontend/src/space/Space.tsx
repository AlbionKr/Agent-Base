import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";
import { usd, type Config, type Ev, type World } from "../model";
import { sceneNodes, type SceneNode } from "../nodes";
import { Num } from "../ui";
import { COLOR, SpaceEngine } from "./engine";

export type SpaceHandle = {
  /** Play the flow for one live event. Replayed history is drawn, not animated. */
  play: (ev: Ev) => void;
  /** Where a node is on screen right now, for the panel that grows out of it. */
  rectOf: (id: string) => DOMRect | null;
  focusNode: (id: string) => void;
};

const SEGMENTS = 24;

/** A ring of segments: how much of today's allowance is used. */
function BudgetRing({ spent, cap }: { spent: number; cap: number }) {
  const used = cap > 0 ? Math.min(SEGMENTS, spent > 0 ? Math.max(1, Math.round((spent / cap) * SEGMENTS)) : 0) : 0;
  const filled = `${"0.62 0.38 ".repeat(used)}0 ${SEGMENTS - used}`;
  return (
    <svg className="budget" viewBox="0 0 80 80" aria-hidden="true">
      <circle cx="40" cy="40" r="36" pathLength={SEGMENTS} strokeDasharray="0.62 0.38" className="track" />
      {used > 0 && <circle cx="40" cy="40" r="36" pathLength={SEGMENTS} strokeDasharray={filled} className="used" />}
    </svg>
  );
}

function NodeIcon({ node }: { node: SceneNode }) {
  const common = { width: 26, height: 26, viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 1.7, strokeLinecap: "round", strokeLinejoin: "round", "aria-hidden": true } as const;
  if (node.status === "frozen") {
    return <svg {...common}><rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" stroke="none" /><path d="M8 11V8a4 4 0 018 0v3" /></svg>;
  }
  switch (node.id.startsWith("station:") ? node.id : node.kind) {
    case "agent":
      return <svg {...common}><rect x="5" y="8" width="14" height="11" rx="3" /><path d="M12 8V4.5" /><circle cx="12" cy="3.6" r="1" fill="currentColor" /><circle cx="9.3" cy="13.2" r="1.1" fill="currentColor" stroke="none" /><circle cx="14.7" cy="13.2" r="1.1" fill="currentColor" stroke="none" /></svg>;
    case "station:payees":
      return <svg {...common}><circle cx="9" cy="12" r="5" /><circle cx="15" cy="12" r="5" /></svg>;
    case "station:news":
      return <svg {...common}><rect x="4" y="5" width="16" height="14" rx="2" /><path d="M8 9.5h8M8 12.5h8M8 15.5h5" /></svg>;
    case "station:markets":
      return <svg {...common}><rect x="4" y="4" width="16" height="16" rx="2" /><path d="M7.5 15l3.2-4 2.8 2.4 3.4-5" /></svg>;
    case "attacker":
      return <svg {...common}><path d="M12 2.8l9.2 9.2-9.2 9.2L2.8 12z" fill="currentColor" stroke="none" /><path d="M12 8v5" stroke="#05060a" strokeWidth="2" /><circle cx="12" cy="15.8" r="1.1" fill="#05060a" stroke="none" /></svg>;
    default:
      return <svg {...common}><rect x="5" y="8" width="14" height="11" rx="3" strokeDasharray="2.4 2.4" /><circle cx="9.3" cy="13.2" r="1.1" fill="currentColor" stroke="none" /><circle cx="14.7" cy="13.2" r="1.1" fill="currentColor" stroke="none" /></svg>;
  }
}

const STATUS_WORD: Record<string, string> = { active: "Active", waiting: "Waiting for a person", frozen: "Frozen", lost: "Lost funds", hostile: "Hostile", idle: "Standing by" };

type Props = { world: World; config: Config | null; selected: string | null; onSelect: (id: string) => void };

export const Space = forwardRef<SpaceHandle, Props>(function Space({ world, config, selected, onSelect }, ref) {
  const host = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const engine = useRef<SpaceEngine | null>(null);
  const buttons = useRef(new Map<string, HTMLButtonElement>());
  const wraps = useRef(new Map<string, HTMLDivElement>());
  const lastRead = useRef(new Map<string, number>());
  const [hovered, setHovered] = useState<string | null>(null);
  const nodes = useMemo(() => sceneNodes(world, config), [world, config]);

  useEffect(() => {
    const still = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const e = new SpaceEngine(canvas.current!, host.current!, still);
    engine.current = e;
    wraps.current.forEach((el, id) => e.register(id, el));
    return () => {
      engine.current = null;
      e.destroy();
    };
  }, []);

  useEffect(() => {
    engine.current?.setNodes(nodes.map((n) => ({ id: n.id, kind: n.kind, frozen: n.status === "frozen" })));
  }, [nodes]);

  useEffect(() => engine.current?.setSelected(selected), [selected]);

  // the vault's glow answers a change in its balance
  const vaultBalance = world.balances?.vault_usdc ?? null;
  const firstBalance = useRef(true);
  useEffect(() => {
    if (vaultBalance === null) return;
    if (firstBalance.current) firstBalance.current = false;
    else engine.current?.pulseCore();
  }, [vaultBalance]);

  useImperativeHandle(ref, () => ({
    rectOf: (id) => buttons.current.get(id)?.getBoundingClientRect() ?? null,
    focusNode: (id) => buttons.current.get(id)?.focus(),
    play: (ev) => {
      const e = engine.current;
      if (!e) return;
      const agent = ev.agent_id ? `agent:${ev.agent_id}` : "";
      const p = ev.payload;
      switch (ev.kind) {
        case "decision":
          if (p.verdict === "DENY") e.flow("attack", (p.taint ?? []).length > 0 ? "station:news" : "attacker", agent, { blocked: true });
          else if (p.verdict === "REQUIRE_APPROVAL") e.ripple(agent, COLOR.review, { reach: 60 });
          return;
        case "execution": {
          if (!p.ok) return;
          const detail = p.detail ?? {};
          if (detail.top_up_signature) e.flow("money", "vault", agent, { count: 4 });
          if (detail.via === "vault.pay") e.flow("money", "vault", "station:payees");
          else e.flow("money", agent, detail.via === "x402.exact" ? "station:news" : "station:markets", { delay: detail.top_up_signature ? 0.7 : 0 });
          return;
        }
        case "context": {
          // an agent reads several posts at once: one beam says it
          const now = performance.now();
          if (now - (lastRead.current.get(agent) ?? 0) < 1600) return;
          lastRead.current.set(agent, now);
          e.flow("read", String(p.source ?? "").startsWith("x402-news") ? "station:news" : "station:markets", agent);
          return;
        }
        case "approval_resolved":
          e.ripple(agent, p.status === "approved" ? COLOR.safe : COLOR.review, { reach: 60 });
          return;
        case "anomaly":
          e.flow("loss", agent, "attacker", { count: 3 });
          return;
        case "freeze":
          e.ripple(agent, COLOR.frozen, { reach: 80, width: 3 });
          e.ripple(agent, COLOR.frozen, { reach: 50, delay: 0.18 });
          return;
        case "chain_refusal":
          // a stolen key goes straight at the vault program, and stops at it
          e.flow("attack", "attacker", "vault", { blocked: true });
          return;
        case "naive_action":
          if (!p.executed) return;
          e.flow("loss", "naive", "attacker", { count: 8 });
          e.ripple("naive", COLOR.danger, { reach: 70, width: 3 });
          return;
      }
    },
  }));

  const hover = (id: string | null) => {
    setHovered(id);
    engine.current?.setHover(id);
  };

  return (
    <div className={selected ? "space dimmed" : "space"} ref={host}>
      <canvas ref={canvas} aria-hidden="true" />
      {nodes.map((node) => {
        const vault = node.kind === "vault";
        // the two services at the top carry their names above them, clear of the orbit below
        const labelAbove = node.id === "station:payees" || node.id === "station:news";
        return (
          <div
            key={node.id}
            className="nodewrap"
            ref={(el) => {
              if (el) wraps.current.set(node.id, el);
              else wraps.current.delete(node.id);
              engine.current?.register(node.id, el);
            }}
          >
            <div className={`nodebody ${node.kind} ${node.status}${selected === node.id ? " open" : ""}`}>
              {node.tag && <span className="nodetag">{node.tag}</span>}
              <button
                ref={(el) => {
                  if (el) buttons.current.set(node.id, el);
                  else buttons.current.delete(node.id);
                }}
                className="node"
                onClick={() => onSelect(node.id)}
                onMouseEnter={() => hover(node.id)}
                onMouseLeave={() => hover(null)}
                onFocus={() => hover(node.id)}
                onBlur={() => hover(null)}
                aria-label={`${node.label}. ${node.tag ?? STATUS_WORD[node.status]}. ${node.sub}. Open details`}
              >
                {vault ? (
                  <>
                    <span className="balance" data-stat="vault">
                      {world.balances?.vault_usdc != null ? <Num value={Number(world.balances.vault_usdc)} format={usd} /> : "no chain"}
                    </span>
                    <span className="hud">in the vault</span>
                  </>
                ) : (
                  <>
                    {node.budget && <BudgetRing spent={node.budget.spent} cap={node.budget.cap} />}
                    <NodeIcon node={node} />
                  </>
                )}
              </button>
              {!vault && (
                <div className={labelAbove ? "nodelabel above" : "nodelabel"}>
                  <b>{node.label}</b>
                  {node.budget ? (
                    <span className="spend" title={node.sub}>{usd(node.budget.spent)} / {usd(node.budget.cap)} today</span>
                  ) : (
                    <span>{node.sub}</span>
                  )}
                </div>
              )}
              {hovered === node.id && selected === null && (
                <div className="tooltip" role="tooltip">
                  <b>{node.label}</b>
                  <span>{node.tag ?? STATUS_WORD[node.status]}</span>
                  <span>{node.sub}</span>
                  <i>Click for details</i>
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
});
