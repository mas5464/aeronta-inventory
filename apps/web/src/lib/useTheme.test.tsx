import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useTheme } from "@/lib/useTheme";

const KEY = "trax-web-theme";

afterEach(() => {
  localStorage.clear();
  document.documentElement.className = "";
});

describe("useTheme", () => {
  it("defaults to dark when nothing is stored (no .light class)", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("reads a stored 'light' preference and applies the .light class on mount", () => {
    localStorage.setItem(KEY, "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("treats any non-'light' stored value as dark (dark-first)", () => {
    localStorage.setItem(KEY, "garbage");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("toggles dark→light: adds .light and persists 'light'", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("light");
  });

  it("toggles light→dark: removes .light and persists 'dark'", () => {
    localStorage.setItem(KEY, "light");
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("dark");
  });
});
