import { useCallback, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "trax-web-theme";

// apps/web convention (Aeronta parent-brand): :root is the LIGHT default; the
// `.dark` class opts into the dark override (globals.css). So dark = add class,
// light = remove class. Stored "light"/"dark" values from the old dark-first
// convention remain valid — only the unset default changed.
function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("dark", theme === "dark");
}

function readInitialTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "dark" ? "dark" : "light";
  } catch {
    return "light"; // localStorage unavailable → light default
  }
}

// Light-first, user-toggleable theme. No prefers-color-scheme fallback — light
// is the deliberate default (matches the CSS :root default and the parent
// application at aeronta.com), not inferred from OS.
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
