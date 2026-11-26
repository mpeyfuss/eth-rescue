import { useCallback, useRef, useState } from "react";
import { addressProblem } from "../plan/address";
import { discoveryScope, type Network } from "../plan/draft";
import type { ScanRequest, ScanResult } from "./types";

export type Scan = (request: ScanRequest) => Promise<ScanResult>;

export interface TokenMeta {
  decimals?: number;
  symbol?: string;
  name?: string;
}

export type ScanStatus =
  | { kind: "idle" }
  | { kind: "scanning" }
  | { kind: "done"; result: ScanResult }
  | { kind: "failed"; message: string };

const IDLE: ScanStatus = { kind: "idle" };

// Loaded on first scan: the RPC client is most of the bundle.
const scanWallet: Scan = async (request) => (await import("./scan")).scanWallet(request);

function tokenMetaFrom(result: ScanResult): Record<string, TokenMeta> {
  const entries: Record<string, TokenMeta> = {};
  for (const candidate of result.candidates) {
    const contract = candidate.meta?.contractAddress;
    if (candidate.action.type !== "erc20" || !contract) continue;
    entries[contract.toLowerCase()] = {
      decimals: candidate.meta?.decimals,
      symbol: candidate.meta?.symbol,
      name: candidate.meta?.tokenName,
    };
  }
  return entries;
}

// The latest scan of the current wallet. A scan started for another wallet or
// network never shows here, even if it finishes later.
export function useAssetScan({
  network,
  compromised,
  alchemyKey,
  scan = scanWallet,
  onTokenMeta,
}: {
  network: Network;
  compromised: string;
  alchemyKey: string | null;
  scan?: Scan;
  onTokenMeta?: (entries: Record<string, TokenMeta>) => void;
}) {
  const address = compromised.trim();
  const scope = discoveryScope(network, compromised);
  const [state, setState] = useState<{ scope: string; status: ScanStatus }>({ scope: "", status: IDLE });
  const latest = useRef(0);
  const status = state.scope === scope ? state.status : IDLE;
  const addressReady = !addressProblem(address);

  const run = useCallback(async () => {
    if (addressProblem(address)) return;
    const id = ++latest.current;
    setState({ scope, status: { kind: "scanning" } });
    try {
      const result = await scan({ network, compromised: address, alchemyKey });
      if (id !== latest.current) return;
      const meta = tokenMetaFrom(result);
      if (Object.keys(meta).length > 0) onTokenMeta?.(meta);
      setState({ scope, status: { kind: "done", result } });
    } catch (error) {
      if (id !== latest.current) return;
      setState({ scope, status: { kind: "failed", message: error instanceof Error ? error.message : String(error) } });
    }
  }, [address, scope, network, alchemyKey, scan, onTokenMeta]);

  return { status, scope, addressReady, run };
}
