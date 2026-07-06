import { useCallback, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "trax-web-theme";

// apps/web convention: :root is the DARK default; the `.light` class opts into
// the light override (globals.css). So light = add class, dark = remove class.
// (This is the opposite of apps/planner-ui's data-theme attribute mechanism.)
function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("light", theme === "light");
}

function readInitialTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark"; // localStorage unavailable → dark default
  }
}

// Dark-first, user-toggleable theme. No prefers-color-scheme fallback — dark is
// the deliberate default (matches the CSS :root default), not inferred from OS.
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
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* localStorage unavailable — the class still applies for this session */
      }
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
