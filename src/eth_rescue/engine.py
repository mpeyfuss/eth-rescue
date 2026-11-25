import time
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from enum import IntEnum

from eth_account.signers.local import LocalAccount
from web3.exceptions import TransactionNotFound
from web3.types import TxReceipt

from eth_rescue.bundle import (
    BUNDLE_BLOCK_RANGE,
    BundlePreparer,
    has_7702_delegation,
    prepare_7702_bundle,
    prepare_actions,
    prepare_bundle,
    size_actions,
)
from eth_rescue.checks import evaluate_checks, infer_checks, snapshot_checks
from eth_rescue.contract import ACTION_RESULT_TOPIC
from eth_rescue.relay import SECONDS_PER_BLOCK, RelayWeb3
from eth_rescue.report import Reporter
from eth_rescue.types import (
    ActionOutcome,
    PreparedAction,
    PreparedBundle,
    RescueData,
    SimulationFailure,
    SimulationOutcome,
)

DEFAULT_MAX_BLOCKS = 25
DEFAULT_FUNDING_TIMEOUT = 30 * 60


class ExitCode(IntEnum):
    OK = 0
    INVALID = 1
    SIMULATION_FAILED = 2
    UNFUNDED = 3
    NOT_INCLUDED = 4
    UNRESCUED = 5
    DECLINED = 6


@dataclass(frozen=True)
class RescueContext:
    w3: RelayWeb3
    victim: LocalAccount
    gas: LocalAccount
    safe: str
    reporter: Reporter
    extra_priority_fee_gwei: Decimal = Decimal(0)
    max_blocks: int = DEFAULT_MAX_BLOCKS
    funding_timeout: float = DEFAULT_FUNDING_TIMEOUT
    poll_interval: float = SECONDS_PER_BLOCK
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

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


Confirm = Callable[[], bool]


def wait_for_funding(ctx: RescueContext, required: int) -> bool:
    deadline = ctx.clock() + ctx.funding_timeout
    while True:
        balance = ctx.w3.eth.get_balance(ctx.gas.address)
        ctx.reporter.funding(ctx.gas.address, required, balance)
        if balance >= required:
            return True
        if ctx.clock() >= deadline:
            ctx.reporter.message("error", "Gas wallet was not funded in time.")
            return False
        ctx.sleep(ctx.poll_interval)


def _failure_label(failure: SimulationFailure) -> str:
    if failure.action_index is not None:
        return f"action #{failure.action_index + 1}"
    return failure.role or "unknown transaction"


def simulate_prepared_bundle(
    ctx: RescueContext, bundle: PreparedBundle
) -> SimulationOutcome:
    """Simulate the exact immutable bundle that may subsequently be submitted."""
    try:
        with ctx.reporter.waiting("Running Flashbots simulation..."):
            result = ctx.w3.relay.simulate(
                bundle.entries, block_tag=bundle.target_block
            )
    except Exception as e:
        failure = SimulationFailure(f"Bundle simulation failed: {e}")
        ctx.reporter.message("error", failure.message)
        return SimulationOutcome(False, bundle=bundle, failures=(failure,))

    if len(result["results"]) != len(bundle.transactions):
        failure = SimulationFailure(
            "Relay simulation response did not include every bundle transaction"
        )
        ctx.reporter.message("error", failure.message)
        return SimulationOutcome(
            False, bundle=bundle, result=result, failures=(failure,)
        )

    ctx.reporter.simulation(result)
    failures = tuple(
        SimulationFailure(
            str(tx_result.get("error") or tx_result.get("revert")),
            transaction_index=index,
            role=bundle.transactions[index].role,
            action_index=bundle.transactions[index].action_index,
        )
        for index, tx_result in enumerate(result["results"])
        if tx_result.get("error") or tx_result.get("revert")
    )
    for failure in failures:
        ctx.reporter.message(
            "error",
            f"Simulation failed at {_failure_label(failure)}: {failure.message}",
        )
    if failures:
        return SimulationOutcome(False, bundle=bundle, result=result, failures=failures)

    ctx.reporter.message("success", "Simulation succeeded.")
    return SimulationOutcome(True, bundle=bundle, result=result)


