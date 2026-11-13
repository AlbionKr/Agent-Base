// The detail panel that grows out of a node on the map. Everything in it is
// read from audit events and the chain state the backend reports.

import { motion } from "framer-motion";
import { useEffect, useRef, useState } from "react";
import type { Entry } from "./describe";
import { agentActivity, agentPayments, agentReads, naiveStory, threatStory } from "./detail";
import { explorerAddress, short, usd, type Config, type NewsView, type World } from "./model";
import { Tag, Tx } from "./ui";

export const DRAWER_WIDTH = 420;
const MARGIN = 16;
const TOP = 76;

export type Box = { left: number; top: number; width: number; height: number };

/** Where the panel rests, so it can start from (and return to) the node it belongs to. */
export function drawerBox(): Box {
  const width = Math.min(DRAWER_WIDTH, window.innerWidth - MARGIN * 2);
  return { left: window.innerWidth - MARGIN - width, top: TOP, width, height: window.innerHeight - TOP - MARGIN };
}

function Copy({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      className="copy"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(value);
          setCopied(true);
          setTimeout(() => setCopied(false), 1400);
        } catch {
          setCopied(false);
        }
      }}
      aria-label={`Copy address ${value}`}
    >
      {copied ? "Copied" : "Copy"}
    </button>
  );
}

function Address({ value, config }: { value: string; config: Config | null }) {
  return (
    <span className="address">
      <a href={explorerAddress(config, value)} target="_blank" rel="noreferrer" title={value}>{short(value)}</a>
      <Copy value={value} />
    </span>
  );
}

function Events({ items, config, empty }: { items: Entry[]; config: Config | null; empty: string }) {
  if (items.length === 0) return <p className="quiet">{empty}</p>;
  return (
    <ol className="log">
      {items.map(({ ev, line }) => (
        <li key={ev.id} className={line.tone}>
          <div className="logrow">
            <span className="dot" aria-hidden="true" />
            <span className="logtag">{line.tag}</span>
            <time dateTime={ev.at}>{new Date(ev.at).toLocaleTimeString()}</time>
          </div>
          <p className="what">{line.title}</p>
          {line.detail && <p className="why">{line.detail}</p>}
          <p className="meta">audit event #{ev.id} <Tx config={config} signature={line.signature} /></p>
        </li>
      ))}
    </ol>
  );
}

