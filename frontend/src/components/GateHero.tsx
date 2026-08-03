import type { GateState, GateStatus, LastOpen } from "../api/types";
import { clockFormatter, duration, prettyNumber, relativeTime } from "../format";
import { useNow } from "../hooks/useNow";

interface GateHeroProps {
  gate: GateStatus;
  lastOpen: LastOpen | null;
}

const labels: Record<GateState, string> = {
  secured: "Secured",
  opening: "Opening",
  open: "Open",
  held_open: "Held open",
  unknown: "Status unavailable",
};

export function GateHero({ gate, lastOpen }: GateHeroProps) {
  const showTimer =
    gate.active_hold !== null ||
    (gate.state === "opening" && gate.opening_expires_at !== null);
  const now = useNow(250, showTimer);

  return (
    <section className="hero" aria-labelledby="hero-state">
      <div className="hero-body">
        <p className="hero-eyebrow">
          <span className="state-dot" aria-hidden="true" />
          <span>Gate status</span>
        </p>
        <h1 className="hero-state" id="hero-state">
          {labels[gate.state]}
        </h1>
        <p className="hero-note">{heroCopy(gate, lastOpen, now)}</p>
        <GateMetadata gate={gate} lastOpen={lastOpen} now={now} />
      </div>
      {showTimer ? <CountdownDial gate={gate} now={now} /> : null}
    </section>
  );
}

function GateMetadata({
  gate,
  lastOpen,
  now,
}: GateHeroProps & { now: number }) {
  const rows: Array<[string, string]> = [];
  if (gate.active_hold !== null) {
    rows.push([
      "Closes at",
      clockFormatter.format(gate.active_hold.expires_at * 1_000),
    ]);
  }
  if (gate.position !== "unknown") rows.push(["Position", gate.position]);
  if (gate.relay !== "unknown") rows.push(["Relay", gate.relay]);
  if (lastOpen !== null) {
    rows.push(["Last open", relativeTime(lastOpen.ts, now)]);
    const who = lastOpen.name || prettyNumber(lastOpen.caller);
    if (who) rows.push(["By", who]);
  }

  return (
    <dl className="hero-meta">
      {rows.map(([term, value]) => (
        <div key={term}>
          <dt>{term}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
}

function CountdownDial({ gate, now }: { gate: GateStatus; now: number }) {
  const hold = gate.active_hold;
  const startedAt = hold?.started_at ?? (gate.opening_expires_at ?? now / 1_000) - 12;
  const expiresAt = hold?.expires_at ?? gate.opening_expires_at ?? now / 1_000;
  const total = Math.max(1, expiresAt - startedAt);
  const remaining = Math.max(0, expiresAt - now / 1_000);
  const progress = Math.min(1, remaining / total);
  const circumference = 2 * Math.PI * 46;
  const offset = circumference * (1 - progress);

  return (
    <div className="hero-dial">
      <div className="ring">
        <svg className="ring-svg" viewBox="0 0 104 104" aria-hidden="true">
          <circle className="ring-track" cx="52" cy="52" r="46" />
          <circle
            className="ring-progress"
            cx="52"
            cy="52"
            r="46"
            style={{
              strokeDasharray: circumference,
              strokeDashoffset: offset,
            }}
          />
        </svg>
        <div className="ring-inner">
          <span className="ring-value">{duration(remaining)}</span>
          <span className="ring-unit">
            {gate.state === "opening" ? "relay" : "remaining"}
          </span>
        </div>
      </div>
      <p className="ring-caption">
        {hold === null ? "Momentary unlock" : `${duration(total)} hold`}
      </p>
    </div>
  );
}

function heroCopy(gate: GateStatus, lastOpen: LastOpen | null, now: number): string {
  switch (gate.state) {
    case "held_open":
      return "The gate is being held open and will close when the timer runs out.";
    case "open":
      return lastOpen === null
        ? "The gate is standing open."
        : `The gate is standing open. Last opened ${relativeTime(lastOpen.ts, now)} by ${lastOpen.name || prettyNumber(lastOpen.caller) || "an unknown caller"}.`;
    case "opening":
      return "Unlock relay fired. The gate is swinging now.";
    case "secured":
      return lastOpen === null
        ? "Locked. No gate opens recorded yet."
        : `Locked. Last opened ${relativeTime(lastOpen.ts, now)} by ${lastOpen.name || prettyNumber(lastOpen.caller) || "an unknown caller"}.`;
    case "unknown":
      return gate.error
        ? `Could not read the gate status from UniFi Access: ${gate.error}`
        : "Could not read the gate status from UniFi Access.";
  }
}
