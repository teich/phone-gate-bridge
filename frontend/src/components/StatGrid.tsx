import type { DashboardStat } from "../api/types";
import { statPresentation } from "../presentation";

export function StatGrid({ stats }: { stats: DashboardStat[] }) {
  return (
    <section className="stats" aria-label="Summary">
      {stats.map((stat) => {
        const presentation = statPresentation[stat.key];
        return (
          <article
            className={`stat tone-${presentation.tone}`}
            data-zero={String(stat.value === 0)}
            key={stat.key}
          >
            <span className="stat-value">{stat.value}</span>
            <span className="stat-label">{presentation.label}</span>
          </article>
        );
      })}
    </section>
  );
}
