import time
from collections.abc import Callable
from dataclasses import dataclass
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
from eth_rescue.relay import SECONDS_PER_BLOCK, RelayWeb3
from eth_rescue.report import Reporter
from eth_rescue.types import (
    RETRYABLE,
    ActionOutcome,
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
# Blocks to keep looking for a bundle after the gas wallet's nonce moved.
LAGGING_RECEIPT_BLOCKS = 3
# Consecutive rebuilds (stale target, fee top-ups) allowed without a submission.
MAX_REBUILDS = 5


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

    @property
    def block(self) -> int:
        return int(self.receipts[0]["blockNumber"])


class InclusionUnknown(RuntimeError):
    """A submitted bundle may have landed, but the chain does not say so clearly."""


Confirm = Callable[[], bool]


def wait_for_funding(
    ctx: RescueContext, required: int, deadline: float | None = None
) -> bool:
    if deadline is None:
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


def _lookup(ctx: RescueContext, bundle: PreparedBundle) -> Inclusion | None:
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
    return Inclusion(bundle, found)


def _find_inclusion(
    ctx: RescueContext, bundles: list[PreparedBundle]
) -> Inclusion | None:
    """Look for any submitted bundle on-chain, re-reading inconsistent results a
    few times (load-balanced RPCs can answer from nodes at different heights)."""
    for attempt in range(INCLUSION_READ_ATTEMPTS):
        try:
            for bundle in bundles:
                inclusion = _lookup(ctx, bundle)
                if inclusion is not None:
                    ctx.reporter.included(inclusion.receipts)
                    return inclusion
            return None
        except InclusionUnknown:
            if attempt == INCLUSION_READ_ATTEMPTS - 1:
                raise
            ctx.sleep(ctx.head_poll_interval)
    raise AssertionError("unreachable")


def _wait_for_block(ctx: RescueContext, number: int) -> bool:
    """Wait until the head reaches `number`; False if it stops advancing."""
    last = ctx.w3.eth.block_number
    moved_at = ctx.clock()
    while last < number:
        if ctx.clock() - moved_at >= ctx.head_timeout:
            return False
        ctx.sleep(ctx.head_poll_interval)
        current = ctx.w3.eth.block_number
        if current > last:
            last, moved_at = current, ctx.clock()
    return True


def _unknown(ctx: RescueContext, reason: object) -> ExitCode:
    ctx.reporter.message(
        "error",
        f"Cannot tell whether the rescue landed ({reason}). Not resubmitting, so "
        "nothing is sent twice. Check the gas wallet's latest transactions on a "
        "block explorer before running again.",
    )
    return ExitCode.NOT_INCLUDED


def _submit(ctx: RescueContext, bundle: PreparedBundle, block_count: int) -> None:
    """Send the bundle for each target block. A failed send may still have
    reached the relay, so the caller watches anyway."""
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
    return ctx.w3.eth.block_number >= bundle.target_block


def _await_lagging_inclusion(
    ctx: RescueContext, submitted: list[PreparedBundle]
) -> Inclusion | None:
    target = ctx.w3.eth.block_number + LAGGING_RECEIPT_BLOCKS
    while True:
        inclusion = _find_inclusion(ctx, submitted)
        if inclusion is not None or ctx.w3.eth.block_number >= target:
            return inclusion
        if not _wait_for_block(ctx, ctx.w3.eth.block_number + 1):
            return None


def _watch_window(
    ctx: RescueContext, submitted: list[PreparedBundle], first: int, count: int
) -> Inclusion | ExitCode | None:
    for target in range(first, first + count):
        if not _wait_for_block(ctx, target):
            inclusion = _find_inclusion(ctx, submitted)
            if inclusion is not None:
                return inclusion
            ctx.reporter.message(
                "error",
                f"The chain head stopped advancing before block {target}; check the "
                "RPC and run again.",
            )
            return ExitCode.NOT_INCLUDED
        inclusion = _find_inclusion(ctx, submitted)
        if inclusion is not None:
            return inclusion
    return None


def send_with_retry(
    ctx: RescueContext,
    prepared: list[PreparedAction],
    preparer: BundlePreparer,
    indices: list[int] | None = None,
) -> Inclusion | ExitCode:
    """Rebuild, re-simulate and resubmit for each block window until included.

    Only blocks actually submitted count toward `max_blocks`. Every earlier
    bundle is checked before a new one is built, and if the gas wallet's nonce
    moved without a visible bundle, nothing is resubmitted.
    """
    submitted: list[PreparedBundle] = []
    blocks_sent = 0
    rebuilds = 0
    while blocks_sent < ctx.max_blocks:
        try:
            inclusion = _find_inclusion(ctx, submitted)
        except InclusionUnknown as e:
            return _unknown(ctx, e)
        if inclusion is not None:
            return inclusion
        if rebuilds >= MAX_REBUILDS:
            ctx.reporter.message(
                "error", f"Could not get a bundle ready after {rebuilds} rebuilds."
            )
            return ExitCode.NOT_INCLUDED
        try:
            bundle = ctx.prepare(preparer, prepared)
            gas_balance = ctx.w3.eth.get_balance(ctx.gas.address)
        except Exception as e:
            ctx.reporter.message("error", f"Could not prepare the next bundle: {e}")
            return ExitCode.NOT_INCLUDED

        if submitted and bundle.gas_nonce != submitted[0].gas_nonce:
            try:
                inclusion = _await_lagging_inclusion(ctx, submitted)
            except InclusionUnknown as e:
                return _unknown(ctx, e)
            if inclusion is not None:
                return inclusion
            return _unknown(
                ctx, "the gas wallet sent a transaction that is not one of the bundles"
            )

        if gas_balance < bundle.required_funding:
            ctx.reporter.message(
                "warning", "Gas wallet balance dropped below requirement (fees rose)."
            )
            if not wait_for_funding(ctx, bundle.required_funding):
                return ExitCode.UNFUNDED
            rebuilds += 1
            continue

        if not _stale(ctx, bundle):
            simulation = simulate_prepared_bundle(ctx, bundle, indices)
            if not simulation and not _stale(ctx, bundle):
                ctx.reporter.message(
                    "error",
                    "Submission blocked: the bundle no longer simulates cleanly.",
                )
                return ExitCode.SIMULATION_FAILED
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
        submitted.append(bundle)
        blocks_sent += block_count
        try:
            outcome = _watch_window(ctx, submitted, bundle.target_block, block_count)
        except InclusionUnknown as e:
            return _unknown(ctx, e)
        if outcome is not None:
            return outcome

    try:
        inclusion = _await_lagging_inclusion(ctx, submitted)
    except InclusionUnknown as e:
        return _unknown(ctx, e)
    if inclusion is not None:
        return inclusion
    ctx.reporter.message(
        "error", f"Not included after {ctx.max_blocks} blocks. Nothing was rescued."
    )
    return ExitCode.NOT_INCLUDED


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
    inclusion: Inclusion, victim: str, indices: list[int]
) -> dict[int, bool | None]:
    """Plan call index -> EVM call result, or None when the call never ran.

    A rescue-7702 call missing its ActionResult log never executed: its batch
    reverted, or a nonce race skipped the authorization and nothing ran.
    """
    results: dict[int, bool | None] = dict.fromkeys(indices)
    victim = victim.lower()
    for transaction, receipt in zip(inclusion.bundle.transactions, inclusion.receipts):
        succeeded = receipt["status"] == 1
        if transaction.role == "rescue" and transaction.action_index is not None:
            results[indices[transaction.action_index]] = succeeded
        if transaction.role != "rescue-7702" or not succeeded:
            continue
        start, size = transaction.batch_start, transaction.batch_size
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
    dropped: list[ActionOutcome] = []
    while True:
        simulation = _fund_and_simulate(
            ctx, [prepared[i] for i in active], preparer, active
        )
        if isinstance(simulation, ExitCode):
            return simulation
        if simulation:
            break
        failing = sorted(
            {
                active[f.action_index]
                for f in simulation.failures
                if f.role == "rescue" and f.action_index is not None
            }
        )
        blocking = [
            f
            for f in simulation.failures
            if f.role != "rescue" or f.action_index is None
        ]
        if not drop_failing or not failing or blocking:
            ctx.reporter.message("error", "Nothing was sent.")
            return ExitCode.SIMULATION_FAILED
        for index in failing:
            ctx.reporter.message(
                "warning",
                f"Leaving out action #{index + 1}: it reverts as a direct transaction.",
            )
            dropped.append(
                ActionOutcome(index, "failed", "reverted in the fallback simulation")
            )
        active = [index for index in active if index not in failing]
        if not active:
            ctx.reporter.outcomes(rescue_data, dropped)
            return dropped

    if confirm is not None and not confirm():
        ctx.reporter.message("warning", "Aborted. Nothing was sent.")
        return ExitCode.DECLINED

    checks = infer_checks(rescue_data)
    active_checks = {index: checks[index] for index in active if index in checks}
    snapshot = snapshot_checks(ctx.w3, active_checks, sleep=ctx.sleep)
    inclusion = send_with_retry(ctx, [prepared[i] for i in active], preparer, active)
    if isinstance(inclusion, ExitCode):
        return inclusion

    executed = execution_results(inclusion, ctx.victim.address, active)
    outcomes = evaluate_checks(
        ctx.w3, active_checks, snapshot, executed, inclusion.block, sleep=ctx.sleep
    )
    outcomes = sorted(outcomes + dropped, key=lambda outcome: outcome.index)
    ctx.reporter.outcomes(rescue_data, outcomes)
    return outcomes


def _check_roles(ctx: RescueContext, rescue_data: list[RescueData]) -> None:
    """The singleton's own address rules, checked before anything is signed."""
    victim, gas, safe = (
        a.lower() for a in (ctx.victim.address, ctx.gas.address, ctx.safe)
    )
    if safe == ZERO_ADDRESS or safe in (victim, gas):
        raise ValueError(
            "The safe must be a non-zero address that differs from the compromised "
            "and gas wallets"
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
        ctx.reporter.outcomes(rescue_data, [outcomes[i] for i in everything])

    return _finish(ctx, rescue_data, [outcomes[i] for i in everything])
