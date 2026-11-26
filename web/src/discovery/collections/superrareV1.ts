import { parseAbi } from "viem";
import { COLLECTION_CONTRACTS } from "../../plan/collections";
import { collectionCandidate } from "./candidate";
import type { CollectionScanner } from "./types";

// The original SupeRare contract follows a pre-final ERC-721 draft: `tokensOf`
// lists a holder's tokens, and `transfer` replaces `transferFrom`.
const ABI = parseAbi(["function tokensOf(address) view returns (uint256[])"]);

export const superRareV1: CollectionScanner = {
  type: "superrare_v1",
  name: "SuperRare V1",
  async scan({ network, compromised }, client) {
    const tokens = await client.readContract({
      address: COLLECTION_CONTRACTS[network].superrare_v1 as `0x${string}`,
      abi: ABI,
      functionName: "tokensOf",
      args: [compromised as `0x${string}`],
    });
    return tokens.map((tokenId) => collectionCandidate(network, "superrare_v1", tokenId));
  },
};
