import { afterEach, beforeEach, expect, test } from "bun:test";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import type { OnchainReader } from "../src/discovery/onchain";

const emptyScan = async () => ({ candidates: [], errors: [], warnings: [] });

const COMPROMISED = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const DESTINATION = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";

const noopOnchain: OnchainReader = {
  readWalletSnapshot: async () => ({ ethBalance: 0n, ethFormatted: "0 ETH", nonce: 0, kind: "eoa" }),
  readErc20Info: async () => ({ name: null, symbol: null, decimals: null, balance: null }),
  readErc721Info: async () => ({ name: null, symbol: null, owner: null }),
  readErc1155Info: async () => ({ balance: null }),
  readOwnershipInfo: async () => ({ owner: null, pendingOwner: null, name: null }),
};

beforeEach(() => localStorage.clear());
afterEach(cleanup);

function planJson(): Record<string, unknown> {
  return JSON.parse(screen.getByLabelText("Plan JSON").textContent ?? "");
}

async function openEmergency(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
}

async function goToStep(user: ReturnType<typeof userEvent.setup>, name: RegExp) {
  const nav = screen.getByRole("navigation", { name: "Rescue steps" });
  await user.click(within(nav).getByRole("button", { name }));
}

async function paste(user: ReturnType<typeof userEvent.setup>, label: string, value: string) {
  const input = screen.getByLabelText(label);
  await user.click(input);
  await user.paste(value);
}

test("initial state shows the two doors and visual education until a mode is chosen", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  expect(screen.getByRole("button", { name: /My wallet is compromised/i })).toBeDefined();
  expect(screen.getByRole("button", { name: /Prepare a break-glass plan/i })).toBeDefined();
  expect(screen.getByRole("heading", { name: "How it works" })).toBeDefined();
  expect(screen.queryByLabelText("Compromised wallet")).toBeNull();

  await user.click(screen.getByRole("button", { name: /Prepare a break-glass plan/i }));
  expect(screen.getByRole("heading", { name: "Before an emergency" })).toBeDefined();
  await goToStep(user, /Review & save/i);
  expect(screen.getByRole("heading", { name: "Save your plan" })).toBeDefined();

  await user.click(screen.getByRole("button", { name: "Active emergency" }));
  await goToStep(user, /Wallets/i);
  expect(screen.getByLabelText("Compromised wallet")).toBeDefined();

  await user.click(screen.getByRole("button", { name: "Start over" }));
  expect(screen.queryByLabelText("Compromised wallet")).toBeNull();
  expect(screen.getByRole("button", { name: /My wallet is compromised/i })).toBeDefined();
});

test("how it works modal opens and closes without losing form input", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await paste(user, "Compromised wallet", COMPROMISED);

  await user.click(screen.getByRole("button", { name: "How it works" }));
  expect(screen.getByRole("dialog", { name: /How ETH Rescue works/i })).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Close guide" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.getByLabelText("Compromised wallet")).toHaveProperty("value", COMPROMISED);
});

test("builds an exportable plan step-by-step from the form", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);

  await paste(user, "Compromised wallet", COMPROMISED.toLowerCase());
  await paste(user, "Destination wallet", DESTINATION);

  await goToStep(user, /Rescue plan/i);
  await user.click(await screen.findByRole("button", { name: /ERC-20 tokens/i }));
  await paste(user, "Contract", TOKEN);
  await paste(user, "Amount (base units)", "1000");

  await goToStep(user, /Review & run/i);
  const download = screen.getByRole("button", { name: "Download plan.json" });
  expect(download).toHaveProperty("disabled", false);

  expect(planJson()).toEqual({
    version: 1,
    network: "mainnet",
    compromised: COMPROMISED,
    destination: DESTINATION,
    options: { priority_fee_gwei: "0", max_blocks: 25, legacy_fallback: true },
    actions: [{ type: "erc20", contract: TOKEN, amount: "1000" }],
  });
});

test("clicking an issue in review jumps back to the relevant step", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await goToStep(user, /Review & run/i);

  const download = screen.getByRole("button", { name: "Download plan.json" });
  expect(download).toHaveProperty("disabled", true);

  await user.click(screen.getByRole("button", { name: /Compromised wallet/i }));
  expect(screen.getByLabelText("Compromised wallet")).toBeDefined();
});

test("refuses a pasted private key", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await paste(user, "Destination wallet", `0x${"ab".repeat(32)}`);
  expect(screen.getByLabelText("Destination wallet")).toHaveProperty("value", "");
  expect(screen.getByRole("alert").textContent).toContain("looks like a private key");
  expect(JSON.stringify(localStorage)).not.toContain("abab");
});

test("shows field errors once a value is entered", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await paste(user, "Compromised wallet", COMPROMISED);
  await paste(user, "Destination wallet", COMPROMISED);
  const destination = screen.getByLabelText("Destination wallet");
  expect(destination.getAttribute("aria-invalid")).toBe("true");
  const errorId = destination.getAttribute("aria-describedby") ?? "";
  expect(document.getElementById(errorId)?.textContent).toBe("must differ from the compromised wallet");
});

