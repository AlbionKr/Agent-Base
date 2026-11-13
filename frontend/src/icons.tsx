// One icon family: 24px grid, 1.7px stroke, round caps. The agent is the same figure as on the map.

export type IconName = "overview" | "agents" | "shield" | "news" | "log" | "setup" | "agent" | "lock" | "left" | "right" | "plus" | "play" | "unlock";

const PATHS: Record<IconName, React.ReactNode> = {
  overview: (
    <>
      <rect x="4" y="4" width="7" height="9" rx="1.5" />
      <rect x="13" y="4" width="7" height="5" rx="1.5" />
      <rect x="13" y="11" width="7" height="9" rx="1.5" />
      <rect x="4" y="15" width="7" height="5" rx="1.5" />
    </>
  ),
  agents: (
    <>
      <rect x="3.5" y="9" width="11" height="9" rx="2.5" />
      <path d="M9 9V6" />
      <path d="M17.5 11.5h1a2 2 0 012 2v3a2 2 0 01-2 2h-1" />
      <circle cx="7" cy="13.5" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="11" cy="13.5" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  shield: (
    <>
      <path d="M12 3.5l7 2.6v5.4c0 4.3-2.9 7.6-7 9-4.1-1.4-7-4.7-7-9V6.1z" />
      <path d="M9 12l2.2 2.2L15.5 10" />
    </>
  ),
  news: (
    <>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M8 9.5h8M8 12.5h8M8 15.5h5" />
    </>
  ),
  log: (
    <>
      <path d="M8 6h12M8 12h12M8 18h12" />
      <circle cx="4.5" cy="6" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="4.5" cy="12" r="0.9" fill="currentColor" stroke="none" />
      <circle cx="4.5" cy="18" r="0.9" fill="currentColor" stroke="none" />
    </>
  ),
  setup: (
    <>
      <path d="M4 7h10M18 7h2M4 17h2M10 17h10" />
      <circle cx="16" cy="7" r="2" />
      <circle cx="8" cy="17" r="2" />
    </>
  ),
  agent: (
    <>
      <rect x="5" y="8" width="14" height="11" rx="3" />
      <path d="M12 8V4.5" />
      <circle cx="12" cy="3.6" r="1" fill="currentColor" />
      <circle cx="9.3" cy="13.2" r="1.1" fill="currentColor" stroke="none" />
      <circle cx="14.7" cy="13.2" r="1.1" fill="currentColor" stroke="none" />
    </>
  ),
  lock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" fill="currentColor" stroke="none" />
      <path d="M8 11V8a4 4 0 018 0v3" />
    </>
  ),
  unlock: (
    <>
      <rect x="5" y="11" width="14" height="9" rx="2" />
      <path d="M8 11V8a4 4 0 017.6-1.6" />
    </>
  ),
  left: <path d="M14.5 6l-6 6 6 6" />,
  right: <path d="M9.5 6l6 6-6 6" />,
  plus: <path d="M12 5v14M5 12h14" />,
  play: <path d="M8 5.5v13l10-6.5z" />,
};

export function Icon({ name, size = 20 }: { name: IconName; size?: number }) {
  return (
    <svg className="icon" width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      {PATHS[name]}
    </svg>
  );
}
