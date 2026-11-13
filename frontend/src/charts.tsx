// Two charts, both drawn from audit events: decisions over time, and a ring for a share of a limit.

import { useLayoutEffect, useRef, useState } from "react";
import type { Bucket, Series } from "./fleet";

/** Allowed, then asked, then blocked: the stack order, bottom up. Each has a word and a glyph in the legend. */
export const SERIES: { key: keyof Pick<Bucket, "allow" | "approval" | "deny">; name: string; tone: "ok" | "wait" | "bad" }[] = [
  { key: "allow", name: "Allowed", tone: "ok" },
  { key: "approval", name: "Asked a person", tone: "wait" },
  { key: "deny", name: "Blocked", tone: "bad" },
];

function useWidth<T extends HTMLElement>(): [React.RefObject<T>, number] {
  const ref = useRef<T>(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setWidth(el.clientWidth);
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    return () => observer.disconnect();
  }, []);
  return [ref, width];
}

const time = (ms: number) => new Date(ms).toLocaleTimeString([], { hour: "numeric", minute: "2-digit", second: "2-digit" });

export function ChartLegend() {
  return (
    <ul className="chartkeys" aria-hidden="true">
      {SERIES.map((s) => (
        <li key={s.key} className={s.tone}><i />{s.name}</li>
      ))}
    </ul>
  );
}

/** Decisions per stretch of time, stacked by outcome. Hover or focus a column for its counts. */
export function ActivityChart({ series, height = 220, label }: { series: Series; height?: number; label: string }) {
  const [box, width] = useWidth<HTMLDivElement>();
  const [hover, setHover] = useState<number | null>(null);

  if (series.buckets.length === 0) {
    return <p className="quiet chartempty">No decisions yet. Each allowed, held or blocked action is counted here as it happens.</p>;
  }

  const AXIS = 28;
  const BOTTOM = 24;
  const plotW = Math.max(0, width - AXIS);
  const plotH = height - BOTTOM - 20;
  const n = series.buckets.length;
  const slot = plotW / n;
  const barW = Math.max(4, Math.min(28, slot - 6));
  const top = Math.max(1, series.peak);
  const unit = (plotH - 4) / top;
  const ticks = top <= 4 ? Array.from({ length: top + 1 }, (_, i) => i) : [0, Math.round(top / 2), top];
  const summary = SERIES.map((s) => `${s.name}: ${series.buckets.reduce((sum, b) => sum + b[s.key], 0)}`).join(", ");
  const active = hover == null ? null : series.buckets[hover];

  return (
    <div className="chart" ref={box} style={{ height }} onMouseLeave={() => setHover(null)}>
      {width > 0 && (
        <svg width={width} height={height} role="img" aria-label={`${label}. ${series.total} decisions from ${time(series.start)} to ${time(series.end)}. ${summary}.`}>
          {ticks.map((t) => (
            <g key={t} className="tick">
              <line x1={AXIS} x2={width} y1={20 + plotH - t * unit} y2={20 + plotH - t * unit} />
              <text x={AXIS - 8} y={20 + plotH - t * unit} dy="0.35em" textAnchor="end">{t}</text>
            </g>
          ))}
          {series.buckets.map((b, i) => {
            const x = AXIS + i * slot + (slot - barW) / 2;
            let y = 20 + plotH;
            const total = b.allow + b.approval + b.deny;
            const parts = SERIES.filter((s) => b[s.key] > 0);
            return (
              <g key={i} className={hover === i ? "col on" : hover != null ? "col dim" : "col"}>
                <rect className="hit" x={AXIS + i * slot} y={0} width={slot} height={20 + plotH} onMouseEnter={() => setHover(i)} />
                {parts.map((s, j) => {
                  const h = b[s.key] * unit;
                  y -= h;
                  const last = j === parts.length - 1;
                  // a 2px gap between stacked segments; the top segment gets the rounded end
                  return <rect key={s.key} className={`seg ${s.tone}`} x={x} y={y} width={barW} height={Math.max(1, h - (last ? 0 : 2))} rx={last ? Math.min(4, barW / 2) : 0} />;
                })}
                {total === 0 && <rect className="zero" x={x} y={20 + plotH - 1} width={barW} height={1} />}
              </g>
            );
          })}
          <text className="axis" x={0} y={0} dy="0.8em">decisions</text>
          <line className="base" x1={AXIS} x2={width} y1={20 + plotH} y2={20 + plotH} />
          <text className="axis" x={AXIS} y={height - 6}>{time(series.start)}</text>
          <text className="axis" x={width} y={height - 6} textAnchor="end">{time(series.end)}</text>
        </svg>
      )}
      {active && hover != null && (
        <div className="charttip" style={{ left: Math.min(Math.max(AXIS + hover * slot + slot / 2, 90), width - 90) }}>
          <b>{time(active.from)} to {time(active.to)}</b>
          {SERIES.map((s) => (
            <span key={s.key} className={s.tone}><i />{s.name}<em className="num">{active[s.key]}</em></span>
          ))}
        </div>
      )}
    </div>
  );
}

/** A share of a limit as a ring, with whatever belongs in the middle. */
export function Ring({ value, max, size = 168, children }: { value: number; max: number; size?: number; children?: React.ReactNode }) {
  const share = max > 0 ? Math.min(1, Math.max(0, value / max)) : 0;
  const stroke = size >= 120 ? 14 : 6;
  const r = (size - stroke) / 2;
  return (
    <div className="ring" style={{ width: size, height: size }}>
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true">
        <circle cx={size / 2} cy={size / 2} r={r} className="track" strokeWidth={stroke} />
        {share > 0 && (
          <circle cx={size / 2} cy={size / 2} r={r} className="used" strokeWidth={stroke} pathLength={100} strokeDasharray={`${Math.max(0.8, share * 100)} 100`} transform={`rotate(-90 ${size / 2} ${size / 2})`} />
        )}
      </svg>
      <div className="ringmid">{children}</div>
    </div>
  );
}
