import {
  fetchNfts,
  fetchTokenBalances,
  fetchTokenMetadata,
  heldBalances,
  nftCandidates,
  tokenCandidates,
} from "./alchemy";
import { collectionFor, isCollectionType } from "../plan/collections";
import { type CollectionScanner, COLLECTION_SCANNERS } from "./collections";
import { publicClient, readOwners } from "./onchain";
import {
  contractCandidates,
  fetchCreatorContracts,
  fetchCreatorListings,
  findUserIds,
  listingCandidates,
  transientApi,
} from "./transient";
import type { Candidate, ScanRequest, ScanResult, SourceError, Warn } from "./types";

type Source = { name: string; run: (warn: Warn) => Promise<Candidate[]> };

async function scanTokens({ network, compromised, alchemyKey }: ScanRequest, warn: Warn): Promise<Candidate[]> {
  if (!alchemyKey) return [];
  const balances = await fetchTokenBalances(network, alchemyKey, compromised, warn);
  const held = heldBalances(balances);
  let metadata: Awaited<ReturnType<typeof fetchTokenMetadata>> = new Map();
  try {
    metadata = await fetchTokenMetadata(
      network,
      alchemyKey,
      held.map((balance) => balance.contractAddress),
    );
  } catch (error) {
    // The balances are already known; show them in base units, unnamed.
    if (held.length > 0) warn(`could not read token names and decimals: ${message(error)}`);
  }
  return tokenCandidates(held, metadata);
}

async function scanNfts({ network, compromised, alchemyKey }: ScanRequest, warn: Warn): Promise<Candidate[]> {
  if (!alchemyKey) return [];
  return nftCandidates(await fetchNfts(network, alchemyKey, compromised, warn));
}

type ReadOwners = typeof readOwners;

async function scanTransient(
  { network, compromised, alchemyKey }: ScanRequest,
  ownersOf: ReadOwners,
  warn: Warn,
): Promise<Candidate[]> {
  const api = transientApi(network);
  const userIds = await findUserIds(api, compromised, warn);
  if (userIds.length === 0) return [];
  const [listings, contracts] = await Promise.all([
    fetchCreatorListings(api, network, userIds, warn),
    fetchCreatorContracts(api, network, userIds, warn),
  ]);
  const addresses = contracts.map((contract) => contract.address.raw_address);
  let owners = new Map<string, string | null>();
  try {
    owners = await ownersOf(network, alchemyKey, addresses);
  } catch (error) {
    // Listings need no RPC; keep them, and mark the contracts unreadable.
    if (addresses.length > 0) warn(`could not read contract owners: ${message(error)}`);
  }
  return [...listingCandidates(listings, compromised), ...contractCandidates(contracts, owners, compromised)];
}

function message(error: unknown): string {
  return error instanceof Error ? error.message.split("\n")[0] : String(error);
}

// A collection's own scanner is the only source for its contract: anything a
// generic source reports there would be a transfer that fails.
function claimedByCollection(request: ScanRequest, candidate: Candidate): boolean {
  if (isCollectionType(candidate.action.type)) return false;
  const contract = "contract" in candidate.action ? candidate.action.contract : candidate.meta?.contractAddress;
  return collectionFor(request.network, contract) !== null;
}

// Each source fails on its own so one outage never hides the others' results.
export async function scanWallet(
  request: ScanRequest,
  ownersOf: ReadOwners = readOwners,
  collections: CollectionScanner[] = COLLECTION_SCANNERS,
): Promise<ScanResult> {
  const client = publicClient(request.network, request.alchemyKey);
  const sources: Source[] = [
    { name: "Tokens (Alchemy)", run: (warn) => scanTokens(request, warn) },
    { name: "NFTs (Alchemy)", run: (warn) => scanNfts(request, warn) },
    { name: "Transient", run: (warn) => scanTransient(request, ownersOf, warn) },
    ...collections.map((collection) => ({
      name: collection.name,
      run: (warn: Warn) => collection.scan(request, client, warn),
    })),
  ];
  const warnings: SourceError[] = [];
  const settled = await Promise.allSettled(
    sources.map((source) => source.run((text) => warnings.push({ source: source.name, message: text }))),
  );
  const candidates: Candidate[] = [];
  const errors: SourceError[] = [];
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") candidates.push(...result.value);
    else errors.push({ source: sources[index].name, message: message(result.reason) });
  });
  const seen = new Set<string>();
  const kept = candidates.filter((c) => !claimedByCollection(request, c) && !seen.has(c.key) && seen.add(c.key));
  return { candidates: kept, errors, warnings };
}
