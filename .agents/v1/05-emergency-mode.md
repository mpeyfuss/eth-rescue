# Phase 5: Multi-Chain Emergency Mode

## Objective

Provide a minimal-interaction rescue of directly held assets across Ethereum,
Base, Arbitrum, and Optimism. Discovery and asset selection are automatic;
gas-wallet funding and one final typed confirmation remain explicit. Every
chain uses sponsored EIP-7702; Emergency has no direct-EOA backend.

## Entry and Accounts

Emergency collects, in order:

1. victim private key;
2. existing or newly generated clean gas-wallet key;
3. one safe address;
4. Alchemy API key through hidden input.

The same accounts and safe are used on every chain. Reject equality between
victim, gas, and safe. Preserve the current generated-gas-wallet disclosure and
acknowledgement behavior.

## Safe-Address Verification

- Accept only a raw valid EVM address and checksum-normalize it.
- Query `eth_getCode` on all four chains and display a table showing likely EOA,
  contract, or unavailable.
- Treat no code as “likely EOA,” not proof of control.
- If code exists, differs across chains, or cannot be read, show a prominent
  warning and require explicit acknowledgement that the destination is valid
  and controlled on every chain.
- Do not offer per-chain overrides.
- Recheck destination code before the final send summary.
- If the safe changes, rebuild and resimulate every payload.

At the global send gate, show the full checksum address and require the user to
re-enter the complete address with no default. Normalized mismatch blocks all
submission and returns to destination verification. This typed re-entry is the
single global irreversible-send confirmation.

## Discovery and Planning

- Run required Alchemy discovery for all four chains.
- Apply Phase 4 eligibility rules.
- Verify candidates onchain and build local standard actions.
- Keep partial results and display exactly which chain/asset class/pages are
  incomplete.
- Build gas-bounded best-effort batches for each viable chain.
- The initial type-4 call always performs a required full-balance native sweep
  before its first asset batch, even when the observed balance is zero.
- A one-batch plan sweeps again and closes in that same type-4 call. A
  multi-batch plan keeps the session open and performs the required final sweep
  in its closing batch.
- Do not expose per-asset selection or manual actions in Emergency.

Use fixed chain order:

1. Ethereum
2. Base
3. Arbitrum
4. Optimism

Do not reorder based on prices or holdings.

## Capability and Funding

For each chain:

- connect and validate chain ID;
- verify type-4 behavior and executor runtime-code hash;
- prepare batches and estimate sponsor funding including buffer and cleanup;
- classify failures as unavailable/skipped without blocking other chains.

Display one funding dashboard showing the same gas-wallet address and the
required native amount on each chain. The user may fund chains in any order.
Proceed with every sufficiently funded, viable chain; report unfunded chains as
skipped rather than aborting funded rescues.

## Simulation and Global Summary

After funding, create fresh session payloads and simulate every batch. The
summary must show:

- chain and batch count;
- current safe classification;
- discovered/eligible/deferred asset counts;
- partial discovery warnings;
- simulated successful and failed action counts;
- required initial and final native-sweep simulation results;
- funding and estimated maximum cost;
- skipped chains and reasons.

Individual asset failures do not block confirmation. Executor/session or stale
payload failures remove that chain from the viable set.

## Automatic Submission

After the safe-address re-entry:

- process viable funded chains in fixed order without further prompts;
- refresh state and re-simulate immediately before each chain's first send;
- if refresh invalidates the chain, record it and continue;
- submit the initial type-4 sweep/asset call, any intermediate sponsor-paid
  batches, and the final sweep/close when it was not part of the initial call;
- attempt delegation cleanup only after session close;
- reconcile potentially pending transactions before any retry;
- continue later chains after target-call, submission, receipt, cleanup, or
  verification failures wherever doing so is safe.

## Final Report

Render per-chain and overall sections for:

- verified rescued assets and actual amounts;
- failed attempts with decoded/truncated reasons;
- disappeared/stale assets;
- deferred sub-$1 or unpriced fungibles;
- unverified custom/unknown results;
- incomplete discovery coverage;
- skipped/unfunded/unsupported chains;
- native balance remaining;
- session close and delegation cleanup status.

Offer Standard mode as the follow-up for deferred or failed items. Do not write
the report or signed payloads to disk by default.

## Tests

- Account reuse and safe equality rejection.
- EOA, contract, inconsistent-code, and unavailable safe checks.
- Full-address re-entry match, mismatch, and changed destination rebuild.
- Fixed chain order independent of discovered value.
- Complete and partial discovery; partial verified results still execute.
- One unsupported, unfunded, simulation-failed, or submission-failed chain does
  not block later chains.
- Exactly-$1 fungibles execute; lower/unpriced fungibles defer.
- Unknown-classification NFTs execute with warning.
- Returned special-contract candidates use Phase 4 overrides, including
  SuperRare V1 records mislabeled as ERC-20.
- Global confirmation is requested exactly once.
- No per-asset, per-batch, or per-chain send confirmation appears afterward.
- Final report does not overstate outer receipt success.
- A failed initial sweep reverts that outer call, changes no asset ownership,
  and triggers delegation/authorization-nonce reconciliation before retry.
- A failed multi-batch closing sweep reverts only the closing call, leaves the
  earlier session open, and does not undo prior successful batches.
- Successful initial and final sweeps each verify zero victim balance at their
  completion.

## Acceptance Criteria

- A test run with fixtures on all four configured chain adapters completes from
  one discovery operation and one safe-address confirmation.
- The victim is never funded.
- No Emergency path offers or silently selects the direct-EOA backend.
- Any skipped or incomplete coverage is unmistakable in both summary and final
  report.
- A safe-address mismatch makes submission impossible.
