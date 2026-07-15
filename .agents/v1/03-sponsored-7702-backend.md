# Phase 3: Sponsored EIP-7702 Backend

## Objective

Build, simulate, submit, monitor, and verify sponsored EIP-7702 rescue sessions
from Python. In sponsored execution, the victim must never need native currency
for gas. The protected Ethereum direct-EOA backend described below is the sole
exception and funds the victim only inside one private ordered bundle.

## Prerequisites

- Phase 1 execution boundaries exist.
- Phase 2 ABI, runtime-code hash, and local deployment fixture are available.

## Preparation

For a new rescue session:

1. Read victim code, victim authorization nonce, sponsor nonce, fees, executor
   deployment, and current chain head.
2. Reject unexpected executor bytecode or a chain that cannot process type-4
   authorization semantics.
3. Sign a chain-specific victim authorization pointing to the configured
   executor.
4. Sign the EIP-712 session configuration binding victim, sponsor, safe,
   current session nonce, and a short deadline.
5. Encode `openSessionAndExecute` with the first gas-bounded action batch and
   `closeAfter=true` when it is also the last batch.
6. Sign the outer type-4 transaction with the gas wallet.

The initial call performs the required full-balance native sweep before its
best-effort asset calls. Subsequent batches are ordinary EIP-1559 transactions
from the gas wallet to the victim, calling `execute`; the final batch calls
`executeAndClose`, or an empty final batch calls `sweepAndClose`. The victim
balance is never used for gas. After session close, build a separate
sponsor-paid type-4 authorization to the zero address. Cleanup failure is
reported but does not change rescued outcomes.

## Capability and Deployment Checks

- Validate RPC chain ID.
- Check configured executor address has exactly the expected runtime-code hash.
- Exercise a harmless type-4 simulation/estimation path rather than trusting a
  static `supports_7702` flag.
- Cache capability only for the current process and invalidate it when the RPC
  endpoint or chain changes.
- Distinguish unsupported transaction type, unsupported simulation fields,
  insufficient sponsor balance, and executor deployment mismatch.

## Fees and Funding

Implement a fee policy per network family:

- Ethereum uses EIP-1559 base and priority fees compatible with the existing
  private submission path.
- OP Stack chains include RPC-estimated execution, L1 data, and operator fee
  effects rather than applying Ethereum's 12-second assumptions blindly.
- Arbitrum uses its RPC gas estimate, including parent-chain posting costs.

Funding requirements include every planned outer transaction, a conservative
buffer, and cleanup. The sponsored backend never funds the victim. Recompute
immediately before each batch and wait for additional sponsor funding when
required. The protected direct-EOA backend has its own narrowly scoped funding
rule below.

## Simulation

- Simulate the exact first type-4 call with its authorization list and the exact
  later call payloads against fresh state.
- Use Flashbots exact raw-transaction simulation on Ethereum where available.
- Use the chain's supported call/simulation surface on L2s, applying the same
  authorization and delegated execution context.
- Decode returned executor outcomes for every asset action and both required
  native-sweep stages. Require the initial outer call and closing sweep to
  succeed even when individual asset actions fail.
- Treat target-call failures as expected best-effort results, not as outer
  simulation failure.
- Block submission on invalid authorization/session, executor revert,
  out-of-gas, malformed response, or stale target/deadline.
- Rebuild if the victim authorization nonce, sponsor nonce, fees, executor
  code, or deadline changes.

## Submission

- Ethereum: deliver the single type-4 rescue transaction privately through the
  existing Flashbots client, using a one-transaction bundle; use the same
  transport for later session calls when privacy is beneficial.
- Base and Optimism: send signed raw transactions to the configured sequencer
  RPC and wait for receipts.
- Arbitrum: send signed raw transactions to the sequencer/RPC and wait for
  receipts.
- Ethereum Sepolia, only when explicit testnet mode is enabled: submit ordered
  rehearsal transactions directly by default. If
  `ETH_RESCUE_SEPOLIA_USE_FLASHBOTS=1`, use Flashbots instead. The direct path
  must be labeled non-atomic and must be impossible to select on mainnet.
