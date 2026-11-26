import type { CollectionType } from "../../plan/collections";
import type { PublicClient } from "../onchain";
import type { Candidate, ScanRequest, Warn } from "../types";

// One per collection that predates ERC-721, so a generic NFT scan would either
// miss its tokens or offer a transfer that fails. Add a file beside this one and
// list it in index.ts.
export interface CollectionScanner {
  type: CollectionType;
  name: string;
  scan: (request: ScanRequest, client: PublicClient, warn: Warn) => Promise<Candidate[]>;
}
