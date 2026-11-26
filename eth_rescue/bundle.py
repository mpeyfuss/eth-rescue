import math
import os
from collections.abc import Callable
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from eth_account.account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import to_canonical_address, to_checksum_address, to_hex
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import BlockData, BlockIdentifier

from eth_rescue.calldata import build_calldata
from eth_rescue.contract import (
    DELEGATION_DESIGNATOR_PREFIX,
    RescueRequest,
    actions_hash,
    decode_rescue_results,
    encode_rescue_call,
    sign_rescue_request,
)
from eth_rescue.templates import GAS_GENERIC, MAX_ACTION_GAS
from eth_rescue.types import (
    BundleTransaction,
    PreparedAction,
    PreparedBundle,
    RescueData,
)

FUNDING_TX_GAS = 21000
UNDELEGATE_TX_GAS = 60000
SWEEP_TX_GAS = 21000  # plain transfer to an existing account
FUNDING_BUFFER = 1.15  # extra headroom on the gas wallet for fee fluctuation
MAX_ACTIONS_PER_RESCUE = 128  # actions per rescue() call in the 7702 bundle
# EIP-7825 per-transaction cap. Under EIP-8037 (Amsterdam) gas above it becomes
# a state-gas reservoir that GAS/gasleft() cannot see, so rescue transactions
# must never exceed it.
MAX_TX_GAS = 2**24
# EthRescue never checks gas itself: only the gas wallet can submit, so these
# budgets are the only thing standing between a rescue and starved actions.
# Intrinsic + authorization + request verification + dispatch, excluding the
# sweep. Measured ~46k Osaka, ~86k Amsterdam (sweep included).
RESCUE_TX_FIXED_GAS = 150_000
# The sweep CALL to an existing safe, and the minimum allowed for a contract
# safe's receive(); creating an empty safe is budgeted from a live estimate.
SWEEP_CALL_GAS = 40_000
# Per action beyond its stipend: cold call, ActionResult event and loop (~3k
# measured on both forks), plus actions-hash work.
RESCUE_ACTION_OVERHEAD_GAS = 6_000
# Each retry window rebuilds and re-signs, so requests only need to outlive one.
RESCUE_REQUEST_TTL = 600
# EIP-7623 prices calldata at up to 40 gas per non-zero byte (10 per zero byte)
# when execution is light, so budgeting every byte at 40 covers both pricings.
CALLDATA_GAS_PER_BYTE = 40
FLOOR_GAS_PER_ZERO_BYTE = 10
FLOOR_GAS_PER_NONZERO_BYTE = 40
TX_BASE_GAS = 21_000
AUTHORIZATION_GAS = 25_000  # EIP-7702 PER_EMPTY_ACCOUNT_COST, charged up front
# Used only when no live estimate of a transfer to an empty safe is possible;
# covers Amsterdam's 120 * CPSB = 183,600 new-account state gas.
NEW_ACCOUNT_GAS_FALLBACK = 250_000
# Whole-transaction estimates only ever raise the constant budget. The node's
# estimator cannot see an action starving inside rescue() (the transaction still
# succeeds), so an estimate alone is never trusted as a gas limit.
TX_ESTIMATE_MULTIPLIER = Decimal("1.5")
# Applied to each action's live estimate; overridable with --gas-multiplier.
DEFAULT_GAS_MULTIPLIER = Decimal("1.5")
MIN_GAS_MULTIPLIER = Decimal(1)
MAX_GAS_MULTIPLIER = Decimal(10)
TARGET_BLOCK_OFFSET = 1
BUNDLE_BLOCK_RANGE = 5  # target current +1 through current +5 (relay maximum)
SET_CODE_TX_TYPE = 0x04
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
# Flashbots eth_sendBundle: at most 100 transactions and 300,000 bytes.
MAX_BUNDLE_TRANSACTIONS = 100
# A worst-case fallback bundle adds undelegate, fund and sweep to the actions.
MAX_LEGACY_ACTIONS = MAX_BUNDLE_TRANSACTIONS - 3
MAX_BUNDLE_BYTES = 300_000
# Most a base fee can rise per block under EIP-1559.
BASE_FEE_STEP = Fraction(9, 8)
PROBE_BALANCE = 10**24

