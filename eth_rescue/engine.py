import time
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from enum import IntEnum
from functools import partial

from eth_account.signers.local import LocalAccount
from web3.exceptions import TransactionNotFound
from web3.types import TxReceipt

from eth_rescue.bundle import (
    BUNDLE_BLOCK_RANGE,
    DEFAULT_GAS_MULTIPLIER,
    MAX_LEGACY_ACTIONS,
    ZERO_ADDRESS,
    BundlePreparer,
    delegation_target,
    prepare_7702_bundle,
    prepare_actions,
    prepare_bundle,
    size_actions,
)
from eth_rescue.checks import evaluate_checks, infer_checks, snapshot_checks
from eth_rescue.contract import ACTION_RESULT_TOPIC, require_rescue_contract
from eth_rescue.delegation import replay_delegates
from eth_rescue.relay import SECONDS_PER_BLOCK, RelayWeb3
from eth_rescue.report import Reporter
from eth_rescue.types import (
    RETRYABLE,
    ActionOutcome,
    CallResult,
    PreparedAction,
    PreparedBundle,
    RescueData,
    SimulationFailure,
    SimulationOutcome,
)

DEFAULT_MAX_BLOCKS = 25
DEFAULT_FUNDING_TIMEOUT = 30 * 60
# Stop waiting when the chain head has not moved for this long.
DEFAULT_HEAD_TIMEOUT = 5 * 60
HEAD_POLL_INTERVAL = 1.0
# Re-reads before deciding a half-visible bundle is genuinely ambiguous.
INCLUSION_READ_ATTEMPTS = 3
# Attempts at a chain read before an RPC error ends the run.
RPC_READ_ATTEMPTS = 5
# Blocks to keep looking for a bundle after the gas wallet's nonce moved.
LAGGING_RECEIPT_BLOCKS = 3
# Consecutive rebuilds (stale target, fee top-ups) allowed without a submission.
MAX_REBUILDS = 5
NOT_FUNDED = "Gas wallet was not funded in time."
# Extra tip that is almost certainly a typo (e.g. "2500" for "2.5").
HIGH_PRIORITY_FEE_GWEI = Decimal(50)


class ExitCode(IntEnum):
    OK = 0
    INVALID = 1
    SIMULATION_FAILED = 2
    UNFUNDED = 3
    NOT_INCLUDED = 4
    UNRESCUED = 5
    DECLINED = 6
    INTERRUPTED = 130


@dataclass
class RunProgress:
    """What the CLI needs to describe an interrupted or crashed run."""

    sent: bool = False


@dataclass(frozen=True)
class RescueContext:
    w3: RelayWeb3
    victim: LocalAccount
    gas: LocalAccount
    safe: str
    reporter: Reporter
    rescue_contract: str
    rescue_codehash: str | None = None
    network: str = ""
    extra_priority_fee_gwei: Decimal = Decimal(0)
    gas_multiplier: Decimal = DEFAULT_GAS_MULTIPLIER
    max_blocks: int = DEFAULT_MAX_BLOCKS
    funding_timeout: float = DEFAULT_FUNDING_TIMEOUT
    poll_interval: float = SECONDS_PER_BLOCK
    head_timeout: float = DEFAULT_HEAD_TIMEOUT
    head_poll_interval: float = HEAD_POLL_INTERVAL
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    progress: RunProgress = field(default_factory=RunProgress)

    def prepare(
        self, preparer: BundlePreparer, prepared: list[PreparedAction]
    ) -> PreparedBundle:
        return preparer(
            self.w3,
            self.victim,
            self.gas,
            prepared,
            self.safe,
            self.extra_priority_fee_gwei,
        )


@dataclass(frozen=True)
class Inclusion:
    bundle: PreparedBundle
    receipts: list[TxReceipt]
    # Plan call index of each of the bundle's actions, in bundle order.
    indices: tuple[int, ...] = ()

    @property
    def block(self) -> int:
        return int(self.receipts[0]["blockNumber"])


class InclusionUnknown(RuntimeError):
    """A submitted bundle may have landed, but the chain does not say so clearly."""


Confirm = Callable[[], bool]
# Each submitted bundle with the plan call index of each of its actions.
Submitted = list[tuple[PreparedBundle, tuple[int, ...]]]


def wait_for_funding(
    ctx: RescueContext,
    required: int,
    deadline: float | None = None,
    landed: Callable[[], bool] | None = None,
) -> bool:
    """Poll until the gas wallet holds `required`. False on timeout, or as soon
    as `landed` reports that waiting no longer matters."""
    if deadline is None:
        deadline = ctx.clock() + ctx.funding_timeout
    while True:
        if landed is not None and landed():
            return False
        try:
            balance = ctx.w3.eth.get_balance(ctx.gas.address)
        except Exception as e:
            ctx.reporter.message(
                "warning", f"Could not read the gas wallet balance; retrying: {e}"
            )
        else:
            ctx.reporter.funding(ctx.gas.address, required, balance)
            if balance >= required:
                return True
        if ctx.clock() >= deadline:
            return False
        ctx.sleep(ctx.poll_interval)


