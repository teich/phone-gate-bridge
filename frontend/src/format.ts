const relativeFormatter = new Intl.RelativeTimeFormat(undefined, { numeric: "auto" });

export const clockFormatter = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
});

export const timestampFormatter = new Intl.DateTimeFormat(undefined, {
  hour: "numeric",
  minute: "2-digit",
  second: "2-digit",
});

export function relativeTime(timestampSeconds: number, nowMs = Date.now()): string {
  const deltaSeconds = timestampSeconds - nowMs / 1_000;
  const absolute = Math.abs(deltaSeconds);
  if (absolute < 45) return "just now";
  const units: Array<[Intl.RelativeTimeFormatUnit, number]> = [
    ["minute", 60],
    ["hour", 3_600],
    ["day", 86_400],
  ];
  let chosen = units[0];
  for (const unit of units) {
    if (absolute >= unit[1]) chosen = unit;
  }
  return relativeFormatter.format(Math.round(deltaSeconds / chosen[1]), chosen[0]);
}

export function duration(seconds: number): string {
  const total = Math.max(0, Math.round(seconds));
  const hours = Math.floor(total / 3_600);
  const minutes = Math.floor((total % 3_600) / 60);
  const remainder = total % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(remainder).padStart(2, "0")}`;
  }
  return `${minutes}:${String(remainder).padStart(2, "0")}`;
}

export function prettyNumber(value: string): string {
  const match = /^\+1(\d{3})(\d{3})(\d{4})$/.exec(value);
  return match === null ? value : `(${match[1]}) ${match[2]}-${match[3]}`;
}