def _await_inclusion(ctx: RescueContext, submissions, bundle) -> Inclusion | None:
    for submission in submissions:
        try:
            with ctx.reporter.waiting("Waiting for bundle result..."):
                submission.wait()
            receipts = submission.receipts()
        except TransactionNotFound:
            continue
        if len(receipts) != len(bundle.transactions):
            raise RuntimeError("Relay did not return every transaction receipt")
        if any(receipt["status"] != 1 for receipt in receipts):
            raise RuntimeError("One or more rescue transactions reverted")
        ctx.reporter.included(receipts)
        return Inclusion(bundle, receipts)
    return None


def _submit(ctx: RescueContext, bundle: PreparedBundle, block_count: int) -> list:
    ctx.reporter.submitting(bundle.target_block, bundle.target_block + block_count - 1)
    submissions = []
    for offset in range(block_count):
        try:
            submissions.append(
                ctx.w3.relay.send_bundle(
                    bundle.entries, target_block_number=bundle.target_block + offset
                )
            )
        except Exception as e:
            if not submissions:
                raise
            ctx.reporter.message(
                "warning",
                f"A later target was rejected ({e}); monitoring the "
                f"{len(submissions)} accepted submission(s).",
            )
            break
    return submissions


def send_with_retry(
    ctx: RescueContext, prepared: list[PreparedAction], preparer: BundlePreparer
) -> Inclusion | ExitCode:
    """Rebuild, re-simulate and resubmit for each block window until included."""
    for first_attempt in range(0, ctx.max_blocks, BUNDLE_BLOCK_RANGE):
        block_count = min(BUNDLE_BLOCK_RANGE, ctx.max_blocks - first_attempt)
        try:
            bundle = ctx.prepare(preparer, prepared)
            gas_balance = ctx.w3.eth.get_balance(ctx.gas.address)
        except Exception as e:
            ctx.reporter.message("error", f"Could not prepare the next bundle: {e}")
            return ExitCode.NOT_INCLUDED

        if gas_balance < bundle.required_funding:
            ctx.reporter.message(
                "warning", "Gas wallet balance dropped below requirement (fees rose)."
            )
            if not wait_for_funding(ctx, bundle.required_funding):
                return ExitCode.UNFUNDED
            continue

        if not simulate_prepared_bundle(ctx, bundle):
            ctx.reporter.message(
                "error", "Submission blocked: the bundle no longer simulates cleanly."
            )
            return ExitCode.SIMULATION_FAILED

        if ctx.w3.eth.block_number >= bundle.target_block:
            ctx.reporter.message(
                "warning",
                f"Target block {bundle.target_block} arrived during simulation; "
                "rebuilding for a later block.",
            )
            continue

        try:
            submissions = _submit(ctx, bundle, block_count)
            inclusion = _await_inclusion(ctx, submissions, bundle)
        except Exception as e:
            ctx.reporter.message("error", f"Relay submission failed: {e}")
            return ExitCode.NOT_INCLUDED
        if inclusion is not None:
            return inclusion

    ctx.reporter.message(
        "error", f"Not included after {ctx.max_blocks} blocks. Nothing was rescued."
    )
    return ExitCode.NOT_INCLUDED


def _fund_and_simulate(
    ctx: RescueContext, prepared: list[PreparedAction], preparer: BundlePreparer
) -> ExitCode | None:
    try:
        required = ctx.prepare(preparer, prepared).required_funding
    except Exception as e:
        ctx.reporter.message("error", f"Could not prepare bundle: {e}")
        return ExitCode.SIMULATION_FAILED
    if not wait_for_funding(ctx, required):
        return ExitCode.UNFUNDED
    try:
        bundle = ctx.prepare(preparer, prepared)
    except Exception as e:
        ctx.reporter.message("error", f"Could not prepare bundle: {e}")
        return ExitCode.SIMULATION_FAILED
    ctx.reporter.bundle(bundle)
    if not simulate_prepared_bundle(ctx, bundle):
        ctx.reporter.message("error", "Nothing was sent.")
        return ExitCode.SIMULATION_FAILED
    return None


def call_results(inclusion: Inclusion, victim: str) -> dict[int, bool]:
    """Per-action EVM call success from EthRescue ActionResult logs."""
    results: dict[int, bool] = {}
    for transaction, receipt in zip(inclusion.bundle.transactions, inclusion.receipts):
        if transaction.role != "rescue-7702" or transaction.batch_start is None:
            continue
        for log in receipt["logs"]:
            topics = log["topics"]
            if log["address"] != victim or bytes(topics[0]) != ACTION_RESULT_TOPIC:
                continue
            index = transaction.batch_start + int.from_bytes(topics[1])
            results[index] = int.from_bytes(log["data"]) == 1
    return results


