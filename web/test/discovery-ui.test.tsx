import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getAddress } from "viem";
import { App } from "../src/App";
import type { OnchainReader } from "../src/discovery/onchain";
import type { Candidate, ScanRequest } from "../src/discovery/types";
import { COMPROMISED } from "./discovery-fixtures";

const NFT = "0xb8CEf6F20A2A14C6282956C61acaDd3CC7E05fac".toLowerCase();
const SPAM = "0x2222222222222222222222222222222222222222";
const candidates: Candidate[] = [
  {
    key: `erc721:${NFT}:3`,
    group: "nfts",
    title: "Frame #3",
    detail: "Before the Frame · ERC-721",
    source: "Alchemy",
    action: { type: "erc721", contract: NFT, token_id: "3", label: "Frame #3" },
    preselect: true,
    meta: { collection: "Before the Frame", contractAddress: getAddress(NFT), tokenId: "3", spam: false },
  },
  {
    key: `erc1155:${SPAM}:1`,
    group: "nfts",
    title: "Free airdrop",
    detail: "Claim your reward · flagged as spam",
    source: "Alchemy",
    action: { type: "erc1155", contract: SPAM, token_id: "1", amount: "1", label: "Free airdrop" },
    preselect: false,
    meta: { collection: "Claim your reward", contractAddress: SPAM, tokenId: "1", spam: true },
  },
  {
    key: `ownership:${NFT}:`,
    group: "contracts",
    title: "Before the Frame",
    detail: "ERC721TL",
    source: "Transient",
    action: { type: "ownership", contract: NFT, label: "Before the Frame" },
    preselect: false,
    blockedReason: "Owned by 0x9999, not the compromised wallet.",
    meta: { contractAddress: getAddress(NFT) },
  },
];

const noopOnchain: OnchainReader = {
  readWalletSnapshot: async () => ({ ethBalance: 0n, ethFormatted: "0 ETH", nonce: 0, kind: "eoa" }),
  readErc20Info: async () => ({ name: null, symbol: null, decimals: null, balance: null }),
  readErc721Info: async () => ({ name: null, symbol: null, owner: null }),
  readErc1155Info: async () => ({ balance: null }),
  readOwnershipInfo: async () => ({ owner: null, pendingOwner: null, name: null }),
};

const scanWallet = mock(async (_request: ScanRequest) => ({ candidates, errors: [], warnings: [] }));

beforeEach(() => {
  localStorage.clear();
  scanWallet.mockClear();
});
afterEach(cleanup);

