from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


@dataclass(frozen=True)
class ActivityEvent:
    ts: float
    event: str
    detail: str
    caller: str
    call_sid: str


@dataclass(frozen=True)
class HistogramBucket:
    start: float
    count: int


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
        try:
            yield conn
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    call_sid TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS events_ts ON events (ts)")
            conn.execute("CREATE INDEX IF NOT EXISTS events_event_ts ON events (event, ts)")
            conn.commit()

    def record(
        self,
        event: str,
        *,
        detail: str = "",
        caller: str = "",
        call_sid: str = "",
    ) -> None:
        item = ActivityEvent(
            ts=time.time(),
            event=event,
            detail=detail,
            caller=caller,
            call_sid=call_sid,
        )
        with self._lock:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO events (ts, event, detail, caller, call_sid) VALUES (?, ?, ?, ?, ?)",
                    (item.ts, item.event, item.detail, item.caller, item.call_sid),
                )
                conn.commit()

    def snapshot(self, recent_limit: int) -> tuple[dict[str, int], list[ActivityEvent]]:
        with self._connect() as conn:
            counts_rows = conn.execute(
                "SELECT event, COUNT(*) AS count FROM events GROUP BY event"
            ).fetchall()
            recent_rows = conn.execute(
                "SELECT ts, event, detail, caller, call_sid FROM events ORDER BY id DESC LIMIT ?",
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
                SELECT ts, event, detail, caller, call_sid FROM events
                WHERE event IN ({placeholders}) ORDER BY id DESC LIMIT 1
                """,
                events,
            ).fetchone()
        return _row_to_event(row) if row else None

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
        ts=float(row["ts"]),
        event=str(row["event"]),
        detail=str(row["detail"]),
        caller=str(row["caller"]),
        call_sid=str(row["call_sid"]),
    )
