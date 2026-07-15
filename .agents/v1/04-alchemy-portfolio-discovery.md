# Phase 4: Alchemy Portfolio Discovery

## Objective

Discover direct native, ERC-20, ERC-721, and ERC-1155 holdings through
Alchemy's current multi-chain Portfolio APIs while treating all indexed data as
untrusted and potentially incomplete.

## Endpoints

Use only these Portfolio endpoints for initial inventory:

- `POST /data/v1/{apiKey}/assets/tokens/by-address`
- `POST /data/v1/{apiKey}/assets/nfts/by-address`

Request Ethereum, Base, Arbitrum, and Optimism in the same logical discovery
run using `eth-mainnet`, `base-mainnet`, `arb-mainnet`, and `opt-mainnet`.
Maintain corresponding testnet identifiers in network configuration when the
endpoint supports them, but request them only when explicit testnet mode is on.

## Credentials and HTTP

- Prompt for the API key using hidden input only when discovery is selected.
- Hold it in memory; do not persist it, include it in exceptions, or print full
  request URLs.
- Use the existing `requests` dependency with a bounded timeout.
- Retry 429 and transient 5xx responses with bounded exponential backoff and
  jitter. Do not retry validation/authentication errors indefinitely.
- Follow every `pageKey` independently for tokens and NFTs.
- Retain verified results from completed pages if a later page fails and mark
  the affected chain/asset class partial.

## Normalization

Validate and normalize:

- network identifier and owner address;
- contract address and NFT token ID;
- raw integer balance;
- token type;
- decimals, symbol, and name as optional display metadata;
- USD price and timestamp;
- NFT spam status/classifications and floor metadata.

Use `Decimal` to calculate:

`usd_value = raw_balance / 10**decimals * usd_unit_price`

A fungible price is usable when it is a parseable positive USD price returned
by the endpoint with a timestamp. Use the latest returned USD entry and display
its timestamp; do not invent an additional staleness cutoff in V1.

Metadata errors, missing decimals, malformed prices, and unknown token types
defer the candidate rather than silently treating it as worthless.

## Special Asset Overrides

Before applying provider token types, price thresholds, or standard action
generation, group token and NFT records by `(chain_id, checksum_contract)` and
look for a reviewed `SpecialAssetAdapter` in the Phase 1 registry. A matching
adapter replaces normal classification for that contract and owns candidate
expansion, identifier parsing, ownership verification, transfer calldata, and
the postcondition.

Only invoke an override when Alchemy returned that contract from at least one
of the two Portfolio endpoints. Do not probe absent contracts or reconstruct
historical ownership from logs. An override may perform bounded onchain
expansion after Alchemy has supplied the trigger candidate. If both endpoints
return the same contract, pass all records to one adapter and emit one
deduplicated asset set.

Initial Ethereum registry:

- CryptoPunks (`0xb47e3cd837dDF8e4c57F05d70Ab865de6e193BBB`):
  require a returned token ID, verify `punkIndexToAddress(id)`, build
  `transferPunk(safe, id)`, and verify the same mapping at the safe.
- SuperRare V1 (`0x41A322b28D0fF354040e2CbC676F0320d8c8850d`):
  treat Alchemy's fungible/ERC-20 record only as a trigger, expand IDs with
  `tokensOf(victim)`, verify `ownerOf(id)`, and build `transfer(safe, id)`.
  Replace the aggregate record with NFTs and never apply the `$1` fungible
  threshold to it.
- Original MoonCats (`0x60cd862c9C687A9dE49aecdC3A99b74A4fc54aB6`):
  require an Alchemy identifier that converts exactly from decimal or hex to
  `bytes5`, verify `catOwners(id)`, and build `giveCat(id, safe)`. Defer a
  record without a usable identifier; do not enumerate the full collection.
- EtherRock (`0x41f28833be34E6EDe3C58D1f597BeF429861c4E2`):
  verify the owner field returned by `rocks(id)` and build
  `giftRock(id, safe)`. Use returned IDs when present; for an aggregate-only
  Alchemy candidate, perform the bounded `0..99` ownership scan.
- CryptoKitties (`0x06012c8cf97BEaD5deAe237070F9587f8E7A266d`):
  require a returned token ID, verify `ownerOf(id)`, and use the legacy
  `transferFrom(victim, safe, id)` path even when interface detection is
  incomplete.

