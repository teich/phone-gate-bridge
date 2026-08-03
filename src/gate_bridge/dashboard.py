"""Admin dashboard: state assembly and rendering.

The dashboard is read-only. It serves one HTML page plus a JSON state endpoint
that the page polls, so the browser never needs a second round trip for assets.
"""

from __future__ import annotations

import json
import time
import xml.sax.saxutils as saxutils
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from gate_bridge.activity import ActivityEvent, ActivityStore

STATIC_DIR = Path(__file__).parent / "static"

# How long after an unlock pulse the gate is still shown as "opening". The
# UniFi relay latches for a few seconds; this keeps the hero panel honest
# without needing to poll the controller faster.
UNLOCK_PULSE_SECONDS = 12.0

OPEN_EVENTS = ("unlock_success",)
DENIED_EVENTS = ("caller_blocked", "signature_invalid", "dashboard_denied")
ERROR_EVENTS = ("unlock_failed", "allowed_callers_error")

# label, tone, group, icon id
EVENT_META: dict[str, tuple[str, str, str, str]] = {
    "unlock_success": ("Gate opened", "good", "opens", "unlock"),
    "hold_open": ("Hold started", "warning", "opens", "timer"),
    "hold_cleared": ("Hold cleared", "neutral", "opens", "timer"),
    "caller_prompted": ("Caller prompted", "neutral", "calls", "phone"),
    "twilio_request": ("Call received", "muted", "calls", "phone"),
    "invalid_digit": ("No valid keypress", "warning", "calls", "keypad"),
    "caller_blocked": ("Caller blocked", "serious", "denied", "shield"),
    "signature_invalid": ("Invalid signature", "critical", "denied", "shield"),
    "unlock_failed": ("Unlock failed", "critical", "errors", "alert"),
    "allowed_callers_error": ("Allowlist unreadable", "critical", "errors", "alert"),
    "dashboard_view": ("Dashboard opened", "muted", "system", "eye"),
    "dashboard_denied": ("Dashboard blocked", "serious", "denied", "shield"),
}


@dataclass(frozen=True)
class GateStatus:
    """Current physical state of the gate, as best we can determine it."""

    state: str = "unknown"  # secured | opening | open | held_open | unknown
    mode: str = ""  # raw UniFi relay/position status
    until: float | None = None
    started_at: float | None = None
    indefinite: bool = False
    available: bool = True
    error: str = ""

    @property
    def remaining(self) -> float | None:
        if self.until is None:
            return None
        return max(0.0, self.until - time.time())

    def to_json(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "label": GATE_LABELS.get(self.state, "Unknown"),
            "mode": self.mode,
            "until": self.until,
            "started_at": self.started_at,
            "remaining": self.remaining,
            "indefinite": self.indefinite,
            "available": self.available,
            "error": self.error,
        }


GATE_LABELS = {
    "secured": "Secured",
    "opening": "Opening",
    "open": "Open",
    "held_open": "Held open",
    "unknown": "Status unavailable",
}


@dataclass(frozen=True)
class DashboardState:
    door_name: str
    gate: GateStatus
    stats: list[dict[str, Any]] = field(default_factory=list)
    chart: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    last_open: dict[str, Any] | None = None
    generated_at: float = 0.0

    def to_json(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "door": self.door_name,
            "gate": self.gate.to_json(),
            "last_open": self.last_open,
            "stats": self.stats,
            "chart": self.chart,
            "events": self.events,
        }


def parse_door_status(door: dict[str, Any], now: float | None = None) -> GateStatus:
    """Map a UniFi Access door record onto a gate state.

    Two independent signals come back on the doors listing:
    `door_position_status` is the physical sensor (`open`/`close`) and
    `door_lock_relay_status` is the strike relay (`unlock`/`lock`). Position
    wins when it says the gate is open, because a gate that has swung is open
    regardless of what the relay has since done. Either field can be absent or
    null on a door with no sensor or no hub, so a payload with no usable signal
    reports `unknown` rather than claiming the gate is secured.
    """
    position = str(door.get("door_position_status") or "").strip().lower()
    relay = str(door.get("door_lock_relay_status") or "").strip().lower()

    if position == "open":
        return GateStatus(state="open", mode=position)
    if relay == "unlock":
        return GateStatus(state="opening", mode=relay)
    if relay == "lock" or position == "close":
        return GateStatus(state="secured", mode=relay or position)
    return GateStatus(
        state="unknown",
        available=False,
        error="Door reported no lock or position status",
    )


