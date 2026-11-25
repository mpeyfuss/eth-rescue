import { expect, test } from "bun:test";
import { assistantPrompt, COMMANDS, detectOs, OS_GUIDES } from "../src/guide";

test("detectOs reads the user agent", () => {
  expect(detectOs("Mozilla/5.0 (Windows NT 10.0; Win64; x64)")).toBe("windows");
  expect(detectOs("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_5)")).toBe("macos");
  expect(detectOs("Mozilla/5.0 (X11; Linux x86_64)")).toBe("linux");
});

test.each(Object.keys(OS_GUIDES) as (keyof typeof OS_GUIDES)[])("%s prompt names the exact commands", (os) => {
  const prompt = assistantPrompt(os);
  expect(prompt).toContain(OS_GUIDES[os].installUv);
  for (const command of Object.values(COMMANDS)) expect(prompt).toContain(command);
  expect(prompt).toContain("Never ask me for a private key or seed phrase");
});
