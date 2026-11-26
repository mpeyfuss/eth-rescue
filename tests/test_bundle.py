import json
import math
from decimal import Decimal
from types import SimpleNamespace

import pytest
from web3.exceptions import ContractLogicError
from eth_abi import decode, encode as abi_encode
from eth_account.account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue import ui, bundle, contract
from eth_rescue.templates import GAS_GENERIC

DESTINATION = Web3.to_checksum_address("0x" + "22" * 20)
TARGET = "0x000000000000000000000000000000000000dEaD"
RESCUE_CONTRACT = "0x" + "55" * 20
GWEI = 10**9


def _action(gas=70_000, data="0xdeadbeef"):
    return {"to": TARGET, "data": data, "gas": gas}


def _decode(signed_transaction):
    return TypedTransaction.from_bytes(HexBytes(signed_transaction)).as_dict()


def _decode_rescue_call(data):
    assert bytes(data[:4]) == contract.RESCUE_SELECTOR
    return decode(
        [contract.REQUEST_TYPE, contract.ACTIONS_TYPE, "bytes"], bytes(data[4:])
    )


class FakeChain:
    """Just enough of web3.eth for the bundle builders; records every request
    that would reach the public RPC."""

    def __init__(self, compromised=None, gas=None, base_fee=10 * GWEI):
        self.chain_id = 1
        self.gas_limit = 60_000_000
        self.max_priority_fee = 1 * GWEI
        self.base_fee = base_fee
        self.nonces = {}
        self.balances = {}
        self.code = {}
        self.estimate_result = 10_000
        self.call_result = None  # None: every action succeeds
        self.requests = []
        if compromised:
            self.nonces[compromised.address] = 7
        if gas:
            self.nonces[gas.address] = 3

    def get_block(self, tag):
        assert tag == "latest"
        return {
            "number": 100,
            "timestamp": 1_000,
            "baseFeePerGas": self.base_fee,
            "gasLimit": self.gas_limit,
        }

    def get_transaction_count(self, address):
        return self.nonces.get(address, 0)

    def get_balance(self, address):
        return self.balances.get(address, 0)

    def get_code(self, address, block_identifier="latest"):
        return self.code.get(address, b"")

    def estimate_gas(self, transaction, state_override=None):
        self.requests.append(("estimate", transaction, state_override))
        result = self.estimate_result
        if callable(result):
            result = result(transaction, state_override)
        if isinstance(result, Exception):
            raise result
        return result

    def call(self, transaction, block_identifier=None, state_override=None):
        self.requests.append(("call", transaction, state_override))
        if isinstance(self.call_result, Exception):
            raise self.call_result
        _request, actions, _signature = _decode_rescue_call(
            HexBytes(transaction["data"])
        )
        results = self.call_result or [True] * len(actions)
        return abi_encode(["bool[]"], [results])


def _w3(chain):
    return SimpleNamespace(eth=chain, to_wei=Web3.to_wei)


@pytest.fixture
def accounts():
    return Account.create(), Account.create()


@pytest.fixture
def chain(accounts):
    compromised, gas = accounts
    return FakeChain(compromised, gas)


def _prepare_7702(chain, accounts, actions=None, destination=DESTINATION):
    compromised, gas = accounts
    return bundle.prepare_7702_bundle(
        _w3(chain),
        compromised,
        gas,
        actions or [_action()],
        destination,
        Decimal(0),
        rescue_contract=RESCUE_CONTRACT,
    )


def _prepare_legacy(chain, accounts, actions=None, destination=DESTINATION):
    compromised, gas = accounts
    return bundle.prepare_bundle(
        _w3(chain), compromised, gas, actions or [_action()], destination, Decimal(0)
    )


# ---------------------------------------------------------------------------
# fees
# ---------------------------------------------------------------------------
def test_compute_fees_adds_extra_priority_fee():
    chain = FakeChain()
    chain.max_priority_fee = 1_500_000_000
    latest = {"baseFeePerGas": 10 * GWEI}

    priority_fee, max_fee = bundle._compute_fees(_w3(chain), latest, Decimal("2.25"))

    assert priority_fee == 3_750_000_000
    assert max_fee == 28_750_000_000


