"""Voice approval with ElevenLabs Agents.

Flow: an action needs approval -> the browser asks the backend for a signed
conversation URL -> the ElevenLabs agent reads the request aloud -> the human
answers -> the agent calls the client tool `approve_action` or `deny_action` ->
the browser reports that to the backend together with what the human said.

Two things keep the voice channel from becoming an approval bypass:

* The tools carry no parameters. The backend binds the decision to the one
  pending approval the session was opened for; the voice model cannot choose
  what gets approved.
* An approval is accepted only if the human's own transcribed words say yes
  (`spoken_decision`). If text inside a market title or a rationale talks the
  voice model into calling `approve_action`, there is no matching "yes" from the
  human and the approval is refused. A denial needs no such proof.

The prompt the agent speaks is built from the validated action only
(`describe_action`), never from the LLM's free-text rationale.

NOT YET VERIFIED LIVE: needs ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID.
"""

from __future__ import annotations

import re
from typing import Sequence

import httpx

SIGNED_URL_ENDPOINT = "https://api.elevenlabs.io/v1/convai/conversation/get-signed-url"

_YES = re.compile(
    r"\b(approve|approved|approving|yes|yeah|yep|confirm|confirmed|go ahead|do it|allow|allowed)\b"
)
_NO = re.compile(
    r"\b(no|nope|don't|do not|dont|deny|denied|reject|rejected|decline|declined|stop|cancel|"
    r"never|not|wait|hold on)\b"
)


def spoken_decision(user_turns: Sequence[str]) -> str | None:
    """What the human said, judged from their LAST non-empty utterance only.

    "approve" needs an affirmative and no negative in that utterance; anything
    with a negative is "deny"; anything else is None (not a decision).
    """
    spoken = [turn.strip().lower() for turn in user_turns if turn and turn.strip()]
    if not spoken:
        return None
    last = spoken[-1].replace("’", "'")
    if _NO.search(last):
        return "deny"
    if _YES.search(last):
        return "approve"
    return None


class VoiceUnavailable(Exception):
    pass


async def signed_url(api_key: str, agent_id: str, *, transport: httpx.AsyncBaseTransport | None = None) -> str:
    """Ask ElevenLabs for a short-lived conversation URL. The API key never reaches the browser."""
    if not api_key or not agent_id:
        raise VoiceUnavailable("ELEVENLABS_API_KEY and ELEVENLABS_AGENT_ID are not set")
    async with httpx.AsyncClient(timeout=15, transport=transport) as http:
        response = await http.get(
            SIGNED_URL_ENDPOINT, params={"agent_id": agent_id}, headers={"xi-api-key": api_key}
        )
    if response.status_code != 200:
        raise VoiceUnavailable(f"ElevenLabs refused the session: HTTP {response.status_code}")
    url = response.json().get("signed_url")
    if not isinstance(url, str) or not url.startswith("wss://"):
        raise VoiceUnavailable("ElevenLabs returned no signed URL")
    return url