def _action_label(index: int) -> str:
    return f"action #{index + 1}"


def _failure_label(failure: SimulationFailure, indices: list[int] | None) -> str:
    if failure.action_index is not None:
        index = failure.action_index
        return _action_label(indices[index] if indices else index)
    return failure.role or "unknown transaction"


def _same_hash(reported: object, expected: bytes) -> bool:
    text = str(reported).lower().removeprefix("0x")
    return text == bytes(expected).hex()


def simulate_prepared_bundle(
    ctx: RescueContext, bundle: PreparedBundle, indices: list[int] | None = None
) -> SimulationOutcome:
    """Simulate the exact immutable bundle that may subsequently be submitted.
    `indices` maps the bundle's action positions back to plan call indices."""
    try:
        with ctx.reporter.waiting("Running Flashbots simulation..."):
            result = ctx.w3.relay.simulate(
                bundle.entries, block_tag=bundle.target_block
            )
    except Exception as e:
        failure = SimulationFailure(f"Bundle simulation failed: {e}")
        ctx.reporter.message("error", failure.message)
        return SimulationOutcome(False, bundle=bundle, failures=(failure,))

    tx_results = result.get("results") or []
    if len(tx_results) != len(bundle.transactions):
        failure = SimulationFailure(
            "Relay simulation response did not include every bundle transaction"
        )
        ctx.reporter.message("error", failure.message)
        return SimulationOutcome(False, bundle=bundle, failures=(failure,))
    for transaction, tx_result in zip(bundle.transactions, tx_results):
        reported = tx_result.get("txHash")
        if reported and not _same_hash(reported, transaction.hash):
            failure = SimulationFailure(
                "Relay simulated a different transaction than the one signed"
            )
            ctx.reporter.message("error", failure.message)
            return SimulationOutcome(False, bundle=bundle, failures=(failure,))

    ctx.reporter.simulation(result)
    failures = tuple(
        SimulationFailure(
            str(tx_result.get("error") or tx_result.get("revert")),
            role=bundle.transactions[index].role,
            action_index=bundle.transactions[index].action_index,
        )
        for index, tx_result in enumerate(tx_results)
        if tx_result.get("error") or tx_result.get("revert")
    )
    for failure in failures:
        ctx.reporter.message(
            "error",
            f"Simulation failed at {_failure_label(failure, indices)}: {failure.message}",
        )
    if failures:
        return SimulationOutcome(False, bundle=bundle, failures=failures)

    ctx.reporter.message("success", "Simulation succeeded.")
    return SimulationOutcome(True, bundle=bundle)


def _lookup(ctx: RescueContext, bundle: PreparedBundle) -> list[TxReceipt] | None:
    """The bundle's receipts when all of its transactions sit in one canonical
    block in bundle order; None when none is visible."""
    receipts: list[TxReceipt | None] = []
    for transaction in bundle.transactions:
        try:
            receipts.append(ctx.w3.eth.get_transaction_receipt(transaction.hash))
        except TransactionNotFound:
            receipts.append(None)
    found = [receipt for receipt in receipts if receipt is not None]
    if not found:
        return None
    if len(found) != len(receipts):
        raise InclusionUnknown("only some of the bundle's transactions are on-chain")
    block_hashes = {bytes(receipt["blockHash"]) for receipt in found}
    if len(block_hashes) != 1:
        raise InclusionUnknown("the bundle's transactions are in different blocks")
    positions = [receipt["transactionIndex"] for receipt in found]
    if positions != sorted(set(positions)):
        raise InclusionUnknown("the bundle's transactions are out of order")
    block = ctx.w3.eth.get_block(found[0]["blockNumber"])
    if bytes(block["hash"]) not in block_hashes:
        raise InclusionUnknown("the bundle's block is no longer canonical")
    return found


def _scan(ctx: RescueContext, submitted: Submitted) -> Inclusion | None:
    """The submitted bundle that landed in full. Rebuilds can share signed
    transactions, so a bundle that is only partly visible is ambiguous only
    when no other bundle landed in full."""
    unknown: InclusionUnknown | None = None
    for bundle, indices in submitted:
        try:
            receipts = _lookup(ctx, bundle)
        except InclusionUnknown as e:
            unknown = unknown or e
            continue
        if receipts is not None:
            return Inclusion(bundle, receipts, indices)
    if unknown is not None:
        raise unknown
    return None


def _find_inclusion(ctx: RescueContext, submitted: Submitted) -> Inclusion | None:
    """Look for any submitted bundle on-chain, re-reading inconsistent results
    and RPC errors a few times (load-balanced RPCs can answer from nodes at
    different heights, and public endpoints drop requests)."""
    ambiguous = failed = 0
    while True:
        try:
            inclusion = _scan(ctx, submitted)
        except InclusionUnknown:
            ambiguous += 1
            if ambiguous >= INCLUSION_READ_ATTEMPTS:
                raise
        except Exception:
            failed += 1
            if failed >= RPC_READ_ATTEMPTS:
                raise
        else:
            if inclusion is not None:
                ctx.reporter.included(inclusion.receipts)
            return inclusion
        ctx.sleep(ctx.head_poll_interval)


