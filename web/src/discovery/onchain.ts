import { createPublicClient, getAddress, http, parseAbi } from "viem";
import type { Network } from "../plan/draft";
import { formatEth } from "../plan/format";
import { rpcUrl } from "./alchemy";
import { REQUEST_TIMEOUT_MS } from "./types";

export const MULTICALL3 = "0xcA11bde05977b3631167028862bE2a173976CA11";
const PUBLIC_RPC: Record<Network, string> = {
  mainnet: "https://ethereum-rpc.publicnode.com",
};

const OWNABLE = parseAbi([
  "function owner() view returns (address)",
  "function pendingOwner() view returns (address)",
  "function name() view returns (string)",
]);

const ERC20_ABI = parseAbi([
  "function name() view returns (string)",
  "function symbol() view returns (string)",
  "function decimals() view returns (uint8)",
  "function balanceOf(address) view returns (uint256)",
]);

const ERC721_ABI = parseAbi([
  "function name() view returns (string)",
  "function symbol() view returns (string)",
  "function ownerOf(uint256) view returns (address)",
]);

const ERC1155_ABI = parseAbi(["function balanceOf(address,uint256) view returns (uint256)"]);

export interface WalletSnapshot {
  ethBalance: bigint;
  ethFormatted: string;
  nonce: number;
  kind: "eoa" | "delegated" | "contract";
  delegateTo?: string;
}

export interface Erc20Info {
  name: string | null;
  symbol: string | null;
  decimals: number | null;
  balance: bigint | null;
}

export interface Erc721Info {
  name: string | null;
  symbol: string | null;
  owner: string | null;
}

export interface Erc1155Info {
  balance: bigint | null;
}

export interface OwnershipInfo {
  owner: string | null;
  pendingOwner: string | null;
  name: string | null;
}

export interface OnchainReader {
  readWalletSnapshot: (network: Network, alchemyKey: string | null, address: string) => Promise<WalletSnapshot>;
  readErc20Info: (network: Network, alchemyKey: string | null, contract: string, compromised?: string) => Promise<Erc20Info>;
  readErc721Info: (
    network: Network,
    alchemyKey: string | null,
    contract: string,
    tokenId?: string,
  ) => Promise<Erc721Info>;
  readErc1155Info: (
    network: Network,
    alchemyKey: string | null,
    contract: string,
    tokenId: string,
    compromised: string,
  ) => Promise<Erc1155Info>;
  readOwnershipInfo: (network: Network, alchemyKey: string | null, contract: string) => Promise<OwnershipInfo>;
}

export function publicClient(network: Network, alchemyKey: string | null) {
  const url = alchemyKey ? rpcUrl(network, alchemyKey) : PUBLIC_RPC[network];
  return createPublicClient({ transport: http(url, { timeout: REQUEST_TIMEOUT_MS, retryCount: 1 }) });
}

export type PublicClient = ReturnType<typeof publicClient>;

const client = publicClient;

// EIP-7702 delegation designator: 0xef0100 + 20-byte target address (23 bytes = 48 chars with 0x).
export function parseEip7702Delegate(code: string | undefined): string | null {
  if (!code || code === "0x") return null;
  const lower = code.toLowerCase();
  if (lower.startsWith("0xef0100") && lower.length === 48) {
    try {
      return getAddress(`0x${lower.slice(8)}`);
    } catch {
      return null;
    }
  }
  return null;
}

export async function readWalletSnapshot(
  network: Network,
  alchemyKey: string | null,
  address: string,
): Promise<WalletSnapshot> {
  const rpc = client(network, alchemyKey);
  const addr = address as `0x${string}`;
  const [ethBalance, code, nonce] = await Promise.all([
    rpc.getBalance({ address: addr }),
    rpc.getCode({ address: addr }),
    rpc.getTransactionCount({ address: addr }),
  ]);
  const delegate = parseEip7702Delegate(code);
  const hasCode = Boolean(code && code !== "0x");
  return {
    ethBalance,
    ethFormatted: formatEth(ethBalance),
    nonce,
    kind: delegate ? "delegated" : hasCode ? "contract" : "eoa",
    delegateTo: delegate ?? undefined,
  };
}