test("the rescue plan scans first, then the results can be added to the plan once", async () => {
  const user = userEvent.setup();
  render(<App scan={scanWallet} onchain={noopOnchain} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));

  const nav = () => within(screen.getByRole("navigation", { name: "Rescue steps" }));
  expect(nav().queryByRole("button", { name: /Find assets/i })).toBeNull();

  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);
  expect(scanWallet).not.toHaveBeenCalled();

  await user.click(screen.getByRole("button", { name: /Next: Rescue plan/i }));
  expect(scanWallet).toHaveBeenCalledTimes(1);
  expect(scanWallet.mock.calls[0][0]).toMatchObject({ network: "mainnet", compromised: COMPROMISED });

  // Flagged spam is hidden until opted in.
  expect(await screen.findByText("Frame #3")).toBeDefined();
  expect(screen.queryByText("Free airdrop")).toBeNull();
  await user.click(screen.getByRole("button", { name: /Show 1 flagged spam/i }));
  expect(screen.getByText("Free airdrop")).toBeDefined();

  expect(screen.getByText("Owned by 0x9999, not the compromised wallet.")).toBeDefined();
  expect(screen.getByRole("checkbox", { name: /^Before the Frame/ })).toHaveProperty("disabled", true);
  expect(screen.getByRole("checkbox", { name: /^Frame #3/ })).toHaveProperty("checked", true);

  await user.click(screen.getByRole("button", { name: "Add 1 to the plan" }));
  expect(screen.getByText("Already in the plan.")).toBeDefined();
  expect(screen.queryByRole("button", { name: /to the plan/ })).toBeNull();
  expect(screen.getByRole("button", { name: "Edit" })).toBeDefined();

  // Coming back to the step keeps the results instead of scanning again.
  await user.click(nav().getByRole("button", { name: /Wallets/i }));
  await user.click(nav().getByRole("button", { name: /Rescue plan/i }));
  expect(scanWallet).toHaveBeenCalledTimes(1);
  expect(screen.getByText("Already in the plan.")).toBeDefined();

  await user.click(nav().getByRole("button", { name: /Review & run/i }));
  const plan = JSON.parse(screen.getByLabelText("Plan JSON").textContent ?? "");
  expect(plan.actions).toEqual([{ type: "erc721", label: "Frame #3", contract: getAddress(NFT), token_id: "3" }]);
});

test("adding by hand waits for the scan to finish", async () => {
  const user = userEvent.setup();
  let finish: (value: { candidates: Candidate[]; errors: never[]; warnings: never[] }) => void = () => {};
  const slowScan = mock(
    (_request: ScanRequest) =>
      new Promise<{ candidates: Candidate[]; errors: never[]; warnings: never[] }>((resolve) => {
        finish = resolve;
      }),
  );
  render(<App scan={slowScan} onchain={noopOnchain} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);
  await user.click(screen.getByRole("button", { name: /Next: Rescue plan/i }));

  expect(screen.getByText("Available once the scan finishes.")).toBeDefined();
  expect(screen.queryByRole("button", { name: /ERC-20 tokens/i })).toBeNull();

  finish({ candidates: [], errors: [], warnings: [] });
  expect(await screen.findByRole("button", { name: /ERC-20 tokens/i })).toBeDefined();
});

test("without a compromised wallet the plan can still be built by hand", async () => {
  const user = userEvent.setup();
  render(<App scan={scanWallet} onchain={noopOnchain} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
  const nav = within(screen.getByRole("navigation", { name: "Rescue steps" }));
  await user.click(nav.getByRole("button", { name: /Rescue plan/i }));

  expect(screen.getByText(/Enter the compromised wallet on the Wallets step/)).toBeDefined();
  expect(screen.getByRole("button", { name: /ERC-20 tokens/i })).toBeDefined();
  expect(scanWallet).not.toHaveBeenCalled();
});

test("a scan that finishes after the wallet changed is ignored", async () => {
  const user = userEvent.setup();
  let finish: (value: { candidates: Candidate[]; errors: never[]; warnings: never[] }) => void = () => {};
  const slowScan = mock(
    (_request: ScanRequest) =>
      new Promise<{ candidates: Candidate[]; errors: never[]; warnings: never[] }>((resolve) => {
        finish = resolve;
      }),
  );
  render(<App scan={slowScan} onchain={noopOnchain} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
  await user.click(screen.getByRole("button", { name: "Settings" }));
  await user.click(screen.getByLabelText("Show all steps on one page"));
  await user.click(screen.getByRole("button", { name: "Close settings" }));

  const compromised = screen.getByLabelText("Compromised wallet");
  await user.click(compromised);
  await user.paste(COMPROMISED);
  expect(slowScan).toHaveBeenCalledTimes(1);
  const stale = finish;
  await user.clear(compromised);
  await user.click(compromised);
  await user.paste(getAddress("0x1111111111111111111111111111111111111111"));
  expect(slowScan).toHaveBeenCalledTimes(2);
  stale({ candidates, errors: [], warnings: [] });
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByText("Frame #3")).toBeNull();
});

test("a scan that throws can be retried", async () => {
  const user = userEvent.setup();
  const failing = mock(async (_request: ScanRequest) => {
    throw new Error("chunk failed to load");
  });
  render(<App scan={failing} onchain={noopOnchain} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);
  await user.click(screen.getByRole("button", { name: /Next: Rescue plan/i }));
  expect(await screen.findByText(/The scan failed: chunk failed to load/)).toBeDefined();
  expect(screen.getByRole("button", { name: /ERC-20 tokens/i })).toBeDefined();

  await user.click(screen.getByRole("button", { name: "Scan again" }));
  expect(failing).toHaveBeenCalledTimes(2);
});
