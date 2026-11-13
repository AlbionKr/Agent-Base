// The base, drawn with PixiJS. Every moving thing is started by one audit event
// and carries that event's id.

import { Application, Container, Graphics, Text, type TextStyleOptions } from "pixi.js";
import { short, usd, type Config, type Ev, type World } from "./model";
import { FAMILIES, ruleFamily } from "./rules";

// Close to the shape of the map card, so the drawing fills it.
const W = 880;
const H = 520;

const C = {
  ink: 0x1a1612,
  inkSoft: 0x6f665b,
  ground: 0xfaf7f2,
  road: 0xe6ddd0,
  brass: 0xd9a441,
  brassDeep: 0x9a6d12,
  guard: 0x2f6f8f,
  guardPale: 0xc9dbe3,
  attack: 0xc8322b,
  gate: 0x6b4fa0,
  stone: 0x8f887d,
  paper: 0xffffff,
  ok: 0x2e7d4f,
};

const FONT = "Public Sans, system-ui, sans-serif";
const DISPLAY = "Public Sans, system-ui, sans-serif";

type Pt = { x: number; y: number };
const VAULT: Pt = { x: 440, y: 228 };
const NEWS: Pt = { x: 790, y: 56 };
const MARKET: Pt = { x: 800, y: 188 };
const ATTACKER: Pt = { x: 95, y: 405 };
const PAYEES: Pt = { x: 95, y: 56 };
const NAIVE: Pt = { x: 770, y: 420 };
// Two agents sit either side of the vault; a third goes below it.
const AGENT_SLOTS: Pt[] = [
  { x: 255, y: 248 },
  { x: 625, y: 248 },
  { x: 440, y: 392 },
];
const WALL_R = 62;

type Mote = { from: Pt; to: Pt; t: number; dur: number; color: number; shape: "coin" | "bolt" | "note"; bounceAt?: number; onDone?: () => void };
type Popup = { box: Container; ttl: number; key: string; height: number };
type Ring = { at: Pt; t: number; dur: number; color: number };
type Flash = { at: Pt; segment: number; t: number; dur: number };

function label(text: string, size: number, color: number, extra: Partial<TextStyleOptions> = {}): Text {
  return new Text({ text, style: { fontFamily: FONT, fontSize: size, fill: color, ...extra } });
}

function lerp(a: Pt, b: Pt, t: number): Pt {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
}

function segmentArc(i: number): [number, number] {
  const n = FAMILIES.length;
  return [(Math.PI * 2 * i) / n + 0.08 - Math.PI / 2, (Math.PI * 2 * (i + 1)) / n - 0.08 - Math.PI / 2];
}

export class Scene {
  private app = new Application();
  private root = new Container();
  private ground = new Graphics();
  private statics = new Container();
  private fx = new Graphics();
  private popups = new Container();
  private motes: Mote[] = [];
  private rings: Ring[] = [];
  private flashes: Flash[] = [];
  private live: Popup[] = [];
  private slots = new Map<string, Pt>();
  private world: World | null = null;
  private config: Config | null = null;
  private reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  private destroyed = false;
  private observer: ResizeObserver | null = null;

  async mount(host: HTMLElement): Promise<void> {
    await this.app.init({
      resizeTo: host,
      backgroundAlpha: 0,
      antialias: true,
      resolution: Math.min(window.devicePixelRatio || 1, 2),
      autoDensity: true,
    });
    if (this.destroyed) return;
    host.appendChild(this.app.canvas);
    this.root.addChild(this.ground, this.statics, this.fx, this.popups);
    this.app.stage.addChild(this.root);
    this.drawGround();
    this.fit();
    this.app.renderer.on("resize", () => this.fit());
    // Pixi only listens to window resizes; the map's box also changes when the
    // layout around it does.
    this.observer = new ResizeObserver(() => {
      if (!this.destroyed) this.app.resize();
    });
    this.observer.observe(host);
    this.app.ticker.add((ticker) => this.tick(ticker.deltaMS / 1000));
    if (this.world) this.redraw();
  }

