"""Dashboard domain state, JSON contract, and compiled asset loading.

The browser UI lives in ``frontend/`` and is built into
``gate_bridge/static/dashboard``. This module deliberately knows nothing about
React or visual labels: it exposes semantic gate/activity data and serves the
versioned frontend bundle.
"""

from __future__ import annotations

import json
import mimetypes
import re
import time
from dataclasses import dataclass, field, replace
from functools import lru_cache
from pathlib import Path, PurePosixPath
from typing import Any

from gate_bridge.activity import ActivityEvent, ActivityStore, HoldWindow

SCHEMA_VERSION = 1
DASHBOARD_DIR = Path(__file__).parent / "static" / "dashboard"

# How long after an unlock pulse the gate is still shown as "opening". The
# UniFi relay latches for a few seconds; this keeps the status honest without
# needing to poll the controller faster.
UNLOCK_PULSE_SECONDS = 12.0

OPEN_EVENTS = ("unlock_success",)
DENIED_EVENTS = (
    "caller_blocked",
    "signature_invalid",
    "dashboard_denied",
    "action_unauthorized",
)
ERROR_EVENTS = (
    "unlock_failed",
    "allowed_callers_error",
    "action_failed",
    "action_unknown",
)
_CALL_EVENT_PRIORITY = {
    "twilio_request": 10,
    "caller_prompted": 20,
    "invalid_digit": 80,
    "caller_blocked": 90,
    "action_unauthorized": 90,
    "unlock_failed": 100,
    "action_failed": 100,
    "action_unknown": 105,
    "unlock_success": 110,
    "hold_open": 110,
    "hold_cleared": 110,
}


@dataclass(frozen=True)
class GateStatus:
    """Physical gate signals plus any active control intent."""

    state: str = "unknown"  # secured | opening | open | held_open | unknown
    position: str = "unknown"  # open | closed | unknown
    relay: str = "unknown"  # locked | unlocked | unknown
    hold: HoldWindow | None = None
    opening_expires_at: float | None = None
    available: bool = True
    error: str = ""

    def to_json(self) -> dict[str, Any]:
        active_hold = None
        if self.hold is not None:
            active_hold = {
                "started_at": self.hold.started_at,
                "expires_at": self.hold.expires_at,
                "caller": self.hold.caller,
                "call_sid": self.hold.call_sid,
            }
        return {
            "state": self.state,
            "position": self.position,
            "relay": self.relay,
            "active_hold": active_hold,
            "opening_expires_at": self.opening_expires_at,
            "available": self.available,
            "error": self.error,
        }


@dataclass(frozen=True)
class DashboardState:
    door_name: str
    gate: GateStatus
    revision: int = 0
    stats: list[dict[str, Any]] = field(default_factory=list)
    chart: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    last_open: dict[str, Any] | None = None
    generated_at: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "server_time": self.generated_at,
            "revision": self.revision,
            "door": self.door_name,
            "gate": self.gate.to_json(),
            "last_open": self.last_open,
            "stats": self.stats,
            "chart": self.chart,
            "events": self.events,
        }


@dataclass(frozen=True)
class DashboardAsset:
    body: bytes
    content_type: str
    cache_control: str


def parse_door_status(door: dict[str, Any], now: float | None = None) -> GateStatus:
    """Map a UniFi Access door record onto normalized physical signals."""
    del now  # Kept as an injectable argument for API compatibility and tests.
    raw_position = str(door.get("door_position_status") or "").strip().lower()
    raw_relay = str(door.get("door_lock_relay_status") or "").strip().lower()

    position = {
        "open": "open",
        "close": "closed",
        "closed": "closed",
    }.get(raw_position, "unknown")
    relay = {
        "unlock": "unlocked",
        "unlocked": "unlocked",
        "lock": "locked",
        "locked": "locked",
    }.get(raw_relay, "unknown")

    if position == "open":
        state = "open"
    elif relay == "unlocked":
        state = "opening"
    elif relay == "locked" or position == "closed":
        state = "secured"
    else:
        return GateStatus(
            state="unknown",
            position=position,
            relay=relay,
            available=False,
            error="Door reported no lock or position status",
        )
    return GateStatus(state=state, position=position, relay=relay)


