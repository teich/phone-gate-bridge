import json
import sqlite3
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from gate_bridge.activity import ActivityEvent, ActivityStore, HoldWindow
from gate_bridge.dashboard import (
    GateStatus,
    build_dashboard_state,
    build_gate_status,
    group_events,
    load_dashboard_asset,
    missing_dashboard_assets,
    parse_door_status,
    render_dashboard_json,
)

NOW = 1_700_000_000.0
SECURED = {"door_position_status": "close", "door_lock_relay_status": "lock"}


class DoorStatusTests(unittest.TestCase):
    def test_closed_and_locked_is_secured(self):
        status = parse_door_status(SECURED, now=NOW)
        self.assertEqual(status.state, "secured")
        self.assertEqual(status.position, "closed")
        self.assertEqual(status.relay, "locked")

    def test_open_position_wins_over_relock(self):
        status = parse_door_status(
            {"door_position_status": "open", "door_lock_relay_status": "lock"},
            now=NOW,
        )
        self.assertEqual(status.state, "open")
        self.assertEqual(status.position, "open")
        self.assertEqual(status.relay, "locked")

    def test_released_relay_is_opening(self):
        status = parse_door_status(
            {"door_position_status": "close", "door_lock_relay_status": "unlock"},
            now=NOW,
        )
        self.assertEqual(status.state, "opening")
        self.assertEqual(status.relay, "unlocked")

    def test_single_signal_still_reports(self):
        self.assertEqual(
            parse_door_status({"door_lock_relay_status": "lock"}, now=NOW).state,
            "secured",
        )
        self.assertEqual(
            parse_door_status({"door_position_status": "close"}, now=NOW).state,
            "secured",
        )

    def test_no_usable_signal_is_unknown(self):
        for payload in ({}, {"door_position_status": None, "door_lock_relay_status": None}):
            status = parse_door_status(payload, now=NOW)
            self.assertEqual(status.state, "unknown")
            self.assertFalse(status.available)


class GateStatusTests(unittest.TestCase):
    def _unlock(self, ts):
        return ActivityEvent(
            ts=ts,
            event="unlock_success",
            detail="Gate",
            caller="+1",
            call_sid="CA",
        )

    def _hold(self, *, expires_at=NOW + 1_800):
        return HoldWindow(
            started_at=NOW - 200,
            expires_at=expires_at,
            caller="+1",
            call_sid="CA",
        )

    def test_recent_unlock_shows_timed_opening(self):
        status = build_gate_status(
            door=SECURED,
            door_error="",
            last_unlock=self._unlock(NOW - 3),
            now=NOW,
        )
        self.assertEqual(status.state, "opening")
        self.assertEqual(status.opening_expires_at, NOW + 9)

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

    def test_active_hold_is_finite_and_separate_from_physical_signals(self):
        hold = self._hold()
        status = build_gate_status(
            door={"door_position_status": "open", "door_lock_relay_status": "unlock"},
            door_error="",
            last_unlock=self._unlock(NOW - 1),
            active_hold=hold,
            now=NOW,
        )
        self.assertEqual(status.state, "held_open")
        self.assertEqual(status.hold, hold)
        self.assertEqual(status.position, "open")
        self.assertEqual(status.relay, "unlocked")

    def test_expired_or_physically_secured_hold_is_not_shown(self):
        secured = build_gate_status(
            door=SECURED,
            door_error="",
            last_unlock=None,
            active_hold=self._hold(),
            now=NOW,
        )
        expired = build_gate_status(
            door={"door_position_status": "open"},
            door_error="",
            last_unlock=None,
            active_hold=self._hold(expires_at=NOW),
            now=NOW,
        )
        self.assertEqual(secured.state, "secured")
        self.assertEqual(expired.state, "open")

    def test_unreadable_door_reports_unknown_but_recent_unlock_surfaces(self):
        unknown = build_gate_status(
            door=None,
            door_error="Access API timeout",
            last_unlock=None,
            now=NOW,
        )
        opening = build_gate_status(
            door=None,
            door_error="Access API timeout",
            last_unlock=self._unlock(NOW - 2),
            now=NOW,
        )
        self.assertEqual(unknown.state, "unknown")
        self.assertFalse(unknown.available)
        self.assertIn("timeout", unknown.error)
        self.assertEqual(opening.state, "opening")