def sized_actions(
    ctx: RescueContext, rescue_data: list[RescueData]
) -> list[PreparedAction]:
    """Encode actions and raise their gas limits to live estimates."""
    floors = prepare_actions(rescue_data)
    prepared, failures = size_actions(ctx.w3, ctx.victim.address, floors)
    for index, (floor, action) in enumerate(zip(floors, prepared)):
        if index in failures:
            ctx.reporter.message(
                "warning",
                f"Action #{index + 1} would revert if sent now; keeping its "
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
    preparer: BundlePreparer,
    confirm: Confirm | None,
) -> list[ActionOutcome] | ExitCode:
    failure = _fund_and_simulate(ctx, prepared, preparer)
    if failure is not None:
        return failure
    if confirm is not None and not confirm():
        ctx.reporter.message("warning", "Aborted. Nothing was sent.")
        return ExitCode.DECLINED

    checks = infer_checks(rescue_data)
    snapshot = snapshot_checks(ctx.w3, checks)
    inclusion = send_with_retry(ctx, prepared, preparer)
    if isinstance(inclusion, ExitCode):
        return inclusion

    outcomes = evaluate_checks(
        ctx.w3,
        checks,
        snapshot,
        len(rescue_data),
        call_results(inclusion, ctx.victim.address),
    )
    ctx.reporter.outcomes(rescue_data, outcomes)
    return outcomes


def _preflight(ctx: RescueContext, rescue_data: list[RescueData]) -> bool:
    try:
        has_7702_delegation(ctx.w3, ctx.victim.address)
        prepare_actions(rescue_data)
    except ValueError as e:
        ctx.reporter.message("error", str(e))
        return False
    return True


def simulate(ctx: RescueContext, rescue_data: list[RescueData]) -> ExitCode:
    """Fund-check and simulate the phase-1 bundle without submitting it."""
    if not _preflight(ctx, rescue_data):
        return ExitCode.INVALID
    ctx.reporter.plan(rescue_data)
    failure = _fund_and_simulate(
        ctx, sized_actions(ctx, rescue_data), prepare_7702_bundle
    )
    return failure or ExitCode.OK


def _failed(outcomes: list[ActionOutcome]) -> list[int]:
    return [outcome.index for outcome in outcomes if outcome.status == "failed"]


def run(
    ctx: RescueContext,
    rescue_data: list[RescueData],
    *,
    confirm: Confirm,
    legacy_fallback: bool = True,
) -> ExitCode:
    if not _preflight(ctx, rescue_data):
        return ExitCode.INVALID
    ctx.reporter.plan(rescue_data)
    # Size once, before any delegation changes: these estimates also describe the
    # undelegated victim that the legacy fallback executes as.
    prepared = sized_actions(ctx, rescue_data)

    ctx.reporter.phase("Phase 1: EIP-7702 rescue")
    outcomes = _execute_phase(ctx, rescue_data, prepared, prepare_7702_bundle, confirm)
    if isinstance(outcomes, ExitCode):
        return outcomes
    failed = _failed(outcomes)
    if not failed:
        ctx.reporter.message("success", "Rescue complete.")
        return ExitCode.OK
    if not legacy_fallback:
        ctx.reporter.message(
            "warning", f"{len(failed)} action(s) failed; legacy fallback is disabled."
        )
        return ExitCode.UNRESCUED

    ctx.reporter.phase("Phase 2: legacy fallback")
    ctx.reporter.message(
        "info",
        f"Retrying {len(failed)} failed action(s) as direct victim transactions.",
    )
    outcomes = _execute_phase(
        ctx,
        [rescue_data[index] for index in failed],
        [prepared[index] for index in failed],
        prepare_bundle,
        confirm=None,
    )
    if isinstance(outcomes, ExitCode) or _failed(outcomes):
        ctx.reporter.message("error", "Some actions were not rescued.")
        return ExitCode.UNRESCUED
    ctx.reporter.message("success", "Rescue complete.")
    return ExitCode.OK