Autoglyphs and the MoonCat old wrapper/Acclimator use the normal ERC-721
adapter. Keep canonical regression fixtures so provider metadata cannot route
them to a nonstandard action accidentally.

All override addresses, ABIs, argument ordering, and calldata are local code.
Never use API-provided calldata, ABIs, targets, or function signatures.

## Emergency Selection Policy

- Include native currency regardless of USD value.
- Include an ERC-20 when its calculated value is greater than or equal to
  exactly `$1.00`.
- Defer unpriced, malformed-price, missing-decimals, and sub-$1 fungibles to
  Standard.
- Request NFT spam exclusion with `spamConfidenceLevel: HIGH`; Alchemy then
  excludes classifications at both `HIGH` and `VERY_HIGH` confidence.
- If spam filtering is unavailable, unsupported, or rejected by the account
  tier, retry NFT discovery without the filter, include returned NFTs as
  unknown, and warn that screening was unavailable.
- Include every returned NFT not excluded by the high-confidence threshold,
  regardless of floor price.
- NFT floor metadata is display-only and never a rescue gate.
- A special override that produces NFTs follows the NFT policy even when the
  source record was labeled ERC-20 by Alchemy.

Spam-filtered NFTs are hidden from normal output, but counts/reasons may be
available in an explicit Standard review. Do not claim Alchemy classification
is definitive; no published accuracy metric is assumed.

## Onchain Verification and Action Generation

Immediately before planning each candidate:

- native: read current victim balance;
- ERC-20: call `balanceOf(victim)` and transfer the current verified balance;
- ERC-721: require `ownerOf(tokenId) == victim`;
- ERC-1155: call `balanceOf(victim, tokenId)` and transfer the current verified
  amount;
- verify contract code exists and token type is compatible with the generated
  standard action.
- special asset: run the adapter's authoritative ownership check and generate
  only its hardcoded local action.

Generate calldata exclusively through local templates. Never execute API
calldata, ABIs, links, images, or arbitrary metadata. If onchain verification
disagrees, mark the item disappeared or deferred and continue.

Direct ownership discovery intentionally does not cover escrowed/listed NFTs,
staked assets, protocol deposits, claims, roles, or contract ownership.

## Standard Discovery Surface

Standard receives all normalized categories:

- Emergency-eligible assets;
- unpriced and sub-$1 fungibles;
- metadata/verification failures;
- NFTs with unknown classification;
- optionally revealed spam-classified NFTs.

Selection remains user-driven. A missing Alchemy key must not block manual or
JSON planning.

## Tests

- Multi-chain request construction without credential leakage.
- Independent token/NFT pagination and deterministic deduplication.
- 429/5xx retries, auth failure, timeout, malformed JSON, and partial pages.
- Exact `$0.99`, `$1.00`, and `$1.01` selection using `Decimal`.
- Unpriced, missing-decimals, multiple-price, and malformed-price handling.
- High-and-very-high spam filtering and unfiltered fallback when unavailable.
- NFT inclusion with null spam status or no floor.
- ERC-721 and ERC-1155 token ID/balance normalization.
- Onchain balance/ownership disagreement.
- API-supplied metadata cannot influence calldata targets or arguments.
- Override lookup occurs before token-type and price policy, is chain-scoped,
  and deterministically replaces conflicting token/NFT records.
- SuperRare V1 ERC-20 classification expands through `tokensOf` and never
  enters fungible value filtering.
- CryptoPunk ownership/transfer, MoonCat `bytes5` parsing, EtherRock bounded
  enumeration, and CryptoKitty legacy transfer behavior.
- An absent canonical contract triggers no calls; a returned special candidate
  without a required identifier is deferred with an explicit reason.
- Wrapped MoonCats and Autoglyphs remain on the generic ERC-721 path.

## Acceptance Criteria

- Emergency receives a deterministic eligible/deferred inventory plus explicit
  completeness warnings.
- Partial API failure never appears as an empty, complete wallet.
- Standard can display deferred candidates without changing Emergency policy.
- Reviewed special-contract deviations produce authoritative local actions
  without trusting Alchemy's reported token standard.
- No API key or raw authenticated URL appears in logs, tracebacks, or reports.