BundlePreparer = Callable[
    [Web3, LocalAccount, LocalAccount, list[PreparedAction], str, Decimal],
    PreparedBundle,
]


class BundleLimitError(ValueError):
    pass


def _probe_address() -> str:
    """A fresh address nobody holds a key for. Dry runs sign requests for it, so
    what reaches the public RPC can never be submitted by anyone."""
    return to_checksum_address(os.urandom(20))


def _compute_fees(
    w3: Web3, latest: BlockData, extra_priority_fee_gwei: Decimal
) -> tuple[int, int]:
    """Return (priority_fee, max_fee_per_gas) from the latest block."""
    if extra_priority_fee_gwei < 0:
        raise ValueError("Extra priority fee cannot be negative")
    base_fee = latest["baseFeePerGas"] * 5 // 4
    priority_fee = w3.eth.max_priority_fee + w3.to_wei(extra_priority_fee_gwei, "gwei")
    max_fee_per_gas = 2 * base_fee + priority_fee
    return priority_fee, max_fee_per_gas


def _window_fee_cap(
    latest_base_fee: int, priority_fee: int, max_fee_per_gas: int
) -> int:
    """Highest effective gas price any block in the submission window can charge:
    the base fee may rise by 1/8 (and at least 1 wei) in each of the
    BUNDLE_BLOCK_RANGE blocks after `latest`."""
    steps = BUNDLE_BLOCK_RANGE
    base = math.ceil(latest_base_fee * BASE_FEE_STEP**steps)
    if latest_base_fee > 0:
        base += steps
    return min(max_fee_per_gas, base + priority_fee)


@dataclass(frozen=True)
class _ChainState:
    chain_id: int
    victim_nonce: int
    gas_nonce: int
    priority_fee: int
    max_fee_per_gas: int
    effective_fee_cap: int
    target_block: int
    timestamp: int
    block_gas_limit: int


def _chain_state(
    w3: Web3, victim: LocalAccount, gas: LocalAccount, extra_priority_fee_gwei: Decimal
) -> _ChainState:
    latest = w3.eth.get_block("latest")
    chain_id = w3.eth.chain_id
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)
    priority_fee, max_fee_per_gas = _compute_fees(w3, latest, extra_priority_fee_gwei)
    return _ChainState(
        chain_id=chain_id,
        victim_nonce=victim_nonce,
        gas_nonce=gas_nonce,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=_window_fee_cap(
            int(latest["baseFeePerGas"]), priority_fee, max_fee_per_gas
        ),
        target_block=latest["number"] + TARGET_BLOCK_OFFSET,
        timestamp=latest["timestamp"],
        block_gas_limit=int(latest["gasLimit"]),
    )


def calldata_floor_gas(data: bytes, authorizations: int = 0) -> int:
    """EIP-7623 minimum gas limit for a transaction carrying `data`."""
    zeros = data.count(0)
    floor = (
        TX_BASE_GAS
        + FLOOR_GAS_PER_ZERO_BYTE * zeros
        + FLOOR_GAS_PER_NONZERO_BYTE * (len(data) - zeros)
    )
    return floor + AUTHORIZATION_GAS * authorizations


def _data_bytes(action: PreparedAction) -> bytes:
    return bytes.fromhex(action["data"][2:])


