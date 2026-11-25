import type { Network, PlanAction } from "../plan/draft";

export type CandidateGroup = "tokens" | "nfts" | "listings" | "contracts";

export interface Candidate {
  key: string;
  group: CandidateGroup;
  title: string;
  detail: string;
  action: PlanAction;
  preselect: boolean;
  // Set when the asset was found but this plan cannot rescue it.
  blockedReason?: string;
}

export interface SourceError {
  source: string;
  message: string;
}

export interface ScanResult {
  candidates: Candidate[];
  errors: SourceError[];
}

export interface ScanRequest {
  network: Network;
  victim: string;
  alchemyKey: string | null;
}

export class HttpError extends Error {}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) throw new HttpError(`${new URL(url).host} returned HTTP ${response.status}`);
  return response.json() as Promise<T>;
}