def _head(ctx: RescueContext) -> int | None:
    try:
        return ctx.w3.eth.block_number
    except Exception:
        return None


def _block_number(ctx: RescueContext) -> int:
    for _attempt in range(RPC_READ_ATTEMPTS - 1):
        head = _head(ctx)
        if head is not None:
            return head
        ctx.sleep(ctx.head_poll_interval)
    return ctx.w3.eth.block_number


def _wait_for_block(ctx: RescueContext, number: int) -> bool:
    """Wait until the head reaches `number`; False if it stops advancing. A
    failed read counts as no progress, so only a lasting outage stops it."""
    last = _head(ctx)
    moved_at = ctx.clock()
    while last is None or last < number:
        if ctx.clock() - moved_at >= ctx.head_timeout:
            return False
        ctx.sleep(ctx.head_poll_interval)
        current = _head(ctx)
        if current is not None and (last is None or current > last):
            last, moved_at = current, ctx.clock()
    return True


def _sentence(text: object) -> str:
    text = str(text).strip().rstrip(".")
    return f"{text[:1].upper()}{text[1:]}."


def _unknown(ctx: RescueContext, reason: object) -> ExitCode:
    ctx.reporter.message(
        "error",
        f"{_sentence(reason)} Cannot tell whether the rescue landed, so nothing is "
        "resubmitted (it could be sent twice). Check the gas wallet's latest "
        "transactions on a block explorer before running again.",
    )
    return ExitCode.NOT_INCLUDED


@dataclass(frozen=True)
class Stop:
    """Why the send loop ended without seeing a bundle land. `code` is the
    outcome only when nothing was sent; None then means every action was left
    out. `await_receipts` is False when waiting for more blocks is pointless."""

    reason: str
    code: ExitCode | None = ExitCode.NOT_INCLUDED
    await_receipts: bool = True


def _settle(
    ctx: RescueContext, submitted: Submitted, stop: Stop
) -> Inclusion | ExitCode | None:
    """Once anything was submitted, every stop means the same thing: a bundle
    may still land, so look for late receipts, and without one the outcome is
    unknown rather than "nothing was sent"."""
    if not submitted:
        if stop.code is not None:
            ctx.reporter.message("error", f"{stop.reason} Nothing was sent.")
        return stop.code
    if stop.await_receipts:
        inclusion = _await_lagging_inclusion(ctx, submitted)
        if inclusion is not None:
            return inclusion
    return _unknown(ctx, stop.reason)


def _droppable(simulation: SimulationOutcome, active: list[int]) -> list[int]:
    """Plan call indices whose own transactions revert; empty when anything
    else (funding, undelegation, the relay itself) failed too."""
    if any(f.role != "rescue" or f.action_index is None for f in simulation.failures):
        return []
    return sorted({active[f.action_index] for f in simulation.failures})


def _leave_out(ctx: RescueContext, failing: list[int]) -> None:
    for index in failing:
        ctx.reporter.message(
            "warning",
            f"Leaving out action #{index + 1}: it reverts as a direct transaction.",
        )


def _left_out_outcomes(indices: list[int]) -> list[ActionOutcome]:
    return [
        ActionOutcome(index, "failed", "reverted in the fallback simulation")
        for index in indices
    ]


def _submit(ctx: RescueContext, bundle: PreparedBundle, block_count: int) -> None:
    """Send the bundle for each target block. A failed send may still have
    reached the relay, so the caller watches anyway."""
    ctx.progress.sent = True
    ctx.reporter.submitting(bundle.target_block, bundle.target_block + block_count - 1)
    for offset in range(block_count):
        target = bundle.target_block + offset
        try:
            ctx.w3.relay.send_bundle(bundle.entries, target_block_number=target)
        except Exception as e:
            ctx.reporter.message(
                "warning",
                f"The relay did not confirm the bundle for block {target}: {e}",
            )


def _stale(ctx: RescueContext, bundle: PreparedBundle) -> bool:
    return _block_number(ctx) >= bundle.target_block


def _await_lagging_inclusion(
    ctx: RescueContext, submitted: Submitted
) -> Inclusion | None:
    target = _block_number(ctx) + LAGGING_RECEIPT_BLOCKS
    while True:
        inclusion = _find_inclusion(ctx, submitted)
        if inclusion is not None:
            return inclusion
        head = _block_number(ctx)
        if head >= target:
            return None
        if not _wait_for_block(ctx, head + 1):
            return None


