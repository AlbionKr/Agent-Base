// The base, drawn with PixiJS. Every moving thing is started by one audit event
// and carries that event's id.

import { Application, Container, Graphics, Text, type TextStyleOptions } from "pixi.js";
import { short, usd, type Config, type Ev, type World } from "./model";

const W = 1000;
const H = 680;

const C = {
  ink: 0x14262e,
  inkSoft: 0x4b5f66,
  ground: 0xdde5dc,
  road: 0xc9d3c8,
  brass: 0xc8962c,
  brassDeep: 0x8f6a16,
  guard: 0x2f6f8f,
  attack: 0xc8322b,
  gate: 0x6b4fa0,
  stone: 0x8c8f86,
  paper: 0xf5f7f2,
  ok: 0x2e7d4f,
};

const FONT = "Public Sans, system-ui, sans-serif";
const DISPLAY = "Bricolage Grotesque, Public Sans, system-ui, sans-serif";

type Pt = { x: number; y: number };
const VAULT: Pt = { x: 500, y: 345 };
const NEWS: Pt = { x: 850, y: 105 };
const MARKET: Pt = { x: 895, y: 345 };
const ATTACKER: Pt = { x: 120, y: 585 };
const PAYEES: Pt = { x: 105, y: 88 };
const NAIVE: Pt = { x: 760, y: 560 };
const AGENT_SLOTS: Pt[] = [
  { x: 345, y: 275 },
  { x: 560, y: 150 },
  { x: 700, y: 300 },
  { x: 345, y: 500 },
];
const WALL_R = 62;

type Mote = { from: Pt; to: Pt; t: number; dur: number; color: number; shape: "coin" | "bolt" | "note"; bounceAt?: number; onDone?: () => void };
type Popup = { box: Container; ttl: number; key: string; height: number };
type Ring = { at: Pt; t: number; dur: number; color: number };

function label(text: string, size: number, color: number, extra: Partial<TextStyleOptions> = {}): Text {
  return new Text({ text, style: { fontFamily: FONT, fontSize: size, fill: color, ...extra } });
}