def build_gate_status(
    *,
    door: dict[str, Any] | None,
    door_error: str,
    last_unlock: ActivityEvent | None,
    hold_started_at: float | None = None,
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
        # The controller reports that the gate is released but not why. A hold
        # is distinguished from a momentary pulse by the activity log, which
        # also supplies the start time. No end time is available here, so the
        # hold reads as indefinite.
        if status.state in {"open", "opening"} and hold_started_at is not None:
            status = GateStatus(
                state="held_open",
                mode=status.mode,
                started_at=hold_started_at,
                indefinite=True,
            )

    # A momentary unlock can finish between polls, leaving no trace on the
    # door record, so surface a recent pulse from the activity log instead.
    if status.state in {"secured", "unknown"} and last_unlock is not None:
        if now - last_unlock.ts <= UNLOCK_PULSE_SECONDS:
            return GateStatus(
                state="opening",
                mode=status.mode,
                until=last_unlock.ts + UNLOCK_PULSE_SECONDS,
                started_at=last_unlock.ts,
                available=status.available,
                error=status.error,
            )
    return status


def _describe_caller(number: str, caller_names: dict[str, str]) -> str:
    if not number:
        return ""
    return caller_names.get(number, "")


def _format_detail(event: str, detail: str) -> str:
    value = detail.strip()
    if event == "hold_open" and value.isdigit():
        return f"{value} min hold"
    if event == "invalid_digit":
        return f"pressed {value}" if value and value != "empty" else "no keypress"
    return detail


def _event_json(item: ActivityEvent, caller_names: dict[str, str]) -> dict[str, Any]:
    label, tone, group, icon = EVENT_META.get(
        item.event, (item.event.replace("_", " ").capitalize(), "muted", "system", "dot")
    )
    return {
        "ts": item.ts,
        "event": item.event,
        "label": label,
        "tone": tone,
        "group": group,
        "icon": icon,
        "caller": item.caller,
        "name": _describe_caller(item.caller, caller_names),
        "detail": _format_detail(item.event, item.detail),
        "call_sid": item.call_sid,
        "steps": [],
        "count": 1,
    }


def group_events(
    recent: list[ActivityEvent], caller_names: dict[str, str]
) -> list[dict[str, Any]]:
    """Collapse one phone call into a single row.

    A single call writes several rows (`twilio_request`, `caller_prompted`,
    `unlock_success`), which makes the raw log unreadable. Events sharing a
    Twilio call SID become one entry headlined by the call's outcome — the
    newest event, since the terminal one is always written last — with the
    earlier steps kept on the entry rather than discarded.

    `recent` must be newest-first.
    """
    grouped: list[dict[str, Any]] = []
    by_sid: dict[str, dict[str, Any]] = {}

    for item in recent:
        if not item.call_sid:
            grouped.append(_event_json(item, caller_names))
            continue

        existing = by_sid.get(item.call_sid)
        if existing is None:
            entry = _event_json(item, caller_names)
            by_sid[item.call_sid] = entry
            grouped.append(entry)
            continue

        label = EVENT_META.get(item.event, (item.event, "", "", ""))[0]
        existing["steps"].insert(0, label)
        existing["count"] += 1
        if not existing["name"]:
            existing["name"] = _describe_caller(item.caller, caller_names)
        if not existing["caller"]:
            existing["caller"] = item.caller

    return grouped


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

    counts, recent = store.snapshot(recent_limit)
    buckets = store.hourly_histogram(OPEN_EVENTS, hours=24, now=now)
    bucket_counts = [b.count for b in buckets]

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
            "label": "Opens today",
            "value": store.count_since(OPEN_EVENTS, midnight),
            "tone": "neutral",
        },
        {
            "key": "opens_week",
            "label": "Opens this week",
            "value": store.count_since(OPEN_EVENTS, week_ago),
            "tone": "neutral",
        },
        {
            "key": "callers_week",
            "label": "Callers this week",
            "value": store.distinct_callers_since(OPEN_EVENTS, week_ago),
            "tone": "neutral",
        },
        {
            "key": "denied_week",
            "label": "Denied this week",
            "value": store.count_since(DENIED_EVENTS, week_ago),
            "tone": "serious",
        },
        {
            "key": "errors_week",
            "label": "Failures this week",
            "value": store.count_since(ERROR_EVENTS, week_ago),
            "tone": "critical",
        },
    ]

    return DashboardState(
        door_name=door_name,
        gate=gate,
        stats=stats,
        chart={
            "buckets": [{"start": b.start, "count": b.count} for b in buckets],
            "max": max(bucket_counts) if bucket_counts else 0,
            "total": sum(bucket_counts),
        },
        events=group_events(recent, caller_names),
        last_open=last_open,
        generated_at=now,
    )