def _watch_window(
    ctx: RescueContext, submitted: Submitted, first: int, count: int
) -> Inclusion | Stop | None:
    for target in range(first, first + count):
        if not _wait_for_block(ctx, target):
            inclusion = _find_inclusion(ctx, submitted)
            if inclusion is not None:
                return inclusion
            return Stop(
                f"The chain head stopped advancing before block {target}; check "
                "the RPC.",
                await_receipts=False,
            )
        inclusion = _find_inclusion(ctx, submitted)
        if inclusion is not None:
            return inclusion
    return None


def send_with_retry(
    ctx: RescueContext,
    prepared: list[PreparedAction],
    preparer: BundlePreparer,
    indices: list[int] | None = None,
    *,
    drop_failing: bool = False,
) -> Inclusion | ExitCode | None:
    """Rebuild, re-simulate and resubmit for each block window until included.

    Only blocks actually submitted count toward `max_blocks`. Every earlier
    bundle is checked before a new one is built, and if the gas wallet's nonce
    moved without a visible bundle, nothing is resubmitted. With
    `drop_failing`, actions that start reverting in a later window are left out
    of the next bundle; None means every action was left out before any bundle
    landed.
    """
    active = list(range(len(prepared)) if indices is None else indices)
    submitted: Submitted = []
    try:
        outcome = _send_windows(
            ctx, dict(zip(active, prepared)), active, preparer, submitted, drop_failing
        )
        if isinstance(outcome, Stop):
            return _settle(ctx, submitted, outcome)
        return outcome
    except InclusionUnknown as e:
        return _unknown(ctx, e)
    except Exception as e:  # an RPC outage that outlasted every retry
        if submitted:
            return _unknown(ctx, f"RPC error: {e}")
        ctx.reporter.message("error", f"RPC error: {e}. Nothing was sent.")
        return ExitCode.INVALID


def _send_windows(
    ctx: RescueContext,
    by_index: dict[int, PreparedAction],
    active: list[int],
    preparer: BundlePreparer,
    submitted: Submitted,
    drop_failing: bool,
) -> Inclusion | Stop:
    blocks_sent = 0
    rebuilds = 0
    while blocks_sent < ctx.max_blocks:
        inclusion = _find_inclusion(ctx, submitted)
        if inclusion is not None:
            return inclusion
        if rebuilds >= MAX_REBUILDS:
            return Stop(f"Could not get a bundle ready after {rebuilds} rebuilds.")
        try:
            bundle = ctx.prepare(preparer, [by_index[i] for i in active])
            gas_balance = ctx.w3.eth.get_balance(ctx.gas.address)
        except Exception as e:
            ctx.reporter.message(
                "warning", f"Could not prepare the next bundle; retrying: {e}"
            )
            rebuilds += 1
            ctx.sleep(ctx.head_poll_interval)
            continue

        if submitted and bundle.gas_nonce != submitted[0][0].gas_nonce:
            return Stop(
                "The gas wallet sent a transaction that is not one of the bundles."
            )

        if gas_balance < bundle.required_funding:
            ctx.reporter.message(
                "warning", "Gas wallet balance dropped below requirement (fees rose)."
            )
            # An earlier bundle can still land while the wallet waits for a top-up.
            landed = (
                (lambda: _find_inclusion(ctx, submitted) is not None)
                if submitted
                else None
            )
            if not wait_for_funding(ctx, bundle.required_funding, landed=landed):
                return Stop(NOT_FUNDED, ExitCode.UNFUNDED)
            rebuilds += 1
            continue

        if not _stale(ctx, bundle):
            simulation = simulate_prepared_bundle(ctx, bundle, active)
            if not simulation and not _stale(ctx, bundle):
                # The rebuilt bundle reuses the gas wallet's nonce, so a clean
                # funding transaction means no earlier bundle has landed.
                failing = _droppable(simulation, active) if drop_failing else []
                if not failing:
                    return Stop(
                        "Submission blocked: the bundle no longer simulates cleanly.",
                        ExitCode.SIMULATION_FAILED,
                    )
                _leave_out(ctx, failing)
                active = [index for index in active if index not in failing]
                if not active:
                    return Stop("Every remaining action now reverts.", code=None)
                continue
        if _stale(ctx, bundle):
            ctx.reporter.message(
                "warning",
                f"Target block {bundle.target_block} arrived before submission; "
                "rebuilding for a later block.",
            )
            rebuilds += 1
            continue

        rebuilds = 0
        block_count = min(BUNDLE_BLOCK_RANGE, ctx.max_blocks - blocks_sent)
        _submit(ctx, bundle, block_count)
        submitted.append((bundle, tuple(active)))
        blocks_sent += block_count
        outcome = _watch_window(ctx, submitted, bundle.target_block, block_count)
        if outcome is not None:
            return outcome

    return Stop(f"Not included after {ctx.max_blocks} blocks.")


def _report_predictions(
    ctx: RescueContext, bundle: PreparedBundle, indices: list[int]
) -> None:
    for position, result in enumerate(bundle.predicted):
        if result is False:
            ctx.reporter.message(
                "warning",
                f"Action #{indices[position] + 1} reverts in a dry run of the rescue "
                "call, so it is expected to fail.",
            )


