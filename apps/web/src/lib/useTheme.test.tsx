import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useTheme } from "@/lib/useTheme";

const KEY = "trax-web-theme";

afterEach(() => {
  localStorage.clear();
  document.documentElement.className = "";
});

describe("useTheme", () => {
  it("defaults to light when nothing is stored (no .dark class)", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("reads a stored 'dark' preference and applies the .dark class on mount", () => {
    localStorage.setItem(KEY, "dark");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
  });

  it("treats any non-'dark' stored value as light (light-first)", () => {
    localStorage.setItem(KEY, "garbage");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
  });

  it("toggles light→dark: adds .dark and persists 'dark'", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("dark")).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("dark");
  });

  it("toggles dark→light: removes .dark and persists 'light'", () => {
    localStorage.setItem(KEY, "dark");
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("dark")).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("light");
  });
});
