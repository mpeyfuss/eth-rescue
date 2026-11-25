import {
  fetchNfts,
  fetchTokenBalances,
  fetchTokenMetadata,
  heldBalances,
  nftCandidates,
  tokenCandidates,
} from "./alchemy";
import { readOwners } from "./onchain";
import {
  contractCandidates,
  fetchCreatorContracts,
  fetchCreatorListings,
  findUserIds,
  listingCandidates,
  transientApi,
} from "./transient";
import type { Candidate, ScanRequest, ScanResult, SourceError } from "./types";

type Source = { name: string; run: () => Promise<Candidate[]> };

async function scanTokens({ network, victim, alchemyKey }: ScanRequest): Promise<Candidate[]> {
  if (!alchemyKey) return [];
  const balances = await fetchTokenBalances(network, alchemyKey, victim);
  const held = heldBalances(balances);
  const metadata = await fetchTokenMetadata(
    network,
    alchemyKey,
    held.map((balance) => balance.contractAddress),
  );
  return tokenCandidates(held, metadata);
}

async function scanNfts({ network, victim, alchemyKey }: ScanRequest): Promise<Candidate[]> {
  if (!alchemyKey) return [];
  return nftCandidates(await fetchNfts(network, alchemyKey, victim));
}

type ReadOwners = typeof readOwners;

async function scanTransient(
  { network, victim, alchemyKey }: ScanRequest,
  ownersOf: ReadOwners,
): Promise<Candidate[]> {
  const api = transientApi(network);
  const userIds = await findUserIds(api, victim);
  if (userIds.length === 0) return [];
  const [listings, contracts] = await Promise.all([
    fetchCreatorListings(api, network, userIds),
    fetchCreatorContracts(api, network, userIds),
  ]);
  const addresses = contracts.map((contract) => contract.address.raw_address);
  const owners = await ownersOf(network, alchemyKey, addresses);
  return [...listingCandidates(listings, victim), ...contractCandidates(contracts, owners, victim)];
}

function message(error: unknown): string {
  return error instanceof Error ? error.message.split("\n")[0] : String(error);
}

// Each source fails on its own so one outage never hides the others' results.
export async function scanWallet(request: ScanRequest, ownersOf: ReadOwners = readOwners): Promise<ScanResult> {
  const sources: Source[] = [
    { name: "Tokens (Alchemy)", run: () => scanTokens(request) },
    { name: "NFTs (Alchemy)", run: () => scanNfts(request) },
    { name: "Transient", run: () => scanTransient(request, ownersOf) },
  ];
  const settled = await Promise.allSettled(sources.map((source) => source.run()));
  const candidates: Candidate[] = [];
  const errors: SourceError[] = [];
  settled.forEach((result, index) => {
    if (result.status === "fulfilled") candidates.push(...result.value);
    else errors.push({ source: sources[index].name, message: message(result.reason) });
  });
  const seen = new Set<string>();
  return { candidates: candidates.filter((c) => !seen.has(c.key) && seen.add(c.key)), errors };
}
