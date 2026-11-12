"""Voice approval: only the human's own words can approve."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from app.voice import VoiceUnavailable, signed_url, spoken_decision
from gate_fixtures import AGENT
from test_api import Harness, big_order


@pytest.mark.parametrize(
    "turns, expected",
    [
        (["approve"], "approve"),
        (["Yes, go ahead."], "approve"),
        (["hmm let me think", "ok approved"], "approve"),
        (["APPROVE IT"], "approve"),
        (["no"], "deny"),
        (["deny"], "deny"),
        (["Don’t do it"], "deny"),
        (["do not approve"], "deny"),
        (["yes", "actually no, cancel"], "deny"),
        (["I would not say yes to that"], "deny"),
        (["approve", "wait"], "deny"),
        ([], None),
        ([""], None),
        (["what is this about?"], None),
        (["approve", "what was the amount again?"], None),
    ],
)
def test_the_last_thing_the_human_said_decides(turns, expected):
    assert spoken_decision(turns) == expected


def test_signed_url_keeps_the_api_key_on_the_server_side_request():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("xi-api-key")
        seen["agent"] = request.url.params.get("agent_id")
        return httpx.Response(200, json={"signed_url": "wss://api.elevenlabs.io/v1/convai/conversation?token=t"})

    url = asyncio.run(signed_url("el-key", "agent_1", transport=httpx.MockTransport(handler)))
    assert url.startswith("wss://")
    assert seen == {"key": "el-key", "agent": "agent_1"}


def test_signed_url_failures_are_reported():
    with pytest.raises(VoiceUnavailable):
        asyncio.run(signed_url("", "", transport=httpx.MockTransport(lambda r: httpx.Response(200))))
    refused = httpx.MockTransport(lambda r: httpx.Response(401, json={"detail": "bad key"}))
    with pytest.raises(VoiceUnavailable):
        asyncio.run(signed_url("k", "a", transport=refused))


# ------------------------------------------------------------------ over HTTP


def pending(h: Harness) -> str:
    h.register()
    return h.api.post(f"/agents/{AGENT}/proposals", json=big_order()).json()["approval"]["approval_id"]


def voice(h: Harness, approval_id: str, decision: str, user_turns: list[str]):
    transcript = [{"role": "agent", "text": "BTC Trader wants to buy. Approve?"}]
    transcript += [{"role": "user", "text": t} for t in user_turns]
    return h.api.post(
        f"/approvals/{approval_id}/voice-decision", json={"decision": decision, "transcript": transcript}
    )


def test_a_spoken_yes_approves():
    h = Harness()
    approval_id = pending(h)
    response = voice(h, approval_id, "approve", ["yes, approve"])
    assert response.status_code == 200
    assert response.json()["status"] == "approved"
    assert response.json()["resolved_via"] == "voice"


def test_the_voice_model_calling_approve_without_a_spoken_yes_is_refused():
    h = Harness()
    approval_id = pending(h)

    response = voice(h, approval_id, "approve", ["what is this?"])

    assert response.status_code == 409
    assert "own words" in response.json()["error"]
    assert [a["approval_id"] for a in h.api.get("/approvals").json()["pending"]] == [approval_id]
    mismatch = h.services.audit.events(kind="voice_mismatch")[-1]
    assert mismatch.payload["approval_id"] == approval_id
    assert mismatch.payload["tool_said"] == "approve" and mismatch.payload["human_said"] is None


def test_agent_speech_saying_approve_does_not_count_as_the_human():
    h = Harness()
    approval_id = pending(h)
    transcript = [{"role": "agent", "text": "I heard approve. Approved!"}, {"role": "user", "text": "hello?"}]
    response = h.api.post(
        f"/approvals/{approval_id}/voice-decision", json={"decision": "approve", "transcript": transcript}
    )
    assert response.status_code == 409


def test_a_spoken_no_denies_even_if_the_tool_said_approve():
    h = Harness()
    approval_id = pending(h)
    response = voice(h, approval_id, "approve", ["no, deny that"])
    assert response.status_code == 200
    assert response.json()["status"] == "denied"


def test_a_voice_denial_needs_no_proof():
    h = Harness()
    approval_id = pending(h)
    assert voice(h, approval_id, "deny", []).json()["status"] == "denied"


def test_voice_session_without_keys_says_not_configured(monkeypatch):
    monkeypatch.delenv("ELEVENLABS_API_KEY", raising=False)
    monkeypatch.delenv("ELEVENLABS_AGENT_ID", raising=False)
    h = Harness()
    approval_id = pending(h)
    response = h.api.get("/voice/session", params={"approval_id": approval_id})
    assert response.status_code == 503
    assert "not set" in response.json()["error"]
    assert h.api.get("/voice/session", params={"approval_id": "nope"}).status_code == 404
