"""Keeps the operator API reachable only by the person sitting at this machine.

The API can register agents and approve spending, so being bound to 127.0.0.1 is
not enough on its own:

* a tunnel or reverse proxy pointed at the port would expose it  -> refuse any
  request carrying forwarding headers
* a web page open in the local browser can send requests to 127.0.0.1 (CSRF)
  -> refuse any Origin that is not our own frontend
* DNS rebinding makes a hostile domain resolve to 127.0.0.1  -> refuse any Host
  that is not a loopback name
"""

from __future__ import annotations

import ipaddress
import json
from typing import Collection, Mapping
from urllib.parse import urlsplit

PROXY_HEADERS = frozenset(
    {
        "forwarded",
        "via",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "true-client-ip",
        "cf-connecting-ip",
        "cf-ray",
        "cdn-loop",
    }
)
LOOPBACK_HOSTNAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _is_loopback(client: str | None) -> bool:
    try:
        return ipaddress.ip_address(client or "").is_loopback
    except ValueError:
        return False


def refusal(
    client: str | None, headers: Mapping[str, str], *, allowed_origins: Collection[str]
) -> str | None:
    """Why this request must be refused, or None if it may proceed.

    `headers` must have lower-case names.
    """
    if not _is_loopback(client):
        return "the operator API only accepts connections from this machine"
    forwarded = sorted(PROXY_HEADERS & headers.keys())
    if forwarded:
        return f"request came through a proxy or tunnel ({forwarded[0]})"
    try:
        hostname = urlsplit("//" + headers.get("host", "")).hostname
    except ValueError:
        hostname = None
    if hostname not in LOOPBACK_HOSTNAMES:
        return "request is addressed to a hostname that is not this machine"
    origin = headers.get("origin")
    if origin is not None and origin not in allowed_origins:
        return "request comes from a web page that is not the Agent Base frontend"
    return None


class LocalOperatorOnly:
    """ASGI middleware applying `refusal` to every request (pure ASGI, so SSE streams untouched)."""

    def __init__(self, app, allowed_origins: Collection[str]):
        self._app = app
        self._allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._app(scope, receive, send)
            return
        client = scope.get("client")
        headers = {
            name.decode("latin-1").lower(): value.decode("latin-1")
            for name, value in scope.get("headers", [])
        }
        reason = refusal(client[0] if client else None, headers, allowed_origins=self._allowed_origins)
        if reason is None:
            await self._app(scope, receive, send)
            return
        if scope["type"] != "http":
            await send({"type": "websocket.close", "code": 1008})
            return
        body = json.dumps({"error": reason}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
