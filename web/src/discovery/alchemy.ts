import { formatUnits, getAddress } from "viem";
import { actionKey, type Network } from "../plan/draft";
import { type Candidate, fetchJson, requireArray, type Warn } from "./types";

const HOSTS: Record<Network, string> = {
  mainnet: "eth-mainnet.g.alchemy.com",
};
const MAX_PAGES = 20;
const METADATA_BATCH = 100;

export const rpcUrl = (network: Network, key: string) => `https://${HOSTS[network]}/v2/${key}`;
const nftUrl = (network: Network, key: string) => `https://${HOSTS[network]}/nft/v3/${key}`;

export interface TokenBalance {
  contractAddress: string;
  tokenBalance: string | null;
}

export interface TokenMetadata {
  name: string | null;
  symbol: string | null;
  decimals: number | null;
}

export interface AlchemyNft {
  contract: { address: string; name?: string | null; symbol?: string | null; isSpam?: boolean | null };
  tokenId: string;
  tokenType: string;
  name?: string | null;
  balance?: string | null;
}

interface RpcResponse<T> {
  id: number;
  result?: T;
  error?: { message: string };
}

async function rpc<T>(url: string, method: string, params: unknown[]): Promise<T> {
  const body = { jsonrpc: "2.0", id: 1, method, params };
  const response = await fetchJson<RpcResponse<T>>(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  if (response.error || response.result === undefined) throw new Error(response.error?.message ?? `${method} failed`);
  return response.result;
}

export async function fetchTokenBalances(
  network: Network,
  key: string,
  owner: string,
  warn?: Warn,
): Promise<TokenBalance[]> {
  const balances: TokenBalance[] = [];
  let pageKey: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const options = pageKey ? { pageKey } : {};
    const result = await rpc<{ tokenBalances: TokenBalance[]; pageKey?: string }>(
      rpcUrl(network, key),
      "alchemy_getTokenBalances",
      [owner, "erc20", options],
    );
    balances.push(...requireArray<TokenBalance>(result?.tokenBalances, "Alchemy token balance"));
    pageKey = result.pageKey;
    if (!pageKey) return balances;
  }
  warn?.(`only the first ${balances.length} token balances were read; there are more`);
  return balances;
}

export async function fetchTokenMetadata(
  network: Network,
  key: string,
  contracts: string[],
): Promise<Map<string, TokenMetadata>> {
  const metadata = new Map<string, TokenMetadata>();
  for (let start = 0; start < contracts.length; start += METADATA_BATCH) {
    const chunk = contracts.slice(start, start + METADATA_BATCH);
    const batch = chunk.map((contract, id) => ({ jsonrpc: "2.0", id, method: "alchemy_getTokenMetadata", params: [contract] }));
    const responses = await fetchJson<unknown>(rpcUrl(network, key), {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(batch),
    });
    for (const response of requireArray<RpcResponse<TokenMetadata>>(responses, "Alchemy token metadata")) {
      const contract = chunk[response?.id];
      if (contract && response.result) metadata.set(contract.toLowerCase(), response.result);
    }
  }
  return metadata;
}

export async function fetchNfts(network: Network, key: string, owner: string, warn?: Warn): Promise<AlchemyNft[]> {
  const nfts: AlchemyNft[] = [];
  let pageKey: string | undefined;
  for (let page = 0; page < MAX_PAGES; page++) {
    const query = new URLSearchParams({ owner, withMetadata: "true", pageSize: "100" });
    if (pageKey) query.set("pageKey", pageKey);
    const result = await fetchJson<{ ownedNfts: AlchemyNft[]; pageKey?: string | null }>(
      `${nftUrl(network, key)}/getNFTsForOwner?${query}`,
    );
    nfts.push(...requireArray<AlchemyNft>(result?.ownedNfts, "Alchemy NFT"));
    pageKey = result.pageKey ?? undefined;
    if (!pageKey) return nfts;
  }
  warn?.(`only the first ${nfts.length} NFTs were read; there are more`);
  return nfts;
}

export function heldBalances(balances: TokenBalance[]): TokenBalance[] {
  return balances.filter((balance) => hexToBigInt(balance.tokenBalance) > 0n);
}

function hexToBigInt(value: string | null): bigint {
  try {
    return value ? BigInt(value) : 0n;
  } catch {
    return 0n;
  }
}

export function tokenCandidates(balances: TokenBalance[], metadata: Map<string, TokenMetadata>): Candidate[] {
  return balances.flatMap((balance) => {
    const amount = hexToBigInt(balance.tokenBalance);
    if (amount === 0n) return [];
    const contract = getAddress(balance.contractAddress);
    const meta = metadata.get(contract.toLowerCase());
    const symbol = meta?.symbol || "Unknown token";
    const shown = meta?.decimals != null ? formatUnits(amount, meta.decimals) : `${amount} base units`;
    const action = { type: "erc20" as const, contract, amount: amount.toString(), label: symbol };
    return [
      {
        key: actionKey(action),
        group: "tokens" as const,
        title: meta?.name ? `${meta.name} (${symbol})` : symbol,
        detail: `${shown} · ${contract}`,
        source: "Alchemy",
        action,
        // Token lists are full of airdropped spam, so the user opts in to each one.
        preselect: false,
        meta: {
          decimals: meta?.decimals ?? undefined,
          symbol: meta?.symbol ?? undefined,
          tokenName: meta?.name ?? undefined,
          formattedAmount: shown,
          contractAddress: contract,
        },
      },
    ];
  });
}

export function nftCandidates(nfts: AlchemyNft[]): Candidate[] {
  return nfts.flatMap((nft): Candidate[] => {
    const contract = getAddress(nft.contract.address);
    const collection = nft.contract.name || nft.contract.symbol || contract;
    const name = nft.name || `${collection} #${nft.tokenId}`;
    const spam = Boolean(nft.contract.isSpam);
    const common = {
      group: "nfts" as const,
      title: name,
      preselect: !spam,
      source: "Alchemy",
      meta: {
        collection,
        contractAddress: contract,
        tokenId: nft.tokenId,
        spam,
      },
    };
    const spamNote = spam ? " · flagged as spam" : "";
    if (nft.tokenType === "ERC721") {
      const action = { type: "erc721" as const, contract, token_id: nft.tokenId, label: name };
      const detail = `${collection} · ERC-721 · ${contract} #${nft.tokenId}${spamNote}`;
      return [{ ...common, key: actionKey(action), detail, action }];
    }
    if (nft.tokenType === "ERC1155") {
      const known = /^[1-9]\d*$/.test(nft.balance ?? "");
      const amount = known ? (nft.balance as string) : "1";
      const action = { type: "erc1155" as const, contract, token_id: nft.tokenId, amount, label: name };
      const shown = known ? `× ${amount}` : "× 1 (balance not reported; check it)";
      const detail = `${collection} · ERC-1155 · ${contract} #${nft.tokenId} ${shown}${spamNote}`;
      return [{ ...common, key: actionKey(action), detail, action }];
    }
    return [];
  });
}
