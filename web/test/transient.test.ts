import { expect, test } from "bun:test";
import { getAddress } from "viem";
import { contractCandidates, listingCandidates, type TransientListing } from "../src/discovery/transient";
import { validatePlan } from "../src/plan/validate";
import { fixture, planWith, VICTIM } from "./discovery-fixtures";

const listings = async (): Promise<TransientListing[]> => (await fixture("transient-auctions")).results;

test("listings sold by the victim become delist actions", async () => {
  const raw = await listings();
  const candidates = listingCandidates(raw, VICTIM);
  expect(candidates).toHaveLength(2);
  expect(candidates[0].action).toEqual({
    type: "transient_delist_erc721",
    auction_house: "0x6f66b95a0C512f3497FB46660E0BC3B94B989F8d",
    contract: getAddress(raw[0].nft_token.nft_contract.address.raw_address),
    token_id: String(raw[0].nft_token.token_id),
    label: raw[0].nft_token.name,
  });
  expect(candidates.every((candidate) => candidate.preselect)).toBe(true);
  expect(validatePlan(planWith(candidates.map((candidate) => candidate.action)))).toEqual([]);
});

test("listings sold by someone else are dropped", async () => {
  const [first, second] = await listings();
  const resold = { ...second, seller_address: { raw_address: "0x9999999999999999999999999999999999999999" } };
  expect(listingCandidates([first, resold], VICTIM)).toHaveLength(1);
});

test("listings with a bid are shown but blocked", async () => {
  const [first] = await listings();
  const [candidate] = listingCandidates([{ ...first, highest_bid: "1000000000000000" }], VICTIM);
  expect(candidate.preselect).toBe(false);
  expect(candidate.blockedReason).toContain("has a bid");
});

test("creator contracts are offered only when owner() is the victim", async () => {
  const contracts = (await fixture("transient-contracts")).results;
  const [owned, other] = contracts.map((contract: { address: { raw_address: string } }) => contract.address.raw_address);
  const owners = new Map<string, string | null>([
    [owned, VICTIM.toLowerCase()],
    [other, "0x9999999999999999999999999999999999999999"],
  ]);
  const [ownedCandidate, otherCandidate] = contractCandidates(contracts, owners, VICTIM);
  expect(ownedCandidate.preselect).toBe(true);
  expect(ownedCandidate.blockedReason).toBeUndefined();
  expect(otherCandidate.blockedReason).toContain("Owned by 0x9999");
  expect(contractCandidates(contracts.slice(0, 1), new Map(), VICTIM)[0].blockedReason).toContain("Could not read owner()");
  expect(validatePlan(planWith([ownedCandidate.action]))).toEqual([]);
});