def _fund_and_simulate(
    ctx: RescueContext,
    prepared: list[PreparedAction],
    preparer: BundlePreparer,
    indices: list[int],
) -> SimulationOutcome | ExitCode:
    """Build the bundle, wait until the gas wallet covers it (rebuilding with
    current fees after each top-up), then simulate it."""
    deadline = ctx.clock() + ctx.funding_timeout
    for _attempt in range(MAX_REBUILDS):
        try:
            bundle = ctx.prepare(preparer, prepared)
            balance = ctx.w3.eth.get_balance(ctx.gas.address)
        except Exception as e:
            ctx.reporter.message("error", f"Could not prepare bundle: {e}")
            return ExitCode.SIMULATION_FAILED
        if balance < bundle.required_funding:
            if not wait_for_funding(ctx, bundle.required_funding, deadline):
                ctx.reporter.message("error", f"{NOT_FUNDED} Nothing was sent.")
                return ExitCode.UNFUNDED
            continue
        ctx.reporter.funding(ctx.gas.address, bundle.required_funding, balance)
        ctx.reporter.bundle(bundle)
        for warning in bundle.warnings:
            ctx.reporter.message("warning", warning)
        _report_predictions(ctx, bundle, indices)
        simulation = simulate_prepared_bundle(ctx, bundle, indices)
        if simulation or not _stale(ctx, bundle):
            return simulation
        ctx.reporter.message(
            "warning", "The target block arrived during simulation; rebuilding."
        )
    ctx.reporter.message(
        "error", "Could not build a funded, current bundle; run again."
    )
    return ExitCode.SIMULATION_FAILED


def execution_results(
    inclusion: Inclusion,
    victim: str,
    indices: list[int],
    *,
    ran: dict[int, bool | None] | None = None,
) -> dict[int, CallResult]:
    """Plan call index -> its result.

    `ran` says, per rescue-7702 transaction (by bundle position), whether
    EthRescue ran it. False means its authorization was skipped and other code
    ran our calldata, so its logs prove nothing and none of its calls ran; None
    means that could not be established. Positions left out count as EthRescue,
    whose missing ActionResult log means the call never ran (its batch reverted).
    """
    results: dict[int, CallResult] = dict.fromkeys(indices)
    victim = victim.lower()
    ran = ran or {}
    for position, (transaction, receipt) in enumerate(
        zip(inclusion.bundle.transactions, inclusion.receipts)
    ):
        succeeded = receipt["status"] == 1
        if transaction.role == "rescue" and transaction.action_index is not None:
            results[indices[transaction.action_index]] = succeeded
        if transaction.role != "rescue-7702" or not succeeded:
            continue
        start, size = transaction.batch_start, transaction.batch_size
        ethrescue = ran.get(position, True)
        if ethrescue is None:
            for call in range(start, start + size):
                results[indices[call]] = "unknown"
            continue
        if not ethrescue:
            continue
        for log in receipt["logs"]:
            topics = log["topics"]
            if (
                str(log["address"]).lower() != victim
                or len(topics) < 2
                or bytes(topics[0]) != ACTION_RESULT_TOPIC
            ):
                continue
            offset = int.from_bytes(bytes(topics[1]))
            if offset < size:
                results[indices[start + offset]] = (
                    int.from_bytes(bytes(log["data"])) == 1
                )
    return results


class BlockMismatch(RuntimeError):
    """The block an RPC returned does not hold the rescue receipts."""


class IncompleteBlock(RuntimeError):
    """The RPC left out the authorization lists the replay depends on."""


def _replay_rescue(
    ctx: RescueContext, inclusion: Inclusion, positions: list[int]
) -> dict[int, bool]:
    receipts = {position: inclusion.receipts[position] for position in positions}
    block = ctx.w3.eth.get_block(
        receipts[positions[0]]["blockHash"], full_transactions=True
    )
    transactions = block["transactions"]
    indices = {p: int(receipt["transactionIndex"]) for p, receipt in receipts.items()}
    for position, index in indices.items():
        if index >= len(transactions) or bytes(transactions[index]["hash"]) != bytes(
            receipts[position]["transactionHash"]
        ):
            raise BlockMismatch(f"block {inclusion.block} does not match its receipts")
    # The first rescue transaction always carries the victim's authorization;
    # without it the replay would see no delegation change at all.
    if not transactions[indices[positions[0]]].get("authorizationList"):
        raise IncompleteBlock(
            f"the RPC returned block {inclusion.block} without authorization lists"
        )
    parent = int(block["number"]) - 1
    victim = ctx.victim.address
    delegates = replay_delegates(
        transactions[: max(indices.values()) + 1],
        victim,
        ctx.w3.eth.chain_id,
        nonce=ctx.w3.eth.get_transaction_count(victim, parent),
        delegate=delegation_target(ctx.w3, victim, parent),
    )
    rescue = ctx.rescue_contract.lower()
    return {
        position: (delegates[index] or "").lower() == rescue
        for position, index in indices.items()
    }