def _action_tx_gas(action: PreparedAction) -> int:
    """Gas one action adds to a rescue() transaction, including its calldata."""
    data_length = (len(action["data"]) - 2) // 2
    # offset + 3 head words + bytes length, then the padded bytes payload
    encoded_length = 5 * 32 + -(-data_length // 32) * 32
    return (
        action["gas"]
        + action["gas"] // 63  # EIP-150: the full stipend needs 64/63 available
        + RESCUE_ACTION_OVERHEAD_GAS
        + CALLDATA_GAS_PER_BYTE * encoded_length
    )


def _legacy_tx_gas(action: PreparedAction) -> int:
    """Gas limit for an action sent as its own transaction (legacy fallback)."""
    return max(action["gas"], calldata_floor_gas(_data_bytes(action)))


def prepare_actions(rescue_data: list[RescueData]) -> list[PreparedAction]:
    """Encode calldata and attach the fixed per-action gas limit."""
    if not rescue_data:
        raise ValueError("No rescue actions provided")
    prepared: list[PreparedAction] = []
    for index, data in enumerate(rescue_data):
        tx_data = build_calldata(data["function_signature"], data["args"])
        target = to_checksum_address(data["address"])
        gas = data.get("gas_estimate", GAS_GENERIC)
        action: PreparedAction = {"to": target, "data": tx_data, "gas": gas}
        if gas > MAX_ACTION_GAS:
            raise ValueError(
                f"Action #{index + 1} needs more gas than one transaction allows"
            )
        prepared.append(action)
    return prepared


def _estimate_without_code(w3: Web3, transaction: dict) -> int:
    """Estimate `transaction` as if its sender had no code. The override only
    hides a delegation, so an RPC that refuses overrides is asked plainly only
    when the sender has none; a revert is final either way."""
    sender = transaction["from"]
    try:
        return w3.eth.estimate_gas(transaction, state_override={sender: {"code": "0x"}})
    except ContractLogicError:
        raise
    except Exception:  # most likely no state-override support
        if w3.eth.get_code(sender):
            raise
    return w3.eth.estimate_gas(transaction)


def size_actions(
    w3: Web3,
    sender: str,
    prepared: list[PreparedAction],
    multiplier: Decimal = DEFAULT_GAS_MULTIPLIER,
) -> tuple[list[PreparedAction], dict[int, str]]:
    """Raise each action's gas limit to its live estimate times `multiplier`.

    The template or plan gas is a floor: an estimate (which an attacker may
    influence through chain state) can only increase a limit, never lower it.
    The sender is estimated without code, as the legacy fallback runs it, so an
    attacker's existing delegation on the victim never executes here.
    Returns the sized actions and the reason for each action whose estimate
    failed; those keep their floor.
    """
    sized: list[PreparedAction] = []
    failures: dict[int, str] = {}
    for index, action in enumerate(prepared):
        transaction = {"from": sender, "to": action["to"], "data": action["data"]}
        try:
            estimate = _estimate_without_code(w3, transaction)
        except Exception as e:  # reverts and RPC refusals look alike here
            failures[index] = str(e)
            sized.append(action)
            continue
        margin = math.ceil(estimate * multiplier)
        sized.append(action | {"gas": max(action["gas"], min(margin, MAX_ACTION_GAS))})
    return sized, failures


def _transfer_estimate(w3: Web3, safe: str) -> int | None:
    """Gas for a 1-wei transfer to `safe`, from a probe address the state
    override funds (so a dust or empty gas wallet cannot break the estimate)."""
    sender = _probe_address()
    try:
        return w3.eth.estimate_gas(
            {"from": sender, "to": safe, "value": 1},
            state_override={sender: {"balance": hex(PROBE_BALANCE)}},
        )
    except Exception:
        return None


def safe_receive_gas(w3: Web3, safe: str) -> int:
    """Gas an ETH transfer to `safe` needs beyond 21,000: the new-account charge
    for an empty account, the receive() of a contract or delegated safe (at
    least SWEEP_CALL_GAS, raised by a live estimate), or 0 for a plain EOA."""
    has_code = bool(w3.eth.get_code(safe))
    empty = (
        not has_code
        and w3.eth.get_transaction_count(safe) == 0
        and w3.eth.get_balance(safe) == 0
    )
    if not has_code and not empty:
        return 0
    estimate = _transfer_estimate(w3, safe)
    if empty:
        if estimate is None:
            return NEW_ACCOUNT_GAS_FALLBACK
        return max(0, estimate - SWEEP_TX_GAS)
    if estimate is None:
        return SWEEP_CALL_GAS
    margin = math.ceil((estimate - SWEEP_TX_GAS) * TX_ESTIMATE_MULTIPLIER)
    return max(SWEEP_CALL_GAS, margin)


def _rescue_base_gas(receive_gas: int) -> int:
    return RESCUE_TX_FIXED_GAS + SWEEP_CALL_GAS + receive_gas


def _batch_tx_gas(batch: list[PreparedAction], base_gas: int) -> int:
    return base_gas + sum(_action_tx_gas(action) for action in batch)


def _rescue_batches(
    prepared: list[PreparedAction], base_gas: int
) -> list[list[PreparedAction]]:
    """Split actions into rescue() calls that fit both the action and gas caps."""
    batches: list[list[PreparedAction]] = []
    current: list[PreparedAction] = []
    current_gas = base_gas
    for index, action in enumerate(prepared):
        action_gas = _action_tx_gas(action)
        if base_gas + action_gas > MAX_TX_GAS:
            raise ValueError(
                f"Action #{index + 1} needs more gas than one transaction allows"
            )
        if current and (
            len(current) == MAX_ACTIONS_PER_RESCUE
            or current_gas + action_gas > MAX_TX_GAS
        ):
            batches.append(current)
            current, current_gas = [], base_gas
        current.append(action)
        current_gas += action_gas
    if current:
        batches.append(current)
    return batches


def check_bundle_limits(
    transactions: list[BundleTransaction],
    total_gas: int = 0,
    block_gas_limit: int | None = None,
) -> None:
    """Refuse a bundle the relay would reject or no block could hold, before
    anyone funds it."""
    if block_gas_limit is not None and total_gas > block_gas_limit:
        raise BundleLimitError(
            f"The bundle's gas limits add up to {total_gas:,}, more than a block "
            f"holds ({block_gas_limit:,}). Split the plan into smaller plans."
        )
    if len(transactions) > MAX_BUNDLE_TRANSACTIONS:
        raise BundleLimitError(
            f"The bundle needs {len(transactions)} transactions; relays accept at "
            f"most {MAX_BUNDLE_TRANSACTIONS}. Split the plan into smaller plans."
        )
    size = sum(len(transaction.signed_transaction) for transaction in transactions)
    if size > MAX_BUNDLE_BYTES:
        raise BundleLimitError(
            f"The bundle is {size:,} bytes; relays accept at most "
            f"{MAX_BUNDLE_BYTES:,}. Split the plan into smaller plans."
        )


def _required_funding_7702(rescue_gas_limits: list[int], max_fee_per_gas: int) -> int:
    total_gas = sum(rescue_gas_limits)
    return int(total_gas * max_fee_per_gas * FUNDING_BUFFER)


def _victim_funding_value(
    gas_limits: list[int],
    max_fee_per_gas: int,
    effective_fee_cap: int,
    sweep_gas: int = SWEEP_TX_GAS,
) -> int:
    rescue_cost = sum(gas * max_fee_per_gas for gas in gas_limits)
    return rescue_cost + sweep_gas * effective_fee_cap


def _required_funding(
    gas_limits: list[int],
    max_fee_per_gas: int,
    effective_fee_cap: int,
    needs_undelegation: bool = True,
    sweep_gas: int = SWEEP_TX_GAS,
) -> int:
    """Total ETH the gas wallet must hold for sponsor txs and victim funding."""
    victim_funding = _victim_funding_value(
        gas_limits, max_fee_per_gas, effective_fee_cap, sweep_gas
    )
    sponsor_gas = FUNDING_TX_GAS
    if needs_undelegation:
        sponsor_gas += UNDELEGATE_TX_GAS
    sponsor_cost = sponsor_gas * max_fee_per_gas
    return int((victim_funding + sponsor_cost) * FUNDING_BUFFER)


def _sign_sponsored_call(
    *,
    chain_id: int,
    nonce: int,
    to: str,
    data: bytes,
    gas: int,
    priority_fee: int,
    max_fee_per_gas: int,
    sponsor_key: bytes,
    authorization=None,
) -> HexBytes:
    """A zero-value call paid by the gas wallet; type 4 when it carries an
    EIP-7702 authorization."""
    transaction = {
        "chainId": chain_id,
        "nonce": nonce,
        "maxPriorityFeePerGas": priority_fee,
        "maxFeePerGas": max_fee_per_gas,
        "gas": gas,
        "to": to,
        "value": 0,
        "data": data,
    }
    if authorization is not None:
        transaction |= {
            "type": SET_CODE_TX_TYPE,
            "accessList": [],
            "authorizationList": [authorization],
        }
    return Account.sign_transaction(transaction, sponsor_key).raw_transaction


def _sign_7702_undelegation(
    *,
    chain_id: int,
    tx_nonce: int,
    authority_nonce: int,
    authority_key: bytes,
    sponsor_key: bytes,
    sponsor_address: str,
    priority_fee: int,
    max_fee_per_gas: int,
) -> HexBytes:
    authorization = Account.sign_authorization(
        {
            "chainId": chain_id,
            "address": ZERO_ADDRESS,
            "nonce": authority_nonce,
        },
        authority_key,
    )
    return _sign_sponsored_call(
        chain_id=chain_id,
        nonce=tx_nonce,
        to=sponsor_address,
        data=b"",
        gas=UNDELEGATE_TX_GAS,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        sponsor_key=sponsor_key,
        authorization=authorization,
    )


def delegation_target(
    w3: Web3, address: str, block: BlockIdentifier = "latest"
) -> str | None:
    """The address `address` delegates to under EIP-7702, or None without code."""
    code = bytes(w3.eth.get_code(address, block))
    if not code:
        return None
    if len(code) == 23 and code.startswith(DELEGATION_DESIGNATOR_PREFIX):
        return to_checksum_address(code[3:])
    raise ValueError(f"Account {address} has unexpected non-EIP-7702 code")


def has_7702_delegation(w3: Web3, address: str) -> bool:
    return delegation_target(w3, address) is not None


def _rescue_calldata(
    victim: LocalAccount,
    chain_id: int,
    rescuer: str,
    batch: list[PreparedAction],
    safe_address: str,
    deadline: int,
) -> bytes:
    request = RescueRequest(
        rescuer=rescuer,
        safe=safe_address,
        deadline=deadline,
        actions_hash=actions_hash(batch),
    )
    signature = sign_rescue_request(victim.key, chain_id, victim.address, request)
    return encode_rescue_call(request, batch, signature)


def _dry_run_batches(
    w3: Web3,
    victim: LocalAccount,
    chain_id: int,
    rescue_contract: str,
    batches: list[list[PreparedAction]],
    safe_address: str,
    deadline: int,
) -> tuple[list[int | None], list[bool | None], list[str]]:
    """Run each rescue() batch against the public RPC without exposing anything
    usable: the victim is given the delegation by a state override instead of a
    signed authorization, and each request is signed for a keyless probe
    rescuer, so EthRescue would reject it from any real sender.

    Returns each batch's gas estimate (authorization cost included for the first
    batch), each action's predicted call result, and warnings. Calldata has the
    same length as the real request, so the estimates carry over.
    """
    delegated = {
        victim.address: {
            "code": to_hex(
                DELEGATION_DESIGNATOR_PREFIX + to_canonical_address(rescue_contract)
            )
        }
    }
    estimates: list[int | None] = []
    predicted: list[bool | None] = []
    warnings: list[str] = []
    for index, batch in enumerate(batches):
        rescuer = _probe_address()
        calldata = _rescue_calldata(
            victim, chain_id, rescuer, batch, safe_address, deadline
        )
        transaction = {"from": rescuer, "to": victim.address, "data": to_hex(calldata)}
        try:
            results = decode_rescue_results(
                w3.eth.call(transaction, "latest", delegated)
            )
            estimate = w3.eth.estimate_gas(transaction, state_override=delegated)
        except Exception as e:  # no state-override support, or a revert
            warnings.append(
                f"Could not dry-run rescue transaction #{index + 1}; using its fixed "
                f"gas budget and skipping the per-action preview: {e}"
            )
            estimates.append(None)
            predicted.extend([None] * len(batch))
            continue
        estimates.append(estimate + (AUTHORIZATION_GAS if index == 0 else 0))
        predicted.extend(results if len(results) == len(batch) else [None] * len(batch))
    return estimates, predicted, warnings


def _rescue_gas_limit(
    budget: int, estimate: int | None, calldata: bytes, auths: int
) -> int:
    limit = budget
    if estimate is not None:
        limit = max(limit, math.ceil(estimate * TX_ESTIMATE_MULTIPLIER))
    floor = calldata_floor_gas(calldata, auths)
    if floor > MAX_TX_GAS:
        raise BundleLimitError("A rescue transaction carries too much calldata")
    return min(MAX_TX_GAS, max(limit, floor))


def prepare_7702_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: Decimal,
    *,
    rescue_contract: str,
) -> PreparedBundle:
    """Build the phase-1 bundle: delegate the victim to the network's EthRescue
    deployment and rescue everything in one sponsored call per batch, each
    authorized by a victim-signed EIP-712 request bound to the gas wallet.

    All actions are optional inside rescue(): if one fails mid-race the call
    still succeeds, the bundle stays valid, and the full ETH balance is swept.
    Nothing signed here leaves the machine except in the bundle itself.
    """
    state = _chain_state(w3, victim, gas, extra_priority_fee_gwei)
    chain_id = state.chain_id
    deadline = state.timestamp + RESCUE_REQUEST_TTL

    base_gas = _rescue_base_gas(safe_receive_gas(w3, safe_address))
    batches = _rescue_batches(prepared, base_gas)
    estimates, predicted, warnings = _dry_run_batches(
        w3, victim, chain_id, rescue_contract, batches, safe_address, deadline
    )

    authorization = Account.sign_authorization(
        {"chainId": chain_id, "address": rescue_contract, "nonce": state.victim_nonce},
        victim.key,
    )
    calldatas = [
        _rescue_calldata(victim, chain_id, gas.address, batch, safe_address, deadline)
        for batch in batches
    ]
    gas_limits = [
        _rescue_gas_limit(
            _batch_tx_gas(batch, base_gas), estimate, calldata, int(index == 0)
        )
        for index, (batch, estimate, calldata) in enumerate(
            zip(batches, estimates, calldatas)
        )
    ]

    transactions: list[BundleTransaction] = []
    batch_start = 0
    for offset, (batch, calldata, limit) in enumerate(
        zip(batches, calldatas, gas_limits)
    ):
        # The delegation set by the first rescue tx is live for the rest of the
        # bundle, so continuation batches are plain sponsored calls to the victim.
        signed = _sign_sponsored_call(
            chain_id=chain_id,
            nonce=state.gas_nonce + offset,
            to=victim.address,
            data=calldata,
            gas=limit,
            priority_fee=state.priority_fee,
            max_fee_per_gas=state.max_fee_per_gas,
            sponsor_key=gas.key,
            authorization=authorization if offset == 0 else None,
        )
        transactions.append(
            BundleTransaction(
                "rescue-7702", signed, batch_start=batch_start, batch_size=len(batch)
            )
        )
        batch_start += len(batch)
    check_bundle_limits(transactions, sum(gas_limits), state.block_gas_limit)

    return PreparedBundle(
        transactions=transactions,
        victim_nonce=state.victim_nonce,
        gas_nonce=state.gas_nonce,
        priority_fee=state.priority_fee,
        max_fee_per_gas=state.max_fee_per_gas,
        effective_fee_cap=state.effective_fee_cap,
        target_block=state.target_block,
        required_funding=_required_funding_7702(gas_limits, state.max_fee_per_gas),
        victim_funding=0,
        sweep_value=0,
        warnings=tuple(warnings),
        predicted=tuple(predicted),
    )


