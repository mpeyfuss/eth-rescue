# V1 Rescue Overhaul Roadmap

## Objective

Evolve `eth-rescue` from an Ethereum-only, Flashbots-bundle CLI into a two-mode
rescue tool:

- **Emergency:** discover and rescue directly held assets across Ethereum,
  Base, Arbitrum, and Optimism with minimal interaction.
- **Standard:** build a reviewed rescue for one chain from optional discovery,
  the existing guided actions, or a JSON plan.

The implementation is split into independently verifiable phases. Each phase
must leave the repository runnable and must not silently weaken the active
sweeper threat model.

## Locked Product Decisions

- Initial mainnets: Ethereum (`1`), Base (`8453`), Arbitrum One (`42161`), and
  OP Mainnet (`10`).
- Rehearsal networks: Ethereum Sepolia (`11155111`), Base Sepolia (`84532`),
  Arbitrum Sepolia (`421614`), and OP Sepolia (`11155420`).
- Emergency uses one victim key, one gas-wallet key, and exactly one safe
  address on every chain. There are no per-chain safe-address overrides.
- Emergency chain order is fixed: Ethereum, Base, Arbitrum, Optimism.
- Emergency requires Alchemy Portfolio discovery. Standard discovery is
  optional and Standard remains usable without an Alchemy key.
- The Alchemy key is requested through hidden input and held only in memory.
- Native currency is always attempted. Fungible tokens are automatically
  attempted in Emergency only when their returned USD value is at least $1.
- Unpriced and sub-$1 fungible tokens are deferred to Standard.
- NFT discovery requests use Alchemy's `HIGH` spam-confidence threshold, which
  excludes both `HIGH` and `VERY_HIGH` classified spam. Unknown or unavailable
  spam classification does not exclude an NFT.
- Asset calls are best effort. Sponsored EIP-7702 execution performs a required
  full-balance native sweep during the initial type-4 call before asset calls,
  then sweeps again when the session closes. Authorization, session, replay,
  expiry, native-transfer, and structural failures remain hard failures.
- Discovery is a proposal source, never an execution authority. Current
  onchain balances and ownership are verified before calldata is built.
- Partial discovery results may be rescued, but missing coverage must be
  reported prominently.
- Emergency uses sponsored EIP-7702 only and never funds the victim or changes
  backend mid-run. Standard defaults to EIP-7702 on every chain.
- Ethereum Standard retains a protected direct-EOA Flashbots backend for
  delegated-caller incompatibility. Its bundle uses sponsored EIP-7702 sweeps
  before and after a temporarily undelegated, funded direct-EOA action
  sequence. L2 mainnets support only sponsored EIP-7702 execution.
- Alchemy candidates pass through a chain-and-contract override registry before
  token-standard policy. The first Ethereum overrides cover CryptoPunks,
  SuperRare V1, original MoonCats, EtherRock, and CryptoKitties; Autoglyphs and
  wrapped MoonCats remain on the normal ERC-721 path.
- Testnets are hidden unless `ETH_RESCUE_ENABLE_TESTNETS=1`. Ethereum Sepolia
  defaults to direct sequential rehearsal submission because bundle inclusion
  is unreliable; `ETH_RESCUE_SEPOLIA_USE_FLASHBOTS=1` explicitly restores the
  Flashbots transport. Sequential execution is test-only and never offered on
  a mainnet.
- Private keys, Alchemy keys, and signed rescue payloads are not persisted.

## Phase Order

1. [Foundation and chain abstractions](01-foundation-chain-abstractions.md)
2. [Rescue executor contract](02-rescue-executor-contract.md)
3. [Sponsored EIP-7702 backend](03-sponsored-7702-backend.md)
4. [Alchemy Portfolio discovery](04-alchemy-portfolio-discovery.md)
5. [Multi-chain Emergency mode](05-emergency-mode.md)
6. [Per-chain Standard mode](06-standard-mode.md)
7. [Hardening, deployment, and release](07-hardening-deployment-release.md)

Phases 2 and 4 may be developed independently after Phase 1. Phase 5 requires
Phases 2-4. Phase 6 requires Phases 1-4. Phase 7 is the mainnet release gate.

## Target Architecture

Keep these responsibilities separate:

- **Network configuration:** chain identity, RPC endpoints, Alchemy network
  name, native symbol, executor deployment, and submission transport.
- **Discovery:** untrusted indexed candidates, special-contract overrides, and
  completeness metadata.
- **Planning:** verified actions, expected outcomes, batching, and costs.
- **Execution:** protected Ethereum direct-EOA bundle or sponsored EIP-7702
  session.
- **Submission:** Flashbots private delivery on Ethereum or sequencer/RPC
  delivery on L2s.
- **Verification:** executor events plus authoritative post-transaction reads.
- **Orchestration:** Emergency multi-chain flow or Standard single-chain flow.

Do not expose Web3, HTTP response, or Alchemy response shapes across these
boundaries. Normalize them into project-owned types.

## Compatibility Strategy

- Phase 1 must preserve current Ethereum mainnet behavior. Sepolia deliberately
  changes to the explicit, test-only transport policy above.
- Introduce new types and strategies behind adapters before replacing the
  existing orchestration.
- The first EIP-7702 release is additive. Remove no bundle behavior until the
  Standard compatibility backend has equivalent tests.
- Existing JSON plans remain accepted. Add fields only as optional values with
  defaults or introduce a versioned plan shape.
- Existing action templates remain the source of standard calldata.

## Global Verification Requirements

Every phase must run the relevant existing unit tests plus its new focused
tests. Contract or end-to-end phases must also run the Anvil integration suite.
Before enabling a mainnet executor, complete all checks in Phase 7, including
testnet rehearsals and an independent contract security review.

## Definition of V1 Complete

- Emergency can discover, fund, simulate, submit, and report a rescue across
  all four mainnets while safely continuing past an unavailable chain.
- Standard can operate with or without Alchemy and preserves every existing
  manual action.
- Standard defaults to a sponsored, repeatable EIP-7702 session without
  funding the victim; only its explicit protected Ethereum direct-EOA backend
  may temporarily fund the victim inside the private bundle.
- A successful sponsored session clears the victim's native balance during the
  initial call and again at session close.
- Known outcomes are verified from onchain state; the CLI never equates a
  successful outer receipt with a completely successful rescue.
- Unsupported execution paths fail closed with an actionable explanation.
- Documentation clearly distinguishes direct holdings from escrowed, listed,
  staked, controlled, or protocol-specific positions.
