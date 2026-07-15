# Phase 2: Rescue Executor Contract

## Objective

Implement the security-critical contract used as an EIP-7702 delegate. It must
let a clean sponsor operate a short-lived rescue session from the victim's
address, attempt arbitrary zero-value rescue calls repeatedly, sweep native
currency to one bound safe, and report each result without allowing an
individual asset failure to revert the batch.

## Prerequisites

- Phase 1 domain concepts are stable.
- Foundry and Anvil remain development/test dependencies only.

## Contract Project

Create a production contract directory separate from test fixtures, with a
Foundry configuration, source, tests, deployment script, compiler lock, and
generated ABI consumed by Python. Do not make the Python package require
Foundry at runtime.

Avoid a new Solidity library dependency for the first implementation. Implement
only the required EIP-712 digest and strict ECDSA recovery, including 65-byte
length, valid `v`, low-`s`, nonzero recovery, and recovered victim checks.

## Contract Interface

Define these conceptual values in the ABI; exact Solidity integer widths may be
optimized without changing their meaning:

```solidity
enum ResultPolicy { CallSuccess, OptionalBool }

struct SessionConfig {
    address sponsor;
    address safe;
    uint256 nonce;
    uint48 deadline;
}

struct Action {
    address target;
    uint256 gasLimit;
    ResultPolicy resultPolicy;
    bytes data;
}
```

Expose:

- `openSessionAndExecute(config, victimSignature, actions, closeAfter)` for the
  initial type-4 transaction;
- `execute(actions)` for later sponsor-paid best-effort asset batches;
- `executeAndClose(actions)` for a final best-effort asset batch followed by a
  required native sweep and close;
- `sweepAndClose()` for a required final native sweep when no asset batch
  remains;
- read-only session state needed for simulation, nonce selection, and support.

All functions execute in the EIP-7702 authority account's context, so
`address(this)` is the victim and storage belongs to the victim.

EIP-7702 installs a pointer to already deployed code and runs no constructor or
initcode. The authorization list is processed before the outer transaction
calls the victim, so the first sponsored type-4 call installs the delegation,
authenticates the session, sweeps native currency, and attempts its first asset
batch atomically.

## Session Authentication

- The EIP-712 domain binds chain ID and `address(this)`.
- The signed session binds sponsor, safe, session nonce, deadline, chain ID,
  victim, contract name, and version.
- `msg.sender` must equal the signed sponsor for open, execute, and close.
- The nonce must equal the next namespaced session nonce and increments on open.
- A session cannot be reopened from an old signature.
- An expired, closed, unauthorized, malformed, or replayed session reverts.
- A valid new session overwrites prior active session fields so a legitimate
  rescue can replace stale executor state.

Use an ERC-7201-style namespaced storage root and a reentrancy flag in that
namespace. Preserve used/nonces after close.

The sponsor is deliberately a session key: while the session is active it can
choose arbitrary zero-value calldata. The native sweep destination remains
hard-bound to the safe. The CLI must use a distinct, clean gas wallet.

## Best-Effort Execution

- Validate all action shapes before beginning execution.
- Reject zero targets, calls to the executor/victim itself, empty gas limits,
  and gas limits above a documented cap.
- Forward at most the action gas limit while reserving enough outer gas to
  record the outcome and continue.
- Do not let a target consume all remaining gas.
- Capture at most 256 bytes of return or revert data and record the original
  return-data length. Do not copy or hash unbounded return data.
- `CallSuccess` succeeds when the EVM call succeeds.
- `OptionalBool` succeeds when the call succeeds and returns either no data or
  ABI boolean `true`; malformed or `false` results fail semantically.
- A failed action emits and returns a failure, then execution continues.
- Asset execution is protected by the session reentrancy guard.

After authenticating the session and validating every action shape,
`openSessionAndExecute` performs a required initial sweep before any asset
call. It sends the entire current victim balance to the bound safe, reserves
enough gas to finish accounting, and reverts if the transfer fails or the
victim balance is nonzero at sweep completion. A zero balance is a successful
zero-amount sweep.

When `closeAfter` is true, the same initial transaction sweeps again after the
best-effort actions and closes the session. Otherwise later `execute` calls may
continue the session, and `executeAndClose` or `sweepAndClose` performs the
required final sweep before closing. This catches native currency received or
force-sent after the initial sweep. A successful receipt proves zero balance at
that sweep's completion, not that value cannot be force-sent later.

The EIP-7702 delegation and authorization nonce are not rolled back when outer
execution reverts. Session storage, sweep transfers, and asset calls are rolled
back normally. The signed initializer must therefore remain safe to call after
a failed initial execution, and the client must reconcile delegation and nonce
state before retrying.

## Events and Results

Emit session-opened and session-closed events plus one event per action and a
`NativeSwept` event containing session nonce, initial/final stage, and amount.
Action results include session nonce, batch nonce, index, target,
selector, EVM success, semantic success, return-data length, and bounded data.

Return equivalent result structs so `eth_call` can provide the same diagnostic
surface before submission. A successful outer receipt is never documented as
proof that all result entries succeeded.

## Security Tests

- Valid sponsored open, initial sweep, repeated execute, final sweep, close,
  and reopen.
- Wrong sponsor, victim, safe, chain, nonce, deadline, signature length, `v`,
  high-`s`, and replay all revert.
- Existing malicious or unrelated delegated storage cannot bypass open checks.
- Reentrant ERC-20/NFT/native receivers cannot reenter execute or close.
- A gas-burning target cannot suppress later actions or result events.
- A return-data bomb is truncated without exhausting outer gas.
- Reverting, false-returning, no-return, fee-on-transfer, and normal ERC-20s
  produce correct semantic outcomes.
- ERC-721/1155 and ownership calls originate from the victim address.
- The initial sweep occurs before the first target call and uses no victim gas.
- One failed action does not block later actions or the required closing sweep.
- A rejecting safe reverts the containing outer transaction; no rolled-back
  asset action is reported as rescued.
- An initial-call revert leaves the delegation and authorization nonce changed
  but rolls back session storage, asset calls, and sweep value.
- A single-batch call sweeps before and after its actions and closes; a
  multi-batch session sweeps initially and again on its final call.
- Native currency received after the initial sweep is cleared by the final
  sweep. Both successful stages verify zero balance and emit `NativeSwept`.
- Fuzz action counts, gas caps, return sizes, signatures, deadlines, and nonces.
- Invariants: only sponsor operates an active session; native value can only be
  swept to the safe; the session nonce never decreases; closed sessions cannot
  execute.

## Acceptance Criteria

- Foundry formatting, build, unit, fuzz, and invariant tests pass.
- ABI and deployed runtime code hash are reproducible.
- The contract has no owner, upgrade mechanism, withdrawal authority, or
  dependency on an offchain service.
- No individual rescue-call failure can falsely turn into an unreported batch
  success.
- Mainnet deployment remains disabled until Phase 7 review is complete.
