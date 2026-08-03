import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gate_bridge.activity import ActivityEvent, ActivityStore
from gate_bridge.dashboard import (
    GateStatus,
    build_dashboard_state,
    build_gate_status,
    group_events,
    parse_door_status,
    render_dashboard_html,
    render_dashboard_json,
)

NOW = 1_700_000_000.0

SECURED = {"door_position_status": "close", "door_lock_relay_status": "lock"}


class DoorStatusTests(unittest.TestCase):
    def test_closed_and_locked_is_secured(self):
        self.assertEqual(parse_door_status(SECURED, now=NOW).state, "secured")

    def test_open_position_is_open(self):
        status = parse_door_status(
            {"door_position_status": "open", "door_lock_relay_status": "lock"}, now=NOW
        )
        self.assertEqual(status.state, "open")

    def test_position_wins_over_relock(self):
        # The relay can re-latch while the gate is still swung open; the
        # physical sensor is the truth.
        status = parse_door_status(
            {"door_position_status": "open", "door_lock_relay_status": "lock"}, now=NOW
        )
        self.assertEqual(status.state, "open")

    def test_released_relay_is_opening(self):
        status = parse_door_status(
            {"door_position_status": "close", "door_lock_relay_status": "unlock"}, now=NOW
        )
        self.assertEqual(status.state, "opening")

    def test_relay_only_door_still_reports(self):
        status = parse_door_status({"door_lock_relay_status": "lock"}, now=NOW)
        self.assertEqual(status.state, "secured")

    def test_position_only_door_still_reports(self):
        status = parse_door_status({"door_position_status": "close"}, now=NOW)
        self.assertEqual(status.state, "secured")

    def test_no_usable_signal_is_unknown(self):
        for payload in ({}, {"door_position_status": None, "door_lock_relay_status": None}):
            status = parse_door_status(payload, now=NOW)
            self.assertEqual(status.state, "unknown")
            self.assertFalse(status.available)


class GateStatusTests(unittest.TestCase):
    def _unlock(self, ts):
        return ActivityEvent(ts=ts, event="unlock_success", detail="Gate", caller="+1", call_sid="CA")

    def test_recent_unlock_shows_opening(self):
        status = build_gate_status(
            door=SECURED,
            door_error="",
            last_unlock=self._unlock(NOW - 3),
            now=NOW,
        )
        self.assertEqual(status.state, "opening")

    def test_old_unlock_does_not_show_opening(self):
        status = build_gate_status(
            door=SECURED,
            door_error="",
            last_unlock=self._unlock(NOW - 600),
            now=NOW,
        )
        self.assertEqual(status.state, "secured")

    def test_live_open_beats_stale_activity_log(self):
        status = build_gate_status(
            door={"door_position_status": "open", "door_lock_relay_status": "unlock"},
            door_error="",
            last_unlock=self._unlock(NOW - 600),
            now=NOW,
        )
        self.assertEqual(status.state, "open")

    def test_hold_wins_over_recent_unlock(self):
        status = build_gate_status(
            door={"door_position_status": "open", "door_lock_relay_status": "unlock"},
            door_error="",
            last_unlock=self._unlock(NOW - 1),
            hold_started_at=NOW - 200,
            now=NOW,
        )
        self.assertEqual(status.state, "held_open")
        self.assertEqual(status.started_at, NOW - 200)
        self.assertTrue(status.indefinite)

    def test_hold_ignored_while_gate_is_secured(self):
        status = build_gate_status(
            door=SECURED,
            door_error="",
            last_unlock=None,
            hold_started_at=NOW - 200,
            now=NOW,
        )
        self.assertEqual(status.state, "secured")

    def test_unreadable_door_reports_unknown(self):
        status = build_gate_status(
            door=None,
            door_error="Access API timeout",
            last_unlock=None,
            now=NOW,
        )
        self.assertEqual(status.state, "unknown")
        self.assertFalse(status.available)
        self.assertIn("timeout", status.error)

    def test_unreadable_door_still_shows_recent_unlock(self):
        status = build_gate_status(
            door=None,
            door_error="Access API timeout",
            last_unlock=self._unlock(NOW - 2),
            now=NOW,
        )
        self.assertEqual(status.state, "opening")


