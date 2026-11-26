import { COLLECTION_CONTRACTS, COLLECTION_NAMES, type CollectionType } from "../../plan/collections";
import { actionKey, type Network } from "../../plan/draft";
import type { Candidate } from "../types";

export function collectionCandidate(network: Network, type: CollectionType, tokenId: bigint | number): Candidate {
  const name = COLLECTION_NAMES[type];
  const id = tokenId.toString();
  const title = type === "cryptopunk" ? `CryptoPunk #${id}` : `${name} #${id}`;
  const action = { type, token_id: id, label: title };
  const contract = COLLECTION_CONTRACTS[network][type];
  return {
    key: actionKey(action),
    group: "nfts",
    title,
    detail: `${name} · ${contract} #${id}`,
    source: `${name} contract`,
    action,
    preselect: true,
    meta: { collection: name, contractAddress: contract, tokenId: id },
  };
}
