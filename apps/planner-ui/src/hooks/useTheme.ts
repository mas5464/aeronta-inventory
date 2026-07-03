import { useCallback, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "trax-io-theme";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

function readInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" ? "light" : "dark"; // dark-first: any other/missing value defaults to dark
}

// Dark-first, user-toggleable theme. No prefers-color-scheme fallback — dark is the
// deliberate default for a first-time visitor, not inferred from OS settings.
export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = readInitialTheme();
    applyTheme(initial);
    return initial;
  });

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      applyTheme(next);
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
