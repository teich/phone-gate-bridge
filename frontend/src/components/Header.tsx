import type { ConnectionStatus } from "../api/useDashboardState";
import type { Theme } from "../hooks/useTheme";
import { Icon } from "./Icon";

interface HeaderProps {
  door: string;
  connection: ConnectionStatus;
  theme: Theme;
  onToggleTheme: () => void;
}

const connectionLabel: Record<ConnectionStatus, string> = {
  live: "Live",
  retry: "Reconnecting",
  down: "Offline",
};

export function Header({
  door,
  connection,
  theme,
  onToggleTheme,
}: HeaderProps) {
  const nextTheme = theme === "light" ? "dark" : "light";
  return (
    <header className="topbar">
      <div className="brand">
        <span className="brand-mark" aria-hidden="true" />
        <span className="brand-text">
          <strong>{door}</strong>
          <span>Phone Gate Bridge</span>
        </span>
      </div>
      <div className="topbar-actions">
        <p className="live" data-status={connection}>
          <span className="live-dot" aria-hidden="true" />
          <span>{connectionLabel[connection]}</span>
        </p>
        <button
          className="icon-button"
          type="button"
          aria-label={`Switch to ${nextTheme} theme`}
          onClick={onToggleTheme}
        >
          <Icon name={nextTheme === "light" ? "sun" : "moon"} />
        </button>
      </div>
    </header>
  );
}