- Never publicly broadcast a funding transaction to the victim. The protected
  direct-EOA backend may include one only inside its private atomic bundle.
- Never implement a rapid sequential multi-sender fallback on L2s.

Retry only by rebuilding from fresh chain state. A submitted transaction may
still land, so nonce and receipt reconciliation precede every replacement.
If the initial execution reverts, reconcile the persistent delegation and
incremented victim authorization nonce even though session storage and value
transfers rolled back.

## Verification

Parse executor events from the victim address and associate them with planned
actions. Then run authoritative postconditions:

- ERC-20: current victim and safe balances, recording actual safe delta;
- ERC-721: `ownerOf(tokenId)`;
- ERC-1155: victim and safe `balanceOf`;
- ownership: `owner()` when the template declares that postcondition;
- native: successful `NativeSwept` event, safe balance delta, and zero victim
  balance at sweep completion;
- custom: event/return outcome only unless the plan defines a postcondition.

Classify each item as rescued, failed, disappeared/stale, or unverified. Do not
call the chain successful merely because the outer receipt has status `1`.

## Protected Ethereum Direct-EOA Backend

Retain direct victim transactions only as a Standard-mode Ethereum backend.
Sponsored EIP-7702 remains the default; the direct backend may be selected
deliberately or recommended after exact simulation diagnoses delegated-caller
incompatibility such as `tx.origin` or code-size checks. Emergency never uses
it, and L2 mainnets never expose it.

Build one private Flashbots bundle in this exact order:

1. sponsor installs the executor, opens a session with no asset actions, sweeps
   the full preexisting native balance, and closes;
2. sponsor submits a type-4 authorization clearing the victim delegation;
3. gas wallet funds the victim only for the direct action gas budget;
4. victim signs and originates the ordered direct actions with no delegated
   code, so `msg.sender == tx.origin == victim`;
5. sponsor reinstalls the executor with a fresh session, sweeps all unused
   funding and newly received native currency, and closes;
6. sponsor clears the delegation again.

Do not allow any reverting transaction hashes in this bundle. Every direct
action must simulate cleanly; split, revise, or remove a failing action rather
than treating it as best effort. Recompute both accounts' nonces and both
authorization nonces from the exact ordered sequence. A missed bundle is
rebuilt and resimulated from current state.

Ethereum Sepolia may execute this sequence directly only in explicit testnet
mode and must label partial inclusion as possible. The Flashbots environment
override restores ordered bundle submission. No sequential mainnet path is
permitted.

## Tests

- Initial type-4 fields, authorization, session signature, and calldata.
- Sponsored transactions use sponsor nonces and no victim funding.
- Fee/funding policies for Ethereum, OP Stack, and Arbitrum fixtures.
- Capability rejection and executor code-hash mismatch.
- Exact simulation decoding for mixed successful/failed actions.
- Receipt/log association and postcondition classifications.
- Stale nonce, expired deadline, fee change, dropped transaction, replacement,
  partial receipt, and cleanup failure.
- Ethereum private one-transaction submission.
- Ethereum Sepolia sequential rehearsal default, explicit Flashbots override,
  and guards preventing the sequential transport on mainnet.
- L2 raw submission and explicit refusal of unsafe fallback.
- Required initial-before-assets and final full-balance sweep behavior,
  rejecting-safe rollback, persistent delegation after outer revert, and
  receipt/nonce reconciliation.
- Protected direct-EOA bundle ordering, pre/post full sweeps, temporary clear,
  direct caller context, funding only for victim-originated actions, no
  permitted reverts, and final cleanup.
- Anvil integration for install/open, multiple calls, sweep, close, and clear.

## Acceptance Criteria

- A local compromised EOA with zero native balance can rescue all fixture asset
  types using only sponsor-paid transactions.
- Repeated calls work until session close.
- Mixed target failures do not block compatible assets.
- Every reported success has either an authoritative postcondition or an
  explicit unverified label.
