// Gate dashboard. Read-only: everything here polls and renders, nothing writes.

const POLL_MS = 3000;
const MAX_BACKOFF_MS = 30000;
const SVG_NS = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);
const rtf = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });
const clockFmt = new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" });
const stampFmt = new Intl.DateTimeFormat(undefined, {
  hour: "numeric", minute: "2-digit", second: "2-digit",
});

const dom = {
  root: document.documentElement,
  hero: $("hero"),
  heroState: $("hero-state"),
  heroMode: $("hero-mode"),
  heroNote: $("hero-note"),
  heroMeta: $("hero-meta"),
  dial: $("hero-dial"),
  ring: $("ring"),
  ringValue: $("ring-value"),
  ringUnit: $("ring-unit"),
  ringCaption: $("ring-caption"),
  stats: $("stats"),
  chartPlot: $("chart-plot"),
  chartAxis: $("chart-axis"),
  chartTotal: $("chart-total"),
  chartNote: $("chart-table-note"),
  feed: $("feed"),
  feedEmpty: $("feed-empty"),
  feedCount: $("feed-count"),
  filters: $("filters"),
  live: $("live"),
  updated: $("updated"),
  tooltip: $("tooltip"),
};

let state = JSON.parse($("bootstrap").textContent);
let filter = "all";
let failures = 0;
let ringAnimation = null;
let countdownTarget = null;
let holdTotals = new Map();

// ---------------------------------------------------------------- utilities

function el(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") node.className = value;
    else if (key === "text") node.textContent = value;
    else node.setAttribute(key, value);
  }
  for (const child of children) if (child) node.append(child);
  return node;
}

