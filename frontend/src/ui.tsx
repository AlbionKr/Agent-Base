import { useEffect, useRef, useState } from "react";
import type { Tone } from "./describe";
import { explorerTx, short, type Config } from "./model";

/** A small shape for each kind of outcome, so colour is never the only signal. */
export function Glyph({ tone }: { tone: Tone }) {
  const common = { width: 14, height: 14, viewBox: "0 0 14 14", "aria-hidden": true, className: "glyph" } as const;
  switch (tone) {
    case "ok":
      return <svg {...common}><path d="M2.5 7.5l3 3 6-7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" /></svg>;
    case "bad":
      return <svg {...common}><path d="M7 1l6 6-6 6-6-6z" fill="currentColor" /></svg>;
    case "wait":
      return <svg {...common}><circle cx="7" cy="7" r="5.5" fill="none" stroke="currentColor" strokeWidth="2" /><path d="M7 4v3.4l2.2 1.4" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" /></svg>;
    case "lock":
      return <svg {...common}><rect x="2.5" y="6" width="9" height="7" rx="1.5" fill="currentColor" /><path d="M4.5 6V4.5a2.5 2.5 0 015 0V6" fill="none" stroke="currentColor" strokeWidth="1.8" /></svg>;
    case "warn":
      return <svg {...common}><path d="M3 7h8" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" /></svg>;
    default:
      return <svg {...common}><circle cx="7" cy="7" r="2.5" fill="currentColor" /></svg>;
  }
}

export function Tag({ tone, children }: { tone: Tone; children: React.ReactNode }) {
  return (
    <span className={`tag ${tone}`}>
      <Glyph tone={tone} />
      {children}
    </span>
  );
}

export function Tx({ config, signature }: { config: Config | null; signature?: string }) {
  if (!signature) return null;
  return (
    <a className="tx" href={explorerTx(config, signature)} target="_blank" rel="noreferrer">
      transaction {short(signature)}
    </a>
  );
}

const REDUCED = typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;

/** A number that counts to its new value when it changes. The value shown at rest is always the exact one. */
export function Num({ value, format = String }: { value: number; format?: (n: number) => string }) {
  const [shown, setShown] = useState(value);
  const from = useRef(value);
  useEffect(() => {
    const start = from.current;
    from.current = value;
    // A hidden tab gets no animation frames, so there the number just changes.
    if (REDUCED || document.hidden || start === value || !Number.isFinite(start) || !Number.isFinite(value)) {
      setShown(value);
      return;
    }
    // Whatever happens to the frames, the exact value is on screen within a second.
    const settle = setTimeout(() => setShown(value), 800);
    let frame = 0;
    const began = performance.now();
    const step = (now: number) => {
      const k = Math.min(1, (now - began) / 600);
      setShown(k === 1 ? value : start + (value - start) * (1 - Math.pow(1 - k, 3)));
      if (k < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => {
      cancelAnimationFrame(frame);
      clearTimeout(settle);
    };
  }, [value]);
  // On the way, whole cents (or whole numbers) keep the width steady.
  const text = shown === value ? format(value) : format === String ? String(Math.round(shown)) : format(Math.round(shown * 100) / 100);
  return <span className="num">{text}</span>;
}
