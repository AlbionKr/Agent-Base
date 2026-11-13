// The space scene: one canvas for stars, the vault's particle core, orbit rings
// and flows, plus the positions of the DOM nodes that sit on top of it.
// One requestAnimationFrame loop; it stops while the tab is hidden.

import type { NodeKind } from "../nodes";

const TAU = Math.PI * 2;

type RGB = [number, number, number];
export const COLOR: Record<"accent" | "danger" | "safe" | "review" | "frozen" | "read" | "white", RGB> = {
  accent: [255, 178, 36],
  danger: [255, 77, 94],
  safe: [61, 220, 151],
  review: [167, 139, 250],
  frozen: [125, 211, 252],
  read: [103, 232, 249],
  white: [255, 255, 255],
};
const rgba = (c: RGB, a: number) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;

export type Placed = { id: string; kind: NodeKind; frozen: boolean };
export type FlowKind = "money" | "loss" | "attack" | "read";
type Pt = { x: number; y: number };

type NodeState = Placed & { slot: number; angle: number; dir: 1 | -1; vel: number; x: number; y: number; scale: number; front: boolean; seen: boolean };
type Flow = { kind: FlowKind; from: string; to: string; t: number; dur: number; delay: number; bend: number; blocked: boolean; count: number; done: boolean };
type Ripple = { at: string; t: number; dur: number; color: RGB; from: number; to: number; width: number; delay: number };
type Star = { x: number; y: number; size: number; alpha: number; layer: number; phase: number; twinkle: boolean };

/** Agents share one ring, as a fraction of the outer one, and go round it in this many seconds. */
const AGENT_RING_X = 0.58;
const AGENT_RING_Y = 0.5;
const AGENT_PERIOD = 150;
const FIRST_AGENT_ANGLE = 3.5;
/** Closer than this, in radians, an agent turns back from the one in front. */
const MIN_GAP = 0.95;
/** Services stay almost still on the outer ring. */
const STATION_ANGLE: Record<string, number> = { "station:payees": 3.95, "station:news": 5.475, "station:markets": 0.2 };
/** How far in front of a node an attack is stopped when it is blocked. */
const SHIELD = 22;