@lru_cache(maxsize=4)
def _asset(name: str) -> str:
    return (STATIC_DIR / name).read_text(encoding="utf-8")


ICON_SPRITE = """
<svg class="sprite" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
<defs>
<symbol id="i-unlock" viewBox="0 0 24 24"><path d="M7 11V7a5 5 0 0 1 9.9-1M5 11h14v10H5z"/></symbol>
<symbol id="i-timer" viewBox="0 0 24 24"><circle cx="12" cy="13" r="8"/><path d="M12 9v4l2.5 2.5M9 2h6"/></symbol>
<symbol id="i-phone" viewBox="0 0 24 24"><path d="M6.5 3h3l1.5 4.5-2 1.5a12 12 0 0 0 6 6l1.5-2 4.5 1.5v3a2 2 0 0 1-2.2 2A17 17 0 0 1 4.5 5.2 2 2 0 0 1 6.5 3z"/></symbol>
<symbol id="i-keypad" viewBox="0 0 24 24"><circle cx="6" cy="6" r="1.4"/><circle cx="12" cy="6" r="1.4"/><circle cx="18" cy="6" r="1.4"/><circle cx="6" cy="12" r="1.4"/><circle cx="12" cy="12" r="1.4"/><circle cx="18" cy="12" r="1.4"/><circle cx="12" cy="18" r="1.4"/></symbol>
<symbol id="i-shield" viewBox="0 0 24 24"><path d="M12 3l7 3v6c0 4.5-3 7.7-7 9-4-1.3-7-4.5-7-9V6z"/><path d="M12 9v4M12 16h.01"/></symbol>
<symbol id="i-alert" viewBox="0 0 24 24"><path d="M12 4l9 15H3z"/><path d="M12 10v4M12 16.5h.01"/></symbol>
<symbol id="i-eye" viewBox="0 0 24 24"><path d="M2 12s3.6-6 10-6 10 6 10 6-3.6 6-10 6S2 12 2 12z"/><circle cx="12" cy="12" r="2.5"/></symbol>
<symbol id="i-dot" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/></symbol>
<symbol id="i-sun" viewBox="0 0 24 24"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M19.1 4.9l-1.4 1.4M6.3 17.7l-1.4 1.4"/></symbol>
<symbol id="i-moon" viewBox="0 0 24 24"><path d="M20 14.5A8.5 8.5 0 0 1 9.5 4a8.5 8.5 0 1 0 10.5 10.5z"/></symbol>
</defs>
</svg>
""".strip()