test("imports a plan file", async () => {
  const user = userEvent.setup();
  const { container } = render(<App onchain={noopOnchain} scan={emptyScan} />);
  const plan = {
    version: 1,
    network: "mainnet",
    compromised: COMPROMISED,
    destination: DESTINATION,
    actions: [{ type: "ownership", contract: TOKEN, label: "Creator contract" }],
  };
  const file = new File([JSON.stringify(plan)], "plan.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);

  expect(await screen.findByText("Creator contract")).toBeDefined();
  expect(planJson()).toMatchObject({ ...plan, options: { max_blocks: 25 } });
});

test("reports an unreadable import", async () => {
  const user = userEvent.setup();
  const { container } = render(<App onchain={noopOnchain} scan={emptyScan} />);
  const file = new File(["{nope"], "broken.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
  expect((await screen.findByRole("alert")).textContent).toStartWith("Could not import broken.json");
});

test("custom actions get one input per parameter and a destination-address quick fill", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await paste(user, "Destination wallet", DESTINATION);
  await goToStep(user, /Rescue plan/i);
  await user.click(screen.getByRole("button", { name: /Custom call/i }));
  await paste(user, "Function signature", "transferPunk(address,uint256)");
  await user.click(screen.getByRole("button", { name: /Use destination wallet/i }));
  await paste(user, "arg 2 (uint256)", "78");

  await goToStep(user, /Review & run/i);
  const [action] = planJson().actions as Record<string, unknown>[];
  expect(action.args).toEqual([DESTINATION, "78"]);
});

test("a non-canonical signature names the canonical form", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await goToStep(user, /Rescue plan/i);
  await user.click(screen.getByRole("button", { name: /Custom call/i }));
  await paste(user, "Function signature", "transferPunk(address to, uint punkIndex)");
  expect(screen.getAllByText(/use transferPunk\(address,uint256\)/).length).toBeGreaterThan(0);
});

test("custom arguments are never saved in the browser", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await goToStep(user, /Rescue plan/i);
  await user.click(screen.getByRole("button", { name: /Custom call/i }));
  await paste(user, "Function signature", "f(uint256)");
  await paste(user, "arg 1 (uint256)", "78");
  const stored = JSON.parse(localStorage.getItem("eth-rescue:draft") ?? "{}");
  expect(stored.plan.actions[0].signature).toBe("f(uint256)");
  expect(stored.plan.actions[0].args).toEqual([]);
});

test("install guide follows the chosen operating system", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  await goToStep(user, /Review & run/i);
  await user.click(screen.getByRole("button", { name: "Windows" }));
  expect(screen.getByLabelText("Assistant prompt").textContent).toContain("install.ps1");
  await user.click(screen.getByRole("button", { name: "macOS" }));
  expect(screen.getByLabelText("Assistant prompt").textContent).toContain("install.sh");
});

test("the network picker and testnet plans are absent unless enabled", async () => {
  const user = userEvent.setup();
  const { container } = render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  expect(screen.queryByLabelText("Network")).toBeNull();
  const plan = { version: 1, network: "sepolia", compromised: COMPROMISED, destination: DESTINATION, actions: [] };
  const file = new File([JSON.stringify(plan)], "rehearsal.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);
  expect((await screen.findByRole("alert")).textContent).toContain('network "sepolia" is not supported here');
  await goToStep(user, /Review & run/i);
  expect(planJson().network).toBe("mainnet");
});

test("rescue options live in settings, and an options problem opens settings", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await openEmergency(user);
  expect(screen.queryByLabelText("Priority fee (gwei)")).toBeNull();

  await user.click(screen.getByRole("button", { name: "Settings" }));
  const dialog = screen.getByRole("dialog", { name: "Settings" });
  await user.click(within(dialog).getByLabelText("Retry failed actions as direct transactions"));
  const maxBlocks = within(dialog).getByLabelText("Max blocks");
  await user.clear(maxBlocks);
  await user.type(maxBlocks, "0");
  await user.click(within(dialog).getByRole("button", { name: "Close settings" }));

  await goToStep(user, /Review & run/i);
  expect((planJson().options as Record<string, unknown>).legacy_fallback).toBe(false);
  await user.click(screen.getByRole("button", { name: /Max blocks/ }));
  expect(screen.getByRole("dialog", { name: "Settings" })).toBeDefined();
});

test("moving to another step scrolls back up to the steps when they are out of view", async () => {
  const user = userEvent.setup();
  render(<App onchain={noopOnchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
  const stepper = screen.getByRole("navigation", { name: "Rescue steps" });
  const scrolled: Element[] = [];
  stepper.scrollIntoView = function (this: Element) {
    scrolled.push(this);
  };

  stepper.getBoundingClientRect = () => ({ top: 0 }) as DOMRect;
  await goToStep(user, /Rescue plan/i);
  expect(scrolled).toEqual([]);

  stepper.getBoundingClientRect = () => ({ top: -800 }) as DOMRect;
  await user.click(await screen.findByRole("button", { name: /Next: Review/i }));
  expect(scrolled).toEqual([stepper]);
});