def build_gate_status(
    *,
    door: dict[str, Any] | None,
    door_error: str,
    last_unlock: ActivityEvent | None,
    active_hold: HoldWindow | None = None,
    now: float | None = None,
) -> GateStatus:
    now = time.time() if now is None else now

    if door is None:
        status = GateStatus(
            state="unknown",
            available=False,
            error=door_error or "Door status unavailable",
        )
    else:
        status = parse_door_status(door, now=now)

    # A momentary unlock can finish between polls, leaving no trace on the
    # door record, so surface a recent successful pulse from the activity log.
    if status.state in {"secured", "unknown"} and last_unlock is not None:
        if 0 <= now - last_unlock.ts <= UNLOCK_PULSE_SECONDS:
            status = replace(
                status,
                state="opening",
                opening_expires_at=last_unlock.ts + UNLOCK_PULSE_SECONDS,
            )
    elif status.state == "opening" and last_unlock is not None:
        if 0 <= now - last_unlock.ts <= UNLOCK_PULSE_SECONDS:
            status = replace(
                status,
                opening_expires_at=last_unlock.ts + UNLOCK_PULSE_SECONDS,
            )

    # Hold intent is separate from the physical signals. Only call the gate
    # held-open when a finite, uncleared hold overlaps an open/opening signal.
    if active_hold is not None and active_hold.expires_at > now:
        if status.state in {"open", "opening"}:
            status = replace(status, state="held_open", hold=active_hold)
    return status


def _describe_caller(number: str, caller_names: dict[str, str]) -> str:
    return caller_names.get(number, "") if number else ""


def _event_id(item: ActivityEvent) -> str:
    if item.call_sid:
        return f"call:{item.call_sid}"
    if item.id:
        return f"event:{item.id}"
    # Only synthetic/unit-test events lack a database id.
    return f"event:legacy:{item.ts}:{item.event}:{item.caller}"


def _event_json(item: ActivityEvent, caller_names: dict[str, str]) -> dict[str, Any]:
    return {
        "id": _event_id(item),
        "raw_id": item.id,
        "ts": item.ts,
        "event": item.event,
        "caller": item.caller,
        "name": _describe_caller(item.caller, caller_names),
        "detail": item.detail,
        "data": item.data,
        "call_sid": item.call_sid,
        "steps": [],
        "count": 1,
    }


def group_events(
    recent: list[ActivityEvent], caller_names: dict[str, str]
) -> list[dict[str, Any]]:
    """Collapse the activity rows for one Twilio call into one stable entry.

    ``recent`` must be newest-first. A row keeps the same ``call:<CallSid>`` id
    while the call advances from received to prompted to its final outcome, so
    React can update it in place instead of replacing the DOM node.
    """
    grouped: list[dict[str, Any] | list[ActivityEvent]] = []
    by_sid: dict[str, list[ActivityEvent]] = {}

    for item in recent:
        if not item.call_sid:
            grouped.append(_event_json(item, caller_names))
            continue

        items = by_sid.get(item.call_sid)
        if items is None:
            items = []
            by_sid[item.call_sid] = items
            grouped.append(items)
        items.append(item)

    result: list[dict[str, Any]] = []
    for entry in grouped:
        if isinstance(entry, dict):
            result.append(entry)
            continue

        # Twilio may retry a callback after its first delivery succeeded. The
        # newest raw row can therefore be another `twilio_request`; choose the
        # strongest outcome rather than assuming the last row is terminal.
        outcome = max(
            entry,
            key=lambda item: (
                _CALL_EVENT_PRIORITY.get(item.event, 60),
                item.id,
                item.ts,
            ),
        )
        rendered = _event_json(outcome, caller_names)
        rendered["steps"] = [
            item.event for item in reversed(entry) if item is not outcome
        ]
        rendered["count"] = len(entry)
        for item in entry:
            if not rendered["name"]:
                rendered["name"] = _describe_caller(item.caller, caller_names)
            if not rendered["caller"]:
                rendered["caller"] = item.caller
        result.append(rendered)
    return result


