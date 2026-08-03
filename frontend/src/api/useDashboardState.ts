import { useCallback, useEffect, useRef, useState } from "react";

import {
  parseDashboardState,
  type DashboardState,
} from "./types";

const POLL_MS = 3_000;
const MAX_BACKOFF_MS = 30_000;

export type ConnectionStatus = "live" | "retry" | "down";

interface DashboardResource {
  dashboard: DashboardState | null;
  connection: ConnectionStatus;
  updatedAt: number | null;
  error: string;
  refresh: () => void;
}

export function dashboardContentSignature(state: DashboardState): string {
  const { server_time: _serverTime, ...semanticState } = state;
  return JSON.stringify(semanticState);
}

export function useDashboardState(): DashboardResource {
  const [dashboard, setDashboard] = useState<DashboardState | null>(null);
  const [connection, setConnection] = useState<ConnectionStatus>("live");
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const [error, setError] = useState("");
  const refreshRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    let stopped = false;
    let failures = 0;
    let pollTimer: number | undefined;
    let controller: AbortController | null = null;
    let semantic = "";

    const apply = (next: DashboardState) => {
      const nextSignature = dashboardContentSignature(next);
      if (nextSignature !== semantic) {
        semantic = nextSignature;
        setDashboard(next);
      }
      setUpdatedAt(next.server_time);
      setError("");
      failures = 0;
      setConnection("live");
    };

    const schedule = () => {
      window.clearTimeout(pollTimer);
      if (stopped || document.visibilityState !== "visible") return;
      const delay =
        failures === 0
          ? POLL_MS
          : Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** Math.min(failures, 5));
      pollTimer = window.setTimeout(load, delay);
    };

    const load = async () => {
      if (stopped) return;
      controller?.abort();
      controller = new AbortController();
      try {
        const response = await fetch("/dashboard/api/state", {
          headers: { accept: "application/json" },
          cache: "no-store",
          signal: controller.signal,
        });
        if (!response.ok) throw new Error(`Dashboard API returned HTTP ${response.status}`);
        const next = parseDashboardState(await response.json());
        if (!stopped) apply(next);
      } catch (caught) {
        if (stopped || (caught instanceof DOMException && caught.name === "AbortError")) {
          return;
        }
        failures += 1;
        setConnection(failures > 3 ? "down" : "retry");
        setError(caught instanceof Error ? caught.message : "Dashboard refresh failed");
      } finally {
        schedule();
      }
    };

    const onVisibilityChange = () => {
      if (document.visibilityState === "visible") void load();
      else {
        window.clearTimeout(pollTimer);
        controller?.abort();
      }
    };

    refreshRef.current = () => void load();
    document.addEventListener("visibilitychange", onVisibilityChange);
    void load();

    return () => {
      stopped = true;
      controller?.abort();
      window.clearTimeout(pollTimer);
      document.removeEventListener("visibilitychange", onVisibilityChange);
    };
  }, []);

  const refresh = useCallback(() => refreshRef.current(), []);
  return { dashboard, connection, updatedAt, error, refresh };
}
