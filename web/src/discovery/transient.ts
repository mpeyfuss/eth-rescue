import { getAddress } from "viem";
import { sameAddress } from "../plan/address";
import { actionKey, type Network } from "../plan/draft";
import { type Candidate, fetchJson, requireArray, type Warn } from "./types";

const TRANSIENT_APIS: Record<Network, string> = {
  mainnet: "https://api.transient.xyz/v1/catalog",
};
const CHAIN_IDS: Record<Network, string> = { mainnet: "1" };
const MAX_PAGES = 20;

interface Page<T> {
  next: string | null;
  results: T[];
}

interface ApiAddress {
  raw_address: string;
}

export interface TransientUser {
  id: number;
}

export interface TransientListing {
  auction_state: string;
  highest_bid: string | null;
  seller_address: ApiAddress;
  auction_address: ApiAddress;
  nft_token: {
    token_id: number | string;
    name: string;
    nft_contract: { name: string; contract_type: string; address: ApiAddress };
  };
}

export interface TransientContract {
  name: string;
  contract_type: string;
  address: ApiAddress;
}

export function transientApi(network: Network): string {
  return TRANSIENT_APIS[network];
}

// Pagination links must stay on the API's own origin.
function nextPage(api: string, next: unknown): string | null {
  if (next === null || next === undefined) return null;
  const url = new URL(String(next), api);
  if (url.origin !== new URL(api).origin) throw new Error("Transient returned a page link to another site");
  return url.toString();
}

async function fetchAll<T>(api: string, path: string, query: Record<string, string>, warn?: Warn): Promise<T[]> {
  const results: T[] = [];
  let url: string | null = `${api}/${path}?${new URLSearchParams({ ...query, limit: "100" })}`;
  for (let page = 0; url && page < MAX_PAGES; page++) {
    const body: Page<T> = await fetchJson<Page<T>>(url);
    results.push(...requireArray<T>(body.results, `Transient ${path}`));
    url = nextPage(api, body.next);
  }
  if (url) warn?.(`only the first ${results.length} ${path} were read; there are more`);
  return results;
}

export async function findUserIds(api: string, victim: string, warn?: Warn): Promise<number[]> {
  const users = await fetchAll<TransientUser>(api, "users", { address: victim.toLowerCase() }, warn);
  return users.map((user) => user.id);
}

// The auctions `user_id` filter matches the token's creator, not the seller.
export function fetchCreatorListings(
  api: string,
  network: Network,
  userIds: number[],
  warn?: Warn,
): Promise<TransientListing[]> {
  return fetchAll<TransientListing>(
    api,
    "auctions",
    { chain: CHAIN_IDS[network], user_id: userIds.join(","), upcoming_or_live: "true" },
    warn,
  );
}

export function fetchCreatorContracts(
  api: string,
  network: Network,
  userIds: number[],
  warn?: Warn,
): Promise<TransientContract[]> {
  return fetchAll<TransientContract>(api, "contracts", { chain: CHAIN_IDS[network], user_id: userIds.join(",") }, warn);
}

// Any nonzero digit in the amount is a bid; an unreadable amount counts as one.
function hasBid(listing: TransientListing): boolean {
  if (listing.highest_bid === null) return false;
  const mantissa = String(listing.highest_bid).split(/[eE]/)[0];
  return !/^[0-9.]*$/.test(mantissa) || /[1-9]/.test(mantissa);
}

// A JSON number past 2^53 has already lost digits, so the id is unusable.
function exactTokenId(value: number | string): string | null {
  if (typeof value === "number") return Number.isSafeInteger(value) && value >= 0 ? String(value) : null;
  return /^\d+$/.test(value) ? value : null;
}

function listingBlockedReason(listing: TransientListing, tokenId: string | null): string | undefined {
  if (!/721/.test(listing.nft_token.nft_contract.contract_type)) {
    return "This is not an ERC-721 listing. Delist it on Transient, then add it as an ERC-1155 action.";
  }
  if (tokenId === null) return "Transient reported a token ID that cannot be read exactly.";
  if (hasBid(listing)) return "This auction has a bid, so it can no longer be delisted.";
  return undefined;
}

export function listingCandidates(listings: TransientListing[], victim: string): Candidate[] {
  return listings
    .filter((listing) => sameAddress(listing.seller_address.raw_address, victim))
    .map((listing) => {
      const token = listing.nft_token;
      const contract = getAddress(token.nft_contract.address.raw_address);
      const auctionHouse = getAddress(listing.auction_address.raw_address);
      const tokenId = exactTokenId(token.token_id);
      const title = token.name || `${token.nft_contract.name} #${tokenId ?? token.token_id}`;
      const action = {
        type: "transient_delist_erc721" as const,
        auction_house: auctionHouse,
        contract,
        token_id: tokenId ?? "",
        label: title,
      };
      const blockedReason = listingBlockedReason(listing, tokenId);
      return {
        key: actionKey(action),
        group: "listings" as const,
        title,
        detail: `${token.nft_contract.name} (${token.nft_contract.contract_type}) · ${contract} #${tokenId ?? "?"} · listed at ${auctionHouse} (${listing.auction_state.toLowerCase()})`,
        source: "Transient",
        action,
        preselect: !blockedReason,
        blockedReason,
      };
    });
}

export function contractCandidates(
  contracts: TransientContract[],
  owners: Map<string, string | null>,
  victim: string,
): Candidate[] {
  return contracts.map((item) => {
    const contract = getAddress(item.address.raw_address);
    const owner = owners.get(contract.toLowerCase()) ?? null;
    const action = { type: "ownership" as const, contract, label: item.name };
    let blockedReason: string | undefined;
    if (owner === null) blockedReason = "Could not read owner() on-chain.";
    else if (!sameAddress(owner, victim)) blockedReason = `Owned by ${owner}, not the compromised wallet.`;
    return {
      key: actionKey(action),
      group: "contracts" as const,
      title: item.name,
      detail: `${item.contract_type} · ${contract}`,
      source: "Transient",
      action,
      preselect: !blockedReason,
      blockedReason,
    };
  });
}