function mulberry(seed: number): () => number {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function bezier(a: Pt, c: Pt, b: Pt, t: number): Pt {
  const u = 1 - t;
  return { x: u * u * a.x + 2 * u * t * c.x + t * t * b.x, y: u * u * a.y + 2 * u * t * c.y + t * t * b.y };
}

const ease = (t: number) => (t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2);

export class SpaceEngine {
  private ctx: CanvasRenderingContext2D;
  private w = 0;
  private h = 0;
  private cx = 0;
  private cy = 0;
  private rx = 0;
  private ry = 0;
  private coreR = 60;
  private stars: Star[] = [];
  private sphere: Float32Array;
  private glow: HTMLCanvasElement | null = null;
  private nodes = new Map<string, NodeState>();
  private els = new Map<string, HTMLElement>();
  private flows: Flow[] = [];
  private ripples: Ripple[] = [];
  private raf = 0;
  private last = 0;
  private time = 0;
  private hover: string | null = null;
  private selected: string | null = null;
  private corePulse = 0;
  private agentCount = 0;
  private dockAngle = new Map<string, number>();
  private observer: ResizeObserver;
  private destroyed = false;

  constructor(
    private canvas: HTMLCanvasElement,
    private host: HTMLElement,
    private still: boolean,
  ) {
    this.ctx = canvas.getContext("2d")!;
    const rand = mulberry(20261120);
    for (let i = 0; i < 300; i++) {
      const layer = i < 170 ? 0 : i < 260 ? 1 : 2;
      this.stars.push({
        x: rand(),
        y: rand(),
        size: layer === 0 ? 1 : layer === 1 ? 1.4 : 2,
        alpha: 0.18 + rand() * (layer === 2 ? 0.55 : 0.35),
        layer,
        phase: rand() * TAU,
        twinkle: rand() < 0.14,
      });
    }
    // points spread evenly over a sphere
    const count = 460;
    this.sphere = new Float32Array(count * 3);
    for (let i = 0; i < count; i++) {
      const y = 1 - (i / (count - 1)) * 2;
      const r = Math.sqrt(1 - y * y);
      const a = i * 2.399963229728653;
      this.sphere.set([Math.cos(a) * r, y, Math.sin(a) * r], i * 3);
    }
    this.observer = new ResizeObserver(() => this.resize());
    this.observer.observe(host);
    document.addEventListener("visibilitychange", this.onVisibility);
    this.resize();
    this.start();
  }

  destroy(): void {
    this.destroyed = true;
    cancelAnimationFrame(this.raf);
    this.observer.disconnect();
    document.removeEventListener("visibilitychange", this.onVisibility);
  }

  // ------------------------------------------------------------- inputs

  setNodes(placed: Placed[]): void {
    for (const n of this.nodes.values()) n.seen = false;
    for (const p of placed) {
      const known = this.nodes.get(p.id);
      if (known) {
        known.frozen = p.frozen;
        known.seen = true;
        continue;
      }
      const slot = p.kind === "agent" ? this.agentCount++ : 0;
      const angle = p.kind === "agent" ? this.freeAngle() : (STATION_ANGLE[p.id] ?? 0);
      const node: NodeState = { ...p, slot, angle, dir: 1, vel: 1, x: 0, y: 0, scale: 1, front: true, seen: true };
      this.nodes.set(p.id, node);
      // one that is already frozen when the page loads starts where frozen agents rest
      if (p.kind === "agent" && p.frozen) node.angle = this.dockFor(node);
    }
    for (const [id, n] of this.nodes) {
      if (n.seen) continue;
      this.nodes.delete(id);
      this.dockAngle.delete(id);
    }
    if (this.still) this.frame(0);
  }

  /** Where a new agent goes on the ring: the middle of the widest free stretch. */
  private freeAngle(): number {
    const taken = [...this.nodes.values()].filter((n) => n.kind === "agent").map((n) => ((n.angle % TAU) + TAU) % TAU).sort((a, b) => a - b);
    if (taken.length === 0) return FIRST_AGENT_ANGLE;
    let best = 0;
    let at = 0;
    taken.forEach((a, i) => {
      const next = i + 1 < taken.length ? taken[i + 1] : taken[0] + TAU;
      if (next - a > best) {
        best = next - a;
        at = a + (next - a) / 2;
      }
    });
    return at;
  }

  /** A frozen agent rests beside the vault, left or right, clear of the services above and below. */
  private dockFor(n: NodeState): number {
    const known = this.dockAngle.get(n.id);
    if (known !== undefined) return known;
    const side = (angle: number) => (Math.cos(angle) > 0 ? "right" : "left");
    const taken = new Set([...this.dockAngle].filter(([id]) => id !== n.id).map(([, angle]) => side(angle)));
    const turn = Math.floor(n.angle / TAU) * TAU;
    // the nearest of: right on this turn, left, right on the next turn
    const spots = [turn, turn + Math.PI, turn + TAU].sort((x, y) => Math.abs(x - n.angle) - Math.abs(y - n.angle));
    const spot = spots.find((angle) => !taken.has(side(angle))) ?? n.angle;
    this.dockAngle.set(n.id, spot);
    return spot;
  }

  register(id: string, el: HTMLElement | null): void {
    if (el) this.els.set(id, el);
    else this.els.delete(id);
    if (el) this.place(id);
  }

  setHover(id: string | null): void {
    this.hover = id;
  }

  setSelected(id: string | null): void {
    this.selected = id;
  }

  /** Something travels from one node to another. Ignored when motion is off. */
  flow(kind: FlowKind, from: string, to: string, opts: { delay?: number; blocked?: boolean; count?: number } = {}): void {
    if (this.still || !this.nodes.has(from) || !this.nodes.has(to)) return;
    this.flows.push({
      kind,
      from,
      to,
      t: 0,
      dur: kind === "attack" ? 1.1 : kind === "read" ? 1.6 : kind === "loss" ? 1.7 : 1.3,
      delay: opts.delay ?? 0,
      bend: kind === "attack" ? 0.1 : 0.2,
      blocked: opts.blocked ?? false,
      count: opts.count ?? (kind === "money" || kind === "loss" ? 6 : 1),
      done: false,
    });
  }

  /** A ring spreads out from a node. */
  ripple(at: string, color: RGB, opts: { delay?: number; reach?: number; width?: number } = {}): void {
    if (this.still || !this.nodes.has(at)) return;
    this.ripples.push({ at, t: 0, dur: 0.9, color, from: 30, to: 30 + (opts.reach ?? 46), width: opts.width ?? 2, delay: opts.delay ?? 0 });
  }

  pulseCore(): void {
    if (!this.still) this.corePulse = 1;
  }

  // ------------------------------------------------------------ geometry

  private resize(): void {
    const rect = this.host.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return;
    this.w = rect.width;
    this.h = rect.height;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    this.canvas.width = Math.round(this.w * dpr);
    this.canvas.height = Math.round(this.h * dpr);
    this.ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    this.cx = this.w / 2;
    this.cy = this.h / 2 - 10;
    this.rx = Math.min(this.w / 2 - 88, 600);
    this.ry = Math.min(this.h / 2 - 112, this.rx * 1.05);
    if (this.ry < this.rx * 0.6) this.rx = this.ry / 0.6;
    this.coreR = Math.max(48, Math.min(104, Math.min(this.rx, this.ry) * 0.235));
    this.host.style.setProperty("--core", `${Math.round(this.coreR * 2)}px`);

    // the core's glow, drawn once
    const size = Math.ceil(this.coreR * 6);
    const glow = document.createElement("canvas");
    glow.width = glow.height = size;
    const g = glow.getContext("2d")!;
    const grad = g.createRadialGradient(size / 2, size / 2, 0, size / 2, size / 2, size / 2);
    grad.addColorStop(0, rgba(COLOR.accent, 0.34));
    grad.addColorStop(0.22, rgba(COLOR.accent, 0.16));
    grad.addColorStop(0.5, rgba(COLOR.accent, 0.05));
    grad.addColorStop(1, rgba(COLOR.accent, 0));
    g.fillStyle = grad;
    g.fillRect(0, 0, size, size);
    this.glow = glow;
    if (this.still) this.frame(0);
  }

  private locate(n: NodeState): void {
    const t = this.time;
    n.scale = 1;
    n.front = true;
    if (n.kind === "vault") {
      n.x = this.cx;
      n.y = this.cy;
    } else if (n.kind === "agent") {
      n.x = this.cx + Math.cos(n.angle) * this.rx * AGENT_RING_X;
      n.y = this.cy + Math.sin(n.angle) * this.ry * AGENT_RING_Y + Math.sin(t * 0.7 + n.slot * 2) * 2.5;
      // the far side of the orbit sits a little further back
      n.scale = 0.93 + 0.07 * (Math.sin(n.angle) + 1) * 0.5;
      n.front = Math.sin(n.angle) >= 0;
    } else if (n.kind === "station") {
      const a = n.angle + Math.sin(t * 0.07 + n.angle) * 0.025;
      n.x = this.cx + Math.cos(a) * this.rx;
      n.y = this.cy + Math.sin(a) * this.ry;
    } else if (n.kind === "attacker") {
      n.x = 96 + Math.sin(t * 0.43) * 6;
      n.y = this.h - 132 + Math.cos(t * 0.31) * 4;
    } else {
      // the unprotected agent drifts on its own, outside the rings
      n.x = this.w - 104 + Math.sin(t * 0.27 + 1) * 8;
      n.y = this.h - 132 + Math.sin(t * 0.19) * 6;
    }
  }

  private place(id: string): void {
    const n = this.nodes.get(id);
    const el = this.els.get(id);
    if (!n || !el) return;
    this.locate(n);
    el.style.transform = `translate3d(${n.x.toFixed(1)}px, ${n.y.toFixed(1)}px, 0) scale(${n.scale.toFixed(3)})`;
    const z = n.kind === "agent" ? (n.front ? "3" : "1") : "2";
    if (el.style.zIndex !== z) el.style.zIndex = z;
    // which half it is in, so its tooltip opens away from the vault
    const half = n.x < this.cx ? "left" : "right";
    if (el.dataset.half !== half) el.dataset.half = half;
    if (n.kind === "agent") {
      // an agent's name sits on the side away from the vault, so it never lies across the core
      const side = el.dataset.side;
      if (n.frozen) {
        if (side !== "top") el.dataset.side = "top";
      } else if (n.y < this.cy - 14 && side !== "top") el.dataset.side = "top";
      else if (n.y > this.cy + 14 && side !== "bottom") el.dataset.side = "bottom";
      else if (!side) el.dataset.side = n.y < this.cy ? "top" : "bottom";
    }
  }

  /** Where a node is now, in the host's own pixels. */
  position(id: string): Pt | null {
    const n = this.nodes.get(id);
    return n ? { x: n.x, y: n.y } : null;
  }

  // ---------------------------------------------------------------- loop

  private onVisibility = (): void => {
    if (document.hidden) cancelAnimationFrame(this.raf);
    else this.start();
  };

  private start(): void {
    if (this.destroyed) return;
    cancelAnimationFrame(this.raf);
    if (this.still) {
      this.frame(0);
      return;
    }
    this.last = performance.now();
    const tick = (now: number) => {
      const dt = Math.min((now - this.last) / 1000, 0.05);
      this.last = now;
      this.frame(dt);
      this.raf = requestAnimationFrame(tick);
    };
    this.raf = requestAnimationFrame(tick);
  }

  private frame(dt: number): void {
    if (this.w < 2) return;
    this.time += dt;
    const ctx = this.ctx;
    ctx.clearRect(0, 0, this.w, this.h);

    const agents = [...this.nodes.values()].filter((n) => n.kind === "agent");
    for (const n of agents) {
      if (n.frozen) {
        // its orbit ends: it glides to rest beside the vault and stays there
        const rest = this.dockFor(n);
        const left = rest - n.angle;
        n.angle = this.still ? rest : n.angle + Math.sign(left) * Math.min(Math.abs(left), dt * 0.5);
        n.vel = 0;
        continue;
      }
      this.dockAngle.delete(n.id);
      // hovering slows a node, an open panel stops it
      const want = this.selected === n.id ? 0 : this.hover === n.id ? 0.12 : 1;
      // agents share one ring: one that is closing on another turns back instead of passing through it
      for (const other of agents) {
        if (other === n) continue;
        let gap = (other.angle - n.angle) % TAU;
        if (gap > Math.PI) gap -= TAU;
        if (gap < -Math.PI) gap += TAU;
        if (Math.abs(gap) < MIN_GAP && Math.sign(gap) * (n.vel - other.vel) > 0.02) n.dir = gap > 0 ? -1 : 1;
      }
      n.vel += (n.dir * want - n.vel) * Math.min(1, dt * 2.5);
      n.angle += (TAU / AGENT_PERIOD) * n.vel * dt;
    }
    for (const n of this.nodes.values()) this.place(n.id);

    this.drawStars(dt);
    this.drawRings();
    this.drawCore(dt);
    this.drawAttackerTrail();
    this.drawFlows(dt);
    this.drawRipples(dt);
  }

  // ------------------------------------------------------------- drawing

  private drawStars(dt: number): void {
    const ctx = this.ctx;
    ctx.fillStyle = "#fff";
    for (const s of this.stars) {
      s.x -= (dt * (2 + s.layer * 3.5)) / this.w;
      if (s.x < 0) s.x += 1;
      const a = s.twinkle ? s.alpha * (0.55 + 0.45 * Math.sin(this.time * 1.3 + s.phase)) : s.alpha;
      ctx.globalAlpha = a;
      ctx.fillRect(s.x * this.w, s.y * this.h, s.size, s.size);
    }
    ctx.globalAlpha = 1;
  }

  private drawRings(): void {
    const ctx = this.ctx;
    ctx.lineWidth = 1;
    ctx.setLineDash([3, 7]);
    const agents = [...this.nodes.values()].filter((n) => n.kind === "agent");
    ctx.strokeStyle = "rgba(255,255,255,0.2)";
    ctx.beginPath();
    ctx.ellipse(this.cx, this.cy, this.rx * AGENT_RING_X, this.ry * AGENT_RING_Y, 0, 0, TAU);
    ctx.stroke();
    ctx.strokeStyle = "rgba(255,255,255,0.13)";
    ctx.beginPath();
    ctx.ellipse(this.cx, this.cy, this.rx, this.ry, 0, 0, TAU);
    ctx.stroke();
    ctx.setLineDash([]);

    // a tether from the vault to each agent it stands behind
    for (const n of agents) {
      const grad = ctx.createLinearGradient(this.cx, this.cy, n.x, n.y);
      const c = n.frozen ? COLOR.frozen : COLOR.accent;
      grad.addColorStop(0, rgba(c, 0.26));
      grad.addColorStop(1, rgba(c, 0.04));
      ctx.strokeStyle = grad;
      ctx.beginPath();
      ctx.moveTo(this.cx, this.cy);
      ctx.lineTo(n.x, n.y);
      ctx.stroke();
    }
  }

  private drawCore(dt: number): void {
    const ctx = this.ctx;
    this.corePulse = Math.max(0, this.corePulse - dt * 1.6);
    const pulse = Math.sin(this.corePulse * Math.PI) * 0.1;
    const breathe = this.still ? 0 : Math.sin(this.time * 0.9) * 0.015;
    const R = this.coreR * (1 + pulse + breathe);

    ctx.globalCompositeOperation = "lighter";
    if (this.glow) {
      const size = this.glow.width * (1 + pulse * 2);
      ctx.globalAlpha = 0.85 + pulse * 1.5;
      ctx.drawImage(this.glow, this.cx - size / 2, this.cy - size / 2, size, size);
      ctx.globalAlpha = 1;
    }
    const spin = this.time * 0.11;
    const cosY = Math.cos(spin);
    const sinY = Math.sin(spin);
    const cosX = Math.cos(0.42);
    const sinX = Math.sin(0.42);
    const p = this.sphere;
    for (let i = 0; i < p.length; i += 3) {
      const x1 = p[i] * cosY + p[i + 2] * sinY;
      const z1 = -p[i] * sinY + p[i + 2] * cosY;
      const y2 = p[i + 1] * cosX - z1 * sinX;
      const z2 = p[i + 1] * sinX + z1 * cosX;
      // keep the middle calm so the balance stays readable
      const rim = Math.min(1, Math.max(0, (Math.hypot(x1, y2) - 0.5) / 0.4));
      const depth = (z2 + 1) / 2;
      const alpha = (0.1 + 0.9 * depth) * (0.12 + 0.88 * rim);
      if (alpha < 0.05) continue;
      const size = 1 + depth * 1.5;
      ctx.fillStyle = depth > 0.75 ? `rgba(255,226,160,${alpha.toFixed(3)})` : rgba(COLOR.accent, Number(alpha.toFixed(3)));
      ctx.fillRect(this.cx + x1 * R - size / 2, this.cy + y2 * R - size / 2, size, size);
    }
    ctx.globalCompositeOperation = "source-over";
    ctx.strokeStyle = rgba(COLOR.accent, 0.32 + pulse * 2);
    ctx.lineWidth = 1;
    ctx.beginPath();
    ctx.arc(this.cx, this.cy, R * 1.16, 0, TAU);
    ctx.stroke();
  }

  private drawAttackerTrail(): void {
    const n = this.nodes.get("attacker");
    if (!n) return;
    const ctx = this.ctx;
    // it came from outside: a faint wake pointing away from the vault
    const dx = n.x - this.cx;
    const dy = n.y - this.cy;
    const d = Math.hypot(dx, dy) || 1;
    const len = 74 + (this.still ? 0 : Math.sin(this.time * 2.1) * 6);
    const ex = n.x + (dx / d) * len;
    const ey = n.y + (dy / d) * len;
    const grad = ctx.createLinearGradient(n.x, n.y, ex, ey);
    grad.addColorStop(0, rgba(COLOR.danger, 0.34));
    grad.addColorStop(1, rgba(COLOR.danger, 0));
    ctx.strokeStyle = grad;
    ctx.lineWidth = 9;
    ctx.lineCap = "round";
    ctx.beginPath();
    ctx.moveTo(n.x, n.y);
    ctx.lineTo(ex, ey);
    ctx.stroke();
    ctx.lineCap = "butt";
  }

  private path(f: Flow): { a: Pt; c: Pt; b: Pt } | null {
    const from = this.nodes.get(f.from);
    const to = this.nodes.get(f.to);
    if (!from || !to) return null;
    const mx = (from.x + to.x) / 2;
    const my = (from.y + to.y) / 2;
    const dx = to.x - from.x;
    const dy = to.y - from.y;
    // along the bottom edge the notices are in the way, so the path arcs well above them
    const low = from.y > this.h * 0.72 && to.y > this.h * 0.72;
    const bend = low ? 0.5 : f.bend;
    const one = { x: mx - dy * bend, y: my + dx * bend };
    const other = { x: mx + dy * bend, y: my - dx * bend };
    // of the two ways to curve, take the one that leans toward the middle of the scene
    const near = (p: Pt) => Math.hypot(p.x - this.cx, p.y - this.cy);
    return { a: from, c: near(one) <= near(other) ? one : other, b: to };
  }

  private drawFlows(dt: number): void {
    const ctx = this.ctx;
    this.flows = this.flows.filter((f) => {
      if (f.delay > 0) {
        f.delay -= dt;
        return true;
      }
      const path = this.path(f);
      if (!path) return false;
      f.t += dt / f.dur;

      if (f.kind === "read") {
        // a dashed beam that draws itself, holds, then fades
        const reveal = Math.min(1, f.t * 2.6);
        const fade = f.t > 0.7 ? Math.max(0, 1 - (f.t - 0.7) / 0.3) : 1;
        ctx.strokeStyle = rgba(COLOR.read, 0.75 * fade);
        ctx.lineWidth = 1.5;
        ctx.setLineDash([5, 6]);
        ctx.lineDashOffset = -this.time * 26;
        ctx.beginPath();
        ctx.moveTo(path.a.x, path.a.y);
        for (let i = 1; i <= 24; i++) {
          const q = bezier(path.a, path.c, path.b, (i / 24) * reveal);
          ctx.lineTo(q.x, q.y);
        }
        ctx.stroke();
        ctx.setLineDash([]);
        ctx.lineDashOffset = 0;
        return f.t < 1;
      }

      if (f.kind === "attack") {
        const head = bezier(path.a, path.c, path.b, ease(Math.min(f.t, 1)));
        const reach = Math.hypot(head.x - path.b.x, head.y - path.b.y);
        const stopAt = (f.to === "vault" ? this.coreR * 1.16 : 38) + SHIELD;
        if (f.blocked && !f.done && reach <= stopAt) {
          f.done = true;
          this.shield(f.to, stopAt);
        }
        if (!f.done) {
          ctx.globalCompositeOperation = "lighter";
          for (let i = 0; i < 14; i++) {
            const q = bezier(path.a, path.c, path.b, ease(Math.max(0, Math.min(f.t, 1) - i * 0.022)));
            ctx.fillStyle = rgba(COLOR.danger, 0.9 * (1 - i / 14));
            const r = 5 * (1 - i / 16);
            ctx.beginPath();
            ctx.arc(q.x, q.y, r, 0, TAU);
            ctx.fill();
          }
          ctx.globalCompositeOperation = "source-over";
        }
        return !f.done && f.t < 1;
      }

      // money: a few motes following each other along the curve
      const color = f.kind === "loss" ? COLOR.danger : COLOR.accent;
      let alive = false;
      ctx.globalCompositeOperation = "lighter";
      for (let k = 0; k < f.count; k++) {
        const t = f.t - k * 0.09;
        if (t < 0) {
          alive = true;
          continue;
        }
        if (t > 1) continue;
        alive = true;
        for (let i = 0; i < 6; i++) {
          const q = bezier(path.a, path.c, path.b, ease(Math.max(0, t - i * 0.018)));
          ctx.fillStyle = rgba(color, 0.85 * (1 - i / 6));
          ctx.beginPath();
          ctx.arc(q.x, q.y, 3.4 * (1 - i / 9), 0, TAU);
          ctx.fill();
        }
      }
      ctx.globalCompositeOperation = "source-over";
      return alive;
    });
  }

  /** The flash where a blocked attack stops. */
  private shield(at: string, radius: number): void {
    this.ripples.push({ at, t: 0, dur: 0.7, color: COLOR.white, from: radius, to: radius + 4, width: 3, delay: 0 });
    this.ripples.push({ at, t: 0, dur: 1.1, color: COLOR.frozen, from: radius, to: radius + 30, width: 1.5, delay: 0.05 });
  }

  private drawRipples(dt: number): void {
    const ctx = this.ctx;
    this.ripples = this.ripples.filter((r) => {
      if (r.delay > 0) {
        r.delay -= dt;
        return true;
      }
      const n = this.nodes.get(r.at);
      if (!n) return false;
      r.t += dt / r.dur;
      const k = Math.min(r.t, 1);
      ctx.strokeStyle = rgba(r.color, 0.85 * (1 - k));
      ctx.lineWidth = r.width;
      ctx.beginPath();
      ctx.arc(n.x, n.y, r.from + (r.to - r.from) * (1 - Math.pow(1 - k, 3)), 0, TAU);
      ctx.stroke();
      return r.t < 1;
    });
  }
}
