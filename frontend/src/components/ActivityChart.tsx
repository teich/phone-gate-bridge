import { useState, type CSSProperties } from "react";

import type { ChartBucket, DashboardChart } from "../api/types";
import { clockFormatter } from "../format";

interface TooltipState {
  bucket: ChartBucket;
  left: number;
  top: number;
}

export function ActivityChart({ chart }: { chart: DashboardChart }) {
  const [tooltip, setTooltip] = useState<TooltipState | null>(null);

  const showTooltip = (element: HTMLElement, bucket: ChartBucket) => {
    const box = element.getBoundingClientRect();
    setTooltip({
      bucket,
      left: box.left + box.width / 2,
      top: box.top,
    });
  };

  return (
    <section className="panel chart-panel" aria-labelledby="chart-title">
      <header className="panel-head">
        <div>
          <h2 id="chart-title">Gate opens</h2>
          <p className="panel-sub">
            Last 24 hours · <span>{chart.total}</span> total
          </p>
        </div>
      </header>
      <figure className="chart">
        <div className="chart-plot" role="list" aria-describedby="chart-table-note">
          {chart.buckets.map((bucket) => {
            const isPeak = chart.max > 0 && bucket.count === chart.max;
            return (
              <button
                type="button"
                className="bar"
                key={bucket.start}
                data-zero={String(bucket.count === 0)}
                data-count={String(bucket.count)}
                data-peak={String(isPeak)}
                role="listitem"
                aria-label={`${clockFormatter.format(bucket.start * 1_000)}: ${bucket.count} opens`}
                style={{
                  "--bar-scale":
                    chart.max > 0 ? String(bucket.count / chart.max) : "0",
                } as CSSProperties}
                onPointerEnter={(event) => showTooltip(event.currentTarget, bucket)}
                onPointerLeave={() => setTooltip(null)}
                onFocus={(event) => showTooltip(event.currentTarget, bucket)}
                onBlur={() => setTooltip(null)}
              />
            );
          })}
        </div>
        <figcaption className="chart-axis">
          {chart.buckets.map((bucket, index) => {
            const hour = new Date(bucket.start * 1_000).getHours();
            const isLast = index === chart.buckets.length - 1;
            const tick = isLast || hour % 6 === 0;
            return (
              <span key={bucket.start} data-tick={String(tick)}>
                {isLast ? "now" : `${hour}:00`}
              </span>
            );
          })}
        </figcaption>
      </figure>
      <p className="visually-hidden" id="chart-table-note">
        {chart.buckets
          .map(
            (bucket) =>
              `${clockFormatter.format(bucket.start * 1_000)}: ${bucket.count}`,
          )
          .join(", ")}
      </p>
      {tooltip === null ? null : (
        <div
          className="tooltip"
          role="status"
          style={{ left: tooltip.left, top: tooltip.top }}
        >
          <strong>
            {tooltip.bucket.count} {tooltip.bucket.count === 1 ? "open" : "opens"}
          </strong>
          <span>{clockFormatter.format(tooltip.bucket.start * 1_000)}</span>
        </div>
      )}
    </section>
  );
}
