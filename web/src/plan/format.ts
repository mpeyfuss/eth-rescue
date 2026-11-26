import { formatUnits, parseUnits } from "viem";
import type { Network } from "./draft";

export function shortAddress(address: string): string {
  const trimmed = address.trim();
  if (trimmed.length < 12) return trimmed;
  return `${trimmed.slice(0, 6)}…${trimmed.slice(-4)}`;
}

// Deterministic muted hue for visual address differentiation.
export function addressColor(address: string): string {
  const clean = address.trim().toLowerCase().replace(/^0x/, "");
  if (clean.length < 6) return "hsl(220 12% 52%)";
  const value = Number.parseInt(clean.slice(0, 6), 16);
  const hue = Number.isNaN(value) ? 220 : value % 360;
  return `hsl(${hue} 55% 48%)`;
}

const EXPLORERS: Record<Network, string> = {
  mainnet: "https://etherscan.io",
};

export function explorerAddressUrl(network: Network, address: string): string {
  return `${EXPLORERS[network]}/address/${address}`;
}

export function explorerTokenUrl(network: Network, contract: string, tokenId?: string): string {
  if (tokenId) return `${EXPLORERS[network]}/nft/${contract}/${tokenId}`;
  return `${EXPLORERS[network]}/token/${contract}`;
}

export function formatTokenAmount(raw: string, decimals: number): string | null {
  if (!/^\d+$/.test(raw.trim())) return null;
  try {
    const formatted = formatUnits(BigInt(raw.trim()), decimals);
    return addGrouping(formatted);
  } catch {
    return null;
  }
}

export function parseHumanAmount(human: string, decimals: number): string | null {
  const cleaned = human.trim().replace(/,/g, "");
  if (!cleaned || !/^\d+(\.\d+)?$/.test(cleaned)) return null;
  const [, frac = ""] = cleaned.split(".");
  if (frac.length > decimals) return null;
  try {
    const value = parseUnits(cleaned, decimals);
    if (value <= 0n) return null;
    return value.toString();
  } catch {
    return null;
  }
}

export function formatEth(wei: bigint): string {
  if (wei === 0n) return "0 ETH";
  const formatted = formatUnits(wei, 18);
  const [whole, frac = ""] = formatted.split(".");
  if (whole === "0" && frac.slice(0, 4) === "0000") return "< 0.0001 ETH";
  const trimmedFrac = frac.slice(0, 4).replace(/0+$/, "");
  return trimmedFrac ? `${addGrouping(whole)}.${trimmedFrac} ETH` : `${addGrouping(whole)} ETH`;
}

function addGrouping(decimal: string): string {
  const [whole, frac] = decimal.split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  if (!frac) return grouped;
  const trimmed = frac.replace(/0+$/, "");
  return trimmed ? `${grouped}.${trimmed}` : grouped;
}