function lerp(a: Pt, b: Pt, t: number): Pt {
  return { x: a.x + (b.x - a.x) * t, y: a.y + (b.y - a.y) * t };
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
    for (let r = 120; r <= 520; r += 80) {
      g.ellipse(VAULT.x, VAULT.y, r * 1.25, r * 0.8).stroke({ width: 1, color: C.road, alpha: 0.9 });
    }
  }

  private road(g: Graphics, a: Pt, b: Pt): void {
    g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ width: 10, color: C.road, cap: "round" });
    g.moveTo(a.x, a.y).lineTo(b.x, b.y).stroke({ width: 1, color: C.inkSoft, alpha: 0.25 });
  }

  private post(at: Pt, title: string, sub: string, color: number, draw: (g: Graphics) => void): void {
    const g = new Graphics();
    draw(g);
    g.position.set(at.x, at.y);
    const t = label(title, 14, C.ink, { fontWeight: "600" });
    t.anchor.set(0.5, 0);
    t.position.set(at.x, at.y + 30);
    const s = label(sub, 11.5, color === C.attack ? C.attack : C.inkSoft, { align: "center" });
    s.anchor.set(0.5, 0);
    s.position.set(at.x, at.y + 48);
    this.statics.addChild(g, t, s);
  }

  private redraw(): void {
    const world = this.world!;
    this.statics.removeChildren().forEach((c) => c.destroy());
    const roads = new Graphics();
    this.statics.addChild(roads);
    const labels = this.config?.labels ?? {};

    this.slots.forEach((slot) => this.road(roads, VAULT, slot));
    this.road(roads, VAULT, NAIVE);

    // outposts
    this.post(NEWS, "News stand", `x402, ${usd("0.001")} a call\n${labels.news_feed ?? ""}`, C.ink, (g) => {
      g.roundRect(-26, -20, 52, 40, 4).fill(C.paper).stroke({ width: 2, color: C.ink });
      g.moveTo(-16, -8).lineTo(16, -8).moveTo(-16, 0).lineTo(16, 0).moveTo(-16, 8).lineTo(6, 8).stroke({ width: 2, color: C.inkSoft });
    });
    this.post(MARKET, "Gemini markets", labels.gemini_orders?.startsWith("SANDBOX") ? "orders: SANDBOX" : "orders: not configured", C.ink, (g) => {
      g.rect(-24, -20, 48, 40).fill(C.paper).stroke({ width: 2, color: C.ink });
      g.moveTo(-16, 10).lineTo(-6, -2).lineTo(4, 6).lineTo(16, -12).stroke({ width: 2.5, color: C.guard });
    });
    this.post(PAYEES, "Allowlisted payees", "the only addresses\nthe vault will pay", C.ink, (g) => {
      g.circle(-12, 0, 13).fill(C.paper).stroke({ width: 2, color: C.ink });
      g.circle(12, 0, 13).fill(C.paper).stroke({ width: 2, color: C.ink });
    });
    this.post(ATTACKER, "Attacker wallet", world.balances?.attacker_usdc != null ? `holds ${usd(world.balances.attacker_usdc)}\n${short(this.config?.attacker)}` : short(this.config?.attacker), C.attack, (g) => {
      g.poly([0, -24, 22, 14, -22, 14]).fill(C.attack).stroke({ width: 2, color: C.ink });
      g.rect(-2, -8, 4, 12).fill(C.paper);
      g.circle(0, 9, 2.4).fill(C.paper);
    });

    // vault core
    const vault = new Graphics();
    const sides = 8;
    const pts: number[] = [];
    for (let i = 0; i < sides; i++) {
      const a = (Math.PI * 2 * i) / sides + Math.PI / 8;
      pts.push(Math.cos(a) * 64, Math.sin(a) * 64);
    }
    vault.poly(pts).fill(C.brass).stroke({ width: 4, color: C.ink });
    vault.poly(pts.map((v) => v * 0.72)).fill(C.paper).stroke({ width: 2, color: C.brassDeep });
    vault.position.set(VAULT.x, VAULT.y);
    const amount = new Text({
      text: world.balances?.vault_usdc != null ? usd(world.balances.vault_usdc) : "no chain",
      style: { fontFamily: DISPLAY, fontSize: 22, fontWeight: "700", fill: C.ink },
    });
    amount.anchor.set(0.5);
    amount.position.set(VAULT.x, VAULT.y - 4);
    const vt = label("vault", 12, C.inkSoft);
    vt.anchor.set(0.5);
    vt.position.set(VAULT.x, VAULT.y + 16);
    this.statics.addChild(vault, amount, vt);

    // protected agents
    this.slots.forEach((slot, id) => {
      const agent = world.agents[id];
      if (!agent) return;
      const bal = world.balances?.agents[id];
      const frozen = agent.frozen || bal?.frozen_onchain === true;
      const g = new Graphics();
      // Each wall segment is one active rule family.
      const rules = ["mandate", "allowlists", "caps", "rate limit", "taint", "approval"];
      rules.forEach((_, i) => {
        const a0 = (Math.PI * 2 * i) / rules.length + 0.08;
        const a1 = (Math.PI * 2 * (i + 1)) / rules.length - 0.08;
        g.moveTo(Math.cos(a0) * WALL_R, Math.sin(a0) * WALL_R)
          .arc(0, 0, WALL_R, a0, a1)
          .stroke({ width: 7, color: frozen ? C.stone : C.guard, cap: "butt" });
      });
      // onchain ring: the vault's own caps
      g.circle(0, 0, WALL_R + 10).stroke({ width: 1.5, color: C.brassDeep, alpha: 0.9 });
      g.roundRect(-20, -24, 40, 48, 5).fill(frozen ? C.stone : C.paper).stroke({ width: 3, color: C.ink });
      g.rect(-8, -34, 16, 12).fill(frozen ? C.stone : C.guard).stroke({ width: 2, color: C.ink });
      if (frozen) {
        g.roundRect(-11, -4, 22, 18, 3).fill(C.ink);
        g.moveTo(-6, -4).arc(0, -4, 6, Math.PI, 0).stroke({ width: 3, color: C.ink });
      }
      g.position.set(slot.x, slot.y);
      const name = label(agent.name, 14, C.ink, { fontWeight: "600" });
      name.anchor.set(0.5, 0);
      name.position.set(slot.x, slot.y + WALL_R + 16);
      const detail = frozen
        ? "frozen"
        : bal?.max_loss_bound_usdc
          ? `at most ${usd(bal.max_loss_bound_usdc)} at risk today`
          : `up to ${usd(agent.maxPerDay)} a day`;
      const sub = label(detail, 11.5, frozen ? C.attack : C.inkSoft);
      sub.anchor.set(0.5, 0);
      sub.position.set(slot.x, slot.y + WALL_R + 34);
      this.statics.addChild(g, name, sub);

      const waiting = Object.values(world.approvals).some((a) => a.agent_id === id && a.status === "pending");
      if (waiting) {
        const gate = new Graphics();
        gate.circle(0, 0, WALL_R + 20).stroke({ width: 4, color: C.gate });
        gate.roundRect(-30, -WALL_R - 44, 60, 20, 10).fill(C.gate);
        gate.position.set(slot.x, slot.y);
        const gt = label("needs approval", 10.5, C.paper, { fontWeight: "600" });
        gt.anchor.set(0.5);
        gt.position.set(slot.x, slot.y - WALL_R - 34);
        this.statics.addChild(gate, gt);
      }
    });

    // the naive agent: a tent, no walls
    if (world.balances?.naive || world.naiveLost > 0) {
      const g = new Graphics();
      g.poly([-34, 22, 0, -30, 34, 22]).fill(C.paper).stroke({ width: 3, color: C.stone });
      g.poly([-8, 22, 0, 2, 8, 22]).fill(C.stone);
      g.position.set(NAIVE.x, NAIVE.y);
      const name = label("Naive agent", 14, C.ink, { fontWeight: "600" });
      name.anchor.set(0.5, 0);
      name.position.set(NAIVE.x, NAIVE.y + 30);
      const lost = world.naiveLost > 0 ? `, lost ${usd(world.naiveLost)}` : "";
      const sub = label(`no policy engine, no vault\nholds ${usd(world.balances?.naive?.usdc)}${lost}`, 11.5, world.naiveLost > 0 ? C.attack : C.inkSoft, { align: "center" });
      sub.anchor.set(0.5, 0);
      sub.position.set(NAIVE.x, NAIVE.y + 48);
      this.statics.addChild(g, name, sub);
    }

    if (this.slots.size === 0) {
      const hint = label("No agents yet. Place one to start.", 15, C.inkSoft);
      hint.anchor.set(0.5);
      hint.position.set(VAULT.x, VAULT.y + 110);
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
    const head = label(title, 12.5, C.paper, { fontWeight: "600" });
    const text = label(body, 12, C.ink, { wordWrap: true, wordWrapWidth: 250, lineHeight: 16 });
    const foot = label(`audit event #${eventId}`, 10.5, C.inkSoft);
    const width = Math.max(head.width + 20, Math.min(270, text.width + 20), foot.width + 20);
    const height = 26 + text.height + 30;
    const bg = new Graphics();
    bg.roundRect(0, 0, width, height, 6).fill(C.paper).stroke({ width: 2, color });
    bg.roundRect(0, 0, width, 24, 6).fill(color);
    bg.rect(0, 14, width, 10).fill(color);
    head.position.set(10, 5);
    text.position.set(10, 30);
    foot.position.set(10, 34 + text.height);
    box.addChild(bg, head, text, foot);

    const key = `${Math.round(at.x)}:${Math.round(at.y)}:${below ? "b" : "a"}`;
    const stacked = this.live.filter((p) => p.key === key).reduce((sum, p) => sum + p.height + 6, 0);
    const x = Math.min(Math.max(at.x - width / 2, 8), W - width - 8);
    let y = below ? at.y + WALL_R + 58 + stacked : at.y - WALL_R - 22 - height - stacked;
    if (y < 6) y = at.y + WALL_R + 58 + stacked; // no room above: go below
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
          this.fly({
            from,
            to: slot,
            dur: 0.9,
            color: C.attack,
            shape: "bolt",
            bounceAt: 1 - (WALL_R + 6) / Math.hypot(slot.x - from.x, slot.y - from.y),
            onDone: () => this.rings.push({ at: slot, t: 0, dur: 0.6, color: C.attack }),
          });
          this.popup(slot, `Blocked: ${(p.rules_fired ?? [])[0] ?? "DENY"}`, p.reason ?? "", C.attack, ev.id);
        } else if (p.verdict === "REQUIRE_APPROVAL") {
          this.popup(slot, "Waiting for a human", p.reason ?? "", C.gate, ev.id);
        }
        return;
      }
      case "execution": {
        if (!slot) return;
        const detail = p.detail ?? {};
        if (!p.ok) {
          this.popup(slot, "Execution failed", String(detail.error ?? ""), C.stone, ev.id);
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
        this.popup(slot, ok ? `Approved by ${p.resolved_via}` : p.status === "expired" ? "No answer: denied" : `Denied by ${p.resolved_via}`, p.summary ?? "", ok ? C.ok : C.gate, ev.id);
        return;
      }
      case "anomaly":
        if (slot) this.popup(slot, `Anomaly: ${(p.rules ?? []).join(", ")}`, (p.reasons ?? [])[0] ?? "", C.attack, ev.id);
        if (slot) this.fly({ from: slot, to: ATTACKER, dur: 1.0, color: C.attack, shape: "coin" });
        return;
      case "freeze":
        if (slot) {
          this.rings.push({ at: slot, t: 0, dur: 1.0, color: C.ink });
          const chain = p.onchain?.status === "confirmed" ? "Frozen onchain by the guardian" : `Frozen in the gate (onchain: ${p.onchain?.status})`;
          this.popup(slot, chain, p.reason ?? "", C.ink, ev.id, true);
        }
        return;
      case "naive_action":
        if (p.executed) {
          for (let i = 0; i < 5; i++) {
            setTimeout(() => this.fly({ from: NAIVE, to: ATTACKER, dur: 1.1, color: C.brass, shape: "coin" }), i * 120);
          }
          this.popup({ x: NAIVE.x, y: NAIVE.y + 30 }, "Drained", `Sent ${usd(p.amount_usd)} to ${short(p.payee)}. Nothing checked it.`, C.attack, ev.id);
        }
        return;
    }
  }

  // ------------------------------------------------------------- tick

  private tick(dt: number): void {
    const g = this.fx;
    g.clear();
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
        g.circle(pos.x, pos.y, 7).fill(m.color).stroke({ width: 2, color: C.ink });
      } else if (m.shape === "bolt") {
        g.poly([pos.x, pos.y - 9, pos.x + 9, pos.y, pos.x, pos.y + 9, pos.x - 9, pos.y]).fill(m.color).stroke({ width: 2, color: C.ink });
      } else {
        g.rect(pos.x - 6, pos.y - 8, 12, 16).fill(C.paper).stroke({ width: 1.5, color: m.color });
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
