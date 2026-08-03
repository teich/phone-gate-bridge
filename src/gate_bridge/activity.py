from __future__ import annotations

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping


@dataclass(frozen=True)
class ActivityEvent:
    ts: float
    event: str
    detail: str
    caller: str
    call_sid: str
    id: int = 0
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HistogramBucket:
    start: float
    count: int


@dataclass(frozen=True)
class HoldWindow:
    """A finite hold-open request that is still active."""

    started_at: float
    expires_at: float
    caller: str
    call_sid: str

    @property
    def duration_seconds(self) -> float:
        return max(0.0, self.expires_at - self.started_at)


@dataclass(frozen=True)
class ActionAttempt:
    """Durable idempotency record for a controller-changing call action."""

    call_sid: str
    action: str
    caller: str
    status: str
    requested_at: float
    completed_at: float | None = None
    detail: str = ""


class ActivityStore:
    def __init__(self, db_path: str) -> None:
        self._db_path = Path(db_path)
        self._lock = threading.Lock()
        parent = self._db_path.parent
        if str(parent) and str(parent) != ".":
            parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Open a connection and always close it.

        `sqlite3.Connection` as a context manager commits but does not close,
        which leaks handles under the dashboard's polling.
        """
        conn = sqlite3.connect(str(self._db_path), timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            # The webhook writes while one or more dashboards may be polling.
            # WAL keeps those readers from blocking the short activity writes.
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    call_sid TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}'
                )
                """
            )
            columns = {
                str(row["name"])
                for row in conn.execute("PRAGMA table_info(events)").fetchall()
            }
            if "data_json" not in columns:
                conn.execute(
                    "ALTER TABLE events ADD COLUMN data_json TEXT NOT NULL DEFAULT '{}'"
                )
            conn.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS events_event_ts ON events (event, ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS events_call_sid ON events (call_sid)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS action_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    call_sid TEXT NOT NULL,
                    action TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    status TEXT NOT NULL,
                    requested_at REAL NOT NULL,
                    completed_at REAL,
                    detail TEXT NOT NULL DEFAULT '',
                    UNIQUE (call_sid, action)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS action_attempts_requested_at "
                "ON action_attempts (requested_at)"
            )
            conn.commit()

    def record(
        self,
        event: str,
        *,
        detail: str = "",
        caller: str = "",
        call_sid: str = "",
        data: Mapping[str, Any] | None = None,
        at: float | None = None,
    ) -> ActivityEvent:
        item = ActivityEvent(
            ts=time.time() if at is None else at,
            event=event,
            detail=detail,
            caller=caller,
            call_sid=call_sid,
            data=dict(data or {}),
        )
        encoded_data = json.dumps(
            item.data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT INTO events
                        (ts, event, detail, caller, call_sid, data_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item.ts,
                        item.event,
                        item.detail,
                        item.caller,
                        item.call_sid,
                        encoded_data,
                    ),
                )
                conn.commit()
        return ActivityEvent(
            id=int(cursor.lastrowid),
            ts=item.ts,
            event=item.event,
            detail=item.detail,
            caller=item.caller,
            call_sid=item.call_sid,
            data=item.data,
        )

    def record_hold_open(
        self,
        *,
        duration_seconds: int,
        caller: str = "",
        call_sid: str = "",
        at: float | None = None,
    ) -> ActivityEvent:
        if duration_seconds <= 0:
            raise ValueError("duration_seconds must be positive")
        started_at = time.time() if at is None else at
        expires_at = started_at + duration_seconds
        return self.record(
            "hold_open",
            detail=str(max(1, round(duration_seconds / 60))),
            caller=caller,
            call_sid=call_sid,
            data={
                "duration_seconds": duration_seconds,
                "expires_at": expires_at,
            },
            at=started_at,
        )

    def record_hold_cleared(
        self,
        *,
        caller: str = "",
        call_sid: str = "",
        reason: str = "",
        at: float | None = None,
    ) -> ActivityEvent:
        return self.record(
            "hold_cleared",
            detail=reason,
            caller=caller,
            call_sid=call_sid,
            data={"reason": reason} if reason else {},
            at=at,
        )

    def snapshot(self, recent_limit: int) -> tuple[dict[str, int], list[ActivityEvent]]:
        with self._connect() as conn:
            counts_rows = conn.execute(
                "SELECT event, COUNT(*) AS count FROM events GROUP BY event"
            ).fetchall()
            recent_rows = conn.execute(
                """
                SELECT id, ts, event, detail, caller, call_sid, data_json
                FROM events ORDER BY id DESC LIMIT ?
                """,
                (max(1, recent_limit),),
            ).fetchall()
        counts = {str(row["event"]): int(row["count"]) for row in counts_rows}
        return counts, [_row_to_event(row) for row in recent_rows]

    def count_since(self, events: tuple[str, ...], since: float) -> int:
        if not events:
            return 0
        placeholders = ",".join("?" for _ in events)
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT COUNT(*) AS count FROM events WHERE event IN ({placeholders}) AND ts >= ?",
                (*events, since),
            ).fetchone()
        return int(row["count"]) if row else 0

    def distinct_callers_since(self, events: tuple[str, ...], since: float) -> int:
        if not events:
            return 0
        placeholders = ",".join("?" for _ in events)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT COUNT(DISTINCT caller) AS count FROM events
                WHERE event IN ({placeholders}) AND ts >= ? AND caller != ''
                """,
                (*events, since),
            ).fetchone()
        return int(row["count"]) if row else 0

    def latest(self, events: tuple[str, ...]) -> ActivityEvent | None:
        if not events:
            return None
        placeholders = ",".join("?" for _ in events)
        with self._connect() as conn:
            row = conn.execute(
                f"""
                SELECT id, ts, event, detail, caller, call_sid, data_json FROM events
                WHERE event IN ({placeholders}) ORDER BY id DESC LIMIT 1
                """,
                events,
            ).fetchone()
        return _row_to_event(row) if row else None

    def latest_id(self) -> int:
        with self._connect() as conn:
            row = conn.execute("SELECT COALESCE(MAX(id), 0) AS id FROM events").fetchone()
        return int(row["id"]) if row else 0

    def active_hold(self, *, now: float | None = None) -> HoldWindow | None:
        """Return the latest hold only when it has not been cleared or expired.

        The JSON payload is the durable contract for new events. The `detail`
        fallback keeps early `hold_open` rows (which stored whole minutes as a
        string) readable after upgrading an existing database.
        """
        now = time.time() if now is None else now
        latest = self.latest(("hold_open", "hold_cleared"))
        if latest is None or latest.event == "hold_cleared":
            return None

        expires_at = _as_float(latest.data.get("expires_at"))
        if expires_at is None:
            duration_seconds = _as_float(latest.data.get("duration_seconds"))
            if duration_seconds is None:
                try:
                    duration_seconds = float(int(latest.detail.strip()) * 60)
                except (TypeError, ValueError):
                    return None
            expires_at = latest.ts + duration_seconds

        if expires_at <= now:
            return None
        return HoldWindow(
            started_at=latest.ts,
            expires_at=expires_at,
            caller=latest.caller,
            call_sid=latest.call_sid,
        )

    def begin_action(
        self,
        *,
        call_sid: str,
        action: str,
        caller: str,
        at: float | None = None,
    ) -> tuple[ActionAttempt, bool]:
        """Reserve an action once for a Twilio call.

        Returns `(attempt, created)`. A duplicate callback receives the existing
        result and must not call the controller again.
        """
        if not call_sid.strip():
            raise ValueError("call_sid is required for an idempotent action")
        if not action.strip():
            raise ValueError("action is required")
        requested_at = time.time() if at is None else at

        with self._lock:
            with self._connect() as conn:
                cursor = conn.execute(
                    """
                    INSERT OR IGNORE INTO action_attempts
                        (call_sid, action, caller, status, requested_at)
                    VALUES (?, ?, ?, 'pending', ?)
                    """,
                    (call_sid, action, caller, requested_at),
                )
                created = cursor.rowcount == 1
                row = conn.execute(
                    """
                    SELECT call_sid, action, caller, status, requested_at,
                           completed_at, detail
                    FROM action_attempts
                    WHERE call_sid = ? AND action = ?
                    """,
                    (call_sid, action),
                ).fetchone()
                conn.commit()
        if row is None:
            raise RuntimeError("action reservation was not readable")
        return _row_to_action(row), created

    def finish_action(
        self,
        *,
        call_sid: str,
        action: str,
        status: str,
        detail: str = "",
        at: float | None = None,
    ) -> ActionAttempt:
        if status not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid final action status")
        completed_at = time.time() if at is None else at
        with self._lock:
            with self._connect() as conn:
                row = _finish_action_row(
                    conn,
                    call_sid=call_sid,
                    action=action,
                    status=status,
                    detail=detail,
                    completed_at=completed_at,
                )
                conn.commit()
        return _row_to_action(row)

    def finish_action_with_event(
        self,
        *,
        call_sid: str,
        action: str,
        status: str,
        event: str,
        event_detail: str = "",
        caller: str = "",
        data: Mapping[str, Any] | None = None,
        at: float | None = None,
    ) -> tuple[ActionAttempt, ActivityEvent]:
        """Finalize a controller action and its visible audit row atomically."""
        if status not in {"succeeded", "failed", "unknown"}:
            raise ValueError("invalid final action status")
        completed_at = time.time() if at is None else at
        event_data = dict(data or {})
        encoded_data = json.dumps(
            event_data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

        with self._lock:
            with self._connect() as conn:
                action_row = _finish_action_row(
                    conn,
                    call_sid=call_sid,
                    action=action,
                    status=status,
                    detail=event_detail,
                    completed_at=completed_at,
                )
                cursor = conn.execute(
                    """
                    INSERT INTO events
                        (ts, event, detail, caller, call_sid, data_json)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        completed_at,
                        event,
                        event_detail,
                        caller,
                        call_sid,
                        encoded_data,
                    ),
                )
                conn.commit()

        return (
            _row_to_action(action_row),
            ActivityEvent(
                id=int(cursor.lastrowid),
                ts=completed_at,
                event=event,
                detail=event_detail,
                caller=caller,
                call_sid=call_sid,
                data=event_data,
            ),
        )

    def recover_pending_actions(
        self,
        *,
        reason: str = "service restarted before the result was recorded",
        at: float | None = None,
    ) -> int:
        """Mark interrupted actions unknown without risking a repeated command."""
        recovered_at = time.time() if at is None else at
        with self._lock:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT call_sid, action, caller
                    FROM action_attempts
                    WHERE status = 'pending'
                    ORDER BY id
                    """
                ).fetchall()
                if not rows:
                    return 0

                conn.execute(
                    """
                    UPDATE action_attempts
                    SET status = 'unknown', completed_at = ?, detail = ?
                    WHERE status = 'pending'
                    """,
                    (recovered_at, reason),
                )
                for row in rows:
                    action = str(row["action"])
                    conn.execute(
                        """
                        INSERT INTO events
                            (ts, event, detail, caller, call_sid, data_json)
                        VALUES (?, 'action_unknown', ?, ?, ?, ?)
                        """,
                        (
                            recovered_at,
                            f"{action}: {reason}",
                            str(row["caller"]),
                            str(row["call_sid"]),
                            json.dumps(
                                {"action": action, "reason": reason},
                                separators=(",", ":"),
                                sort_keys=True,
                            ),
                        ),
                    )
                conn.commit()
        return len(rows)

    def hourly_histogram(
        self,
        events: tuple[str, ...],
        *,
        hours: int = 24,
        now: float | None = None,
    ) -> list[HistogramBucket]:
        """Counts per hour for the trailing `hours`, oldest bucket first.

        Buckets are aligned to local wall-clock hours so the strip lines up with
        the timestamps shown in the activity feed.
        """
        hours = max(1, hours)
        now = time.time() if now is None else now
        local = time.localtime(now)
        current_hour_start = now - (local.tm_min * 60 + local.tm_sec + now % 1)
        starts = [current_hour_start - (hours - 1 - i) * 3600 for i in range(hours)]
        counts = [0] * hours

        if not events:
            return [HistogramBucket(start=s, count=0) for s in starts]

        placeholders = ",".join("?" for _ in events)
        with self._connect() as conn:
            rows = conn.execute(
                f"SELECT ts FROM events WHERE event IN ({placeholders}) AND ts >= ?",
                (*events, starts[0]),
            ).fetchall()

        for row in rows:
            index = int((float(row["ts"]) - starts[0]) // 3600)
            if 0 <= index < hours:
                counts[index] += 1

        return [HistogramBucket(start=s, count=c) for s, c in zip(starts, counts)]


def _row_to_event(row: sqlite3.Row) -> ActivityEvent:
    return ActivityEvent(
        id=int(row["id"]),
        ts=float(row["ts"]),
        event=str(row["event"]),
        detail=str(row["detail"]),
        caller=str(row["caller"]),
        call_sid=str(row["call_sid"]),
        data=_decode_data(str(row["data_json"])),
    )


def _finish_action_row(
    conn: sqlite3.Connection,
    *,
    call_sid: str,
    action: str,
    status: str,
    detail: str,
    completed_at: float,
) -> sqlite3.Row:
    cursor = conn.execute(
        """
        UPDATE action_attempts
        SET status = ?, completed_at = ?, detail = ?
        WHERE call_sid = ? AND action = ? AND status = 'pending'
        """,
        (status, completed_at, detail, call_sid, action),
    )
    if cursor.rowcount != 1:
        raise ValueError("action is missing or already finished")
    row = conn.execute(
        """
        SELECT call_sid, action, caller, status, requested_at,
               completed_at, detail
        FROM action_attempts
        WHERE call_sid = ? AND action = ?
        """,
        (call_sid, action),
    ).fetchone()
    if row is None:
        raise RuntimeError("finished action was not readable")
    return row


def _row_to_action(row: sqlite3.Row) -> ActionAttempt:
    completed = row["completed_at"]
    return ActionAttempt(
        call_sid=str(row["call_sid"]),
        action=str(row["action"]),
        caller=str(row["caller"]),
        status=str(row["status"]),
        requested_at=float(row["requested_at"]),
        completed_at=float(completed) if completed is not None else None,
        detail=str(row["detail"]),
    )


def _decode_data(raw: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
