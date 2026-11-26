import type { Network, PlanAction } from "../plan/draft";

export type CandidateGroup = "tokens" | "nfts" | "listings" | "contracts";

export interface CandidateMeta {
  decimals?: number;
  symbol?: string;
  tokenName?: string;
  formattedAmount?: string;
  collection?: string;
  contractAddress?: string;
  tokenId?: string;
  spam?: boolean;
}

export interface Candidate {
  key: string;
  group: CandidateGroup;
  title: string;
  detail: string;
  // Who reported the asset; names come from them, the addresses are what count.
  source: string;
  action: PlanAction;
  preselect: boolean;
  // Set when the asset was found but this plan cannot rescue it.
  blockedReason?: string;
  meta?: CandidateMeta;
}

export interface SourceError {
  source: string;
  message: string;
}

export interface ScanResult {
  candidates: Candidate[];
  errors: SourceError[];
  // A source answered, but not completely (e.g. page limit reached).
  warnings: SourceError[];
}

export type Warn = (message: string) => void;

export interface ScanRequest {
  network: Network;
  compromised: string;
  alchemyKey: string | null;
}

export class HttpError extends Error {}

export const REQUEST_TIMEOUT_MS = 20_000;

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const host = new URL(url).host;
  let response: Response;
  try {
    response = await fetch(url, { ...init, signal: AbortSignal.timeout(REQUEST_TIMEOUT_MS) });
  } catch (error) {
    if (error instanceof DOMException && error.name === "TimeoutError") throw new HttpError(`${host} timed out`);
    throw error;
  }
  if (!response.ok) throw new HttpError(`${host} returned HTTP ${response.status}`);
  return response.json() as Promise<T>;
}

export function requireArray<T>(value: unknown, what: string): T[] {
  if (!Array.isArray(value)) throw new HttpError(`unexpected ${what} response`);
  return value as T[];
}
