# Phase 1: Foundation and Chain Abstractions

## Objective

Create stable project-owned models and boundaries for multiple networks,
discovery providers, execution backends, and result reporting without changing
the current Ethereum rescue behavior.

## Prerequisites

- Current unit and integration tests pass.
- The decisions in `00-roadmap.md` are treated as requirements.

## Implementation

### Network configuration

Replace the relay-required `Network` shape with a typed configuration that can
represent both Ethereum bundles and L2 sponsored execution. Each configuration
must include:

- stable key and display label;
- chain ID and testnet/mainnet designation;
- public/default RPC URL;
- native symbol;
- Alchemy Portfolio network identifier;
- submission transport (`flashbots` or `sequencer_rpc`);
- optional relay endpoint and builder routing;
- optional configured executor address and expected runtime-code hash;
- capability flags used only for UI expectations, never instead of a live
  runtime check.

Define the four mainnets and four Sepolia networks centrally. Do not duplicate
chain IDs or Alchemy names in discovery or orchestration modules.

The network chooser exposes only mainnets by default. It exposes testnets only
when `ETH_RESCUE_ENABLE_TESTNETS=1`; do not infer testnet mode from RPC URLs or
chain IDs. Ethereum Sepolia uses a direct sequential rehearsal transport by
default, with a prominent non-atomic/test-only warning. Setting
`ETH_RESCUE_SEPOLIA_USE_FLASHBOTS=1` selects the Flashbots bundle transport.
No sequential EOA rescue transport may be configured for a mainnet.

### Domain models

Introduce project-owned types for:

- `AssetKind`: native, ERC-20, ERC-721, ERC-1155;
- `DiscoveredAsset`: chain, contract/token identity including opaque legacy
  identifiers, raw balance, metadata, price data, spam status, source, adapter
  key, and verification status;
- `DiscoveryCompleteness`: complete or partial per chain and asset class, with
  user-facing reasons;
- `RescueAction`: target, calldata, gas cap, description, action kind, expected
  postcondition, and source;
- `ChainRescuePlan`: network, victim, sponsor, safe, ordered actions, batches,
  funding estimate, and deferred candidates;
- `ActionOutcome`: attempted, EVM success, semantic success, verification
  status, revert/return summary, transaction hash, and action identity;
- `ChainRescueReport`: rescued, failed, deferred, disappeared, unverified, and
  skipped records.

Use integer base units and `Decimal` for display prices. Never store monetary
values as binary floats.

### Behavioral interfaces

Define narrow protocols or abstract base classes:

- `DiscoveryProvider.discover(address, networks) -> DiscoveryResult`;
- `ExecutionBackend.prepare(plan) -> PreparedExecution`;
- `ExecutionBackend.simulate(prepared) -> SimulationReport`;
- `ExecutionBackend.submit(prepared) -> SubmissionReport`;
- `ExecutionBackend.verify(plan, submission) -> ChainRescueReport`.
- `SpecialAssetAdapter`: normalize/expand a returned provider candidate,
  verify ownership, build local transfer calldata, and verify its postcondition.

Define an injectable registry keyed by `(chain_id, checksum_contract_address)`.
The production registry contains only reviewed canonical contracts; tests may
provide fixture registries. Provider token-type labels never bypass a matching
override.

Keep submission details behind the backend. Emergency orchestration must not
call `send_bundle` or `send_raw_transaction` directly.

### CLI routing

Add a top-level mode selection boundary for Emergency and Standard, but route
Standard to the existing flow until later phases replace it. Do not expose a
nonfunctional Emergency option before its dependencies exist; guard it behind
an internal availability flag or introduce it only in Phase 5.

### Error model

Add specific exceptions for:

- unsupported chain capability;
- incomplete discovery;
- invalid executor deployment;
- simulation failure;
- stale prepared execution;
- submission failure;
- postcondition verification failure.

Expected chain/provider failures become structured results where orchestration
can safely continue. Programming errors and invalid invariants must surface.

## Public Interface Changes

- `Network` no longer requires a relay URL or builders.
- Standard action templates return `RescueAction` or are adapted into it.
- Existing JSON `RescueData` remains accepted through a conversion boundary.
- `connect` returns a normal Web3 client plus an execution backend rather than
  attaching a mandatory relay to a Web3 subclass.

## Tests

- All eight network definitions have unique keys and expected chain IDs.
- Relay configuration is required only for Flashbots networks.
- Ethereum mainnet connection validation is unchanged; Sepolia transport and
  visibility follow the explicit environment settings above.
- Testnets are absent by default and all four appear when testnet mode is on.
- Sepolia defaults to sequential rehearsal submission and the override selects
  Flashbots; neither setting enables sequential mainnet submission.
- Existing action templates convert without changing target or calldata.
- Domain values reject cross-chain mismatches, invalid addresses, negative
  balances, and invalid token identifiers.
- Special-asset registry keys are chain-scoped and checksum-normalized.
- Structured partial and skipped reports render without raising.
- Existing bundle tests continue to pass through the compatibility adapter.

## Acceptance Criteria

- No user-visible behavior regression in the current flow.
- No Alchemy or Solidity dependency is introduced in this phase.
- Network, discovery, execution, and orchestration code no longer depend on a
  relay-specific Web3 type.
- Later phases can add a backend/provider without modifying existing domain
  models or the current bundle implementation.