export async function readErc20Info(
  network: Network,
  alchemyKey: string | null,
  contract: string,
  compromised?: string,
): Promise<Erc20Info> {
  const addr = contract as `0x${string}`;
  const calls = [
    { address: addr, abi: ERC20_ABI, functionName: "name" as const },
    { address: addr, abi: ERC20_ABI, functionName: "symbol" as const },
    { address: addr, abi: ERC20_ABI, functionName: "decimals" as const },
    ...(compromised ? [{ address: addr, abi: ERC20_ABI, functionName: "balanceOf" as const, args: [compromised as `0x${string}`] }] : []),
  ];
  const results = await client(network, alchemyKey).multicall({
    contracts: calls,
    allowFailure: true,
    multicallAddress: MULTICALL3,
  });
  return {
    name: results[0]?.status === "success" ? String(results[0].result) : null,
    symbol: results[1]?.status === "success" ? String(results[1].result) : null,
    decimals: results[2]?.status === "success" ? Number(results[2].result) : null,
    balance: results[3]?.status === "success" ? (results[3].result as bigint) : null,
  };
}

export async function readErc721Info(
  network: Network,
  alchemyKey: string | null,
  contract: string,
  tokenId?: string,
): Promise<Erc721Info> {
  const addr = contract as `0x${string}`;
  const validTokenId = tokenId && /^\d+$/.test(tokenId.trim()) ? BigInt(tokenId.trim()) : null;
  const calls = [
    { address: addr, abi: ERC721_ABI, functionName: "name" as const },
    { address: addr, abi: ERC721_ABI, functionName: "symbol" as const },
    ...(validTokenId !== null
      ? [{ address: addr, abi: ERC721_ABI, functionName: "ownerOf" as const, args: [validTokenId] }]
      : []),
  ];
  const results = await client(network, alchemyKey).multicall({
    contracts: calls,
    allowFailure: true,
    multicallAddress: MULTICALL3,
  });
  return {
    name: results[0]?.status === "success" ? String(results[0].result) : null,
    symbol: results[1]?.status === "success" ? String(results[1].result) : null,
    owner: results[2]?.status === "success" ? getAddress(String(results[2].result)) : null,
  };
}

export async function readErc1155Info(
  network: Network,
  alchemyKey: string | null,
  contract: string,
  tokenId: string,
  compromised: string,
): Promise<Erc1155Info> {
  if (!/^\d+$/.test(tokenId.trim())) return { balance: null };
  const addr = contract as `0x${string}`;
  const results = await client(network, alchemyKey).multicall({
    contracts: [
      {
        address: addr,
        abi: ERC1155_ABI,
        functionName: "balanceOf",
        args: [compromised as `0x${string}`, BigInt(tokenId.trim())],
      },
    ],
    allowFailure: true,
    multicallAddress: MULTICALL3,
  });
  return {
    balance: results[0]?.status === "success" ? (results[0].result as bigint) : null,
  };
}

export async function readOwnershipInfo(
  network: Network,
  alchemyKey: string | null,
  contract: string,
): Promise<OwnershipInfo> {
  const addr = contract as `0x${string}`;
  const results = await client(network, alchemyKey).multicall({
    contracts: [
      { address: addr, abi: OWNABLE, functionName: "owner" },
      { address: addr, abi: OWNABLE, functionName: "pendingOwner" },
      { address: addr, abi: OWNABLE, functionName: "name" },
    ],
    allowFailure: true,
    multicallAddress: MULTICALL3,
  });
  return {
    owner: results[0]?.status === "success" ? getAddress(String(results[0].result)) : null,
    pendingOwner: results[1]?.status === "success" ? getAddress(String(results[1].result)) : null,
    name: results[2]?.status === "success" ? String(results[2].result) : null,
  };
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

export const defaultOnchainReader: OnchainReader = {
  readWalletSnapshot,
  readErc20Info,
  readErc721Info,
  readErc1155Info,
  readOwnershipInfo,
};
