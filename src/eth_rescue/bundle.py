from collections.abc import Callable
from decimal import Decimal

from eth_account.account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import to_checksum_address, to_hex
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue.calldata import build_calldata
from eth_rescue.contract import (
    build_deploy_data,
    compute_create_address,
    encode_rescue_call,
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
# Intrinsic + authorization + dispatch + sweep CALL, excluding new-account
# creation (charged separately). Measured ~46k Osaka (+25k fresh safe), ~86k
# Amsterdam.
RESCUE_TX_FIXED_GAS = 150_000
# Cold call, ActionResult event and loop cost outside each stipend; ~3k measured
# on Osaka and Amsterdam. Calldata is budgeted at the 16 gas/byte upper bound.
RESCUE_ACTION_OVERHEAD_GAS = 6_000
CALLDATA_GAS_PER_BYTE = 16
# Used only when no funded account can estimate a transfer to an empty safe;
# covers Amsterdam's 120 * CPSB = 183,600 new-account state gas.
NEW_ACCOUNT_GAS_FALLBACK = 250_000
ESTIMATE_MARGIN_NUMERATOR, ESTIMATE_MARGIN_DENOMINATOR = 3, 2  # 1.5x
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
    # offset + 4 head words + bytes length, then the padded bytes payload
    encoded_length = 6 * 32 + -(-data_length // 32) * 32
    return (
        action["gas"]
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
    w3: Web3, sender: str, prepared: list[PreparedAction]
) -> tuple[list[PreparedAction], dict[int, str]]:
    """Raise each action's gas limit to its live estimate with margin.

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
        margin = estimate * ESTIMATE_MARGIN_NUMERATOR // ESTIMATE_MARGIN_DENOMINATOR
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


def _deploy_tx_gas(w3: Web3, deploy_data: bytes) -> int:
    """The deploy cost is a pure function of the initcode and the fork's code
    deposit pricing, so the node's estimate is exact; there is no fallback
    because EIP-8037 makes any fixed guess dangerously low."""
    return int(w3.eth.estimate_gas({"data": to_hex(deploy_data)}) * 1.1)


def _rescue_base_gas(new_account_gas: int) -> int:
    return RESCUE_TX_FIXED_GAS + new_account_gas


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


def _required_funding_7702(
    deploy_gas: int, prepared: list[PreparedAction], max_fee_per_gas: int, base_gas: int
) -> int:
    """Total ETH the gas wallet must hold for the deploy + rescue call txs."""
    total_gas = deploy_gas + sum(
        _batch_tx_gas(batch, base_gas) for batch in _rescue_batches(prepared, base_gas)
    )
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


def _sign_deploy_tx(
    *,
    chain_id: int,
    tx_nonce: int,
    deploy_data: bytes,
    deploy_gas: int,
    sponsor_key: bytes,
    priority_fee: int,
    max_fee_per_gas: int,
) -> HexBytes:
    signed = Account.sign_transaction(
        {
            "chainId": chain_id,
            "nonce": tx_nonce,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee_per_gas,
            "gas": deploy_gas,
            "value": 0,
            "data": deploy_data,
        },
        sponsor_key,
    )
    return signed.raw_transaction


def _sign_7702_rescue_tx(
    *,
    chain_id: int,
    tx_nonce: int,
    victim_nonce: int,
    victim_key: bytes,
    victim_address: str,
    sponsor_key: bytes,
    rescue_contract: str,
    calldata: bytes,
    gas: int,
    priority_fee: int,
    max_fee_per_gas: int,
) -> HexBytes:
    authorization = Account.sign_authorization(
        {
            "chainId": chain_id,
            "address": rescue_contract,
            "nonce": victim_nonce,
        },
        victim_key,
    )
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


def prepare_7702_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: Decimal,
) -> PreparedBundle:
    """Build the phase-1 bundle: deploy EthRescue, then rescue everything
    through the victim's delegation in one sponsored call per batch.

    All actions are optional inside rescue(): if one fails mid-race the call
    still succeeds, the bundle stays valid, and the full ETH balance is swept.
    """
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)
    priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)
    effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
    target_block = w3.eth.block_number + TARGET_BLOCK_OFFSET
    chain_id = w3.eth.chain_id

    rescue_contract = compute_create_address(gas.address, gas_nonce)
    deploy_data = build_deploy_data(gas.address, safe_address)
    deploy_gas = _deploy_tx_gas(w3, deploy_data)
    base_gas = _rescue_base_gas(_new_account_gas(w3, gas.address, safe_address))

    transactions: list[BundleTransaction] = [
        BundleTransaction(
            "deploy",
            _sign_deploy_tx(
                chain_id=chain_id,
                tx_nonce=gas_nonce,
                deploy_data=deploy_data,
                deploy_gas=deploy_gas,
                sponsor_key=gas.key,
                priority_fee=priority_fee,
                max_fee_per_gas=max_fee_per_gas,
            ),
        )
    ]
    batches = _rescue_batches(prepared, base_gas)
    transactions.append(
        BundleTransaction(
            "rescue-7702",
            _sign_7702_rescue_tx(
                chain_id=chain_id,
                tx_nonce=gas_nonce + 1,
                victim_nonce=victim_nonce,
                victim_key=victim.key,
                victim_address=victim.address,
                sponsor_key=gas.key,
                rescue_contract=rescue_contract,
                calldata=encode_rescue_call(batches[0]),
                gas=_batch_tx_gas(batches[0], base_gas),
                priority_fee=priority_fee,
                max_fee_per_gas=max_fee_per_gas,
            ),
            batch_start=0,
        )
    )
    # The delegation set by the first rescue tx is live for the rest of the
    # bundle, so continuation batches are plain sponsored calls to the victim.
    batch_start = len(batches[0])
    for offset, batch in enumerate(batches[1:], start=2):
        signed = Account.sign_transaction(
            {
                "chainId": chain_id,
                "nonce": gas_nonce + offset,
                "maxPriorityFeePerGas": priority_fee,
                "maxFeePerGas": max_fee_per_gas,
                "gas": _batch_tx_gas(batch, base_gas),
                "to": victim.address,
                "value": 0,
                "data": encode_rescue_call(batch),
            },
            gas.key,
        )
        transactions.append(
            BundleTransaction(
                "rescue-7702", signed.raw_transaction, batch_start=batch_start
            )
        )
        batch_start += len(batch)

    return PreparedBundle(
        transactions=transactions,
        victim_nonce=victim_nonce,
        gas_nonce=gas_nonce,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=effective_fee_cap,
        target_block=target_block,
        required_funding=_required_funding_7702(
            deploy_gas, prepared, max_fee_per_gas, base_gas
        ),
        victim_funding=0,
        sweep_value=0,
        expected_residual=0,
        rescue_contract=rescue_contract,
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
