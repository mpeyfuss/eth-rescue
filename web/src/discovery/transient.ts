import { getAddress } from "viem";
import { sameAddress } from "../plan/address";
import { actionKey, type Network } from "../plan/draft";
import { type Candidate, fetchJson } from "./types";

// Testnet data lives on Transient's dev API.
const TRANSIENT_APIS: Record<Network, string> = {
  mainnet: "https://api.transient.xyz/v1/catalog",
  sepolia: "https://api-dev.transient.xyz/v1/catalog",
};
const CHAIN_IDS: Record<Network, string> = { mainnet: "1", sepolia: "11155111" };
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
    token_id: number;
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

async function fetchAll<T>(api: string, path: string, query: Record<string, string>): Promise<T[]> {
  const results: T[] = [];
  let url: string | null = `${api}/${path}?${new URLSearchParams({ ...query, limit: "100" })}`;
  for (let page = 0; url && page < MAX_PAGES; page++) {
    const body: Page<T> = await fetchJson<Page<T>>(url);
    results.push(...body.results);
    url = body.next;
  }
  return results;
}

export async function findUserIds(api: string, victim: string): Promise<number[]> {
  const users = await fetchAll<TransientUser>(api, "users", { address: victim.toLowerCase() });
  return users.map((user) => user.id);
}

// The auctions `user_id` filter matches the token's creator, not the seller.
export function fetchCreatorListings(api: string, network: Network, userIds: number[]): Promise<TransientListing[]> {
  return fetchAll<TransientListing>(api, "auctions", {
    chain: CHAIN_IDS[network],
    user_id: userIds.join(","),
    upcoming_or_live: "true",
  });
}

export function fetchCreatorContracts(api: string, network: Network, userIds: number[]): Promise<TransientContract[]> {
  return fetchAll<TransientContract>(api, "contracts", { chain: CHAIN_IDS[network], user_id: userIds.join(",") });
}

function hasBid(listing: TransientListing): boolean {
  return listing.highest_bid !== null && BigInt(listing.highest_bid.split(".")[0] || "0") > 0n;
}

export function listingCandidates(listings: TransientListing[], victim: string): Candidate[] {
  return listings
    .filter((listing) => sameAddress(listing.seller_address.raw_address, victim))
    .map((listing) => {
      const token = listing.nft_token;
      const contract = getAddress(token.nft_contract.address.raw_address);
      const title = token.name || `${token.nft_contract.name} #${token.token_id}`;
      const action = {
        type: "transient_delist_erc721" as const,
        auction_house: getAddress(listing.auction_address.raw_address),
        contract,
        token_id: String(token.token_id),
        label: title,
      };
      const blockedReason = hasBid(listing) ? "This auction has a bid, so it can no longer be delisted." : undefined;
      return {
        key: actionKey(action),
        group: "listings" as const,
        title,
        detail: `${token.nft_contract.name} · listed on Transient (${listing.auction_state.toLowerCase()})`,
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
      action,
      preselect: !blockedReason,
      blockedReason,
    };
  });
}
