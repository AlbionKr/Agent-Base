"""Append-only audit log in SQLite.

Two things live here:

* `events` — everything that happened, in order. The dashboard, the base view and
  the SSE stream read only this. Every animation maps to one event id.
* `actions` — one row per proposed action with its lifecycle status. The policy
  engine's history (`PastAction`) is built from it, so budgets and rate limits
  survive a restart.

Money is stored as decimal strings. Floats are refused at the door.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from app.policy.models import PastAction, Verdict


class ActionStatus(str, Enum):
    ALLOWED = "allowed"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    FAILED = "failed"
    DENIED = "denied"
    APPROVAL_DENIED = "approval_denied"
    EXPIRED = "expired"


# Everything that was not refused counts against budgets. A FAILED execution may
# still have moved money, and an ALLOWED or pending action reserves its amount so
# two concurrent proposals cannot both fit under the same remaining budget.
REFUSED = frozenset({ActionStatus.DENIED, ActionStatus.APPROVAL_DENIED, ActionStatus.EXPIRED})


@dataclass(frozen=True)
class AuditEvent:
    id: int
    at: datetime
    kind: str
    agent_id: str | None
    payload: dict[str, Any]

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "at": self.at.isoformat(),
            "kind": self.kind,
            "agent_id": self.agent_id,
            "payload": self.payload,
        }


@dataclass(frozen=True)
class ActionRecord:
    action_id: str
    agent_id: str
    at: datetime
    action_type: str
    destination: str
    amount_usd: Decimal
    verdict: Verdict
    status: ActionStatus
    action_json: dict[str, Any]


_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    at       TEXT NOT NULL,
    kind     TEXT NOT NULL,
    agent_id TEXT,
    payload  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS actions (
    action_id   TEXT PRIMARY KEY,
    agent_id    TEXT NOT NULL,
    at          TEXT NOT NULL,
    action_type TEXT NOT NULL,
    destination TEXT NOT NULL,
    amount_usd  TEXT NOT NULL,
    verdict     TEXT NOT NULL,
    status      TEXT NOT NULL,
    action_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS actions_agent_at ON actions (agent_id, at);
CREATE TABLE IF NOT EXISTS outflows (
    signature         TEXT NOT NULL,
    wallet            TEXT NOT NULL,
    agent_id          TEXT NOT NULL,
    at                TEXT NOT NULL,
    amount_units      TEXT NOT NULL,
    matched_action_id TEXT,
    PRIMARY KEY (signature, wallet)
);
"""


