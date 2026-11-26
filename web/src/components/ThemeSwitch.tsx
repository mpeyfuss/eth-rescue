import { useEffect, useState } from "react";
import {
  applyTheme,
  loadThemeChoice,
  onSystemThemeChange,
  saveThemeChoice,
  THEME_CHOICES,
  type ThemeChoice,
} from "../theme";

export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(loadThemeChoice);

  useEffect(() => {
    applyTheme(choice);
    if (choice !== "system") return;
    return onSystemThemeChange(() => applyTheme("system"));
  }, [choice]);

  function choose(next: ThemeChoice) {
    saveThemeChoice(next);
    setChoice(next);
  }

  return (
    <div className="theme-switch" role="group" aria-label="Theme">
      {THEME_CHOICES.map(({ value, label }) => (
        <button key={value} type="button" aria-pressed={choice === value} onClick={() => choose(value)}>
          {label}
        </button>
      ))}
    </div>
  );
}
