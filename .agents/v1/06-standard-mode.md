# Phase 6: Per-Chain Standard Mode

## Objective

Replace the current single-path wizard with a per-chain reviewed planning flow
that preserves every existing action, optionally accelerates standard asset
entry with Alchemy, and exposes compatibility fallbacks honestly.

## Network and Account Setup

- Select exactly one supported mainnet or rehearsal network.
- Collect victim, gas wallet, and one safe using the same distinct-address and
  destination verification rules as Emergency, scoped to the selected chain.
- Connect and validate before performing onchain lookups.

## Optional Discovery

Ask whether to discover directly held assets with Alchemy.

- If skipped, never request or read an Alchemy key.
- If selected, prompt for the key through hidden input and run Phase 4 discovery
  only for the selected chain.
- Show Emergency-eligible, sub-$1, unpriced, unknown, and verification-failed
  groups.
- Hide spam by default behind an explicit reveal.
- Let the user select all, none, or individual candidates.
- Reverify selected candidates before action generation.
- Apply the Phase 4 special-contract registry before presenting provider token
  types, including SuperRare V1 expansion from a returned ERC-20 record.

Discovery results are a checklist, not a claim that the wallet has no other
positions.

## Manual Planning

Preserve and adapt the current actions:

- ERC-20 transfer;
- ERC-721 transfer;
- ERC-1155 transfer;
- Transient auction-house delist followed by transfer;
- contract ownership transfer;
- custom target, canonical function signature, arguments, and fallback gas;
- load a saved JSON action plan.

Allow discovered and manual actions in one ordered plan. Preserve descriptions,
review, add, remove, rebuild, and cancel behavior. Keep JSON compatibility and
validate all inputs before calldata encoding.

Explicitly warn that generic discovery does not find listed/escrowed NFTs,
staked positions, deposits, claims, roles, or contract ownership. Those remain
manual until a protocol-specific detector is deliberately added.

## Simulation and Execution Choice

Prepare the complete plan through the sponsored EIP-7702 backend by default.
Show per-action best-effort simulation outcomes rather than requiring every
target call to succeed.

The user may:

- remove or revise failed actions;
- continue with failed actions as best effort;
- retry fresh state;
- execute compatible actions now and build a follow-up batch;
- cancel.

After a session begins, allow repeated sponsor-paid calls for retries or newly
added actions until the user closes the session. The initial call sweeps the
full native balance before its first asset batch; the closing call sweeps again
after its last batch. The victim is never funded on this backend.

### Ethereum execution backends

Retain direct victim transactions as an explicit protected Flashbots backend
alongside the default sponsored EIP-7702 backend. Simulation should recommend
it when delegated execution is incompatible, but users may select it
deliberately for an existing workflow.

The Ethereum mainnet bundle order is fixed:

1. sponsor installs the executor, opens and immediately closes an empty
   session, and sweeps the full preexisting native balance;
2. sponsor clears the victim delegation;
3. gas wallet funds only the direct victim action gas budget;
4. victim originates every ordered action with no delegated code;
5. sponsor reinstalls the executor, opens and immediately closes a fresh empty
   session, and sweeps unused funding or newly received native currency;
6. sponsor clears the victim delegation again.

Allow no reverting transaction hashes. Every direct action must simulate
cleanly; the user must split, revise, or remove failures. This backend is not
best effort because there is no executor wrapper around the direct calls.

Do not recommend the EOA backend merely for a normal target revert, stale
balance, or bad calldata. Base, Arbitrum, and Optimism fail closed when no safe
sponsored path exists.

When explicit testnet mode is enabled, Ethereum Sepolia uses direct ordered
submission by default and clearly labels it non-atomic/test-only. The
`ETH_RESCUE_SEPOLIA_USE_FLASHBOTS=1` override uses the bundle backend instead.
Neither sequential EOA execution nor this override changes mainnet policy.

Both Ethereum production backends must verify a zero victim balance at their
final sponsored sweep. Only the protected direct backend funds the victim, and
only during the private bundle section where the victim originates actions.

## Confirmation and Reporting

- Render the exact ordered action plan, safe, backend, expected failures,
  maximum funding, and simulation results.
- Require explicit confirmation before the first submission.
- Confirm again only when the user materially changes the safe or plan after a
  submitted attempt.
- Verify and report outcomes using Phase 3 postconditions.
- Clearly distinguish rescued, failed, deferred, and unverified actions.

## Tests

- Standard works end-to-end without an Alchemy key.
- Selecting discovery prompts once and merges selected assets with manual
  actions in deterministic order.
- Sub-$1 and unpriced tokens appear for selection.
- Spam remains hidden unless explicitly revealed.
- Every existing wizard and JSON validation test remains represented.
- Transient delist ordering and owner checks remain intact.
- Repeated session calls add/retry actions safely.
- 7702 Standard sweeps before the first asset batch and again at close without
  funding the victim.
- Ethereum offers both backends, defaults to 7702, and recommends the EOA
  backend only for diagnosed delegated incompatibility.
- The protected direct bundle performs pre/post sponsored sweeps, clears code
  for victim-originated actions, allows no reverts, and clears delegation at
  completion.
- L2 incompatibility refuses unsafe execution.
- Changing safe rebuilds and resimulates the entire plan.

## Acceptance Criteria

- A current user can reproduce every existing rescue without Alchemy.
- A discovery user can build standard transfers without manually entering
  contract addresses, token IDs, or balances.
- Manual/protocol-specific actions coexist with discovered actions.
- No UI path implies direct-holding discovery is comprehensive.
