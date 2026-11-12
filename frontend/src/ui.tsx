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