def _rescue_ran(ctx: RescueContext, inclusion: Inclusion) -> dict[int, bool | None]:
    """Whether EthRescue ran each rescue-7702 transaction, by bundle position.

    ActionResult logs come from whatever code the compromised wallet runs, so
    they count only where EthRescue was its delegate. That is replayed from the
    block itself: the wallet's nonce and delegation before the block, then every
    transaction up to each rescue, so an attacker's re-delegation later in the
    block changes nothing and one earlier in the block (or between batches) is
    seen. None where the block could not be read.
    """
    positions = [
        position
        for position, transaction in enumerate(inclusion.bundle.transactions)
        if transaction.role == "rescue-7702"
    ]
    if not positions:
        return {}
    for attempt in range(RPC_READ_ATTEMPTS):
        try:
            ran = _replay_rescue(ctx, inclusion, positions)
            break
        except Exception as e:  # RPC errors, a node on another fork
            error = e
            if attempt + 1 < RPC_READ_ATTEMPTS:
                ctx.sleep(ctx.head_poll_interval)
    else:
        ctx.reporter.message(
            "warning",
            f"Could not replay block {inclusion.block} to confirm EthRescue ran the "
            f"rescue ({error}). Typed actions are judged by their on-chain checks; "
            "other calls are not retried, since they may have run.",
        )
        return dict.fromkeys(positions)
    if not all(ran.values()):
        ctx.reporter.message(
            "warning",
            f"In block {inclusion.block} the compromised wallet was not delegated to "
            "EthRescue when the rescue ran: its authorization was skipped, so the "
            "results its code reported are ignored.",
        )
    return ran


def sized_actions(
    ctx: RescueContext, rescue_data: list[RescueData]
) -> list[PreparedAction]:
    """Encode actions and raise their gas limits to live estimates."""
    floors = prepare_actions(rescue_data)
    prepared, failures = size_actions(
        ctx.w3, ctx.victim.address, floors, ctx.gas_multiplier
    )
    for index, (floor, action) in enumerate(zip(floors, prepared)):
        if index in failures:
            ctx.reporter.message(
                "warning",
                f"Action #{index + 1} would revert if sent now on its own; keeping its "
                f"{floor['gas']:,} gas limit: {failures[index]}",
            )
        elif action["gas"] > floor["gas"]:
            ctx.reporter.message(
                "info",
                f"Action #{index + 1} gas limit raised from {floor['gas']:,} "
                f"to {action['gas']:,} from the live estimate.",
            )
    return prepared


def _execute_phase(
    ctx: RescueContext,
    rescue_data: list[RescueData],
    prepared: list[PreparedAction],
    indices: list[int],
    preparer: BundlePreparer,
    confirm: Confirm | None,
    *,
    drop_failing: bool = False,
) -> list[ActionOutcome] | ExitCode:
    """Run the plan calls in `indices` through one bundle kind. With
    `drop_failing`, calls that revert in simulation are left out (recorded as
    failed) so one unrecoverable asset cannot block the rest."""
    active = list(indices)
    dropped: list[int] = []
    while True:
        simulation = _fund_and_simulate(
            ctx, [prepared[i] for i in active], preparer, active
        )
        if isinstance(simulation, ExitCode):
            return simulation
        if simulation:
            break
        failing = _droppable(simulation, active) if drop_failing else []
        if not failing:
            ctx.reporter.message("error", "Nothing was sent.")
            return ExitCode.SIMULATION_FAILED
        _leave_out(ctx, failing)
        dropped += failing
        active = [index for index in active if index not in failing]
        if not active:
            outcomes = _left_out_outcomes(dropped)
            ctx.reporter.outcomes(rescue_data, outcomes)
            return outcomes

    if confirm is not None and not confirm():
        ctx.reporter.message("warning", "Aborted. Nothing was sent.")
        return ExitCode.DECLINED

    checks = infer_checks(rescue_data)
    active_checks = {index: checks[index] for index in active if index in checks}
    snapshot = snapshot_checks(ctx.w3, active_checks, sleep=ctx.sleep)
    inclusion = send_with_retry(
        ctx,
        [prepared[i] for i in active],
        preparer,
        active,
        drop_failing=drop_failing,
    )
    if isinstance(inclusion, ExitCode):
        return inclusion

    landed = inclusion.indices if inclusion is not None else ()
    dropped += [index for index in active if index not in landed]
    outcomes: list[ActionOutcome] = []
    if inclusion is not None:
        executed = execution_results(
            inclusion,
            ctx.victim.address,
            list(landed),
            ran=_rescue_ran(ctx, inclusion),
        )
        outcomes = evaluate_checks(
            ctx.w3, active_checks, snapshot, executed, inclusion.block, sleep=ctx.sleep
        )
    outcomes = _finished_steps(
        rescue_data,
        sorted(
            outcomes + _left_out_outcomes(dropped), key=lambda outcome: outcome.index
        ),
    )
    ctx.reporter.outcomes(rescue_data, outcomes)
    return outcomes


