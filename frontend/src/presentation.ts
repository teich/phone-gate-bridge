export type Tone =
  | "good"
  | "warning"
  | "serious"
  | "critical"
  | "neutral"
  | "muted";

export type EventGroup = "opens" | "calls" | "denied" | "errors" | "system";

export interface EventPresentation {
  label: string;
  tone: Tone;
  group: EventGroup;
  icon: IconName;
}

export type IconName =
  | "unlock"
  | "timer"
  | "phone"
  | "keypad"
  | "shield"
  | "alert"
  | "eye"
  | "dot"
  | "sun"
  | "moon";

const eventPresentation: Record<string, EventPresentation> = {
  unlock_success: { label: "Gate opened", tone: "good", group: "opens", icon: "unlock" },
  hold_open: { label: "Hold started", tone: "warning", group: "opens", icon: "timer" },
  hold_cleared: { label: "Hold cleared", tone: "neutral", group: "opens", icon: "timer" },
  caller_prompted: {
    label: "Caller prompted",
    tone: "neutral",
    group: "calls",
    icon: "phone",
  },
  twilio_request: { label: "Call received", tone: "muted", group: "calls", icon: "phone" },
  invalid_digit: {
    label: "No valid keypress",
    tone: "warning",
    group: "calls",
    icon: "keypad",
  },
  caller_blocked: {
    label: "Caller blocked",
    tone: "serious",
    group: "denied",
    icon: "shield",
  },
  action_unauthorized: {
    label: "Action blocked",
    tone: "serious",
    group: "denied",
    icon: "shield",
  },
  signature_invalid: {
    label: "Invalid signature",
    tone: "critical",
    group: "denied",
    icon: "shield",
  },
  unlock_failed: {
    label: "Unlock failed",
    tone: "critical",
    group: "errors",
    icon: "alert",
  },
  action_failed: {
    label: "Action failed",
    tone: "critical",
    group: "errors",
    icon: "alert",
  },
  action_unknown: {
    label: "Action result unknown",
    tone: "critical",
    group: "errors",
    icon: "alert",
  },
  allowed_callers_error: {
    label: "Allowlist unreadable",
    tone: "critical",
    group: "errors",
    icon: "alert",
  },
  dashboard_view: {
    label: "Dashboard opened",
    tone: "muted",
    group: "system",
    icon: "eye",
  },
  dashboard_denied: {
    label: "Dashboard blocked",
    tone: "serious",
    group: "denied",
    icon: "shield",
  },
};

export function presentEvent(event: string): EventPresentation {
  return (
    eventPresentation[event] ?? {
      label: event.replaceAll("_", " ").replace(/^\w/, (letter) => letter.toUpperCase()),
      tone: "muted",
      group: "system",
      icon: "dot",
    }
  );
}

export const statPresentation = {
  opens_today: { label: "Opens today", tone: "neutral" },
  opens_week: { label: "Opens this week", tone: "neutral" },
  callers_week: { label: "Callers this week", tone: "neutral" },
  denied_week: { label: "Denied this week", tone: "serious" },
  errors_week: { label: "Failures this week", tone: "critical" },
} as const;