def render_dashboard_html(state: DashboardState) -> bytes:
    title = f"{state.door_name} — Gate Control"
    bootstrap = json.dumps(state.to_json(), separators=(",", ":"))
    # Guard against a literal </script> inside serialized detail text.
    bootstrap = bootstrap.replace("</", "<\\/")

    html = f"""<!doctype html>
<html lang="en" data-state="{saxutils.escape(state.gate.state)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="color-scheme" content="dark light">
<title>{saxutils.escape(title)}</title>
<style>{_asset('dashboard.css')}</style>
</head>
<body>
{ICON_SPRITE}
<div class="glow" aria-hidden="true"></div>

<header class="topbar">
  <div class="brand">
    <span class="brand-mark" aria-hidden="true"></span>
    <span class="brand-text">
      <strong>{saxutils.escape(state.door_name)}</strong>
      <span>Phone Gate Bridge</span>
    </span>
  </div>
  <div class="topbar-actions">
    <p class="live" id="live" data-status="live">
      <span class="live-dot" aria-hidden="true"></span>
      <span class="live-text">Live</span>
    </p>
    <button class="icon-button" id="theme-toggle" type="button" aria-label="Switch theme">
      <svg class="only-dark" aria-hidden="true"><use href="#i-sun"/></svg>
      <svg class="only-light" aria-hidden="true"><use href="#i-moon"/></svg>
    </button>
  </div>
</header>

<main>
  <section class="hero" id="hero" aria-labelledby="hero-state">
    <div class="hero-body">
      <p class="hero-eyebrow">
        <span class="state-dot" aria-hidden="true"></span>
        <span id="hero-mode">Gate status</span>
      </p>
      <h1 class="hero-state" id="hero-state">Secured</h1>
      <p class="hero-note" id="hero-note"></p>
      <dl class="hero-meta" id="hero-meta"></dl>
    </div>
    <div class="hero-dial" id="hero-dial" data-visible="false">
      <div class="ring" id="ring">
        <div class="ring-inner">
          <span class="ring-value" id="ring-value">—</span>
          <span class="ring-unit" id="ring-unit">remaining</span>
        </div>
      </div>
      <p class="ring-caption" id="ring-caption"></p>
    </div>
  </section>

  <section class="stats" id="stats" aria-label="Summary"></section>

  <section class="panel chart-panel" aria-labelledby="chart-title">
    <header class="panel-head">
      <div>
        <h2 id="chart-title">Gate opens</h2>
        <p class="panel-sub">Last 24 hours &middot; <span id="chart-total">0</span> total</p>
      </div>
    </header>
    <figure class="chart" id="chart">
      <div class="chart-plot" id="chart-plot" role="img" aria-describedby="chart-table-note"></div>
      <figcaption class="chart-axis" id="chart-axis"></figcaption>
    </figure>
    <p class="visually-hidden" id="chart-table-note"></p>
  </section>

  <section class="panel" aria-labelledby="feed-title">
    <header class="panel-head">
      <div>
        <h2 id="feed-title">Activity</h2>
        <p class="panel-sub"><span id="feed-count">0</span> events shown</p>
      </div>
      <div class="chips" id="filters" role="group" aria-label="Filter activity">
        <button type="button" class="chip" data-filter="all" aria-pressed="true">All</button>
        <button type="button" class="chip" data-filter="opens" aria-pressed="false">Opens</button>
        <button type="button" class="chip" data-filter="denied" aria-pressed="false">Denied</button>
        <button type="button" class="chip" data-filter="errors" aria-pressed="false">Errors</button>
      </div>
    </header>
    <ol class="feed" id="feed"></ol>
    <p class="empty" id="feed-empty" hidden>Nothing here yet.</p>
  </section>
</main>

<footer class="foot">
  <span>Read-only &middot; local networks only</span>
  <span id="updated">—</span>
</footer>

<div class="tooltip" id="tooltip" role="status" aria-live="off" hidden></div>

<script type="application/json" id="bootstrap">{bootstrap}</script>
<script type="module">{_asset('dashboard.js')}</script>
</body>
</html>"""
    return html.encode("utf-8")


def render_dashboard_json(state: DashboardState) -> bytes:
    return json.dumps(state.to_json(), separators=(",", ":")).encode("utf-8")
