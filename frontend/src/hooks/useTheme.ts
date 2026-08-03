import { useLayoutEffect, useState } from "react";

export type Theme = "dark" | "light";

function initialTheme(): Theme {
  const saved = window.localStorage.getItem("gate-theme");
  if (saved === "dark" || saved === "light") return saved;
  return window.matchMedia("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(initialTheme);

  useLayoutEffect(() => {
    document.documentElement.dataset.theme = theme;
    window.localStorage.setItem("gate-theme", theme);
  }, [theme]);

  const toggle = () => setTheme((current) => (current === "light" ? "dark" : "light"));
  return [theme, toggle];
}