function Posts({ items, empty }: { items: NewsView[]; empty: string }) {
  if (items.length === 0) return <p className="quiet">{empty}</p>;
  return (
    <ul className="posts">
      {items.map((n) => (
        <li key={n.eventId} className={n.blockedBy.length ? "tainted" : ""}>
          <span className="hud">{n.source} {n.itemId}</span>
          <span>{n.text.replace(/[\u{E0000}-\u{E007F}]/gu, "␣")}</span>
          {n.blockedBy.length > 0 && <span className="flag">Attack post. Cited when blocking decision{n.blockedBy.length > 1 ? "s" : ""} #{n.blockedBy.join(", #")}</span>}
        </li>
      ))}
    </ul>
  );
}

function Facts({ rows }: { rows: [string, React.ReactNode][] }) {
  return (
    <dl className="facts">
      {rows.map(([k, v]) => (
        <div key={k}><dt>{k}</dt><dd>{v}</dd></div>
      ))}
    </dl>
  );
}

function AgentPanel({ id, world, config }: { id: string; world: World; config: Config | null }) {
  const agent = world.agents[id];
  const bal = world.balances?.agents[id];
  if (!agent) return <p className="quiet">This agent is no longer registered.</p>;
  const frozen = agent.frozen || bal?.frozen_onchain === true;
  const waiting = Object.values(world.approvals).some((a) => a.agent_id === id && a.status === "pending");
  const cap = Number(bal?.daily_cap_usdc ?? agent.maxPerDay);
  const spent = Number(bal?.spent_today_usdc ?? 0);
  const share = cap > 0 ? Math.min(1, spent / cap) : 0;
  return (
    <>
      <header>
        <p className="hud">Protected agent</p>
        <h2 id="drawer-title">{agent.name}</h2>
        <div className="badges">
          {frozen ? <Tag tone="lock">{bal?.frozen_onchain ? "Frozen onchain" : "Frozen in the gate"}</Tag> : waiting ? <Tag tone="wait">Waiting for your answer</Tag> : <Tag tone="ok">Active</Tag>}
          {bal && <Address value={bal.hot_wallet} config={config} />}
        </div>
        <p className="quiet">{agent.proposer ? `Proposals: ${agent.proposer}` : "Has not run a cycle yet."}</p>
      </header>

      <section>
        <h3>Budget</h3>
        <div className="budgetrow">
          <svg viewBox="0 0 60 60" className="gauge" aria-hidden="true">
            <circle cx="30" cy="30" r="25" className="track" />
            <circle cx="30" cy="30" r="25" className="used" pathLength={100} strokeDasharray={`${share * 100} 100`} />
          </svg>
          <div>
            <p className="big">{bal?.spent_today_usdc != null ? `${usd(spent)} of ${usd(cap)} spent today` : `Up to ${usd(agent.maxPerDay)} a day`}</p>
            <div className="bar" aria-hidden="true"><i style={{ width: `${share * 100}%` }} /></div>
            <p className="quiet">Counted by the vault program onchain. Hot wallet holds {usd(bal?.hot_wallet_usdc)}; at most {usd(bal?.max_loss_bound_usdc)} can still leave today.</p>
          </div>
        </div>
      </section>

      <section>
        <h3>Policy</h3>
        <Facts
          rows={[
            ["Most per action", usd(agent.maxPerAction)],
            ["Most per day", usd(agent.maxPerDay)],
            ["Asks a person above", usd(agent.approvalThreshold)],
            ["Also asks a person when", "an amount or market traces back to something it read"],
            ["Markets it may trade", agent.markets.length ? agent.markets.join(", ") : "none"],
            ["Payees it may pay", agent.payees.length ? agent.payees.map((p) => short(p)).join(", ") : "none"],
          ]}
        />
      </section>

      <section>
        <h3>Activity</h3>
        <Events items={agentActivity(world, id, 40)} config={config} empty="Nothing yet." />
      </section>

      <section>
        <h3>Payments</h3>
        <Events items={agentPayments(world, id)} config={config} empty="No payment has been carried out." />
      </section>

      <section>
        <h3>Recent reads</h3>
        <Posts items={agentReads(world, id, 8)} empty="It has not read anything yet." />
      </section>
    </>
  );
}

function VaultPanel({ world, config }: { world: World; config: Config | null }) {
  const agents = Object.values(world.agents);
  return (
    <>
      <header>
        <p className="hud">Vault program</p>
        <h2 id="drawer-title">{world.balances?.vault_usdc != null ? `${usd(world.balances.vault_usdc)} in the vault` : "Vault"}</h2>
        <p className="quiet">The chain enforces every agent's caps here. A stolen agent key cannot take more than what is left of that agent's allowance.</p>
      </header>
      <section>
        <h3>Onchain</h3>
        <Facts
          rows={[
            ["Cluster", config?.labels.cluster ?? "–"],
            ["Program", config ? <Address value={config.program_id} config={config} /> : "–"],
            ["Vault account", config?.vault ? <Address value={config.vault} config={config} /> : "–"],
            ["Direct calls refused", String(world.counts.refusedOnchain)],
          ]}
        />
      </section>
      <section>
        <h3>Allowances</h3>
        {agents.length === 0 ? (
          <p className="quiet">No agent has an allowance yet.</p>
        ) : (
          <Facts
            rows={agents.map((a): [string, React.ReactNode] => {
              const b = world.balances?.agents[a.id];
              return [a.name, b?.spent_today_usdc != null ? `${usd(b.spent_today_usdc)} of ${usd(b.daily_cap_usdc)} today, ${usd(b.per_tx_cap_usdc)} per call${b.frozen_onchain ? ", frozen" : ""}` : `up to ${usd(a.maxPerDay)} a day`];
            })}
          />
        )}
      </section>
    </>
  );
}

function StationPanel({ id, world, config }: { id: string; world: World; config: Config | null }) {
  const labels = config?.labels ?? {};
  const agents = Object.values(world.agents);
  if (id === "station:news") {
    const seen = new Map<string, NewsView>();
    for (const n of world.news) if (n.source.startsWith("x402-news")) seen.set(`${n.source}/${n.itemId}`, n);
    return (
      <>
        <header>
          <p className="hud">Paid service</p>
          <h2 id="drawer-title">News stand</h2>
          <p className="quiet">Agents pay {usd("0.001")} a call over x402. Everything it returns is treated as untrusted content.</p>
        </header>
        <section>
          <h3>This run</h3>
          <Facts rows={[["Feed", labels.news_feed ?? "–"], ["Facilitator", labels.x402_facilitator ?? "–"], ["Posts read", String(seen.size)]]} />
        </section>
        <section>
          <h3>Posts the agents paid for</h3>
          <Posts items={[...seen.values()].reverse().slice(0, 12)} empty="No news bought yet." />
        </section>
      </>
    );
  }
  if (id === "station:markets") {
    const read = world.news.filter((n) => !n.source.startsWith("x402-news")).length;
    return (
      <>
        <header>
          <p className="hud">Prediction markets</p>
          <h2 id="drawer-title">Gemini markets</h2>
          <p className="quiet">Agents read market data here and place orders only through the gate.</p>
        </header>
        <section>
          <h3>This run</h3>
          <Facts rows={[["Orders", labels.gemini_orders ?? "–"], ["Market data", labels.gemini_market_data ?? "–"], ["Market descriptions read", String(read)]]} />
        </section>
        <section>
          <h3>Markets each agent may trade</h3>
          {agents.length === 0 ? <p className="quiet">No agents yet.</p> : <Facts rows={agents.map((a): [string, React.ReactNode] => [a.name, a.markets.join(", ") || "none"])} />}
        </section>
      </>
    );
  }
  return (
    <>
      <header>
        <p className="hud">Allowlist</p>
        <h2 id="drawer-title">Allowed payees</h2>
        <p className="quiet">The only addresses the vault will pay for an agent. The list is part of the mandate the owner signed, and the vault program checks it again onchain.</p>
      </header>
      <section>
        <h3>By agent</h3>
        {agents.length === 0 ? (
          <p className="quiet">No agents yet.</p>
        ) : (
          agents.map((a) => (
            <div key={a.id} className="payees">
              <p className="big">{a.name}</p>
              {a.payees.length === 0 ? <p className="quiet">No payees allowed.</p> : a.payees.map((p) => <Address key={p} value={p} config={config} />)}
            </div>
          ))
        )}
      </section>
    </>
  );
}

function AttackerPanel({ world, config }: { world: World; config: Config | null }) {
  const story = threatStory(world, config?.attacker);
  const guarded = world.protectedLostUnits / 1_000_000;
  return (
    <>
      <header>
        <p className="hud">Hostile</p>
        <h2 id="drawer-title">Attacker</h2>
        <div className="badges">
          <Tag tone="bad">Holds {usd(world.balances?.attacker_usdc)}</Tag>
          {config?.attacker && <Address value={config.attacker} config={config} />}
        </div>
        <p className="quiet">A wallet we control, so what it takes in this demo is real and can be returned.</p>
      </header>
      <section>
        <h3>The story so far</h3>
        <Facts
          rows={[
            ["Attempts stopped", String(story.stopped.length)],
            ["Taken from the unprotected agent", usd(world.naiveLost)],
            ["Taken from guarded agents", usd(guarded)],
          ]}
        />
      </section>
      <section>
        <h3>What it tried, and was stopped at</h3>
        <Events items={story.stopped} config={config} empty="Nothing has been tried yet." />
      </section>
      <section>
        <h3>What it got</h3>
        <Events items={story.taken} config={config} empty="Nothing has been taken." />
      </section>
    </>
  );
}

function NaivePanel({ world, config }: { world: World; config: Config | null }) {
  const wallet = world.balances?.naive?.wallet;
  return (
    <>
      <header>
        <p className="hud">No guard</p>
        <h2 id="drawer-title">Unprotected agent</h2>
        <div className="badges">
          {world.naiveLost > 0 ? <Tag tone="bad">Lost {usd(world.naiveLost)}</Tag> : <Tag tone="plain">Nothing lost yet</Tag>}
          {wallet && <Address value={wallet} config={config} />}
        </div>
        <p className="quiet">The same kind of agent reading the same posts, with no policy engine and no vault. Whatever it decides to pay, it pays.</p>
      </header>
      <section>
        <h3>Money</h3>
        <Facts rows={[["Held at most", usd(world.naiveHeld)], ["Holds now", usd(world.balances?.naive?.usdc)], ["Lost", usd(world.naiveLost)], ["Proposals", world.naiveProposer ?? "has not run yet"]]} />
      </section>
      <section>
        <h3>What happened to it</h3>
        <Events items={naiveStory(world)} config={config} empty="It has not acted yet." />
      </section>
    </>
  );
}

type Props = { nodeId: string; from: Box | null; world: World; config: Config | null; onClose: () => void };

/** `from` is the node's box on screen: the panel starts there and goes back there. */
export function Drawer({ nodeId, from, world, config, onClose }: Props) {
  const panel = useRef<HTMLElement>(null);
  const rest = drawerBox();
  const origin = from
    ? { x: from.left - rest.left, y: from.top - rest.top, scaleX: from.width / rest.width, scaleY: from.height / rest.height, opacity: 0.2, borderRadius: 60 }
    : { x: 40, y: 0, scaleX: 1, scaleY: 1, opacity: 0, borderRadius: 12 };

  useEffect(() => {
    panel.current?.querySelector<HTMLElement>("button.close")?.focus();
  }, []);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onClose();
      return;
    }
    if (e.key !== "Tab" || !panel.current) return;
    // keep the focus inside the panel while it is open
    const focusable = [...panel.current.querySelectorAll<HTMLElement>("button, a[href], [tabindex]:not([tabindex='-1'])")].filter((el) => !el.hasAttribute("disabled"));
    if (focusable.length === 0) return;
    const first = focusable[0];
    const last = focusable[focusable.length - 1];
    if (e.shiftKey && document.activeElement === first) {
      e.preventDefault();
      last.focus();
    } else if (!e.shiftKey && document.activeElement === last) {
      e.preventDefault();
      first.focus();
    }
  }

  const agentId = nodeId.startsWith("agent:") ? nodeId.slice(6) : null;
  return (
    <>
      <motion.div className="scrim" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.2 }} onClick={onClose} />
      <motion.aside
        ref={panel}
        className="drawer"
        role="dialog"
        aria-modal="true"
        aria-labelledby="drawer-title"
        style={{ left: rest.left, top: rest.top, width: rest.width, height: rest.height, transformOrigin: "0 0" }}
        initial={origin}
        animate={{ x: 0, y: 0, scaleX: 1, scaleY: 1, opacity: 1, borderRadius: 12 }}
        exit={origin}
        transition={{ type: "spring", stiffness: 420, damping: 36, mass: 0.9 }}
        onKeyDown={onKeyDown}
      >
        <button className="close" onClick={onClose} aria-label="Close details">
          <svg width="14" height="14" viewBox="0 0 14 14" aria-hidden="true"><path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>
        </button>
        <motion.div className="drawerbody" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} transition={{ duration: 0.18, delay: 0.1 }}>
          {agentId ? (
            <AgentPanel id={agentId} world={world} config={config} />
          ) : nodeId === "vault" ? (
            <VaultPanel world={world} config={config} />
          ) : nodeId === "attacker" ? (
            <AttackerPanel world={world} config={config} />
          ) : nodeId === "naive" ? (
            <NaivePanel world={world} config={config} />
          ) : (
            <StationPanel id={nodeId} world={world} config={config} />
          )}
        </motion.div>
      </motion.aside>
    </>
  );
}