class HistogramTests(unittest.TestCase):
    def test_buckets_span_requested_hours_and_count_in_window(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "a.sqlite3"))
            now = time.time()
            for _ in range(3):
                store.record("unlock_success")
            buckets = store.hourly_histogram(("unlock_success",), hours=24, now=now)
            self.assertEqual(len(buckets), 24)
            self.assertEqual(sum(b.count for b in buckets), 3)
            self.assertEqual(buckets[-1].count, 3)

    def test_counts_and_distinct_callers(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "a.sqlite3"))
            store.record("unlock_success", caller="+17075551111")
            store.record("unlock_success", caller="+17075551111")
            store.record("unlock_success", caller="+17075552222")
            store.record("caller_blocked", caller="+17075559999")
            since = time.time() - 3600
            self.assertEqual(store.count_since(("unlock_success",), since), 3)
            self.assertEqual(store.distinct_callers_since(("unlock_success",), since), 2)
            self.assertEqual(store.latest(("unlock_success",)).caller, "+17075552222")
            self.assertIsNone(store.latest(("hold_open",)))


class GroupEventsTests(unittest.TestCase):
    def _ev(self, ts, event, sid="", caller="+17075551111", detail=""):
        return ActivityEvent(ts=ts, event=event, detail=detail, caller=caller, call_sid=sid)

    def test_one_call_collapses_to_its_outcome(self):
        # newest first, as snapshot() returns
        recent = [
            self._ev(NOW + 2, "unlock_success", "CA1", detail="Gate"),
            self._ev(NOW + 1, "caller_prompted", "CA1"),
            self._ev(NOW, "twilio_request", "CA1", detail="/twilio/voice"),
        ]
        rows = group_events(recent, {"+17075551111": "Oren"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["event"], "unlock_success")
        self.assertEqual(rows[0]["count"], 3)
        self.assertEqual(rows[0]["steps"], ["Call received", "Caller prompted"])
        self.assertEqual(rows[0]["name"], "Oren")

    def test_separate_calls_stay_separate(self):
        recent = [
            self._ev(NOW + 3, "unlock_success", "CA2"),
            self._ev(NOW + 2, "caller_prompted", "CA2"),
            self._ev(NOW + 1, "caller_blocked", "CA1"),
        ]
        rows = group_events(recent, {})
        self.assertEqual([r["event"] for r in rows], ["unlock_success", "caller_blocked"])

    def test_events_without_a_call_sid_are_not_merged(self):
        recent = [
            self._ev(NOW + 1, "dashboard_view", caller="", detail="10.0.0.5"),
            self._ev(NOW, "dashboard_view", caller="", detail="10.0.0.6"),
        ]
        self.assertEqual(len(group_events(recent, {})), 2)

    def test_hold_duration_is_formatted(self):
        rows = group_events([self._ev(NOW, "hold_open", "CA9", detail="45")], {})
        self.assertEqual(rows[0]["detail"], "45 min hold")


class RenderTests(unittest.TestCase):
    def _state(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "a.sqlite3"))
            store.record("unlock_success", detail="Gate", caller="+17075551111", call_sid="CA1")
            store.record("caller_blocked", caller="+17075559999")
            return build_dashboard_state(
                store=store,
                door_name="Gate",
                gate=GateStatus(state="held_open", mode="custom", until=time.time() + 900),
                caller_names={"+17075551111": "Oren"},
                recent_limit=50,
            )

    def test_json_payload_shape(self):
        payload = json.loads(render_dashboard_json(self._state()))
        self.assertEqual(payload["door"], "Gate")
        self.assertEqual(payload["gate"]["state"], "held_open")
        self.assertEqual(payload["gate"]["label"], "Held open")
        self.assertGreater(payload["gate"]["remaining"], 0)
        self.assertEqual(len(payload["chart"]["buckets"]), 24)
        self.assertEqual({s["key"] for s in payload["stats"]} & {"opens_today"}, {"opens_today"})

        events = {item["event"]: item for item in payload["events"]}
        self.assertEqual(events["unlock_success"]["name"], "Oren")
        self.assertEqual(events["unlock_success"]["group"], "opens")
        self.assertEqual(events["caller_blocked"]["group"], "denied")
        self.assertEqual(events["caller_blocked"]["tone"], "serious")

    def test_html_embeds_assets_and_bootstrap(self):
        rendered = render_dashboard_html(self._state()).decode("utf-8")
        self.assertIn("<title>Gate — Gate Control</title>", rendered)
        self.assertIn('id="bootstrap"', rendered)
        self.assertIn("--ring-progress", rendered)  # css inlined
        self.assertIn("/dashboard/api/state", rendered)  # js inlined
        self.assertNotIn("</script>{", rendered)

    def test_bootstrap_json_is_parseable(self):
        rendered = render_dashboard_html(self._state()).decode("utf-8")
        start = rendered.index('id="bootstrap">') + len('id="bootstrap">')
        end = rendered.index("</script>", start)
        payload = json.loads(rendered[start:end].replace("<\\/", "</"))
        self.assertEqual(payload["door"], "Gate")


if __name__ == "__main__":
    unittest.main()