def test_compute_fees_rejects_negative_extra_priority_fee():
    with pytest.raises(ValueError, match="cannot be negative"):
        bundle._compute_fees(_w3(FakeChain()), {"baseFeePerGas": 1}, Decimal("-0.01"))


def test_window_fee_cap_covers_base_fee_rising_every_block_of_the_window():
    base = 10 * GWEI
    cap = bundle._window_fee_cap(base, priority_fee=GWEI, max_fee_per_gas=10**12)

    worst_base = base
    for _ in range(bundle.BUNDLE_BLOCK_RANGE):
        worst_base += max(worst_base // 8, 1)
    assert cap >= worst_base + GWEI


def test_window_fee_cap_never_exceeds_max_fee_even_at_zero_base_fee():
    assert bundle._window_fee_cap(0, priority_fee=5, max_fee_per_gas=5) == 5


def test_calldata_floor_gas_prices_zero_and_nonzero_bytes():
    assert bundle.calldata_floor_gas(b"\x00\x01\x02") == 21_000 + 10 + 2 * 40
    assert bundle.calldata_floor_gas(b"", authorizations=1) == 46_000


# ---------------------------------------------------------------------------
# actions and batches
# ---------------------------------------------------------------------------
def test_prepare_actions_uses_fixed_gas_without_rpc():
    rescue_data = [
        {
            "address": TARGET,
            "function_signature": "transfer(address,uint256)",
            "args": ["0x000000000000000000000000000000000000BEEF", 5],
            "gas_estimate": 70_000,
        },
        {
            "address": TARGET,
            "function_signature": "transfer(address,uint256)",
            "args": ["0x000000000000000000000000000000000000BEEF", 5],
        },
    ]

    prepared = bundle.prepare_actions(rescue_data)

    assert [action["gas"] for action in prepared] == [70_000, GAS_GENERIC]


def test_prepare_actions_rejects_action_over_transaction_gas_cap():
    rescue_data = [
        {
            "address": TARGET,
            "function_signature": "f()",
            "args": [],
            "gas_estimate": bundle.MAX_TX_GAS,
        }
    ]

    with pytest.raises(ValueError, match="Action #1 needs more gas"):
        bundle.prepare_actions(rescue_data)


def test_action_tx_gas_covers_stipend_overhead_and_calldata_upper_bound():
    action = _action(gas=50_000, data="0x" + "ab" * 33)

    expected_calldata = bundle.CALLDATA_GAS_PER_BYTE * (5 * 32 + 64)
    assert bundle._action_tx_gas(action) == (
        50_000 + 50_000 // 63 + bundle.RESCUE_ACTION_OVERHEAD_GAS + expected_calldata
    )


def test_batch_budget_covers_the_eip_7623_calldata_floor():
    actions = [_action(gas=1, data="0x" + "ff" * 2_000) for _ in range(5)]
    encoded = abi_encode([contract.ACTIONS_TYPE], [contract._abi_actions(actions)])

    budget = bundle._batch_tx_gas(actions, bundle._rescue_base_gas(0))

    assert budget >= bundle.calldata_floor_gas(encoded, authorizations=1)


def test_rescue_batches_split_at_action_limit():
    actions = [_action() for _ in range(bundle.MAX_ACTIONS_PER_RESCUE + 1)]

    batches = bundle._rescue_batches(actions, bundle.RESCUE_TX_FIXED_GAS)

    assert [len(batch) for batch in batches] == [bundle.MAX_ACTIONS_PER_RESCUE, 1]


def test_rescue_batches_split_at_transaction_gas_cap():
    actions = [_action(gas=4_000_000) for _ in range(5)]

    batches = bundle._rescue_batches(actions, bundle.RESCUE_TX_FIXED_GAS)

    assert [len(batch) for batch in batches] == [4, 1]
    assert all(
        bundle._batch_tx_gas(batch, bundle.RESCUE_TX_FIXED_GAS) <= bundle.MAX_TX_GAS
        for batch in batches
    )


def test_rescue_batches_reject_action_that_cannot_fit_one_transaction():
    with pytest.raises(ValueError, match="Action #2 needs more gas"):
        bundle._rescue_batches(
            [_action(), _action(gas=bundle.MAX_ACTION_GAS)],
            bundle.MAX_TX_GAS - 5_000_000,
        )


# ---------------------------------------------------------------------------
# size_actions
# ---------------------------------------------------------------------------
def _sizing_chain(estimates):
    chain = FakeChain()
    chain.estimate_result = lambda tx, override: estimates[tx["to"]]
    return chain


def test_size_actions_raises_limits_to_estimate_with_margin():
    chain = _sizing_chain({"heavy": 140_000, "light": 30_000})
    actions = [
        {"to": "heavy", "data": "0x01", "gas": 75_000},
        {"to": "light", "data": "0x02", "gas": 75_000},
    ]

    sized, failures = bundle.size_actions(_w3(chain), "compromised", actions)

    assert [action["gas"] for action in sized] == [210_000, 75_000]
    assert failures == {}


def test_size_actions_estimates_the_compromised_without_any_delegation():
    chain = _sizing_chain({"heavy": 140_000})

    bundle.size_actions(
        _w3(chain), "compromised", [{"to": "heavy", "data": "0x", "gas": 1}]
    )

    [(_kind, transaction, override)] = chain.requests
    assert transaction == {"from": "compromised", "to": "heavy", "data": "0x"}
    assert override == {"compromised": {"code": "0x"}}


def test_size_actions_retries_without_override_when_rpc_refuses_it():
    chain = FakeChain()
    chain.estimate_result = lambda tx, override: (
        ValueError("override unsupported") if override else 90_000
    )

    sized, failures = bundle.size_actions(
        _w3(chain), "compromised", [{"to": "t", "data": "0x", "gas": 1}]
    )

    assert sized[0]["gas"] == 135_000
    assert failures == {}


def test_size_actions_never_estimates_through_an_existing_delegation():
    chain = FakeChain()
    chain.code["compromised"] = b"\xef\x01\x00" + b"\x99" * 20
    chain.estimate_result = lambda tx, override: (
        ValueError("override unsupported") if override else 7_000_000
    )

    sized, failures = bundle.size_actions(
        _w3(chain), "compromised", [{"to": "t", "data": "0x", "gas": 75_000}]
    )

    assert sized[0]["gas"] == 75_000
    assert failures == {0: "override unsupported"}
    assert [override for _kind, _tx, override in chain.requests] == [
        {"compromised": {"code": "0x"}}
    ]


def test_size_actions_does_not_rerun_a_revert_without_the_override():
    chain = FakeChain()
    chain.estimate_result = lambda tx, override: (
        ContractLogicError("execution reverted") if override else 7_000_000
    )

    sized, failures = bundle.size_actions(
        _w3(chain), "compromised", [{"to": "t", "data": "0x", "gas": 75_000}]
    )

    assert sized[0]["gas"] == 75_000
    assert 0 in failures
    assert len(chain.requests) == 1


def test_size_actions_keeps_floor_and_reports_failed_estimates():
    chain = _sizing_chain({"gone": RuntimeError("execution reverted: wrong owner")})

    sized, failures = bundle.size_actions(
        _w3(chain), "compromised", [{"to": "gone", "data": "0x", "gas": 75_000}]
    )

    assert sized[0]["gas"] == 75_000
    assert failures == {0: "execution reverted: wrong owner"}


def test_size_actions_caps_runaway_estimates():
    chain = _sizing_chain({"huge": 100_000_000})

    sized, _ = bundle.size_actions(
        _w3(chain), "compromised", [{"to": "huge", "data": "0x", "gas": 1}]
    )

    assert sized[0]["gas"] == bundle.MAX_ACTION_GAS


def test_size_actions_applies_custom_multiplier_rounding_up():
    chain = _sizing_chain({"heavy": 100_001})

    sized, _ = bundle.size_actions(
        _w3(chain),
        "compromised",
        [{"to": "heavy", "data": "0x", "gas": 1}],
        Decimal("2.5"),
    )

    assert sized[0]["gas"] == 250_003


# ---------------------------------------------------------------------------
# destination_receive_gas
# ---------------------------------------------------------------------------
def test_destination_receive_gas_is_zero_for_existing_eoa():
    chain = FakeChain()
    chain.balances[DESTINATION] = 1

    assert bundle.destination_receive_gas(_w3(chain), DESTINATION) == 0
    assert chain.requests == []


def test_destination_receive_gas_uses_funded_probe_estimate_for_empty_destination():
    chain = FakeChain()
    chain.estimate_result = 204_600

    assert bundle.destination_receive_gas(_w3(chain), DESTINATION) == 183_600
    [(_kind, transaction, override)] = chain.requests
    assert transaction["to"] == DESTINATION and transaction["value"] == 1
    assert override == {transaction["from"]: {"balance": hex(bundle.PROBE_BALANCE)}}


def test_destination_receive_gas_falls_back_for_empty_destination_without_estimate():
    chain = FakeChain()
    chain.estimate_result = ValueError("insufficient funds")

    assert (
        bundle.destination_receive_gas(_w3(chain), DESTINATION)
        == bundle.NEW_ACCOUNT_GAS_FALLBACK
    )


def test_destination_receive_gas_gives_contract_destination_execution_gas():
    chain = FakeChain()
    chain.code[DESTINATION] = b"\x60\x00"
    chain.estimate_result = ValueError("no estimate")

    assert (
        bundle.destination_receive_gas(_w3(chain), DESTINATION) == bundle.SWEEP_CALL_GAS
    )

    chain.estimate_result = 21_000 + 60_000
    assert bundle.destination_receive_gas(_w3(chain), DESTINATION) == 90_000


# ---------------------------------------------------------------------------
# prepare_7702_bundle
# ---------------------------------------------------------------------------
def test_prepare_7702_bundle_shape_and_nonces(chain, accounts):
    compromised, gas = accounts

    prepared = _prepare_7702(chain, accounts)

    [transaction] = prepared.transactions
    assert transaction.role == "rescue-7702"
    assert (transaction.batch_start, transaction.batch_size) == (0, 1)
    assert (prepared.compromised_nonce, prepared.gas_nonce) == (7, 3)
    assert prepared.target_block == 101
    decoded = _decode(transaction.signed_transaction)
    assert decoded["nonce"] == 3
    assert decoded["to"] == HexBytes(compromised.address)
    [authorization] = decoded["authorizationList"]
    assert authorization["nonce"] == 7
    assert authorization["address"] == HexBytes(RESCUE_CONTRACT)
    assert Account.recover_transaction(transaction.signed_transaction) == gas.address


def test_prepare_7702_bundle_signs_request_bound_to_gas_wallet(chain, accounts):
    _compromised, gas = accounts
    action = _action()

    prepared = _prepare_7702(chain, accounts, [action])

    data = _decode(prepared.transactions[0].signed_transaction)["data"]
    (rescuer, destination, deadline, signed_hash), actions, signature = (
        _decode_rescue_call(data)
    )
    assert Web3.to_checksum_address(rescuer) == gas.address
    assert Web3.to_checksum_address(destination) == DESTINATION
    assert deadline == 1_000 + bundle.RESCUE_REQUEST_TTL
    assert signed_hash == contract.actions_hash([action])
    assert len(signature) == 65


def test_public_rpc_never_sees_the_real_authorization_or_request_signature(
    chain, accounts
):
    actions = [_action() for _ in range(bundle.MAX_ACTIONS_PER_RESCUE + 1)]

    prepared = _prepare_7702(chain, accounts, actions)

    sent = json.dumps(chain.requests, default=lambda value: HexBytes(value).hex())
    assert "authorizationList" not in sent
    for transaction in prepared.transactions:
        decoded = _decode(transaction.signed_transaction)
        _request, _actions, signature = _decode_rescue_call(decoded["data"])
        assert signature.hex() not in sent
        for authorization in decoded.get("authorizationList", []):
            assert format(authorization["r"], "064x") not in sent
            assert format(authorization["s"], "064x") not in sent


def test_dry_runs_use_keyless_rescuer_and_delegation_override(chain, accounts):
    compromised, gas = accounts

    _prepare_7702(chain, accounts)

    designator = (
        "0x"
        + (
            bundle.DELEGATION_DESIGNATOR_PREFIX + bytes.fromhex(RESCUE_CONTRACT[2:])
        ).hex()
    )
    dry_runs = [request for request in chain.requests if "data" in request[1]]
    assert {kind for kind, _tx, _override in dry_runs} == {"call", "estimate"}
    for _kind, transaction, override in dry_runs:
        assert override == {compromised.address: {"code": designator}}
        assert transaction["from"] != gas.address
        (rescuer, *_), _actions, _sig = _decode_rescue_call(
            HexBytes(transaction["data"])
        )
        assert Web3.to_checksum_address(rescuer) == transaction["from"]


def test_prepare_7702_bundle_reports_predicted_action_results(chain, accounts):
    chain.call_result = [True, False]

    prepared = _prepare_7702(chain, accounts, [_action(), _action()])

    assert prepared.predicted == (True, False)


def test_prepare_7702_bundle_chunks_actions_and_tracks_batches(chain, accounts):
    actions = [_action() for _ in range(bundle.MAX_ACTIONS_PER_RESCUE + 1)]

    prepared = _prepare_7702(chain, accounts, actions)

    assert [(t.batch_start, t.batch_size) for t in prepared.transactions] == [
        (0, bundle.MAX_ACTIONS_PER_RESCUE),
        (bundle.MAX_ACTIONS_PER_RESCUE, 1),
    ]
    decoded = [_decode(t.signed_transaction) for t in prepared.transactions]
    assert [tx["nonce"] for tx in decoded] == [3, 4]
    assert "authorizationList" in decoded[0]
    assert "authorizationList" not in decoded[1]
    first, second = (_decode_rescue_call(tx["data"]) for tx in decoded)
    assert first[0][3] == contract.actions_hash(actions[:-1])
    assert second[0][3] == contract.actions_hash(actions[-1:])
    assert len(prepared.predicted) == len(actions)


def test_prepare_7702_bundle_keeps_budget_when_estimate_is_lower(chain, accounts):
    prepared = _prepare_7702(chain, accounts)

    budget = bundle._batch_tx_gas(
        [_action()], bundle._rescue_base_gas(bundle.NEW_ACCOUNT_GAS_FALLBACK - 0)
    )
    gas_limit = _decode(prepared.transactions[0].signed_transaction)["gas"]
    assert gas_limit <= budget
    assert prepared.warnings == ()


def test_prepare_7702_bundle_raises_limit_to_estimate_plus_authorization(
    chain, accounts
):
    chain.estimate_result = lambda tx, override: 1_000_000 if "data" in tx else 21_000

    prepared = _prepare_7702(chain, accounts)

    expected = math.ceil((1_000_000 + bundle.AUTHORIZATION_GAS) * 1.5)
    assert _decode(prepared.transactions[0].signed_transaction)["gas"] == expected
    assert prepared.required_funding == bundle._required_funding_7702(
        [expected], prepared.max_fee_per_gas
    )


def test_prepare_7702_bundle_estimate_never_exceeds_transaction_cap(chain, accounts):
    chain.balances[DESTINATION] = 1
    chain.estimate_result = bundle.MAX_TX_GAS

    prepared = _prepare_7702(chain, accounts)

    gas_limit = _decode(prepared.transactions[0].signed_transaction)["gas"]
    assert gas_limit == bundle.MAX_TX_GAS


def test_prepare_7702_bundle_warns_and_keeps_budget_when_dry_run_fails(chain, accounts):
    chain.call_result = ValueError("state override not supported")

    prepared = _prepare_7702(chain, accounts)

    [warning] = prepared.warnings
    assert "rescue transaction #1" in warning
    assert "state override not supported" in warning
    assert prepared.predicted == (None,)


def test_prepare_7702_bundle_refuses_bundle_over_relay_size_limit(chain, accounts):
    actions = [_action(gas=1, data="0x" + "ff" * 3_000) for _ in range(110)]

    with pytest.raises(bundle.BundleLimitError, match="300,000"):
        _prepare_7702(chain, accounts, actions)


# ---------------------------------------------------------------------------
# prepare_bundle (legacy fallback)
# ---------------------------------------------------------------------------
def test_prepare_bundle_shape_fees_and_nonces(chain, accounts):
    compromised, gas = accounts
    chain.code[compromised.address] = bundle.DELEGATION_DESIGNATOR_PREFIX + b"\x55" * 20
    chain.balances[compromised.address] = 10**18
    chain.balances[DESTINATION] = 1
    actions = [_action(gas=50_000), _action(gas=70_000)]

    prepared = _prepare_legacy(chain, accounts, actions)

    assert [t.role for t in prepared.transactions] == [
        "undelegate",
        "fund",
        "rescue",
        "rescue",
        "sweep",
    ]
    assert [t.action_index for t in prepared.transactions] == [None, None, 0, 1, None]
    _undelegate, fund, first, second, sweep = (
        _decode(t.signed_transaction) for t in prepared.transactions
    )
    assert _undelegate["nonce"] == 3
    assert _undelegate["authorizationList"][0]["nonce"] == 7
    assert _undelegate["authorizationList"][0]["address"] == bytes(20)
    assert [fund["nonce"], first["nonce"], second["nonce"], sweep["nonce"]] == [
        4,
        8,
        9,
        10,
    ]
    cap = prepared.effective_fee_cap
    assert fund["value"] == 120_000 * prepared.max_fee_per_gas + 21_000 * cap
    assert sweep["maxFeePerGas"] == cap
    assert sweep["value"] == 10**18 + 120_000 * (prepared.max_fee_per_gas - cap)
    assert Account.recover_transaction(prepared.transactions[1].signed_transaction) == (
        gas.address
    )
    assert Account.recover_transaction(prepared.transactions[2].signed_transaction) == (
        compromised.address
    )


def test_prepare_bundle_skips_undelegation_and_zero_sweep(chain, accounts):
    chain.balances[DESTINATION] = 1
    chain.base_fee = 0
    chain.max_priority_fee = 0

    prepared = _prepare_legacy(chain, accounts)

    assert [t.role for t in prepared.transactions] == ["fund", "rescue"]


def test_prepare_bundle_gives_contract_destination_sweep_execution_gas(chain, accounts):
    compromised, _gas = accounts
    chain.balances[compromised.address] = 10**18
    chain.code[DESTINATION] = b"\x60\x00"
    chain.estimate_result = ValueError("no estimate")

    prepared = _prepare_legacy(chain, accounts)

    sweep = _decode(prepared.transactions[-1].signed_transaction)
    assert sweep["gas"] == bundle.SWEEP_TX_GAS + bundle.SWEEP_CALL_GAS


def test_prepare_bundle_raises_gas_to_the_calldata_floor(chain, accounts):
    chain.balances[DESTINATION] = 1
    data = "0x" + "ff" * 10_000

    prepared = _prepare_legacy(chain, accounts, [_action(gas=25_000, data=data)])

    rescue = _decode(prepared.transactions[1].signed_transaction)
    assert rescue["gas"] == bundle.calldata_floor_gas(bytes.fromhex(data[2:]))


def test_check_bundle_limits_counts_transactions():
    transactions = [
        bundle.BundleTransaction("rescue", b"\x01")
        for _ in range(bundle.MAX_BUNDLE_TRANSACTIONS + 1)
    ]

    with pytest.raises(bundle.BundleLimitError, match="101 transactions"):
        bundle.check_bundle_limits(transactions)


def test_required_funding_accounts_for_optional_undelegation():
    max_fee, effective_fee = 20, 10
    compromised_funding = 50_000 * max_fee + bundle.SWEEP_TX_GAS * effective_fee

    plain = bundle._required_funding([50_000], max_fee, effective_fee, False)
    delegated = bundle._required_funding([50_000], max_fee, effective_fee, True)

    assert plain == int(
        (compromised_funding + bundle.FUNDING_TX_GAS * max_fee) * bundle.FUNDING_BUFFER
    )
    assert delegated == int(
        (
            compromised_funding
            + (bundle.FUNDING_TX_GAS + bundle.UNDELEGATE_TX_GAS) * max_fee
        )
        * bundle.FUNDING_BUFFER
    )


def test_required_funding_7702_covers_every_batch():
    assert bundle._required_funding_7702([300_000, 200_000], 20) == int(
        500_000 * 20 * bundle.FUNDING_BUFFER
    )


# ---------------------------------------------------------------------------
# delegation
# ---------------------------------------------------------------------------
def test_delegation_target_recognizes_only_delegation_designator():
    def target(code):
        chain = FakeChain()
        chain.code["a"] = code
        return bundle.delegation_target(_w3(chain), "a")

    assert target(b"") is None
    assert target(b"\xef\x01\x00" + b"\x11" * 20) == Web3.to_checksum_address(
        "0x" + "11" * 20
    )
    with pytest.raises(ValueError, match="unexpected non-EIP-7702 code"):
        target(b"\x60\x00")


def test_sign_7702_undelegation_builds_type_4_clear_authorization():
    compromised = Account.create()
    gas = Account.create()

    raw_tx = bundle._sign_7702_undelegation(
        chain_id=1,
        tx_nonce=3,
        authority_nonce=9,
        authority_key=compromised.key,
        sponsor_key=gas.key,
        sponsor_address=gas.address,
        priority_fee=1_000_000_000,
        max_fee_per_gas=20_000_000_000,
    )

    assert raw_tx[0] == bundle.SET_CODE_TX_TYPE
    transaction = _decode(raw_tx)
    authorization = transaction["authorizationList"][0]
    assert transaction["nonce"] == 3
    assert transaction["to"] == HexBytes(gas.address)
    assert transaction["gas"] == bundle.UNDELEGATE_TX_GAS
    assert authorization["address"] == HexBytes(bundle.ZERO_ADDRESS)
    assert authorization["nonce"] == 9
    assert Account.recover_transaction(raw_tx) == gas.address


def test_compute_fees_scales_the_base_fee_exactly():
    chain = FakeChain()
    chain.max_priority_fee = 0
    latest = {"baseFeePerGas": 10**30 + 3}

    _priority_fee, max_fee = bundle._compute_fees(_w3(chain), latest, Decimal(0))

    assert max_fee == 2 * ((10**30 + 3) * 5 // 4)


def test_check_bundle_limits_refuses_more_gas_than_a_block_holds():
    transactions = [bundle.BundleTransaction("rescue", b"\x01")]

    with pytest.raises(bundle.BundleLimitError, match="more than a block holds"):
        bundle.check_bundle_limits(transactions, 30_000_001, 30_000_000)
    bundle.check_bundle_limits(transactions, 30_000_000, 30_000_000)


def test_prepare_7702_bundle_refuses_gas_beyond_the_block_limit(chain, accounts):
    chain.gas_limit = 1_000_000
    actions = [_action(gas=400_000) for _ in range(3)]

    with pytest.raises(bundle.BundleLimitError, match="more than a block holds"):
        _prepare_7702(chain, accounts, actions)


def test_prepare_bundle_refuses_gas_beyond_the_block_limit(chain, accounts):
    chain.balances[DESTINATION] = 1
    chain.gas_limit = 100_000

    with pytest.raises(bundle.BundleLimitError, match="more than a block holds"):
        _prepare_legacy(chain, accounts, [_action(gas=70_000)])


def test_prepare_bundle_warns_how_much_unused_gas_can_be_left_behind(chain, accounts):
    chain.balances[DESTINATION] = 1

    prepared = _prepare_legacy(chain, accounts, [_action(gas=50_000)])

    [warning] = prepared.warnings
    reserve = (50_000 + bundle.SWEEP_TX_GAS) * prepared.effective_fee_cap
    assert f"up to {ui.ether(reserve, round_up=True)}," in warning


def test_only_the_first_rescue_transaction_pays_for_the_authorization(chain, accounts):
    chain.balances[DESTINATION] = 1
    chain.estimate_result = 8_000_000
    actions = [_action(gas=21_000) for _ in range(bundle.MAX_ACTIONS_PER_RESCUE + 1)]

    prepared = _prepare_7702(chain, accounts, actions)

    first, second = (_decode(t.signed_transaction) for t in prepared.transactions)
    multiplier = bundle.TX_ESTIMATE_MULTIPLIER
    assert first["gas"] == math.ceil(
        (8_000_000 + bundle.AUTHORIZATION_GAS) * multiplier
    )
    assert second["gas"] == math.ceil(8_000_000 * multiplier)
