import { afterEach, beforeEach, expect, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";

const VICTIM = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const TOKEN = "0x41A322b28D0fF354040e2CbC676F0320d8c8850d";

beforeEach(() => localStorage.clear());
afterEach(cleanup);

function planJson(): Record<string, unknown> {
  return JSON.parse(screen.getByLabelText("Plan JSON").textContent ?? "");
}

async function openEmergency(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
}

async function paste(user: ReturnType<typeof userEvent.setup>, label: string, value: string) {
  const input = screen.getByLabelText(label);
  await user.click(input);
  await user.paste(value);
}

test("initial state shows only the two doors until one is chosen", async () => {
  const user = userEvent.setup();
  render(<App />);
  expect(screen.getByRole("button", { name: /My wallet is compromised/i })).toBeDefined();
  expect(screen.getByRole("button", { name: /Prepare a break-glass plan/i })).toBeDefined();
  expect(screen.queryByLabelText("Compromised wallet")).toBeNull();

  await user.click(screen.getByRole("button", { name: /Prepare a break-glass plan/i }));
  expect(screen.getByRole("heading", { name: "Before an emergency" })).toBeDefined();
  expect(screen.getByRole("heading", { name: "Verify and store your break-glass plan" })).toBeDefined();
  expect(screen.getByLabelText("Compromised wallet")).toBeDefined();

  await user.click(screen.getByRole("button", { name: "Active emergency" }));
  expect(screen.getByRole("heading", { name: "Before you start" })).toBeDefined();

  await user.click(screen.getByRole("button", { name: "Start over" }));
  expect(screen.queryByLabelText("Compromised wallet")).toBeNull();
  expect(screen.getByRole("button", { name: /My wallet is compromised/i })).toBeDefined();
});

test("builds an exportable plan from the form", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  const download = screen.getByRole("button", { name: "Download plan.json" });
  expect(download).toHaveProperty("disabled", true);

  await paste(user, "Compromised wallet", VICTIM.toLowerCase());
  await paste(user, "Safe wallet", SAFE);
  await user.click(screen.getByRole("button", { name: "Add action" }));
  await paste(user, "Contract", TOKEN);
  await paste(user, "Amount (base units)", "1000");

  expect(planJson()).toEqual({
    version: 1,
    network: "mainnet",
    victim: VICTIM,
    safe: SAFE,
    options: { priority_fee_gwei: "0", max_blocks: 25, legacy_fallback: true },
    actions: [{ type: "erc20", contract: TOKEN, amount: "1000" }],
  });
  expect(download).toHaveProperty("disabled", false);
});

test("refuses a pasted private key", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  await paste(user, "Safe wallet", `0x${"ab".repeat(32)}`);
  expect(screen.getByLabelText("Safe wallet")).toHaveProperty("value", "");
  expect(screen.getByRole("alert").textContent).toContain("looks like a private key");
  expect(JSON.stringify(localStorage)).not.toContain("abab");
});

test("shows field errors once a value is entered", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  await paste(user, "Compromised wallet", VICTIM);
  await paste(user, "Safe wallet", VICTIM);
  const safe = screen.getByLabelText("Safe wallet");
  expect(safe.getAttribute("aria-invalid")).toBe("true");
  const errorId = safe.getAttribute("aria-describedby") ?? "";
  expect(document.getElementById(errorId)?.textContent).toBe("must differ from the compromised wallet");
});

test("imports a plan file", async () => {
  const user = userEvent.setup();
  const { container } = render(<App />);
  const plan = {
    version: 1,
    network: "mainnet",
    victim: VICTIM,
    safe: SAFE,
    actions: [{ type: "ownership", contract: TOKEN, label: "Creator contract" }],
  };
  const file = new File([JSON.stringify(plan)], "plan.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);

  expect(await screen.findByDisplayValue("Creator contract")).toBeDefined();
  expect(planJson()).toMatchObject({ ...plan, options: { max_blocks: 25 } });
});

test("reports an unreadable import", async () => {
  const user = userEvent.setup();
  const { container } = render(<App />);
  const file = new File(["{nope"], "broken.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
  expect((await screen.findByRole("alert")).textContent).toStartWith("Could not import broken.json");
});

test("custom actions get one input per parameter", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  await user.selectOptions(screen.getByLabelText("Action type"), "custom");
  await user.click(screen.getByRole("button", { name: "Add action" }));
  await paste(user, "Function signature", "transferPunk(address,uint256)");
  await paste(user, "arg 2 (uint256)", "78");
  const [action] = planJson().actions as Record<string, unknown>[];
  expect(action.args).toEqual(["", "78"]);
  expect(screen.getByLabelText("arg 1 (address)")).toBeDefined();
});

test("a non-canonical signature names the canonical form", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  await user.selectOptions(screen.getByLabelText("Action type"), "custom");
  await user.click(screen.getByRole("button", { name: "Add action" }));
  await paste(user, "Function signature", "transferPunk(address to, uint punkIndex)");
  expect(screen.getAllByText(/use transferPunk\(address,uint256\)/).length).toBeGreaterThan(0);
});

test("custom arguments are never saved in the browser", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  await user.selectOptions(screen.getByLabelText("Action type"), "custom");
  await user.click(screen.getByRole("button", { name: "Add action" }));
  await paste(user, "Function signature", "f(uint256)");
  await paste(user, "arg 1 (uint256)", "78");
  const stored = JSON.parse(localStorage.getItem("eth-rescue:draft") ?? "{}");
  expect(stored.plan.actions[0].signature).toBe("f(uint256)");
  expect(stored.plan.actions[0].args).toEqual([]);
});

test("install guide follows the chosen operating system", async () => {
  const user = userEvent.setup();
  render(<App />);
  await openEmergency(user);
  await user.click(screen.getByRole("button", { name: "Windows" }));
  expect(screen.getByLabelText("Assistant prompt").textContent).toContain("install.ps1");
  await user.click(screen.getByRole("button", { name: "macOS" }));
  expect(screen.getByLabelText("Assistant prompt").textContent).toContain("install.sh");
});

test("the network picker and testnet plans are absent unless enabled", async () => {
  const user = userEvent.setup();
  const { container } = render(<App />);
  await openEmergency(user);
  expect(screen.queryByLabelText("Network")).toBeNull();
  const plan = { version: 1, network: "sepolia", victim: VICTIM, safe: SAFE, actions: [] };
  const file = new File([JSON.stringify(plan)], "rehearsal.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
  expect((await screen.findByRole("alert")).textContent).toContain('network "sepolia" is not supported here');
  expect(planJson().network).toBe("mainnet");
});
