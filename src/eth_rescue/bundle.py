import math
from collections.abc import Callable
from decimal import Decimal

from eth_account.account import Account
from eth_account.datastructures import SignedSetCodeAuthorization
from eth_account.signers.local import LocalAccount
from eth_utils import to_canonical_address, to_checksum_address, to_hex
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue.calldata import build_calldata
from eth_rescue.contract import (
    RescueRequest,
    actions_hash,
    encode_rescue_call,
    sign_rescue_request,
)
from eth_rescue.templates import GAS_GENERIC
from eth_rescue.types import (
    BundleEntry,
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
MAX_ACTION_GAS = 8_000_000
# EthRescue never checks gas itself: only the gas wallet can submit, so these
# budgets are the only thing standing between a rescue and starved actions.
# Intrinsic + authorization + request verification + dispatch, excluding the
# sweep. Measured ~46k Osaka, ~86k Amsterdam (sweep included).
RESCUE_TX_FIXED_GAS = 150_000
# The sweep CALL to an existing safe, including a contract safe's receive();
# creating an empty safe is budgeted separately from a live estimate.
SWEEP_CALL_GAS = 40_000
# Per action beyond its stipend: cold call, ActionResult event and loop (~3k
# measured on both forks), plus actions-hash work. Calldata is budgeted
# separately at the 16 gas/byte upper bound.
RESCUE_ACTION_OVERHEAD_GAS = 6_000
# Each retry window rebuilds and re-signs, so requests only need to outlive one.
RESCUE_REQUEST_TTL = 600
CALLDATA_GAS_PER_BYTE = 16
# Used only when no funded account can estimate a transfer to an empty safe;
# covers Amsterdam's 120 * CPSB = 183,600 new-account state gas.
NEW_ACCOUNT_GAS_FALLBACK = 250_000
# Whole-transaction estimates only ever raise the constant budget. The node's
# estimator cannot see an action starving inside tryCall (the transaction still
# succeeds), so an estimate alone is never trusted as a gas limit.
TX_ESTIMATE_MULTIPLIER = Decimal("1.5")
# Applied to each action's live estimate; overridable with --gas-multiplier.
DEFAULT_GAS_MULTIPLIER = Decimal("1.5")
MIN_GAS_MULTIPLIER = Decimal(1)
MAX_GAS_MULTIPLIER = Decimal(10)
TARGET_BLOCK_OFFSET = 1
BUNDLE_BLOCK_RANGE = 5  # target current +1 through current +5 (relay maximum)
SET_CODE_TX_TYPE = 0x04
DELEGATION_DESIGNATOR_PREFIX = b"\xef\x01\x00"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

BundlePreparer = Callable[
    [Web3, LocalAccount, LocalAccount, list[PreparedAction], str, Decimal],
    PreparedBundle,
]


def _compute_fees(w3: Web3, extra_priority_fee_gwei: Decimal) -> tuple[int, int]:
    """Return (priority_fee, max_fee_per_gas) from the latest block."""
    if extra_priority_fee_gwei < 0:
        raise ValueError("Extra priority fee cannot be negative")
    base_fee = int(w3.eth.get_block("latest")["baseFeePerGas"] * 1.25)
    priority_fee = w3.eth.max_priority_fee + w3.to_wei(extra_priority_fee_gwei, "gwei")
    max_fee_per_gas = 2 * base_fee + priority_fee
    return priority_fee, max_fee_per_gas


def _max_next_block_effective_fee(w3: Web3, priority_fee: int) -> int:
    latest_base_fee = int(w3.eth.get_block("latest")["baseFeePerGas"])
    return latest_base_fee + latest_base_fee // 8 + 1 + priority_fee


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


def size_actions(
    w3: Web3,
    sender: str,
    prepared: list[PreparedAction],
    multiplier: Decimal = DEFAULT_GAS_MULTIPLIER,
) -> tuple[list[PreparedAction], dict[int, str]]:
    """Raise each action's gas limit to its live estimate times `multiplier`.

    The template or plan gas is a floor: an estimate (which an attacker may
    influence through chain state) can only increase a limit, never lower it.
    Returns the sized actions and the reason for each action whose estimate
    failed; those keep their floor.
    """
    sized: list[PreparedAction] = []
    failures: dict[int, str] = {}
    for index, action in enumerate(prepared):
        try:
            estimate = w3.eth.estimate_gas(
                {"from": sender, "to": action["to"], "data": action["data"]}
            )
        except Exception as e:  # reverts and RPC refusals look alike here
            failures[index] = str(e)
            sized.append(action)
            continue
        margin = math.ceil(estimate * multiplier)
        sized.append(action | {"gas": max(action["gas"], min(margin, MAX_ACTION_GAS))})
    return sized, failures


def _is_empty_account(w3: Web3, address: str) -> bool:
    return (
        w3.eth.get_transaction_count(address) == 0
        and w3.eth.get_balance(address) == 0
        and not w3.eth.get_code(address)
    )


def _new_account_gas(w3: Web3, funded_sender: str, safe: str) -> int:
    """Extra gas a value transfer pays to create the safe account, per the
    node's current fork rules (0 when the safe already exists)."""
    if not _is_empty_account(w3, safe):
        return 0
    if w3.eth.get_balance(funded_sender) == 0:
        return NEW_ACCOUNT_GAS_FALLBACK
    estimate = w3.eth.estimate_gas({"from": funded_sender, "to": safe, "value": 1})
    return max(0, estimate - SWEEP_TX_GAS)


def _rescue_base_gas(new_account_gas: int) -> int:
    return RESCUE_TX_FIXED_GAS + SWEEP_CALL_GAS + new_account_gas


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


def _required_funding_7702(rescue_gas_limits: list[int], max_fee_per_gas: int) -> int:
    """Total ETH the gas wallet must hold for the rescue calls."""
    total_gas = sum(rescue_gas_limits)
    return int(total_gas * max_fee_per_gas * FUNDING_BUFFER)


def _victim_funding_value(
    prepared: list[PreparedAction],
    max_fee_per_gas: int,
    effective_fee_cap: int,
    sweep_gas: int = SWEEP_TX_GAS,
) -> int:
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    return rescue_cost + sweep_gas * effective_fee_cap


def _required_funding(
    prepared: list[PreparedAction],
    max_fee_per_gas: int,
    effective_fee_cap: int | None = None,
    needs_undelegation: bool = True,
    sweep_gas: int = SWEEP_TX_GAS,
) -> int:
    """Total ETH the gas wallet must hold for sponsor txs and victim funding."""
    if effective_fee_cap is None:
        effective_fee_cap = max_fee_per_gas
    victim_funding = _victim_funding_value(
        prepared, max_fee_per_gas, effective_fee_cap, sweep_gas
    )
    sponsor_gas = FUNDING_TX_GAS
    if needs_undelegation:
        sponsor_gas += UNDELEGATE_TX_GAS
    sponsor_cost = sponsor_gas * max_fee_per_gas
    return int((victim_funding + sponsor_cost) * FUNDING_BUFFER)


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
    signed = Account.sign_transaction(
        {
            "type": SET_CODE_TX_TYPE,
            "chainId": chain_id,
            "nonce": tx_nonce,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee_per_gas,
            "gas": UNDELEGATE_TX_GAS,
            "to": sponsor_address,
            "value": 0,
            "data": b"",
            "accessList": [],
            "authorizationList": [authorization],
        },
        sponsor_key,
    )
    return signed.raw_transaction


def has_7702_delegation(w3: Web3, address: str) -> bool:
    code = bytes(w3.eth.get_code(address))
    if not code:
        return False
    if len(code) == 23 and code.startswith(DELEGATION_DESIGNATOR_PREFIX):
        return True
    raise ValueError(f"Victim account {address} has unexpected non-EIP-7702 code")


def _sign_7702_rescue_tx(
    *,
    chain_id: int,
    tx_nonce: int,
    authorization: SignedSetCodeAuthorization,
    victim_address: str,
    sponsor_key: bytes,
    calldata: bytes,
    gas: int,
    priority_fee: int,
    max_fee_per_gas: int,
) -> HexBytes:
    signed = Account.sign_transaction(
        {
            "type": SET_CODE_TX_TYPE,
            "chainId": chain_id,
            "nonce": tx_nonce,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee_per_gas,
            "gas": gas,
            "to": victim_address,
            "value": 0,
            "data": calldata,
            "accessList": [],
            "authorizationList": [authorization],
        },
        sponsor_key,
    )
    return signed.raw_transaction


def _authorization_param(authorization: SignedSetCodeAuthorization) -> dict:
    return {
        "chainId": authorization.chain_id,
        "address": to_checksum_address(authorization.address),
        "nonce": authorization.nonce,
        "yParity": authorization.y_parity,
        "r": authorization.r,
        "s": authorization.s,
    }


def _estimated_rescue_gas(
    w3: Web3,
    sponsor: str,
    victim: str,
    rescue_contract: str,
    authorization: SignedSetCodeAuthorization,
    calldatas: list[bytes],
    budgets: list[int],
) -> tuple[list[int], list[str]]:
    """Raise each batch's budget to its whole-transaction estimate with margin.

    The first batch is estimated with its 7702 authorization. Later batches run
    after that delegation exists, so they are estimated with a state override
    giving the victim the delegation code; an RPC without override support just
    leaves them on the constant budget.
    """
    delegated = {
        victim: {
            "code": to_hex(
                DELEGATION_DESIGNATOR_PREFIX + to_canonical_address(rescue_contract)
            )
        }
    }
    limits: list[int] = []
    warnings: list[str] = []
    for index, (calldata, budget) in enumerate(zip(calldatas, budgets)):
        transaction = {"from": sponsor, "to": victim, "data": to_hex(calldata)}
        override = None
        if index == 0:
            transaction |= {
                "type": SET_CODE_TX_TYPE,
                "authorizationList": [_authorization_param(authorization)],
            }
        else:
            override = delegated
        try:
            estimate = w3.eth.estimate_gas(transaction, state_override=override)
        except Exception as e:  # unsupported type-4 or override, or a revert
            warnings.append(
                f"Could not estimate rescue transaction #{index + 1}; using the "
                f"fixed budget of {budget:,} gas: {e}"
            )
            limits.append(budget)
            continue
        raised = math.ceil(estimate * TX_ESTIMATE_MULTIPLIER)
        limits.append(min(MAX_TX_GAS, max(budget, raised)))
    return limits, warnings


def _signed_rescue_calldata(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    batch: list[PreparedAction],
    safe_address: str,
    deadline: int,
) -> bytes:
    request = RescueRequest(
        rescuer=gas.address,
        safe=safe_address,
        deadline=deadline,
        actions_hash=actions_hash(batch),
    )
    signature = sign_rescue_request(
        victim.key, w3.eth.chain_id, victim.address, request
    )
    return encode_rescue_call(request, batch, signature)


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
    """
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)
    priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)
    effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
    latest = w3.eth.get_block("latest")
    target_block = latest["number"] + TARGET_BLOCK_OFFSET
    deadline = latest["timestamp"] + RESCUE_REQUEST_TTL
    chain_id = w3.eth.chain_id

    new_account_gas = _new_account_gas(w3, gas.address, safe_address)
    base_gas = _rescue_base_gas(new_account_gas)
    batches = _rescue_batches(prepared, base_gas)

    transactions: list[BundleTransaction] = []
    rescue_nonce = gas_nonce

    authorization = Account.sign_authorization(
        {"chainId": chain_id, "address": rescue_contract, "nonce": victim_nonce},
        victim.key,
    )
    calldatas = [
        _signed_rescue_calldata(w3, victim, gas, batch, safe_address, deadline)
        for batch in batches
    ]
    gas_limits = [_batch_tx_gas(batch, base_gas) for batch in batches]
    gas_limits, warnings = _estimated_rescue_gas(
        w3,
        gas.address,
        victim.address,
        rescue_contract,
        authorization,
        calldatas,
        gas_limits,
    )

    transactions.append(
        BundleTransaction(
            "rescue-7702",
            _sign_7702_rescue_tx(
                chain_id=chain_id,
                tx_nonce=rescue_nonce,
                authorization=authorization,
                victim_address=victim.address,
                sponsor_key=gas.key,
                calldata=calldatas[0],
                gas=gas_limits[0],
                priority_fee=priority_fee,
                max_fee_per_gas=max_fee_per_gas,
            ),
            batch_start=0,
        )
    )
    # The delegation set by the first rescue tx is live for the rest of the
    # bundle, so continuation batches are plain sponsored calls to the victim.
    batch_start = len(batches[0])
    for offset in range(1, len(batches)):
        signed = Account.sign_transaction(
            {
                "chainId": chain_id,
                "nonce": rescue_nonce + offset,
                "maxPriorityFeePerGas": priority_fee,
                "maxFeePerGas": max_fee_per_gas,
                "gas": gas_limits[offset],
                "to": victim.address,
                "value": 0,
                "data": calldatas[offset],
            },
            gas.key,
        )
        transactions.append(
            BundleTransaction(
                "rescue-7702", signed.raw_transaction, batch_start=batch_start
            )
        )
        batch_start += len(batches[offset])

    return PreparedBundle(
        transactions=transactions,
        victim_nonce=victim_nonce,
        gas_nonce=gas_nonce,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=effective_fee_cap,
        target_block=target_block,
        required_funding=_required_funding_7702(gas_limits, max_fee_per_gas),
        victim_funding=0,
        sweep_value=0,
        expected_residual=0,
        rescue_contract=rescue_contract,
        warnings=tuple(warnings),
    )


def _build_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    priority_fee: int,
    max_fee_per_gas: int,
    effective_fee_cap: int,
    victim_nonce: int,
    gas_nonce: int,
    victim_balance: int,
    needs_undelegation: bool = True,
    sweep_gas: int = SWEEP_TX_GAS,
) -> list[BundleEntry]:
    chain_id = w3.eth.chain_id
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    rescue_fee_cap = sum(a["gas"] * effective_fee_cap for a in prepared)
    sweep_value = max(0, victim_balance + rescue_cost - rescue_fee_cap)

    nonce_offset = int(needs_undelegation)
    entries: list[BundleEntry] = []
    if needs_undelegation:
        undelegate_tx = _sign_7702_undelegation(
            chain_id=chain_id,
            tx_nonce=gas_nonce,
            authority_nonce=victim_nonce,
            authority_key=victim.key,
            sponsor_key=gas.key,
            sponsor_address=gas.address,
            priority_fee=priority_fee,
            max_fee_per_gas=max_fee_per_gas,
        )
        entries.append({"signed_transaction": undelegate_tx})
    funding_tx = {
        "to": victim.address,
        "value": _victim_funding_value(
            prepared, max_fee_per_gas, effective_fee_cap, sweep_gas
        ),
        "gas": FUNDING_TX_GAS,
        "maxFeePerGas": max_fee_per_gas,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": gas_nonce + nonce_offset,
        "chainId": chain_id,
    }
    rescue_txs = [
        {
            "to": a["to"],
            "data": a["data"],
            "gas": a["gas"],
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": priority_fee,
            "nonce": victim_nonce + nonce_offset + i,
            "chainId": chain_id,
        }
        for i, a in enumerate(prepared)
    ]
    sweep_tx = {
        "to": safe_address,
        "value": sweep_value,
        "gas": sweep_gas,
        "maxFeePerGas": effective_fee_cap,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": victim_nonce + nonce_offset + len(prepared),
        "chainId": chain_id,
    }
    signed = [w3.eth.account.sign_transaction(funding_tx, private_key=gas.key)]
    signed += [
        w3.eth.account.sign_transaction(tx, private_key=victim.key) for tx in rescue_txs
    ]
    if sweep_value:
        signed.append(w3.eth.account.sign_transaction(sweep_tx, private_key=victim.key))
    entries.extend({"signed_transaction": s.raw_transaction} for s in signed)
    return entries


def prepare_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: Decimal,
) -> PreparedBundle:
    """Build the legacy bundle (victim-signed transactions) for the next block."""
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)
    priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)
    effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
    target_block = w3.eth.block_number + TARGET_BLOCK_OFFSET
    victim_balance = w3.eth.get_balance(victim.address)
    needs_undelegation = has_7702_delegation(w3, victim.address)
    sweep_gas = SWEEP_TX_GAS + _new_account_gas(w3, gas.address, safe_address)
    entries = _build_bundle(
        w3,
        victim,
        gas,
        prepared,
        safe_address,
        priority_fee,
        max_fee_per_gas,
        effective_fee_cap,
        victim_nonce,
        gas_nonce,
        victim_balance,
        needs_undelegation,
        sweep_gas,
    )
    victim_funding = _victim_funding_value(
        prepared, max_fee_per_gas, effective_fee_cap, sweep_gas
    )
    rescue_cost = sum(action["gas"] * max_fee_per_gas for action in prepared)
    rescue_fee_cap = sum(action["gas"] * effective_fee_cap for action in prepared)
    sweep_value = max(0, victim_balance + rescue_cost - rescue_fee_cap)
    roles: list[BundleTransaction] = []
    entry_index = 0
    if needs_undelegation:
        roles.append(BundleTransaction("undelegate", entries[0]["signed_transaction"]))
        entry_index = 1
    roles.append(BundleTransaction("fund", entries[entry_index]["signed_transaction"]))
    entry_index += 1
    for action_index, entry in enumerate(
        entries[entry_index : entry_index + len(prepared)]
    ):
        roles.append(
            BundleTransaction(
                "rescue", entry["signed_transaction"], action_index=action_index
            )
        )
    entry_index += len(prepared)
    if len(entries) > entry_index:
        roles.append(BundleTransaction("sweep", entries[-1]["signed_transaction"]))
    return PreparedBundle(
        transactions=roles,
        victim_nonce=victim_nonce,
        gas_nonce=gas_nonce,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=effective_fee_cap,
        target_block=target_block,
        required_funding=_required_funding(
            prepared,
            max_fee_per_gas,
            effective_fee_cap,
            needs_undelegation,
            sweep_gas,
        ),
        victim_funding=victim_funding,
        sweep_value=sweep_value,
        expected_residual=0,
    )