def _check_roles(ctx: RescueContext, rescue_data: list[RescueData]) -> None:
    """The singleton's own address rules, checked before anything is signed."""
    victim, gas, safe, rescue = (
        a.lower()
        for a in (ctx.victim.address, ctx.gas.address, ctx.safe, ctx.rescue_contract)
    )
    if safe == ZERO_ADDRESS or safe in (victim, gas):
        raise ValueError(
            "The safe must be a non-zero address that differs from the compromised "
            "and gas wallets"
        )
    if safe == rescue:
        raise ValueError(
            "The safe must not be the EthRescue contract: ETH sent there is lost"
        )
    for index, data in enumerate(rescue_data):
        if data["address"].lower() in (victim, ZERO_ADDRESS):
            raise ValueError(
                f"Action #{index + 1} targets the compromised wallet or the zero "
                "address, which EthRescue rejects"
            )


def _preflight(
    ctx: RescueContext, rescue_data: list[RescueData], legacy_fallback: bool = True
) -> bool:
    try:
        require_rescue_contract(ctx.w3, ctx.rescue_contract, ctx.rescue_codehash)
        prepare_actions(rescue_data)
        _check_roles(ctx, rescue_data)
        delegate = delegation_target(ctx.w3, ctx.victim.address)
        if ctx.w3.eth.get_code(ctx.gas.address):
            raise ValueError(
                "The gas wallet has code (a smart account or EIP-7702 delegation). "
                "Use a fresh wallet from `eth-rescue gas-wallet new`."
            )
    except ValueError as e:
        ctx.reporter.message("error", str(e))
        return False
    if delegate is not None and delegate.lower() != ctx.rescue_contract.lower():
        ctx.reporter.message(
            "warning",
            f"The compromised wallet is delegated to {delegate}, likely by the "
            "attacker. The rescue replaces that delegation.",
        )
    if legacy_fallback and len(rescue_data) > MAX_LEGACY_ACTIONS:
        ctx.reporter.message(
            "warning",
            "The legacy fallback fits at most "
            f"{MAX_LEGACY_ACTIONS} actions in one bundle; if more fail, "
            "it stops and you can rerun with a smaller plan.",
        )
    return True


def _describe(
    ctx: RescueContext, rescue_data: list[RescueData], legacy_fallback: bool
) -> None:
    """Everything the confirmation covers, stated before funding or confirming."""
    say = ctx.reporter.message
    say("info", f"Network: {ctx.network or f'chain {ctx.w3.eth.chain_id}'}")
    say("info", f"Compromised wallet: {ctx.victim.address}")
    say(
        "info",
        f"Safe: {ctx.safe}. It receives every typed asset and all ETH left in the "
        "compromised wallet.",
    )
    say("info", f"Gas wallet: {ctx.gas.address}. It pays every fee.")
    if ctx.extra_priority_fee_gwei > HIGH_PRIORITY_FEE_GWEI:
        say(
            "warning",
            f"The plan adds {ctx.extra_priority_fee_gwei} gwei of priority fee to "
            "every gas unit, and the gas wallet must cover it. Check "
            "options.priority_fee_gwei for a typo before funding.",
        )
    for index, data in enumerate(rescue_data):
        if data.get("custom"):
            say(
                "warning",
                f"Action #{index + 1} is a custom call: {data['address']}."
                f"{data['function_signature']} with args {data['args']}. Its "
                "arguments, not the safe setting, decide where anything goes.",
            )
    if legacy_fallback:
        say(
            "info",
            "Legacy fallback is on: actions with no effect in phase 1 are retried as "
            "direct transactions from the compromised wallet, without asking again. "
            "Unverified and partially delivered actions are never retried.",
        )
    else:
        say("info", "Legacy fallback is off: failed actions are reported, not retried.")


def _report_final_state(ctx: RescueContext) -> None:
    try:
        balance = ctx.w3.eth.get_balance(ctx.victim.address)
        delegation = delegation_target(ctx.w3, ctx.victim.address)
    except Exception as e:
        ctx.reporter.message("warning", f"Could not read the final wallet state: {e}")
        return
    ctx.reporter.final_state(balance, delegation)


def _finished_steps(
    rescue_data: list[RescueData], outcomes: list[ActionOutcome]
) -> list[ActionOutcome]:
    """A multi-call plan action (a delist, then its transfer) is done once its
    final step is rescued. Its earlier steps then need no follow-up whatever
    they did on their own, so they count as rescued with it."""
    final_step: dict[int, int] = {}
    for index, data in enumerate(rescue_data):
        if (action := data.get("plan_action")) is not None:
            final_step[action] = index
    rescued = {outcome.index for outcome in outcomes if outcome.status == "rescued"}

    def finish(outcome: ActionOutcome) -> ActionOutcome:
        action = rescue_data[outcome.index].get("plan_action")
        final = final_step.get(action) if action is not None else None
        if final in (None, outcome.index) or final not in rescued:
            return outcome
        if outcome.status == "rescued":
            return outcome
        return ActionOutcome(
            outcome.index,
            "rescued",
            "the step after it was rescued; on its own: "
            f"{outcome.detail or outcome.status}",
        )

    return [finish(outcome) for outcome in outcomes]