  destroy(): void {
    this.destroyed = true;
    this.observer?.disconnect();
    try {
      this.app.destroy(true, { children: true });
    } catch {
      /* not initialised yet */
    }
  }

  private fit(): void {
    const { width, height } = this.app.screen;
    const scale = Math.min(width / W, height / H);
    this.root.scale.set(scale);
    this.root.position.set((width - W * scale) / 2, (height - H * scale) / 2);
  }

  setWorld(world: World, config: Config | null): void {
    this.world = world;
    this.config = config;
    Object.keys(world.agents).forEach((id) => {
      if (!this.slots.has(id)) this.slots.set(id, AGENT_SLOTS[this.slots.size % AGENT_SLOTS.length]);
    });
    if (this.app.renderer) this.redraw();
  }

  // ------------------------------------------------------------ static

  private drawGround(): void {
    const g = this.ground;
    g.clear();
    // contour lines, like a survey map
    for (let r = 120; r <= 600; r += 80) {
      g.ellipse(VAULT.x, VAULT.y, r * 1.3, r * 0.72).stroke({ width: 1.5, color: C.road, alpha: 1 });
    }
  }

  private road(g: Graphics, a: Pt, b: Pt): void {
    g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ width: 12, color: C.road, cap: "round" });
    g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ width: 1, color: C.inkSoft, alpha: 0.25 });
  }

  /** A dark or coloured name plate, centred on `at`. */
  private plate(at: Pt, text: string, color: number): void {
    const t = label(text, 13, C.paper, { fontWeight: "700" });
    t.anchor.set(0.5);
    t.position.set(at.x, at.y);
    const g = new Graphics();
    g.roundRect(at.x - t.width / 2 - 10, at.y - 12, t.width + 20, 24, 12).fill(color);
    this.statics.addChild(g, t);
  }

  private post(at: Pt, title: string, sub: string, color: number, draw: (g: Graphics) => void): void {
    const g = new Graphics();
    draw(g);
    g.position.set(at.x, at.y);
    const t = label(title, 16, C.ink, { fontWeight: "700" });
    t.anchor.set(0.5, 0);
    t.position.set(at.x, at.y + 34);
    const s = label(sub, 13, color === C.attack ? C.attack : C.inkSoft, { align: "center", lineHeight: 17 });
    s.anchor.set(0.5, 0);
    s.position.set(at.x, at.y + 55);
    this.statics.addChild(g, t, s);
  }

  private redraw(): void {
    const world = this.world!;
    this.statics.removeChildren().forEach((c) => c.destroy());
    const roads = new Graphics();
    this.statics.addChild(roads);
    const labels = this.config?.labels ?? {};

    this.slots.forEach((slot) => this.road(roads, VAULT, slot));

    // outposts
    this.post(NEWS, "News stand", `${usd("0.001")} a call over x402\n${labels.news_feed ?? ""}`, C.ink, (g) => {
      g.roundRect(-30, -24, 60, 48, 5).fill(C.paper).stroke({ width: 2.5, color: C.ink });
      g.moveTo(-19, -10).lineTo(19, -10).moveTo(-19, 0).lineTo(19, 0).moveTo(-19, 10).lineTo(7, 10).stroke({ width: 2.5, color: C.inkSoft });
    });
    this.post(MARKET, "Gemini markets", labels.gemini_orders?.startsWith("SANDBOX") ? "orders go to the SANDBOX" : "orders are not sent:\nno sandbox key", C.ink, (g) => {
      g.roundRect(-28, -24, 56, 48, 3).fill(C.paper).stroke({ width: 2.5, color: C.ink });
      g.moveTo(-19, 12).lineTo(-7, -2).lineTo(5, 7).lineTo(19, -14).stroke({ width: 3, color: C.guard });
    });
    this.post(PAYEES, "Allowed payees", "the only addresses\nthe vault will pay", C.ink, (g) => {
      g.circle(-14, 0, 15).fill(C.paper).stroke({ width: 2.5, color: C.ink });
      g.circle(14, 0, 15).fill(C.paper).stroke({ width: 2.5, color: C.ink });
    });
    const took = world.balances?.attacker_usdc != null ? `holds ${usd(world.balances.attacker_usdc)}\n${short(this.config?.attacker)}` : short(this.config?.attacker);
    this.post(ATTACKER, "Attacker", took, C.attack, (g) => {
      g.poly([0, -28, 28, 0, 0, 28, -28, 0]).fill(C.attack).stroke({ width: 2.5, color: C.ink });
      g.roundRect(-2.5, -13, 5, 15, 2).fill(C.paper);
      g.circle(0, 10, 3).fill(C.paper);
    });

    // vault core
    const vault = new Graphics();
    const sides = 8;
    const pts: number[] = [];
    for (let i = 0; i < sides; i++) {
      const a = (Math.PI * 2 * i) / sides + Math.PI / 8;
      pts.push(Math.cos(a) * 74, Math.sin(a) * 74);
    }
    vault.poly(pts).fill(C.brass).stroke({ width: 4, color: C.ink });
    vault.poly(pts.map((v) => v * 0.74)).fill(C.paper).stroke({ width: 2, color: C.brassDeep });
    vault.position.set(VAULT.x, VAULT.y);
    const amount = new Text({
      text: world.balances?.vault_usdc != null ? usd(world.balances.vault_usdc) : "no chain",
      style: { fontFamily: DISPLAY, fontSize: 27, fontWeight: "700", fill: C.ink },
    });
    amount.anchor.set(0.5);
    amount.position.set(VAULT.x, VAULT.y - 6);
    const vt = label("in the vault", 13, C.inkSoft);
    vt.anchor.set(0.5);
    vt.position.set(VAULT.x, VAULT.y + 18);
    this.statics.addChild(vault, amount, vt);

    // protected agents
    this.slots.forEach((slot, id) => {
      const agent = world.agents[id];
      if (!agent) return;
      const bal = world.balances?.agents[id];
      const frozen = agent.frozen || bal?.frozen_onchain === true;
      const waiting = Object.values(world.approvals).some((a) => a.agent_id === id && a.status === "pending");
      const g = new Graphics();
      FAMILIES.forEach((_, i) => {
        const [a0, a1] = segmentArc(i);
        g.moveTo(Math.cos(a0) * WALL_R, Math.sin(a0) * WALL_R)
          .arc(0, 0, WALL_R, a0, a1)
          .stroke({ width: 9, color: frozen ? C.stone : C.guard, cap: "butt" });
      });
      // the outer ring is the vault program's own limit
      g.circle(0, 0, WALL_R + 11).stroke({ width: 2, color: waiting ? C.gate : C.brassDeep, alpha: 0.9 });
      if (waiting) g.circle(0, 0, WALL_R + 18).stroke({ width: 4, color: C.gate });
      g.roundRect(-22, -22, 44, 50, 6).fill(frozen ? C.stone : C.paper).stroke({ width: 3, color: C.ink });
      g.rect(-9, -34, 18, 13).fill(frozen ? C.stone : C.guard).stroke({ width: 2.5, color: C.ink });
      if (frozen) {
        g.roundRect(-12, -1, 24, 19, 3).fill(C.ink);
        g.moveTo(-7, -1).arc(0, -1, 7, Math.PI, 0).stroke({ width: 3.5, color: C.ink });
      } else {
        g.circle(0, 3, 6).fill(C.guard);
      }
      g.position.set(slot.x, slot.y);
      this.statics.addChild(g);

      const name = new Text({ text: agent.name, style: { fontFamily: DISPLAY, fontSize: 18, fontWeight: "700", fill: C.ink } });
      name.anchor.set(0.5, 0);
      name.position.set(slot.x, slot.y + WALL_R + 18);
      this.statics.addChild(name);

      // how much of today's onchain allowance is used
      const cap = Number(bal?.daily_cap_usdc ?? agent.maxPerDay);
      const spent = Number(bal?.spent_today_usdc ?? 0);
      const meterY = slot.y + WALL_R + 46;
      const meter = new Graphics();
      meter.roundRect(slot.x - 62, meterY, 124, 8, 4).fill(frozen ? C.road : C.guardPale);
      if (cap > 0 && spent > 0) {
        meter.roundRect(slot.x - 62, meterY, Math.max(8, Math.min(1, spent / cap) * 124), 8, 4).fill(frozen ? C.stone : C.guard);
      }
      const used = label(bal?.spent_today_usdc != null ? `${usd(spent)} of ${usd(cap)} spent today` : `up to ${usd(agent.maxPerDay)} a day`, 13, C.inkSoft);
      used.anchor.set(0.5, 0);
      used.position.set(slot.x, meterY + 13);
      this.statics.addChild(meter, used);

      if (frozen) this.plate({ x: slot.x, y: slot.y - WALL_R - 30 }, bal?.frozen_onchain ? "Frozen onchain" : "Frozen", C.ink);
      else if (waiting) this.plate({ x: slot.x, y: slot.y - WALL_R - 36 }, "Waiting for your answer", C.gate);
    });

    // the unprotected agent: a tent, no walls
    if (world.balances?.naive || world.naiveLost > 0) {
      const drained = world.naiveLost > 0;
      const g = new Graphics();
      g.poly([-40, 26, 0, -34, 40, 26]).fill(C.paper).stroke({ width: 3, color: drained ? C.attack : C.stone });
      g.poly([-9, 26, 0, 3, 9, 26]).fill(drained ? C.attack : C.stone);
      g.position.set(NAIVE.x, NAIVE.y);
      const name = new Text({ text: "Unprotected agent", style: { fontFamily: DISPLAY, fontSize: 18, fontWeight: "700", fill: C.ink } });
      name.anchor.set(0.5, 0);
      name.position.set(NAIVE.x, NAIVE.y + 34);
      const held = usd(world.balances?.naive?.usdc);
      const sub = label(drained ? `holds ${held} now` : `no policy engine, no vault\nholds ${held}`, 13, C.inkSoft, { align: "center", lineHeight: 17 });
      sub.anchor.set(0.5, 0);
      sub.position.set(NAIVE.x, NAIVE.y + 58);
      this.statics.addChild(g, name, sub);
      if (drained) this.plate({ x: NAIVE.x, y: NAIVE.y - 52 }, `Lost ${usd(world.naiveLost)}`, C.attack);
    }

    if (this.slots.size === 0) {
      const hint = label("No agents yet. Place one to start.", 17, C.inkSoft);
      hint.anchor.set(0.5);
      hint.position.set(VAULT.x, VAULT.y + 120);
      this.statics.addChild(hint);
    }
  }

  // ----------------------------------------------------------- events

  private fly(mote: Omit<Mote, "t">): void {
    if (this.reducedMotion) {
      mote.onDone?.();
      return;
    }
    this.motes.push({ ...mote, t: 0 });
  }

  /** A card next to `at`. Cards for the same place stack instead of covering each other. */
  private popup(at: Pt, title: string, body: string, color: number, eventId: number, below = false): void {
    const box = new Container();
    const head = label(title, 14, C.paper, { fontWeight: "700" });
    const text = label(body, 13, C.ink, { wordWrap: true, wordWrapWidth: 340, lineHeight: 17 });
    const foot = label(`audit event #${eventId}`, 11.5, C.inkSoft);
    const width = Math.max(head.width + 24, Math.min(364, text.width + 24), foot.width + 24);
    const height = 30 + text.height + 32;
    const bg = new Graphics();
    bg.roundRect(0, 0, width, height, 7).fill(C.paper).stroke({ width: 2.5, color });
    bg.roundRect(0, 0, width, 28, 7).fill(color);
    bg.rect(0, 16, width, 12).fill(color);
    head.position.set(12, 6);
    text.position.set(12, 35);
    foot.position.set(12, 40 + text.height);
    box.addChild(bg, head, text, foot);

    const key = `${Math.round(at.x)}:${Math.round(at.y)}:${below ? "b" : "a"}`;
    const stacked = this.live.filter((p) => p.key === key).reduce((sum, p) => sum + p.height + 6, 0);
    const x = Math.min(Math.max(at.x - width / 2, 8), W - width - 8);
    let y = below ? at.y + WALL_R + 108 + stacked : at.y - WALL_R - 50 - height - stacked;
    if (y < 6) y = at.y + WALL_R + 108 + stacked; // no room above: go below
    box.position.set(x, Math.min(y, H - height - 6));
    this.popups.addChild(box);
    this.live.push({ box, ttl: 7, key, height });
  }

  /** Play the animation for one live event. Replayed history is drawn, not animated. */
  play(ev: Ev): void {
    const slot = ev.agent_id ? this.slots.get(ev.agent_id) : undefined;
    const p = ev.payload;
    switch (ev.kind) {
      case "decision": {
        if (!slot) return;
        if (p.verdict === "DENY") {
          const tainted = (p.taint ?? []).length > 0;
          const from = tainted ? NEWS : ATTACKER;
          const rule = String((p.rules_fired ?? [])[0] ?? "");
          const segment = ruleFamily(rule);
          this.fly({
            from,
            to: slot,
            dur: 0.9,
            color: C.attack,
            shape: "bolt",
            bounceAt: 1 - (WALL_R + 6) / Math.hypot(slot.x - from.x, slot.y - from.y),
            onDone: () => {
              this.rings.push({ at: slot, t: 0, dur: 0.6, color: C.attack });
              this.flashes.push({ at: slot, segment, t: 0, dur: 3.5 });
            },
          });
          if (this.reducedMotion) this.flashes.push({ at: slot, segment, t: 0, dur: 3.5 });
          this.popup(slot, `Blocked by ${FAMILIES[segment]}`, `${p.reason ?? ""}${rule ? `\nRule ${rule}` : ""}`, C.attack, ev.id);
        } else if (p.verdict === "REQUIRE_APPROVAL") {
          this.popup(slot, "Waiting for a person", p.reason ?? "", C.gate, ev.id);
        }
        return;
      }
      case "execution": {
        if (!slot) return;
        const detail = p.detail ?? {};
        if (!p.ok) {
          this.popup(slot, "Allowed, but it did not run", String(detail.error ?? ""), C.stone, ev.id);
          return;
        }
        const target = detail.via === "x402.exact" ? NEWS : detail.via === "vault.pay" ? PAYEES : MARKET;
        if (detail.top_up_signature) this.fly({ from: VAULT, to: slot, dur: 0.7, color: C.brass, shape: "coin" });
        this.fly({ from: detail.via === "vault.pay" ? VAULT : slot, to: target, dur: 1.0, color: C.brass, shape: "coin" });
        return;
      }
      case "context":
        if (slot) this.fly({ from: NEWS, to: slot, dur: 0.8, color: C.ink, shape: "note" });
        return;
      case "approval_resolved": {
        if (!slot) return;
        const ok = p.status === "approved";
        this.rings.push({ at: slot, t: 0, dur: 0.8, color: ok ? C.ok : C.gate });
        this.popup(slot, ok ? `Approved by ${p.resolved_via}` : p.status === "expired" ? "No answer, so no" : `Denied by ${p.resolved_via}`, p.summary ?? "", ok ? C.ok : C.gate, ev.id);
        return;
      }
      case "anomaly":
        if (slot) this.popup(slot, "Money left that nothing allowed", (p.reasons ?? [])[0] ?? "", C.attack, ev.id);
        if (slot) this.fly({ from: slot, to: ATTACKER, dur: 1.0, color: C.attack, shape: "coin" });
        return;
      case "freeze":
        if (slot) {
          this.rings.push({ at: slot, t: 0, dur: 1.0, color: C.ink });
          const chain = p.onchain?.status === "confirmed" ? "Frozen onchain by the guardian" : `Frozen in the gate (onchain: ${p.onchain?.status})`;
          this.popup(slot, chain, p.reason ?? "", C.ink, ev.id, true);
        }
        return;
      case "chain_refusal": {
        // A stolen key going straight to the vault program: it never reaches the agent's wall.
        const from = slot ?? ATTACKER;
        this.fly({
          from,
          to: VAULT,
          dur: 0.8,
          color: C.attack,
          shape: "bolt",
          bounceAt: 1 - 84 / Math.hypot(VAULT.x - from.x, VAULT.y - from.y),
          onDone: () => this.rings.push({ at: VAULT, t: 0, dur: 0.7, color: C.attack }),
        });
        this.popup(
          { x: VAULT.x, y: VAULT.y - 12 },
          "Refused by the vault program",
          `${p.note ?? ""}\nOnchain error ${p.error}`,
          C.attack,
          ev.id,
        );
        return;
      }
      case "naive_action":
        if (p.executed) {
          for (let i = 0; i < 5; i++) {
            setTimeout(() => this.fly({ from: NAIVE, to: ATTACKER, dur: 1.1, color: C.brass, shape: "coin" }), i * 120);
          }
          this.popup({ x: NAIVE.x, y: NAIVE.y + 34 }, "Drained", `Sent ${usd(p.amount_usd)} to ${short(p.payee)}. Nothing checked it.`, C.attack, ev.id);
        }
        return;
    }
  }

  // ------------------------------------------------------------- tick

  private tick(dt: number): void {
    const g = this.fx;
    g.clear();
    this.flashes = this.flashes.filter((f) => {
      f.t += dt / f.dur;
      const [a0, a1] = segmentArc(f.segment);
      g.moveTo(f.at.x + Math.cos(a0) * WALL_R, f.at.y + Math.sin(a0) * WALL_R)
        .arc(f.at.x, f.at.y, WALL_R, a0, a1)
        .stroke({ width: 13, color: C.attack, alpha: Math.max(1 - f.t * f.t, 0), cap: "butt" });
      return f.t < 1;
    });
    this.motes = this.motes.filter((m) => {
      m.t += dt / m.dur;
      let pos: Pt;
      if (m.bounceAt !== undefined && m.t > m.bounceAt) {
        // hit the wall: fall back along the path
        if (m.onDone) {
          m.onDone();
          m.onDone = undefined;
        }
        const back = m.bounceAt - (m.t - m.bounceAt) * 0.6;
        pos = lerp(m.from, m.to, back);
        pos.y += (m.t - m.bounceAt) * 60;
        if (m.t > m.bounceAt + 0.45) return false;
      } else {
        pos = lerp(m.from, m.to, Math.min(m.t, 1));
      }
      if (m.shape === "coin") {
        g.circle(pos.x, pos.y, 9).fill(m.color).stroke({ width: 2.5, color: C.ink });
      } else if (m.shape === "bolt") {
        g.poly([pos.x, pos.y - 12, pos.x + 12, pos.y, pos.x, pos.y + 12, pos.x - 12, pos.y]).fill(m.color).stroke({ width: 2.5, color: C.ink });
      } else {
        g.rect(pos.x - 7, pos.y - 9, 14, 18).fill(C.paper).stroke({ width: 2, color: m.color });
      }
      if (m.bounceAt === undefined && m.t >= 1) {
        m.onDone?.();
        return false;
      }
      return true;
    });
    this.rings = this.rings.filter((r) => {
      r.t += dt / r.dur;
      g.circle(r.at.x, r.at.y, WALL_R + 10 + r.t * 40).stroke({ width: 4, color: r.color, alpha: Math.max(1 - r.t, 0) });
      return r.t < 1;
    });
    this.live = this.live.filter((p) => {
      p.ttl -= dt;
      p.box.alpha = Math.min(p.ttl / 0.6, 1);
      if (p.ttl <= 0) {
        p.box.destroy({ children: true });
        return false;
      }
      return true;
    });
  }
}
