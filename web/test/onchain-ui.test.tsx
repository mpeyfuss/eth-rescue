import { afterEach, beforeEach, expect, test } from "bun:test";
import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "../src/App";
import type { OnchainReader } from "../src/discovery/onchain";

const emptyScan = async () => ({ candidates: [], errors: [], warnings: [] });

const COMPROMISED = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const DESTINATION = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const GAS = "0x2546BcD3c84621e976D8185a91A922aE77ECEc30";
const USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";
const SWEEPER = "0x1111111111111111111111111111111111111111";

const mockOnchain: OnchainReader = {
  readWalletSnapshot: async (_net, _key, address) => {
    if (address.toLowerCase() === COMPROMISED.toLowerCase()) {
      return {
        ethBalance: 142_000_000_000_000_000n,
        ethFormatted: "0.142 ETH",
        nonce: 12,
        kind: "delegated",
        delegateTo: SWEEPER,
      };
    }
    if (address.toLowerCase() === GAS.toLowerCase()) {
      return {
        ethBalance: 45_000_000_000_000_000n,
        ethFormatted: "0.045 ETH",
        nonce: 1,
        kind: "contract",
      };
    }
    return { ethBalance: 0n, ethFormatted: "0 ETH", nonce: 0, kind: "eoa" };
  },
  readErc20Info: async () => ({
    name: "USD Coin",
    symbol: "USDC",
    decimals: 6,
    balance: 1_250_500_000n,
  }),
  readErc721Info: async () => ({
    name: "Cool Cats",
    symbol: "COOL",
    owner: COMPROMISED,
  }),
  readErc1155Info: async () => ({
    balance: 4n,
  }),
  readOwnershipInfo: async () => ({
    owner: COMPROMISED,
    pendingOwner: "0x0000000000000000000000000000000000000000",
    name: "Artist Edition",
  }),
};

beforeEach(() => localStorage.clear());
afterEach(cleanup);

test("wallet preflight detects ETH balance, EIP-7702 delegation, and contract gas wallet", async () => {
  const user = userEvent.setup();
  render(<App onchain={mockOnchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));

  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);
  expect(await screen.findByText(/This wallet is delegated/i)).toBeDefined();
  expect(screen.getAllByText(/0\.142 ETH/i).length).toBeGreaterThanOrEqual(1);

  await user.click(screen.getByLabelText("Gas wallet (optional)"));
  await user.paste(GAS);
  expect(await screen.findByText(/gas wallet must be a plain EOA account/i)).toBeDefined();
});

test("ERC-20 action auto-reads decimals & balance, fills max, and converts human input to base units", async () => {
  const user = userEvent.setup();
  render(<App onchain={mockOnchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));

  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);
  await user.click(screen.getByLabelText("Destination wallet"));
  await user.paste(DESTINATION);

  await user.click(screen.getByRole("button", { name: /Next: Rescue plan/i }));
  await user.click(await screen.findByRole("button", { name: /ERC-20 tokens/i }));
  await user.click(screen.getByLabelText("Contract"));
  await user.paste(USDC);

  expect(await screen.findByText(/Wallet holds 1,250\.5 USDC/i)).toBeDefined();
  await user.click(screen.getByRole("button", { name: "Use full balance" }));

  const humanInput = screen.getByLabelText("Amount (USDC)");
  expect(humanInput).toHaveProperty("value", "1250.5");

  // Typing human units updates exact integer base units in plan.json.
  await user.clear(humanInput);
  await user.type(humanInput, "10.25");

  const nav = within(screen.getByRole("navigation", { name: "Rescue steps" }));
  await user.click(nav.getByRole("button", { name: /Review & run/i }));
  const plan = JSON.parse(screen.getByLabelText("Plan JSON").textContent ?? "");
  expect(plan.actions).toEqual([{ type: "erc20", label: "USDC", contract: USDC, amount: "10250000" }]);
});

test("Ownership action auto-checks owner and detects Ownable2Step", async () => {
  const user = userEvent.setup();
  render(<App onchain={mockOnchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));

  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);

  await user.click(screen.getByRole("button", { name: /Next: Rescue plan/i }));
  await user.click(await screen.findByRole("button", { name: /Contract ownership/i }));
  await user.click(screen.getByLabelText("Contract"));
  await user.paste(USDC);

  expect(await screen.findByText(/Owned by the compromised wallet \(Artist Edition\)/i)).toBeDefined();
  expect(screen.getByText(/Ownable2Step detected/i)).toBeDefined();
});

