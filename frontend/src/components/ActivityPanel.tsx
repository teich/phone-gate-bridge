import { useState } from "react";

import type { DashboardEvent } from "../api/types";
import { prettyNumber, relativeTime, timestampFormatter } from "../format";
import { useNow } from "../hooks/useNow";
import { presentEvent, type EventGroup } from "../presentation";
import { Icon } from "./Icon";

type Filter = "all" | "opens" | "denied" | "errors";

const filters: Array<{ key: Filter; label: string }> = [
  { key: "all", label: "All" },
  { key: "opens", label: "Opens" },
  { key: "denied", label: "Denied" },
  { key: "errors", label: "Errors" },
];

export function ActivityPanel({ events }: { events: DashboardEvent[] }) {
  const [filter, setFilter] = useState<Filter>("all");
  const now = useNow(15_000);

  const shown = events.filter((event) => isVisible(presentEvent(event.event).group, filter));

  return (
    <section className="panel" aria-labelledby="feed-title">
      <header className="panel-head">
        <div>
          <h2 id="feed-title">Activity</h2>
          <p className="panel-sub">{shown.length} events shown</p>
        </div>
        <div className="chips" role="group" aria-label="Filter activity">
          {filters.map((item) => (
            <button
              type="button"
              className="chip"
              data-filter={item.key}
              aria-pressed={filter === item.key}
              key={item.key}
              onClick={() => setFilter(item.key)}
            >
              {item.label}
            </button>
          ))}
        </div>
      </header>
      <ol className="feed">
        {events.map((event) => (
          <ActivityRow
            event={event}
            hidden={!isVisible(presentEvent(event.event).group, filter)}
            key={event.id}
            now={now}
          />
        ))}
      </ol>
      {shown.length === 0 ? <p className="empty">Nothing here yet.</p> : null}
    </section>
  );
}

function ActivityRow({
  event,
  hidden,
  now,
}: {
  event: DashboardEvent;
  hidden: boolean;
  now: number;
}) {
  const presentation = presentEvent(event.event);
  const who = event.name || prettyNumber(event.caller);
  const detail = formatDetail(event);

  return (
    <li
      className={`row tone-${presentation.tone}`}
      data-event-id={event.id}
      data-group={presentation.group}
      hidden={hidden}
    >
      <span className="row-icon">
        <Icon name={presentation.icon} />
      </span>
      <div className="row-main">
        <p className="row-label">{presentation.label}</p>
        <p className="row-sub">
          {who ? <span>{who}</span> : null}
          {event.name && event.caller ? <code>{prettyNumber(event.caller)}</code> : null}
          {detail ? <span>{detail}</span> : null}
          {event.steps.length > 0 ? (
            <span className="row-steps">
              via {event.steps.map((step) => presentEvent(step).label).join(" → ")}
            </span>
          ) : null}
        </p>
      </div>
      <time
        className="row-time"
        dateTime={new Date(event.ts * 1_000).toISOString()}
        title={timestampFormatter.format(event.ts * 1_000)}
      >
        {relativeTime(event.ts, now)}
      </time>
    </li>
  );
}

function isVisible(group: EventGroup, filter: Filter): boolean {
  return filter === "all" || group === filter;
}

function formatDetail(event: DashboardEvent): string {
  const value = event.detail.trim();
  if (event.event === "hold_open") {
    const seconds = event.data.duration_seconds;
    if (typeof seconds === "number") return `${Math.round(seconds / 60)} min hold`;
    if (/^\d+$/.test(value)) return `${value} min hold`;
  }
  if (event.event === "invalid_digit") {
    return value && value !== "empty" ? `pressed ${value}` : "no keypress";
  }
  return value;
}