def _stamp(at: datetime) -> str:
    """UTC, fixed width, so string order equals time order."""
    if at.tzinfo is None:
        raise ValueError("audit timestamps must be timezone-aware")
    return at.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _unstamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _check_json(value: Any, path: str = "payload") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        raise TypeError(f"{path}: floats are not allowed in the audit log; use a decimal string")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError(f"{path}: keys must be strings")
            _check_json(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _check_json(item, f"{path}[{index}]")
        return
    raise TypeError(f"{path}: {type(value).__name__} is not JSON; convert it first")


def _dump(value: dict[str, Any]) -> str:
    _check_json(value)
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


class AuditLog:
    def __init__(self, path: str | Path = ":memory:"):
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._db.executescript(_SCHEMA)

    def close(self) -> None:
        self._db.close()

    # ------------------------------------------------------------- events

    def append(
        self, kind: str, payload: dict[str, Any], *, at: datetime, agent_id: str | None = None
    ) -> AuditEvent:
        body = _dump(payload)
        stamp = _stamp(at)
        with self._lock:
            cursor = self._db.execute(
                "INSERT INTO events (at, kind, agent_id, payload) VALUES (?, ?, ?, ?)",
                (stamp, kind, agent_id, body),
            )
            event_id = cursor.lastrowid
        return AuditEvent(
            id=event_id, at=_unstamp(stamp), kind=kind, agent_id=agent_id, payload=json.loads(body)
        )

    def events(
        self,
        *,
        after_id: int = 0,
        limit: int = 500,
        kind: str | None = None,
        agent_id: str | None = None,
    ) -> list[AuditEvent]:
        query = "SELECT id, at, kind, agent_id, payload FROM events WHERE id > ?"
        params: list[Any] = [after_id]
        if kind is not None:
            query += " AND kind = ?"
            params.append(kind)
        if agent_id is not None:
            query += " AND agent_id = ?"
            params.append(agent_id)
        query += " ORDER BY id LIMIT ?"
        params.append(limit)
        with self._lock:
            rows = self._db.execute(query, params).fetchall()
        return [
            AuditEvent(id=r[0], at=_unstamp(r[1]), kind=r[2], agent_id=r[3], payload=json.loads(r[4]))
            for r in rows
        ]

    def last_id(self) -> int:
        with self._lock:
            return self._db.execute("SELECT COALESCE(MAX(id), 0) FROM events").fetchone()[0]

    # ------------------------------------------------------------ actions

    def record_action(
        self,
        *,
        action_id: str,
        agent_id: str,
        at: datetime,
        action_type: str,
        destination: str,
        amount_usd: Decimal,
        verdict: Verdict,
        status: ActionStatus,
        action_json: dict[str, Any],
    ) -> None:
        if not isinstance(amount_usd, Decimal):
            raise TypeError("amount_usd must be a Decimal")
        row = (
            action_id,
            agent_id,
            _stamp(at),
            action_type,
            destination,
            str(amount_usd),
            verdict.value,
            status.value,
            _dump(action_json),
        )
        with self._lock:
            try:
                self._db.execute("INSERT INTO actions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
            except sqlite3.IntegrityError as exc:
                raise ValueError(f"action {action_id!r} is already recorded") from exc

    def transition(
        self, action_id: str, expected: Iterable[ActionStatus], new: ActionStatus
    ) -> bool:
        """Move an action to `new` only if it is currently in `expected`. Atomic."""
        allowed = [s.value for s in expected]
        marks = ",".join("?" for _ in allowed)
        with self._lock:
            cursor = self._db.execute(
                f"UPDATE actions SET status = ? WHERE action_id = ? AND status IN ({marks})",
                [new.value, action_id, *allowed],
            )
            return cursor.rowcount == 1

    def action(self, action_id: str) -> ActionRecord | None:
        with self._lock:
            row = self._db.execute(
                "SELECT action_id, agent_id, at, action_type, destination, amount_usd, verdict,"
                " status, action_json FROM actions WHERE action_id = ?",
                (action_id,),
            ).fetchone()
        return _action_record(row) if row else None

    def actions(self, agent_id: str, *, since: datetime) -> list[ActionRecord]:
        with self._lock:
            rows = self._db.execute(
                "SELECT action_id, agent_id, at, action_type, destination, amount_usd, verdict,"
                " status, action_json FROM actions WHERE agent_id = ? AND at >= ? ORDER BY at, rowid",
                (agent_id, _stamp(since)),
            ).fetchall()
        return [_action_record(row) for row in rows]

    def history(self, agent_id: str, *, since: datetime) -> tuple[PastAction, ...]:
        return tuple(
            PastAction(
                at=record.at,
                action_type=record.action_type,
                destination=record.destination,
                amount_usd=record.amount_usd,
                verdict=record.verdict,
                executed=record.status not in REFUSED,
            )
            for record in self.actions(agent_id, since=since)
        )

    def expire_pending_approvals(self) -> list[str]:
        """Called at startup: approvals do not survive a restart, and silence means no."""
        with self._lock:
            rows = self._db.execute(
                "SELECT action_id FROM actions WHERE status = ? ORDER BY at, rowid",
                (ActionStatus.PENDING_APPROVAL.value,),
            ).fetchall()
            self._db.execute(
                "UPDATE actions SET status = ? WHERE status = ?",
                (ActionStatus.EXPIRED.value, ActionStatus.PENDING_APPROVAL.value),
            )
        return [row[0] for row in rows]

    # ----------------------------------------------------------- outflows

    def record_outflow(
        self,
        *,
        signature: str,
        wallet: str,
        agent_id: str,
        at: datetime,
        amount_units: int,
        matched_action_id: str | None,
    ) -> bool:
        """Remember an onchain outflow. False if this one was already recorded."""
        with self._lock:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO outflows VALUES (?, ?, ?, ?, ?, ?)",
                (signature, wallet, agent_id, _stamp(at), str(amount_units), matched_action_id),
            )
            return cursor.rowcount == 1

    def has_outflow(self, signature: str, wallet: str) -> bool:
        with self._lock:
            row = self._db.execute(
                "SELECT 1 FROM outflows WHERE signature = ? AND wallet = ?", (signature, wallet)
            ).fetchone()
        return row is not None

    def matched_action_ids(self, agent_id: str) -> set[str]:
        with self._lock:
            rows = self._db.execute(
                "SELECT matched_action_id FROM outflows"
                " WHERE agent_id = ? AND matched_action_id IS NOT NULL",
                (agent_id,),
            ).fetchall()
        return {row[0] for row in rows}

    def outflow_times(self, agent_id: str, *, since: datetime) -> list[datetime]:
        with self._lock:
            rows = self._db.execute(
                "SELECT at FROM outflows WHERE agent_id = ? AND at >= ? ORDER BY at",
                (agent_id, _stamp(since)),
            ).fetchall()
        return [_unstamp(row[0]) for row in rows]


def _action_record(row: tuple) -> ActionRecord:
    return ActionRecord(
        action_id=row[0],
        agent_id=row[1],
        at=_unstamp(row[2]),
        action_type=row[3],
        destination=row[4],
        amount_usd=Decimal(row[5]),
        verdict=Verdict(row[6]),
        status=ActionStatus(row[7]),
        action_json=json.loads(row[8]),
    )