test("imported plan auto-hydrates ERC-20 decimals and formats amounts in the review blueprint", async () => {
  const user = userEvent.setup();
  const { container } = render(<App onchain={mockOnchain} scan={emptyScan} />);
  const plan = {
    version: 1,
    network: "mainnet",
    compromised: COMPROMISED,
    destination: DESTINATION,
    actions: [{ type: "erc20", contract: USDC, amount: "1250500000" }],
  };
  const file = new File([JSON.stringify(plan)], "plan.json", { type: "application/json" });
  await user.upload(container.querySelector('input[type="file"]') as HTMLInputElement, file);

  expect(await screen.findByText(/1,250\.5 USDC/i)).toBeDefined();
});

test("ERC-721 ownership and ERC-1155 balance are automatically verified on-chain", async () => {
  const user = userEvent.setup();
  render(<App onchain={mockOnchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));

  await user.click(screen.getByLabelText("Compromised wallet"));
  await user.paste(COMPROMISED);

  await user.click(screen.getByRole("button", { name: /Next: Rescue plan/i }));
  await user.click(await screen.findByRole("button", { name: /ERC-721 NFT/i }));
  await user.click(screen.getByLabelText("Contract"));
  await user.paste(USDC);
  await user.click(screen.getByLabelText("Token ID"));
  await user.paste("7");

  expect(await screen.findByText(/Owned by the compromised wallet/i)).toBeDefined();

  await user.click(await screen.findByRole("button", { name: /ERC-1155 tokens/i }));
  const contracts = screen.getAllByLabelText("Contract");
  await user.click(contracts[contracts.length - 1]);
  await user.paste(USDC);
  const tokenIds = screen.getAllByLabelText("Token ID");
  await user.click(tokenIds[tokenIds.length - 1]);
  await user.paste("2");

  expect(await screen.findByText(/Compromised wallet holds 4 of token #2/i)).toBeDefined();
  await user.click(screen.getByRole("button", { name: /Use full balance \(4\)/i }));
  const amounts = screen.getAllByLabelText("Amount (base units)");
  expect(amounts[amounts.length - 1]).toHaveProperty("value", "4");
});

test("destination preflight flags an unused address and recognizes smart wallets", async () => {
  const user = userEvent.setup();
  const contractDestination = "0x2222222222222222222222222222222222222222";
  const onchain: OnchainReader = {
    ...mockOnchain,
    readWalletSnapshot: async (network, key, address) =>
      address.toLowerCase() === contractDestination
        ? { ethBalance: 0n, ethFormatted: "0 ETH", nonce: 1, kind: "contract" }
        : mockOnchain.readWalletSnapshot(network, key, address),
  };
  render(<App onchain={onchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));

  const field = screen.getByLabelText("Destination wallet");
  await user.click(field);
  await user.paste(DESTINATION);
  expect(await screen.findByText(/isn.t set up on Ethereum mainnet yet/i)).toBeDefined();

  await user.clear(field);
  await user.paste(contractDestination);
  expect(await screen.findByText(/Safe multisigs work/i)).toBeDefined();
});

test("the gas wallet balance note shows when planning ahead but not during a rescue", async () => {
  const PLAIN_GAS = "0x000000000000000000000000000000000000bEEF";
  const user = userEvent.setup();
  render(<App onchain={mockOnchain} scan={emptyScan} />);
  await user.click(screen.getByRole("button", { name: /My wallet is compromised/i }));
  await user.click(screen.getByLabelText("Gas wallet (optional)"));
  await user.paste(PLAIN_GAS);
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(screen.queryByText(/0\.02–0\.05 ETH/)).toBeNull();

  await user.click(screen.getByRole("button", { name: "Plan ahead" }));
  await user.click(within(screen.getByRole("navigation", { name: "Rescue steps" })).getByRole("button", { name: /Wallets/i }));
  expect(await screen.findByText(/Send it about 0\.02–0\.05 ETH/)).toBeDefined();
});
