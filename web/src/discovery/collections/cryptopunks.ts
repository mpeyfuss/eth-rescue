import { parseAbi } from "viem";
import { sameAddress } from "../../plan/address";
import { COLLECTION_CONTRACTS } from "../../plan/collections";
import { MULTICALL3 } from "../onchain";
import { collectionCandidate } from "./candidate";
import type { CollectionScanner } from "./types";

const ABI = parseAbi([
  "function balanceOf(address) view returns (uint256)",
  "function punkIndexToAddress(uint256) view returns (address)",
]);
const PUNK_COUNT = 10_000;
// Bytes of calldata per multicall request: about 1,000 lookups each.
const BATCH_BYTES = 36_000;

// The contract has no per-owner index, so a holder's punks are found by reading
// every punk's owner, which only happens when balanceOf says there are some.
export const cryptoPunks: CollectionScanner = {
  type: "cryptopunk",
  name: "CryptoPunks",
  async scan({ network, compromised }, client, warn) {
    const address = COLLECTION_CONTRACTS[network].cryptopunk as `0x${string}`;
    const owner = compromised as `0x${string}`;
    const balance = await client.readContract({ address, abi: ABI, functionName: "balanceOf", args: [owner] });
    if (balance === 0n) return [];

    const owners = await client.multicall({
      contracts: Array.from({ length: PUNK_COUNT }, (_, index) => ({
        address,
        abi: ABI,
        functionName: "punkIndexToAddress" as const,
        args: [BigInt(index)] as const,
      })),
      allowFailure: false,
      batchSize: BATCH_BYTES,
      multicallAddress: MULTICALL3,
    });
    const punks = owners.flatMap((holder, index) => (sameAddress(holder, compromised) ? [index] : []));
    if (BigInt(punks.length) !== balance) {
      warn(`the contract reports ${balance} punks but ${punks.length} were found`);
    }
    return punks.map((index) => collectionCandidate(network, "cryptopunk", index));
  },
};