def build_dashboard_state(
    *,
    store: ActivityStore,
    door_name: str,
    gate: GateStatus,
    caller_names: dict[str, str],
    recent_limit: int,
    now: float | None = None,
) -> DashboardState:
    now = time.time() if now is None else now
    local = time.localtime(now)
    midnight = now - (local.tm_hour * 3600 + local.tm_min * 60 + local.tm_sec)
    week_ago = now - 7 * 86400

    _, recent = store.snapshot(recent_limit)
    buckets = store.hourly_histogram(OPEN_EVENTS, hours=24, now=now)
    bucket_counts = [bucket.count for bucket in buckets]

    last_unlock = store.latest(OPEN_EVENTS)
    last_open = None
    if last_unlock is not None:
        last_open = {
            "ts": last_unlock.ts,
            "caller": last_unlock.caller,
            "name": _describe_caller(last_unlock.caller, caller_names),
            "detail": last_unlock.detail,
        }

    stats = [
        {
            "key": "opens_today",
            "value": store.count_since(OPEN_EVENTS, midnight),
        },
        {
            "key": "opens_week",
            "value": store.count_since(OPEN_EVENTS, week_ago),
        },
        {
            "key": "callers_week",
            "value": store.distinct_callers_since(OPEN_EVENTS, week_ago),
        },
        {
            "key": "denied_week",
            "value": store.count_since(DENIED_EVENTS, week_ago),
        },
        {
            "key": "errors_week",
            "value": store.count_since(ERROR_EVENTS, week_ago),
        },
    ]

    return DashboardState(
        door_name=door_name,
        gate=gate,
        revision=store.latest_id(),
        stats=stats,
        chart={
            "buckets": [
                {"start": bucket.start, "count": bucket.count} for bucket in buckets
            ],
            "max": max(bucket_counts) if bucket_counts else 0,
            "total": sum(bucket_counts),
        },
        events=group_events(recent, caller_names),
        last_open=last_open,
        generated_at=now,
    )


def render_dashboard_json(state: DashboardState) -> bytes:
    return json.dumps(state.to_json(), separators=(",", ":")).encode("utf-8")


@lru_cache(maxsize=64)
def load_dashboard_asset(request_path: str) -> DashboardAsset | None:
    """Load a safe path from the compiled Vite bundle."""
    if request_path in {"/dashboard", "/dashboard/"}:
        relative = PurePosixPath("index.html")
    elif request_path.startswith("/dashboard/"):
        relative = PurePosixPath(request_path.removeprefix("/dashboard/"))
    else:
        return None

    if (
        not relative.parts
        or relative.is_absolute()
        or ".." in relative.parts
        or relative.parts[0] == "api"
    ):
        return None

    candidate = DASHBOARD_DIR.joinpath(*relative.parts)
    try:
        candidate.resolve().relative_to(DASHBOARD_DIR.resolve())
    except ValueError:
        return None
    if not candidate.is_file():
        return None

    guessed, _ = mimetypes.guess_type(candidate.name)
    content_type = guessed or "application/octet-stream"
    if content_type.startswith("text/") or content_type in {
        "application/javascript",
        "application/json",
        "image/svg+xml",
    }:
        content_type = f"{content_type}; charset=utf-8"
    cache_control = (
        "no-store"
        if relative == PurePosixPath("index.html")
        else "public, max-age=31536000, immutable"
    )
    return DashboardAsset(
        body=candidate.read_bytes(),
        content_type=content_type,
        cache_control=cache_control,
    )


_ASSET_REFERENCE = re.compile(rb"""(?:src|href)=["'](/dashboard/[^"'?#]+)""")


def missing_dashboard_assets() -> list[str]:
    """Return Vite index references that are absent from the package."""
    index = DASHBOARD_DIR / "index.html"
    if not index.is_file():
        return ["/dashboard/index.html"]
    missing = []
    for raw_path in _ASSET_REFERENCE.findall(index.read_bytes()):
        path = raw_path.decode("utf-8")
        if load_dashboard_asset(path) is None:
            missing.append(path)
    return sorted(set(missing))