class ActivityStoreTests(unittest.TestCase):
    def test_existing_database_migrates_without_losing_rows(self):
        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "activity.sqlite3"
            conn = sqlite3.connect(path)
            conn.execute(
                """
                CREATE TABLE events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts REAL NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    caller TEXT NOT NULL,
                    call_sid TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO events (ts, event, detail, caller, call_sid) "
                "VALUES (?, 'unlock_success', 'Gate', '+1', 'CA-old')",
                (NOW,),
            )
            conn.commit()
            conn.close()

            store = ActivityStore(str(path))
            _, recent = store.snapshot(10)
            self.assertEqual(recent[0].call_sid, "CA-old")
            self.assertEqual(recent[0].id, 1)
            self.assertEqual(recent[0].data, {})

    def test_record_returns_stable_id_and_structured_data(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            item = store.record("custom", data={"answer": 42})
            _, recent = store.snapshot(10)
            self.assertGreater(item.id, 0)
            self.assertEqual(recent[0].id, item.id)
            self.assertEqual(recent[0].data, {"answer": 42})

    def test_hold_lifecycle_obeys_clear_and_expiry(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            store.record_hold_open(duration_seconds=1_800, caller="+1", at=NOW)
            hold = store.active_hold(now=NOW + 1)
            self.assertIsNotNone(hold)
            self.assertEqual(hold.expires_at, NOW + 1_800)
            self.assertIsNone(store.active_hold(now=NOW + 1_800))

            store.record_hold_open(duration_seconds=1_800, caller="+1", at=NOW + 2_000)
            store.record_hold_cleared(caller="+1", reason="manual", at=NOW + 2_100)
            self.assertIsNone(store.active_hold(now=NOW + 2_200))

    def test_legacy_hold_minutes_remain_readable(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            store.record("hold_open", detail="30", at=NOW)
            hold = store.active_hold(now=NOW + 1)
            self.assertIsNotNone(hold)
            self.assertEqual(hold.expires_at, NOW + 1_800)

    def test_actions_are_reserved_once_and_finished_once(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            first, created = store.begin_action(
                call_sid="CA1",
                action="open",
                caller="+1",
                at=NOW,
            )
            duplicate, duplicate_created = store.begin_action(
                call_sid="CA1",
                action="open",
                caller="+1",
                at=NOW + 1,
            )
            self.assertTrue(created)
            self.assertFalse(duplicate_created)
            self.assertEqual(first.status, "pending")
            self.assertEqual(duplicate.requested_at, NOW)

            finished = store.finish_action(
                call_sid="CA1",
                action="open",
                status="succeeded",
                at=NOW + 2,
            )
            self.assertEqual(finished.status, "succeeded")
            with self.assertRaises(ValueError):
                store.finish_action(
                    call_sid="CA1",
                    action="open",
                    status="failed",
                )

    def test_action_result_and_activity_event_commit_together(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            store.begin_action(
                call_sid="CA-atomic",
                action="open",
                caller="+1",
                at=NOW,
            )
            with self.assertRaises(sqlite3.IntegrityError):
                store.finish_action_with_event(
                    call_sid="CA-atomic",
                    action="open",
                    status="succeeded",
                    event=None,  # type: ignore[arg-type]
                    caller="+1",
                    at=NOW + 0.5,
                )
            pending, created = store.begin_action(
                call_sid="CA-atomic",
                action="open",
                caller="+1",
                at=NOW + 0.75,
            )
            self.assertFalse(created)
            self.assertEqual(pending.status, "pending")

            attempt, event = store.finish_action_with_event(
                call_sid="CA-atomic",
                action="open",
                status="succeeded",
                event="unlock_success",
                event_detail="Gate",
                caller="+1",
                at=NOW + 1,
            )

            counts, recent = store.snapshot(10)
            self.assertEqual(attempt.status, "succeeded")
            self.assertEqual(event.id, recent[0].id)
            self.assertEqual(counts["unlock_success"], 1)

    def test_pending_action_becomes_unknown_after_restart_recovery(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            store.begin_action(
                call_sid="CA-interrupted",
                action="open",
                caller="+1",
                at=NOW,
            )

            recovered = store.recover_pending_actions(at=NOW + 10)
            attempt, created = store.begin_action(
                call_sid="CA-interrupted",
                action="open",
                caller="+1",
                at=NOW + 11,
            )
            counts, recent = store.snapshot(10)

            self.assertEqual(recovered, 1)
            self.assertFalse(created)
            self.assertEqual(attempt.status, "unknown")
            self.assertEqual(counts["action_unknown"], 1)
            self.assertEqual(recent[0].call_sid, "CA-interrupted")

    def test_histogram_counts_and_distinct_callers(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            now = time.time()
            store.record("unlock_success", caller="+17075551111")
            store.record("unlock_success", caller="+17075551111")
            store.record("unlock_success", caller="+17075552222")
            store.record("caller_blocked", caller="+17075559999")
            buckets = store.hourly_histogram(("unlock_success",), hours=24, now=now)
            self.assertEqual(len(buckets), 24)
            self.assertEqual(sum(bucket.count for bucket in buckets), 3)
            self.assertEqual(
                store.distinct_callers_since(("unlock_success",), now - 3_600),
                2,
            )


class GroupEventsTests(unittest.TestCase):
    def _event(self, row_id, ts, event, sid="", caller="+17075551111", detail=""):
        return ActivityEvent(
            id=row_id,
            ts=ts,
            event=event,
            detail=detail,
            caller=caller,
            call_sid=sid,
        )

    def test_one_call_collapses_to_stable_call_identity(self):
        recent = [
            self._event(3, NOW + 2, "unlock_success", "CA1", detail="Gate"),
            self._event(2, NOW + 1, "caller_prompted", "CA1"),
            self._event(1, NOW, "twilio_request", "CA1", detail="/twilio/voice"),
        ]
        rows = group_events(recent, {"+17075551111": "Oren"})
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["id"], "call:CA1")
        self.assertEqual(rows[0]["event"], "unlock_success")
        self.assertEqual(rows[0]["count"], 3)
        self.assertEqual(rows[0]["steps"], ["twilio_request", "caller_prompted"])
        self.assertEqual(rows[0]["name"], "Oren")

    def test_non_call_events_use_database_identity(self):
        recent = [
            self._event(8, NOW + 1, "dashboard_view", detail="10.0.0.5"),
            self._event(7, NOW, "dashboard_view", detail="10.0.0.6"),
        ]
        rows = group_events(recent, {})
        self.assertEqual([row["id"] for row in rows], ["event:8", "event:7"])

    def test_retry_after_success_does_not_replace_the_call_outcome(self):
        recent = [
            self._event(4, NOW + 3, "twilio_request", "CA1"),
            self._event(3, NOW + 2, "unlock_success", "CA1"),
            self._event(2, NOW + 1, "caller_prompted", "CA1"),
            self._event(1, NOW, "twilio_request", "CA1"),
        ]
        row = group_events(recent, {})[0]
        self.assertEqual(row["event"], "unlock_success")
        self.assertEqual(row["id"], "call:CA1")


class ContractAndAssetTests(unittest.TestCase):
    def _state(self):
        with TemporaryDirectory() as tmp:
            store = ActivityStore(str(Path(tmp) / "activity.sqlite3"))
            store.record(
                "unlock_success",
                detail="Gate",
                caller="+17075551111",
                call_sid="CA1",
            )
            store.record("caller_blocked", caller="+17075559999")
            store.record("action_unauthorized", caller="+17075558888")
            store.record("action_failed", caller="+17075557777")
            return build_dashboard_state(
                store=store,
                door_name="Gate",
                phone_number="+17075551111",
                gate=GateStatus(state="secured", position="closed", relay="locked"),
                caller_names={"+17075551111": "Oren"},
                recent_limit=50,
                now=NOW,
            )

    def test_json_payload_is_versioned_and_semantic(self):
        payload = json.loads(render_dashboard_json(self._state()))
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["server_time"], NOW)
        self.assertEqual(payload["door"], "Gate")
        self.assertEqual(payload["phone_number"], "+17075551111")
        self.assertEqual(payload["gate"]["position"], "closed")
        self.assertEqual(payload["gate"]["relay"], "locked")
        self.assertIsNone(payload["gate"]["active_hold"])
        self.assertGreater(payload["revision"], 0)
        self.assertEqual(len(payload["chart"]["buckets"]), 24)

        events = {item["event"]: item for item in payload["events"]}
        self.assertEqual(events["unlock_success"]["name"], "Oren")
        self.assertEqual(events["unlock_success"]["id"], "call:CA1")
        self.assertTrue(events["caller_blocked"]["id"].startswith("event:"))
        self.assertNotIn("tone", events["caller_blocked"])
        stats = {item["key"]: item["value"] for item in payload["stats"]}
        self.assertEqual(stats["denied_week"], 2)
        self.assertEqual(stats["errors_week"], 1)

    def test_compiled_index_and_hashed_assets_are_present(self):
        self.assertEqual(missing_dashboard_assets(), [])
        index = load_dashboard_asset("/dashboard")
        self.assertIsNotNone(index)
        self.assertEqual(index.cache_control, "no-store")
        self.assertIn("text/html", index.content_type)
        self.assertIn(b"/dashboard/assets/", index.body)

    def test_asset_loader_blocks_api_and_traversal(self):
        self.assertIsNone(load_dashboard_asset("/dashboard/api/state"))
        self.assertIsNone(load_dashboard_asset("/dashboard/../pyproject.toml"))


if __name__ == "__main__":
    unittest.main()