def prepare_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: Decimal,
) -> PreparedBundle:
    """Build the legacy bundle (victim-signed transactions) for the next block:
    clear any delegation, fund the victim, send each action directly, and sweep
    what is left to the safe."""
    state = _chain_state(w3, victim, gas, extra_priority_fee_gwei)
    chain_id = state.chain_id
    max_fee_per_gas = state.max_fee_per_gas
    effective_fee_cap = state.effective_fee_cap
    victim_balance = w3.eth.get_balance(victim.address)
    needs_undelegation = has_7702_delegation(w3, victim.address)
    sweep_gas = SWEEP_TX_GAS + safe_receive_gas(w3, safe_address)
    gas_limits = [_legacy_tx_gas(action) for action in prepared]

    # Each rescue tx pays at most effective_fee_cap per gas inside the window,
    # so whatever the victim holds beyond that is swept.
    rescue_cost = sum(limit * max_fee_per_gas for limit in gas_limits)
    rescue_fee_cap = sum(limit * effective_fee_cap for limit in gas_limits)
    sweep_value = max(0, victim_balance + rescue_cost - rescue_fee_cap)
    victim_funding = _victim_funding_value(
        gas_limits, max_fee_per_gas, effective_fee_cap, sweep_gas
    )
    # The sweep is signed before anyone knows the gas actually used, so the gas
    # reserve for every transaction, the sweep's own included, stays behind
    # except what is actually spent.
    reserve = (sum(gas_limits) + sweep_gas) * effective_fee_cap
    leftover_warning = (
        "The fallback funds the compromised wallet for every transaction's full "
        "gas limit; whatever gas is left unused, up to "
        f"{Web3.from_wei(reserve, 'ether')} ETH, stays in that wallet, where a "
        "sweeper can take it."
    )

    fees = {
        "maxFeePerGas": max_fee_per_gas,
        "maxPriorityFeePerGas": state.priority_fee,
    }
    transactions: list[BundleTransaction] = []
    nonce_offset = int(needs_undelegation)
    victim_nonce = state.victim_nonce + nonce_offset
    if needs_undelegation:
        transactions.append(
            BundleTransaction(
                "undelegate",
                _sign_7702_undelegation(
                    chain_id=chain_id,
                    tx_nonce=state.gas_nonce,
                    authority_nonce=state.victim_nonce,
                    authority_key=victim.key,
                    sponsor_key=gas.key,
                    sponsor_address=gas.address,
                    priority_fee=state.priority_fee,
                    max_fee_per_gas=max_fee_per_gas,
                ),
            )
        )
    funding_tx = {
        "to": victim.address,
        "value": victim_funding,
        "gas": FUNDING_TX_GAS,
        "nonce": state.gas_nonce + nonce_offset,
        "chainId": chain_id,
    } | fees
    transactions.append(
        BundleTransaction(
            "fund", Account.sign_transaction(funding_tx, gas.key).raw_transaction
        )
    )
    for index, (action, limit) in enumerate(zip(prepared, gas_limits)):
        tx = {
            "to": action["to"],
            "data": action["data"],
            "gas": limit,
            "nonce": victim_nonce + index,
            "chainId": chain_id,
        } | fees
        transactions.append(
            BundleTransaction(
                "rescue",
                Account.sign_transaction(tx, victim.key).raw_transaction,
                action_index=index,
            )
        )
    if sweep_value:
        sweep_tx = {
            "to": safe_address,
            "value": sweep_value,
            "gas": sweep_gas,
            "maxFeePerGas": effective_fee_cap,
            "maxPriorityFeePerGas": min(state.priority_fee, effective_fee_cap),
            "nonce": victim_nonce + len(prepared),
            "chainId": chain_id,
        }
        transactions.append(
            BundleTransaction(
                "sweep", Account.sign_transaction(sweep_tx, victim.key).raw_transaction
            )
        )
    sponsor_gas = FUNDING_TX_GAS + (UNDELEGATE_TX_GAS if needs_undelegation else 0)
    check_bundle_limits(
        transactions,
        sponsor_gas + sum(gas_limits) + (sweep_gas if sweep_value else 0),
        state.block_gas_limit,
    )

    return PreparedBundle(
        transactions=transactions,
        victim_nonce=state.victim_nonce,
        gas_nonce=state.gas_nonce,
        priority_fee=state.priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=effective_fee_cap,
        target_block=state.target_block,
        required_funding=_required_funding(
            gas_limits,
            max_fee_per_gas,
            effective_fee_cap,
            needs_undelegation,
            sweep_gas,
        ),
        victim_funding=victim_funding,
        sweep_value=sweep_value,
        warnings=(leftover_warning,),
    )
