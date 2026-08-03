import type { DashboardEvent, DashboardState } from "../api/types";

export function event(
  id: string,
  kind = "unlock_success",
  timestamp = 1_700_000_000,
): DashboardEvent {
  return {
    id,
    raw_id: Number(id.replace(/\D/g, "")) || 0,
    ts: timestamp,
    event: kind,
    caller: "+17075551111",
    name: "Oren",
    detail: "Gate",
    data: {},
    call_sid: id.startsWith("call:") ? id.slice(5) : "",
    steps: [],
    count: 1,
  };
}

export function dashboardState(): DashboardState {
  return {
    schema_version: 1,
    server_time: 1_700_000_000,
    revision: 3,
    door: "Gate",
    gate: {
      state: "secured",
      position: "closed",
      relay: "locked",
      active_hold: null,
      opening_expires_at: null,
      available: true,
      error: "",
    },
    last_open: null,
    stats: [
      { key: "opens_today", value: 1 },
      { key: "opens_week", value: 2 },
      { key: "callers_week", value: 1 },
      { key: "denied_week", value: 0 },
      { key: "errors_week", value: 0 },
    ],
    chart: {
      buckets: [{ start: 1_700_000_000, count: 1 }],
      max: 1,
      total: 1,
    },
    events: [event("call:CA1")],
  };
}
