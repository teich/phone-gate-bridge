import { useEffect } from "react";

import { useDashboardState } from "./api/useDashboardState";
import { ActivityChart } from "./components/ActivityChart";
import { ActivityPanel } from "./components/ActivityPanel";
import { GateHero } from "./components/GateHero";
import { Header } from "./components/Header";
import { StatGrid } from "./components/StatGrid";
import { timestampFormatter } from "./format";
import { useTheme } from "./hooks/useTheme";

export default function App() {
  const { dashboard, connection, updatedAt, error, refresh } = useDashboardState();
  const [theme, toggleTheme] = useTheme();
  const door = dashboard?.door ?? "Gate";

  useEffect(() => {
    document.documentElement.dataset.state = dashboard?.gate.state ?? "unknown";
    document.title = `${door} — Gate Control`;
  }, [dashboard?.gate.state, door]);

  return (
    <div className="app-shell">
      <div className="glow" aria-hidden="true" />
      <Header
        door={door}
        connection={connection}
        theme={theme}
        onToggleTheme={toggleTheme}
      />
      {dashboard === null ? (
        <InitialState error={error} onRetry={refresh} />
      ) : (
        <main>
          <GateHero gate={dashboard.gate} lastOpen={dashboard.last_open} />
          <StatGrid stats={dashboard.stats} />
          <ActivityChart chart={dashboard.chart} />
          <ActivityPanel events={dashboard.events} />
        </main>
      )}
      <footer className="foot">
        <span>Read-only · local networks only</span>
        <span>
          {updatedAt === null
            ? "Connecting…"
            : `Updated ${timestampFormatter.format(updatedAt * 1_000)}`}
        </span>
      </footer>
      <p className="visually-hidden" role="status" aria-live="polite">
        {error}
      </p>
    </div>
  );
}

function InitialState({
  error,
  onRetry,
}: {
  error: string;
  onRetry: () => void;
}) {
  return (
    <main>
      <section className="panel loading-panel" aria-live="polite">
        {error ? (
          <>
            <h1>Dashboard unavailable</h1>
            <p>{error}</p>
            <button className="retry-button" type="button" onClick={onRetry}>
              Try again
            </button>
          </>
        ) : (
          <>
            <span className="loading-dot" aria-hidden="true" />
            <p>Reading the gate…</p>
          </>
        )}
      </section>
    </main>
  );
}
