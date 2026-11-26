import { afterEach, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { WhyTip } from "../src/components/Visual";

afterEach(cleanup);

test("a help tip opens in a dialog and returns focus when closed", async () => {
  const user = userEvent.setup();
  render(
    <WhyTip label="Why a separate gas wallet?">
      <p>Sweeper bots steal ETH sent to the compromised wallet.</p>
    </WhyTip>,
  );
  const trigger = screen.getByRole("button", { name: "Why a separate gas wallet?" });
  expect(screen.queryByRole("dialog")).toBeNull();

  await user.click(trigger);
  expect(screen.getByRole("dialog", { name: "Why a separate gas wallet?" })).toBeDefined();
  expect(screen.getByText(/Sweeper bots/)).toBeDefined();
  expect(document.activeElement).toBe(screen.getByRole("button", { name: "Close" }));

  await user.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(document.activeElement).toBe(trigger);
});