function icon(name) {
  const svg = document.createElementNS(SVG_NS, "svg");
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG_NS, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

function relative(ts) {
  const delta = ts * 1000 - Date.now();
  const abs = Math.abs(delta) / 1000;
  if (abs < 45) return "just now";
  const units = [["minute", 60], ["hour", 3600], ["day", 86400]];
  let chosen = units[0];
  for (const unit of units) if (abs >= unit[1]) chosen = unit;
  return rtf.format(Math.round(delta / 1000 / chosen[1]), chosen[0]);
}

function duration(seconds) {
  const total = Math.max(0, Math.round(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function prettyNumber(value) {
  if (!value) return value;
  const match = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(value);
  return match ? `(${match[1]}) ${match[2]}-${match[3]}` : value;
}

// -------------------------------------------------------------------- hero

function heroCopy(gate, lastOpen) {
  switch (gate.state) {
    case "held_open":
      return gate.indefinite
        ? "The gate is being held open with no scheduled close. It stays open until the hold is cleared."
        : "The gate is being held open and will close on its own when the timer runs out.";
    case "open":
      return lastOpen
        ? `The gate is standing open. Last opened ${relative(lastOpen.ts)} by ${lastOpen.name || prettyNumber(lastOpen.caller) || "an unknown caller"}.`
        : "The gate is standing open.";
    case "opening":
      return "Unlock relay fired. The gate is swinging now.";
    case "secured":
      return lastOpen
        ? `Locked. Last opened ${relative(lastOpen.ts)} by ${lastOpen.name || prettyNumber(lastOpen.caller) || "an unknown caller"}.`
        : "Locked. No gate opens recorded yet.";
    default:
      return gate.error
        ? `Could not read the gate status from UniFi Access: ${gate.error}`
        : "Could not read the gate status from UniFi Access.";
  }
}

function renderHero(next) {
  const gate = next.gate;
  dom.heroState.textContent = gate.label;
  dom.heroMode.textContent = "Gate status";
  dom.heroNote.textContent = heroCopy(gate, next.last_open);

  dom.heroMeta.replaceChildren();
  const meta = [];
  if (gate.state === "held_open" && gate.until) {
    meta.push(["Closes at", clockFmt.format(gate.until * 1000)]);
  }
  if (gate.mode) meta.push(["Reported", gate.mode.replace(/_/g, " ")]);
  if (next.last_open) {
    meta.push(["Last open", relative(next.last_open.ts)]);
    const who = next.last_open.name || prettyNumber(next.last_open.caller);
    if (who) meta.push(["By", who]);
  }
  for (const [term, value] of meta) {
    dom.heroMeta.append(el("div", {}, el("dt", { text: term }), el("dd", { text: value })));
  }

  renderDial(gate);
}

function renderDial(gate) {
  const showDial = gate.state === "held_open" || gate.state === "opening";
  dom.dial.dataset.visible = String(showDial);
  if (!showDial) {
    stopRing();
    return;
  }

  if (gate.indefinite) {
    stopRing();
    dom.ring.style.setProperty("--ring-progress", "1");
    dom.ringValue.textContent = "HOLD";
    dom.ringUnit.textContent = "no timer";
    dom.ringCaption.textContent = "Clear the hold in UniFi Access to close.";
    return;
  }

  const remaining = Math.max(0, gate.remaining ?? 0);
  const total = holdSpan(gate, remaining);
  dom.ringUnit.textContent = gate.state === "opening" ? "relay" : "remaining";
  dom.ringCaption.textContent = gate.state === "opening"
    ? "Momentary unlock"
    : `${duration(total)} hold`;

  startRing(remaining / (total || 1), remaining);
}

// The controller reports an end time but not the original duration, so the
// span comes from a recorded hold event when there is one and otherwise from
// the largest remaining time we have seen for this expiry.
function holdSpan(gate, remaining) {
  if (gate.started_at && gate.until) return Math.max(1, gate.until - gate.started_at);
  const key = String(gate.until ?? "none");
  const seen = holdTotals.get(key) ?? 0;
  if (remaining > seen) holdTotals.set(key, remaining);
  if (holdTotals.size > 8) holdTotals = new Map([[key, holdTotals.get(key)]]);
  return Math.max(1, holdTotals.get(key));
}

function startRing(progress, remaining) {
  const clamped = Math.min(1, Math.max(0, progress));
  countdownTarget = Date.now() + remaining * 1000;
  tickCountdown();

  const reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
  ringAnimation?.cancel();
  if (reduced || remaining <= 0) {
    dom.ring.style.setProperty("--ring-progress", String(clamped));
    return;
  }
  // One long linear animation instead of a per-frame write: the ring drains
  // smoothly between polls at no scripting cost.
  ringAnimation = dom.ring.animate(
    { "--ring-progress": [clamped, 0] },
    { duration: remaining * 1000, easing: "linear", fill: "forwards" },
  );
}

function stopRing() {
  ringAnimation?.cancel();
  ringAnimation = null;
  countdownTarget = null;
}

function tickCountdown() {
  if (countdownTarget === null) return;
  const remaining = (countdownTarget - Date.now()) / 1000;
  dom.ringValue.textContent = remaining <= 0 ? "0:00" : duration(remaining);
}

// ------------------------------------------------------------------- stats

function renderStats(stats) {
  dom.stats.replaceChildren(
    ...stats.map((stat) =>
      el(
        "article",
        { class: `stat tone-${stat.tone}`, "data-zero": String(!stat.value) },
        el("span", { class: "stat-value", text: String(stat.value) }),
        el("span", { class: "stat-label", text: stat.label }),
      ),
    ),
  );
}

// ------------------------------------------------------------------- chart

function renderChart(chart) {
  const buckets = chart.buckets ?? [];
  const max = chart.max ?? 0;
  dom.chartTotal.textContent = String(chart.total ?? 0);

  const bars = buckets.map((bucket) => {
    // Label every bar at the peak value — labelling only the first of a tie
    // reads as a mistake.
    const isPeak = max > 0 && bucket.count === max;
    const bar = el("div", {
      class: "bar",
      "data-zero": String(bucket.count === 0),
      "data-count": String(bucket.count),
      "data-peak": String(isPeak),
      tabindex: "0",
      role: "listitem",
      "aria-label": `${clockFmt.format(bucket.start * 1000)}: ${bucket.count} opens`,
    });
    bar.style.setProperty("--bar-scale", max > 0 ? String(bucket.count / max) : "0");
    bar.addEventListener("pointerenter", () => showTooltip(bar, bucket));
    bar.addEventListener("focus", () => showTooltip(bar, bucket));
    bar.addEventListener("pointerleave", hideTooltip);
    bar.addEventListener("blur", hideTooltip);
    return bar;
  });
  dom.chartPlot.replaceChildren(...bars);
  dom.chartPlot.setAttribute("role", "list");

  dom.chartAxis.replaceChildren(
    ...buckets.map((bucket, index) => {
      const hour = new Date(bucket.start * 1000).getHours();
      const tick = index === buckets.length - 1 || hour % 6 === 0;
      return el("span", {
        "data-tick": String(tick),
        text: index === buckets.length - 1 ? "now" : `${hour}:00`,
      });
    }),
  );

  dom.chartNote.textContent = buckets
    .map((b) => `${clockFmt.format(b.start * 1000)}: ${b.count}`)
    .join(", ");
}

function showTooltip(bar, bucket) {
  const box = bar.getBoundingClientRect();
  dom.tooltip.replaceChildren(
    el("strong", { text: `${bucket.count} ${bucket.count === 1 ? "open" : "opens"}` }),
    el("span", { text: clockFmt.format(bucket.start * 1000) }),
  );
  dom.tooltip.hidden = false;
  dom.tooltip.style.left = `${box.left + box.width / 2}px`;
  dom.tooltip.style.top = `${box.top}px`;
}

function hideTooltip() {
  dom.tooltip.hidden = true;
}

// -------------------------------------------------------------------- feed

function renderFeed(events) {
  dom.feed.replaceChildren(
    ...events.map((item) => {
      const sub = el("p", { class: "row-sub" });
      const who = item.name || prettyNumber(item.caller);
      if (who) sub.append(el("span", { text: who }));
      if (item.name && item.caller) sub.append(el("code", { text: prettyNumber(item.caller) }));
      if (item.detail) sub.append(el("span", { text: item.detail }));
      if (item.steps?.length) {
        sub.append(el("span", { class: "row-steps", text: `via ${item.steps.join(" → ")}` }));
      }

      const row = el(
        "li",
        { class: `row tone-${item.tone}`, "data-group": item.group },
        el("span", { class: "row-icon" }, icon(item.icon)),
        el("div", { class: "row-main" }, el("p", { class: "row-label", text: item.label }), sub),
        el("time", {
          class: "row-time",
          datetime: new Date(item.ts * 1000).toISOString(),
          title: stampFmt.format(item.ts * 1000),
          "data-ts": String(item.ts),
          text: relative(item.ts),
        }),
      );
      return row;
    }),
  );
  applyFilter();
}

function applyFilter() {
  let shown = 0;
  for (const row of dom.feed.children) {
    const visible = filter === "all" || row.dataset.group === filter;
    row.hidden = !visible;
    if (visible) shown += 1;
  }
  dom.feedCount.textContent = String(shown);
  dom.feedEmpty.hidden = shown > 0;
}

function refreshRelativeTimes() {
  for (const node of dom.feed.querySelectorAll("time[data-ts]")) {
    node.textContent = relative(Number(node.dataset.ts));
  }
}

// ------------------------------------------------------------------ render

function render(next, { animateState = false } = {}) {
  const previous = state;
  state = next;

  const commit = () => {
    dom.root.dataset.state = next.gate.state;
    renderHero(next);
    renderStats(next.stats ?? []);
    renderChart(next.chart ?? {});
    renderFeed(next.events ?? []);
    dom.updated.textContent = `Updated ${stampFmt.format((next.generated_at ?? Date.now() / 1000) * 1000)}`;
  };

  const changed = animateState && previous?.gate?.state !== next.gate.state;
  if (changed && document.startViewTransition) document.startViewTransition(commit);
  else commit();
}

// ------------------------------------------------------------------- poll

async function poll() {
  try {
    const response = await fetch("/dashboard/api/state", {
      headers: { accept: "application/json" },
      cache: "no-store",
    });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json(), { animateState: true });
    failures = 0;
    setLive("live", "Live");
  } catch (error) {
    failures += 1;
    setLive(failures > 3 ? "down" : "retry", failures > 3 ? "Offline" : "Reconnecting");
  }
  schedule();
}

function schedule() {
  const delay = failures === 0
    ? POLL_MS
    : Math.min(MAX_BACKOFF_MS, POLL_MS * 2 ** Math.min(failures, 5));
  clearTimeout(schedule.timer);
  if (document.visibilityState === "visible") schedule.timer = setTimeout(poll, delay);
}

function setLive(status, label) {
  dom.live.dataset.status = status;
  dom.live.querySelector(".live-text").textContent = label;
}

// ------------------------------------------------------------------ wiring

dom.filters.addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  filter = button.dataset.filter;
  for (const chip of dom.filters.children) {
    chip.setAttribute("aria-pressed", String(chip === button));
  }
  if (document.startViewTransition) document.startViewTransition(applyFilter);
  else applyFilter();
});

$("theme-toggle").addEventListener("click", () => {
  const next = dom.root.dataset.theme === "light" ? "dark" : "light";
  const swap = () => {
    dom.root.dataset.theme = next;
    localStorage.setItem("gate-theme", next);
  };
  if (document.startViewTransition) document.startViewTransition(swap);
  else swap();
});

document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") poll();
});

// Pin the theme on load so the toggle icon always matches what is on screen.
dom.root.dataset.theme =
  localStorage.getItem("gate-theme") ??
  (matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark");

render(state);
setInterval(tickCountdown, 250);
setInterval(refreshRelativeTimes, 15000);
schedule();
