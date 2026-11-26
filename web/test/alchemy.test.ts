import { expect, test } from "bun:test";
import { heldBalances, nftCandidates, type TokenMetadata, tokenCandidates } from "../src/discovery/alchemy";
import { validatePlan } from "../src/plan/validate";
import { fixture, planWith } from "./discovery-fixtures";

const USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";

test("held tokens become unselected ERC-20 actions with readable amounts", async () => {
  const { tokenBalances } = await fixture("alchemy-token-balances");
  const metadata = new Map<string, TokenMetadata>([[USDC.toLowerCase(), { name: "USD Coin", symbol: "USDC", decimals: 6 }]]);
  const candidates = tokenCandidates(heldBalances(tokenBalances), metadata);

  expect(candidates.map((candidate) => [candidate.title, candidate.detail.split(" · ")[0]])).toEqual([
    ["USD Coin (USDC)", "5"],
    ["Unknown token", "7 base units"],
  ]);
  expect(candidates[0].action).toEqual({ type: "erc20", contract: USDC, amount: "5000000", label: "USDC" });
  expect(candidates.some((candidate) => candidate.preselect)).toBe(false);
  expect(validatePlan(planWith(candidates.map((candidate) => candidate.action)))).toEqual([]);
});

test("NFTs map by standard, spam is unselected and unsupported standards are skipped", async () => {
  const { ownedNfts } = await fixture("alchemy-nfts");
  const candidates = nftCandidates(ownedNfts);

  expect(candidates.map((candidate) => [candidate.action.type, candidate.title, candidate.preselect])).toEqual([
    ["erc721", "Frame #3", true],
    ["erc1155", "Intertwined Odyssey #2", true],
    ["erc1155", "Free airdrop", false],
  ]);
  expect(candidates[1].action).toMatchObject({ token_id: "2", amount: "4" });
  expect(candidates[2].detail).toContain("flagged as spam");
  expect(validatePlan(planWith(candidates.map((candidate) => candidate.action)))).toEqual([]);
});
