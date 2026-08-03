export const DASHBOARD_SCHEMA_VERSION = 1 as const;

export type GateState =
  | "secured"
  | "opening"
  | "open"
  | "held_open"
  | "unknown";

export type GatePosition = "open" | "closed" | "unknown";
export type GateRelay = "locked" | "unlocked" | "unknown";

export interface ActiveHold {
  started_at: number;
  expires_at: number;
  caller: string;
  call_sid: string;
}

export interface GateStatus {
  state: GateState;
  position: GatePosition;
  relay: GateRelay;
  active_hold: ActiveHold | null;
  opening_expires_at: number | null;
  available: boolean;
  error: string;
}

export interface DashboardStat {
  key:
    | "opens_today"
    | "opens_week"
    | "callers_week"
    | "denied_week"
    | "errors_week";
  value: number;
}

export interface ChartBucket {
  start: number;
  count: number;
}

export interface DashboardChart {
  buckets: ChartBucket[];
  max: number;
  total: number;
}

export interface DashboardEvent {
  id: string;
  raw_id: number;
  ts: number;
  event: string;
  caller: string;
  name: string;
  detail: string;
  data: Record<string, unknown>;
  call_sid: string;
  steps: string[];
  count: number;
}

export interface LastOpen {
  ts: number;
  caller: string;
  name: string;
  detail: string;
}

export interface DashboardState {
  schema_version: typeof DASHBOARD_SCHEMA_VERSION;
  server_time: number;
  revision: number;
  door: string;
  phone_number: string;
  gate: GateStatus;
  last_open: LastOpen | null;
  stats: DashboardStat[];
  chart: DashboardChart;
  events: DashboardEvent[];
}

const gateStates = new Set<GateState>([
  "secured",
  "opening",
  "open",
  "held_open",
  "unknown",
]);

export function parseDashboardState(value: unknown): DashboardState {
  if (!isRecord(value)) throw new Error("Dashboard returned a non-object response");
  if (value.schema_version !== DASHBOARD_SCHEMA_VERSION) {
    throw new Error(`Unsupported dashboard schema: ${String(value.schema_version)}`);
  }
  if (
    typeof value.server_time !== "number" ||
    typeof value.revision !== "number" ||
    typeof value.door !== "string" ||
    !isRecord(value.gate) ||
    typeof value.gate.state !== "string" ||
    !gateStates.has(value.gate.state as GateState) ||
    !Array.isArray(value.stats) ||
    !isRecord(value.chart) ||
    !Array.isArray(value.chart.buckets) ||
    !Array.isArray(value.events)
  ) {
    throw new Error("Dashboard response does not match schema version 1");
  }
  return {
    ...value,
    phone_number: typeof value.phone_number === "string" ? value.phone_number : "",
  } as unknown as DashboardState;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
