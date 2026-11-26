import { afterEach, beforeEach, expect, test } from "bun:test";
// Queries come from render(), not screen: types.test.ts swaps the global document mid-run.
import { cleanup, fireEvent, render } from "@testing-library/react";
import { ThemeSwitch } from "../src/components/ThemeSwitch";
import { loadThemeChoice, THEME_STORAGE_KEY } from "../src/theme";

beforeEach(() => {
  localStorage.clear();
  delete document.documentElement.dataset.theme;
});
afterEach(cleanup);

test("choosing a theme applies it and remembers it", () => {
  const { getByRole } = render(<ThemeSwitch />);
  expect(getByRole("button", { name: "System" }).getAttribute("aria-pressed")).toBe("true");

  fireEvent.click(getByRole("button", { name: "Dark" }));
  expect(document.documentElement.dataset.theme).toBe("dark");
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");

  fireEvent.click(getByRole("button", { name: "Light" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
});

test("going back to system forgets the stored choice", () => {
  localStorage.setItem(THEME_STORAGE_KEY, "dark");
  const { getByRole } = render(<ThemeSwitch />);
  expect(getByRole("button", { name: "Dark" }).getAttribute("aria-pressed")).toBe("true");

  fireEvent.click(getByRole("button", { name: "System" }));
  expect(localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  expect(document.documentElement.dataset.theme).toMatch(/^(light|dark)$/);
});

test("ignores an unknown stored value", () => {
  localStorage.setItem(THEME_STORAGE_KEY, "sepia");
  expect(loadThemeChoice()).toBe("system");
});
