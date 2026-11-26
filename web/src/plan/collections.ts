import { sameAddress } from "./address";
import type { ActionType, Network } from "./draft";

export type CollectionType = Extract<ActionType, "cryptopunk" | "superrare_v1">;

// Mirrors eth_rescue/collections.py (tests/test_plan.py checks it).
export const COLLECTION_CONTRACTS: Record<Network, Record<CollectionType, string>> = {
  mainnet: {
    cryptopunk: "0xb47e3cd837dDF8e4c57F05d70Ab865de6e193BBB",
    superrare_v1: "0x41A322b28D0fF354040e2CbC676F0320d8c8850d",
  },
};

export const COLLECTION_NAMES: Record<CollectionType, string> = {
  cryptopunk: "CryptoPunks",
  superrare_v1: "SuperRare V1",
};

export function isCollectionType(type: string): type is CollectionType {
  return Object.hasOwn(COLLECTION_NAMES, type);
}

export function collectionFor(network: Network, address: unknown): CollectionType | null {
  if (typeof address !== "string") return null;
  const contracts = COLLECTION_CONTRACTS[network];
  if (!contracts) return null;
  const match = (Object.keys(contracts) as CollectionType[]).find((type) => sameAddress(contracts[type], address));
  return match ?? null;
}
