import { useEffect, useRef, useState } from "react";
import { getJson, postJson, usd, type ApprovalView, type World } from "./model";

type Turn = { role: "user" | "agent"; text: string };

function useCountdown(deadline: string): number {
  const [left, setLeft] = useState(() => Math.max(0, Math.ceil((Date.parse(deadline) - Date.now()) / 1000)));
  useEffect(() => {
    const timer = setInterval(() => setLeft(Math.max(0, Math.ceil((Date.parse(deadline) - Date.now()) / 1000))), 250);
    return () => clearInterval(timer);
  }, [deadline]);
  return left;
}

export function ApprovalCard({ approval, world, voiceReady, timeoutSeconds }: { approval: ApprovalView; world: World; voiceReady: boolean; timeoutSeconds: number }) {
  const left = useCountdown(approval.deadline);
  const [error, setError] = useState("");
  const [voice, setVoice] = useState<"idle" | "connecting" | "listening">("idle");
  const [heard, setHeard] = useState("");
  const session = useRef<{ endSession: () => Promise<void> } | null>(null);
  const agent = world.agents[approval.agent_id];

  useEffect(() => () => void session.current?.endSession().catch(() => {}), []);

  async function decide(decision: "approve" | "deny") {
    setError("");
    try {
      await postJson(`/approvals/${approval.approval_id}/decision`, { decision });
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function askByVoice() {
    setError("");
    setVoice("connecting");
    const transcript: Turn[] = [];
    const report = async (decision: "approve" | "deny") => {
      try {
        await postJson(`/approvals/${approval.approval_id}/voice-decision`, { decision, transcript });
      } catch (err) {
        setError((err as Error).message);
      } finally {
        await session.current?.endSession().catch(() => {});
        setVoice("idle");
      }
      return "recorded";
    };
    try {
      await navigator.mediaDevices.getUserMedia({ audio: true });
      const start = await getJson<{ signed_url: string; dynamic_variables: Record<string, string> }>(
        `/voice/session?approval_id=${encodeURIComponent(approval.approval_id)}`,
      );
      const { Conversation } = await import("@elevenlabs/client");
      session.current = await Conversation.startSession({
        signedUrl: start.signed_url,
        dynamicVariables: start.dynamic_variables,
        // The tools take no arguments: the backend already knows which approval this session is for.
        clientTools: { approve_action: () => report("approve"), deny_action: () => report("deny") },
        onMessage: ({ message, source }: { message: string; source: string }) => {
          transcript.push({ role: source === "user" ? "user" : "agent", text: message });
          if (source === "user") setHeard(message);
        },
        onConnect: () => setVoice("listening"),
        onDisconnect: () => setVoice("idle"),
        onError: (message: string) => setError(String(message)),
      } as any);
    } catch (err) {
      setError((err as Error).message);
      setVoice("idle");
    }
  }

  return (
    <section className="approval" aria-live="polite">
      <h2>{agent?.name ?? approval.agent_id} needs your answer</h2>
      <p className="ask">{approval.summary}</p>
      <p className="quiet">
        {usd(approval.amount_usd)} is above what it may spend alone ({usd(agent?.approvalThreshold)}). No answer in <b>{left}s</b> means no.
      </p>
      <div className="timer" aria-hidden="true">
        <span style={{ width: `${Math.min(100, (left / timeoutSeconds) * 100)}%` }} />
      </div>
      <div className="actions">
        {voiceReady && (
          <button onClick={askByVoice} disabled={voice !== "idle"}>
            {voice === "idle" ? "Answer by voice" : voice === "connecting" ? "Connecting…" : "Listening…"}
          </button>
        )}
        <button className={voiceReady ? "ghost" : "approve"} onClick={() => decide("approve")}>Approve</button>
        <button className="ghost" onClick={() => decide("deny")}>Deny</button>
      </div>
      {!voiceReady && <p className="quiet">Voice approval is not configured (no ElevenLabs key), so only the buttons work.</p>}
      {heard && <p className="quiet">Heard: “{heard}”</p>}
      {error && <p className="error" role="alert">{error}</p>}
    </section>
  );
}