def _finish(
    ctx: RescueContext, rescue_data: list[RescueData], outcomes: list[ActionOutcome]
) -> ExitCode:
    _report_final_state(ctx)
    checks = infer_checks(rescue_data)
    verified = all(
        outcome.status == "rescued"
        or (outcome.status == "unverified" and outcome.index not in checks)
        for outcome in outcomes
    )
    if not verified:
        ctx.reporter.message("error", "Some actions were not rescued.")
        return ExitCode.UNRESCUED
    manual = sum(outcome.status == "unverified" for outcome in outcomes)
    if manual:
        ctx.reporter.message(
            "success",
            f"Rescue complete. Confirm the {manual} custom action(s) manually.",
        )
    else:
        ctx.reporter.message("success", "Rescue complete.")
    return ExitCode.OK


def _rescue_7702(ctx: RescueContext) -> BundlePreparer:
    return partial(prepare_7702_bundle, rescue_contract=ctx.rescue_contract)


def simulate(
    ctx: RescueContext, rescue_data: list[RescueData], legacy_fallback: bool = True
) -> ExitCode:
    """Fund-check and simulate the phase-1 bundle without submitting it."""
    if not _preflight(ctx, rescue_data, legacy_fallback):
        return ExitCode.INVALID
    ctx.reporter.plan(rescue_data)
    _describe(ctx, rescue_data, legacy_fallback)
    indices = list(range(len(rescue_data)))
    simulation = _fund_and_simulate(
        ctx, sized_actions(ctx, rescue_data), _rescue_7702(ctx), indices
    )
    if isinstance(simulation, ExitCode):
        return simulation
    if not simulation:
        return ExitCode.SIMULATION_FAILED
    predicted = simulation.bundle.predicted
    failing = [index for index, result in enumerate(predicted) if result is False]
    if failing:
        retry_note = (
            " `run` would retry them in the legacy fallback, which this command "
            "does not simulate."
            if legacy_fallback
            else ""
        )
        ctx.reporter.message(
            "error", f"{len(failing)} action(s) revert in the dry run.{retry_note}"
        )
        return ExitCode.SIMULATION_FAILED
    if None in predicted:
        ctx.reporter.message(
            "warning",
            "Per-action results were unavailable from this RPC; only the bundle as "
            "a whole was simulated.",
        )
    return ExitCode.OK


def run(
    ctx: RescueContext,
    rescue_data: list[RescueData],
    *,
    confirm: Confirm,
    legacy_fallback: bool = True,
) -> ExitCode:
    if not _preflight(ctx, rescue_data, legacy_fallback):
        return ExitCode.INVALID
    ctx.reporter.plan(rescue_data)
    _describe(ctx, rescue_data, legacy_fallback)
    # Size once, before any delegation changes: these estimates also describe the
    # undelegated victim that the legacy fallback executes as.
    prepared = sized_actions(ctx, rescue_data)
    everything = list(range(len(rescue_data)))

    ctx.reporter.phase("Phase 1: EIP-7702 rescue")
    phase_1 = _execute_phase(
        ctx, rescue_data, prepared, everything, _rescue_7702(ctx), confirm
    )
    if isinstance(phase_1, ExitCode):
        return phase_1
    outcomes = {outcome.index: outcome for outcome in phase_1}
    retry = [index for index in everything if outcomes[index].status in RETRYABLE]

    if retry and not legacy_fallback:
        ctx.reporter.message(
            "warning", f"{len(retry)} action(s) failed; legacy fallback is disabled."
        )
    elif retry:
        ctx.reporter.phase("Phase 2: legacy fallback")
        ctx.reporter.message(
            "info",
            f"Retrying {len(retry)} action(s) with no effect as direct victim "
            "transactions: " + ", ".join(f"#{index + 1}" for index in retry),
        )
        phase_2 = _execute_phase(
            ctx, rescue_data, prepared, retry, prepare_bundle, None, drop_failing=True
        )
        if isinstance(phase_2, ExitCode):
            ctx.reporter.message("error", "The legacy fallback did not complete.")
            code = _finish(ctx, rescue_data, [outcomes[i] for i in everything])
            # Phase 1 already landed, so "nothing sent" (2) no longer applies; an
            # unknown inclusion (4) or missing funds (3) still needs the user's action.
            if phase_2 in (ExitCode.NOT_INCLUDED, ExitCode.UNFUNDED):
                return phase_2
            return code
        outcomes |= {outcome.index: outcome for outcome in phase_2}
        merged = _finished_steps(rescue_data, [outcomes[i] for i in everything])
        ctx.reporter.outcomes(rescue_data, merged)
        return _finish(ctx, rescue_data, merged)

    return _finish(ctx, rescue_data, [outcomes[i] for i in everything])
