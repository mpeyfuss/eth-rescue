# Phase 7: Hardening, Deployment, and Release

## Objective

Turn the completed V1 implementation into a reproducible, reviewed, and
operationally safe release. Mainnet support is not enabled by default until all
release gates pass.

## Contract Deployment

Deploy the reviewed executor separately to:

- Ethereum Sepolia, Base Sepolia, Arbitrum Sepolia, OP Sepolia;
- Ethereum, Base, Arbitrum, Optimism mainnets after testnet acceptance.

For every deployment record:

- chain ID and address;
- deploy transaction and block;
- compiler and optimizer settings;
- source revision and ABI hash;
- creation and runtime bytecode hashes;
- independent explorer/source verification status.

Store public deployment metadata in the repository. Do not store deployment
keys or secrets. Network configuration must validate the runtime-code hash at
startup and fail closed on mismatch.

## Contract Security Review

Before mainnet enablement:

- complete internal review of authorization, session-key power, storage
  namespace, replay, expiry, gas accounting, result decoding, and cleanup;
- run static analysis appropriate to the final Solidity toolchain;
- run all fuzz and invariant tests for an extended campaign;
- commission or obtain an independent review focused on EIP-7702 delegated
  execution and malicious target contracts;
- resolve or explicitly document every finding.

Mainnet executor addresses remain disabled until review sign-off is recorded.

## Automated Verification

Run and require:

- `uv run ruff format --check .` or the repository's non-mutating format check;
- `uv run ruff check .`;
- `uv run pytest`;
- Foundry format check, build, unit tests, fuzz tests, and invariants;
- Anvil integration tests on a hardfork supporting EIP-7702;
- mocked Alchemy pagination/failure suites;
- backend contract/Python ABI compatibility checks;
- deterministic/reproducible bytecode checks.

Add regression coverage for:

- unauthorized sponsor and stolen/replayed session signatures;
- victim nonce races and delegation replacement;
- expired sessions and stale prepared payloads;
- reentrancy, gas griefing, return-data bombs, and malicious token behavior;
- false/no-return ERC-20s and fee-on-transfer balance deltas;
- destination contract receiver incompatibilities;
- partial discovery and unavailable paid spam filtering;
- exactly-$1 threshold and decimal precision;
- RPC/relay/sequencer timeout, rate limiting, dropped/replaced transactions;
- chain continuation after failure;
- session close and delegation cleanup failure;
- required initial-before-assets and final native sweeps, rejecting-safe
  rollback, persistent delegation after outer revert, and retry reconciliation;
- protected Ethereum direct-backend ordering, temporary delegation clears,
  direct caller context, pre/post full sweeps, and refusal of permitted reverts;
- special-contract override precedence, conflicting Alchemy classifications,
  and canonical calldata/postcondition fixtures;
- testnets hidden by default, explicit testnet enablement, Sepolia sequential
  rehearsal default, and Flashbots override;
- no secret material in logs, errors, reports, or fixtures.

## Testnet Rehearsals

On each Sepolia network, rehearse:

- native-only rescue;
- mixed ERC-20/721/1155 rescue;
- single-call initial sweep, best-effort assets, final sweep, and close;
- multi-batch initial/final sweep behavior;
- one action revert with later actions succeeding;
- false-return token;
- contract safe that accepts assets;
- contract safe that rejects native or ERC-1155 receipt;
- repeated session calls;
- attacker nonce change between simulation and submission;
- close and clear delegation;
- partial mocked discovery combined with live execution.

On Ethereum Sepolia, also rehearse the protected direct sequence with a fixture
that rejects delegated callers. Confirm the default sequential transport warns
about possible partial inclusion and the Flashbots override exercises the exact
bundle path.

Add opt-in, redacted Alchemy probes for known holders of each supported legacy
contract. Capture sanitized response fixtures without API keys or wallet
secrets so classification changes can be detected before release.

Record endpoint compatibility and observed fee behavior. Rehearsals are
explicit opt-in tests and must never use production victim or safe keys.

## Documentation

Update the README and CLI guidance to cover:

- Emergency versus Standard mode;
- supported/deferred chains;
- Alchemy key requirements and address disclosure;
- `$1` fungible policy and NFT spam limitations;
- special-contract overrides and the rule that absent Alchemy contracts are not
  independently scanned;
- direct holdings versus undiscoverable positions;
- one safe address across Emergency chains and double verification;
- sponsor funding per chain;
- persistent EIP-7702 delegation, session close, and cleanup;
- best-effort semantics and postcondition verification;
- both Ethereum execution backends, the protected direct bundle sequence, and
  absence of an EOA L2 mainnet backend;
- attacker nonce/delegation races and experimental-software warning.

Include operator checklists for preparing the safe, funding the gas wallet,
interpreting partial reports, and following up in Standard mode.

## Rollout

1. Merge code with Emergency mainnet execution disabled.
2. Publish testnet executor metadata and run rehearsals.
3. Complete independent review and resolve findings.
4. Deploy and verify mainnet executors.
5. Enable one mainnet at a time in fixed order, beginning with Ethereum.
6. Monitor RPC compatibility and user-reported outcomes before enabling the
   next chain.
7. Keep a kill switch in local configuration that disables a backend or
   executor address without changing contract state or silently selecting an
   unsafe fallback.

The kill switch may prevent new sessions only. It must never imply cancellation
of an already submitted transaction.

## Release Acceptance Criteria

- All automated checks and four-chain rehearsals pass.
- Independent contract review is complete.
- Every enabled executor has verified source and matching runtime-code hash.
- Documentation and UI accurately describe partial discovery, best effort,
  fallback limits, and persistent delegation.
- Emergency failure on one chain demonstrably continues the others.
- Standard remains functional without Alchemy.
- No mainnet path funds a compromised victim outside the protected Ethereum
  direct-action bundle section.
