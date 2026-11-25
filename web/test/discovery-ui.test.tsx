import { afterEach, beforeEach, expect, mock, test } from "bun:test";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { getAddress } from "viem";
import type { Candidate, ScanRequest } from "../src/discovery/types";
import { App } from "../src/App";
import { VICTIM } from "./discovery-fixtures";

const NFT = "0xb8CEf6F20A2A14C6282956C61acaDd3CC7E05fac".toLowerCase();
const candidates: Candidate[] = [
  {
    key: `erc721:${NFT}:3`,
    group: "nfts",
    title: "Frame #3",
    detail: "Before the Frame · ERC-721",
    action: { type: "erc721", contract: NFT, token_id: "3", label: "Frame #3" },
    preselect: true,
  },
  {
    key: `ownership:${NFT}:`,
    group: "contracts",
    title: "Before the Frame",
    detail: "ERC721TL",
    action: { type: "ownership", contract: NFT, label: "Before the Frame" },
    preselect: false,
    blockedReason: "Owned by 0x9999, not the compromised wallet.",
  },
];
const scanWallet = mock(async (_request: ScanRequest) => ({ candidates, errors: [] }));

beforeEach(() => {
  localStorage.clear();
  scanWallet.mockClear();
});
afterEach(cleanup);

test("scan results can be added to the plan once", async () => {
  const user = userEvent.setup();
  render(<App scan={scanWallet} />);
  const scan = screen.getByRole("button", { name: "Scan wallet" });
  expect(scan).toHaveProperty("disabled", true);

  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(VICTIM);
  await user.click(scan);
  expect(scanWallet.mock.calls[0][0]).toMatchObject({ network: "mainnet", victim: VICTIM });

  expect(screen.getByText("Owned by 0x9999, not the compromised wallet.")).toBeDefined();
  expect(screen.getByRole("checkbox", { name: /^Before the Frame/ })).toHaveProperty("disabled", true);
  expect(screen.getByRole("checkbox", { name: /^Frame #3/ })).toHaveProperty("checked", true);

  await user.click(screen.getByRole("button", { name: "Add 1 to the plan" }));
  const plan = JSON.parse(screen.getByLabelText("Plan JSON").textContent ?? "");
  expect(plan.actions).toEqual([{ type: "erc721", label: "Frame #3", contract: getAddress(NFT), token_id: "3" }]);
  expect(screen.getByText("Already in the plan.")).toBeDefined();
  expect(screen.queryByRole("button", { name: /to the plan/ })).toBeNull();
});
