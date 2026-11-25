import { createPublicClient, http, parseAbi } from "viem";
import type { Network } from "../plan/draft";
import { rpcUrl } from "./alchemy";
import { REQUEST_TIMEOUT_MS } from "./types";

// Multicall3 has the same address on mainnet and Sepolia.
const MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11";
const PUBLIC_RPC: Record<Network, string> = {
  mainnet: "https://ethereum-rpc.publicnode.com",
  sepolia: "https://ethereum-sepolia-rpc.publicnode.com",
};
const OWNABLE = parseAbi(["function owner() view returns (address)"]);

function client(network: Network, alchemyKey: string | null) {
  const url = alchemyKey ? rpcUrl(network, alchemyKey) : PUBLIC_RPC[network];
  return createPublicClient({ transport: http(url, { timeout: REQUEST_TIMEOUT_MS, retryCount: 1 }) });
}

// Lowercased contract address -> owner, or null when owner() is missing or reverts.
export async function readOwners(
  network: Network,
  alchemyKey: string | null,
  contracts: string[],
): Promise<Map<string, string | null>> {
  if (contracts.length === 0) return new Map();
  const results = await client(network, alchemyKey).multicall({
    contracts: contracts.map((address) => ({ address: address as `0x${string}`, abi: OWNABLE, functionName: "owner" })),
    allowFailure: true,
    multicallAddress: MULTICALL3,
  });
  return new Map(
    contracts.map((address, index) => {
      const result = results[index];
      return [address.toLowerCase(), result.status === "success" ? (result.result as string) : null];
    }),
  );
}
