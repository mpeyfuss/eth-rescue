from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_account.account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue import bundle
from eth_rescue.contract import encode_rescue_call
from eth_rescue.templates import GAS_GENERIC

SAFE = "0x" + "22" * 20


class FakeSigner:
    def __init__(self, address, key):
        self.address = address
        self.key = key


class FakeAccount:
    def __init__(self):
        self.signed_txs = []

    def sign_transaction(self, tx, private_key=None):
        self.signed_txs.append((tx, private_key))
        return SimpleNamespace(raw_transaction=f"signed-{len(self.signed_txs)}")


def _action(gas=70_000, data="0xdeadbeef"):
    return {
        "to": "0x000000000000000000000000000000000000dEaD",
        "data": data,
        "gas": gas,
    }


def _decode(signed_transaction):
    return TypedTransaction.from_bytes(HexBytes(signed_transaction)).as_dict()


def test_compute_fees_adds_extra_priority_fee():
    class FeeWeb3:
        class Eth:
            max_priority_fee = 1_500_000_000

            def get_block(self, block):
                assert block == "latest"
                return {"baseFeePerGas": 10_000_000_000}

        eth = Eth()

        def to_wei(self, value, unit):
            return Web3.to_wei(value, unit)

    priority_fee, max_fee_per_gas = bundle._compute_fees(FeeWeb3(), Decimal("2.25"))

    assert priority_fee == 3_750_000_000
    assert max_fee_per_gas == 28_750_000_000


def test_compute_fees_rejects_negative_extra_priority_fee():
    with pytest.raises(ValueError, match="cannot be negative"):
        bundle._compute_fees(SimpleNamespace(), Decimal("-0.01"))


def test_prepare_actions_uses_fixed_gas_without_rpc():
    rescue_data = [
        {
            "address": "0x000000000000000000000000000000000000dEaD",
            "function_signature": "transfer(address,uint256)",
            "args": ["0x000000000000000000000000000000000000BEEF", 5],
            "gas_estimate": 70_000,
        },
        {
            "address": "0x000000000000000000000000000000000000dEaD",
            "function_signature": "transfer(address,uint256)",
            "args": ["0x000000000000000000000000000000000000BEEF", 5],
        },
    ]

    prepared = bundle.prepare_actions(rescue_data)

    assert [action["gas"] for action in prepared] == [70_000, GAS_GENERIC]


def test_prepare_actions_rejects_action_over_transaction_gas_cap():
    rescue_data = [
        {
            "address": "0x000000000000000000000000000000000000dEaD",
            "function_signature": "f()",
            "args": [],
            "gas_estimate": bundle.MAX_TX_GAS,
        }
    ]

    with pytest.raises(ValueError, match="Action #1 needs more gas"):
        bundle.prepare_actions(rescue_data)


def test_deploy_tx_gas_buffers_estimate():
    w3 = SimpleNamespace(eth=SimpleNamespace(estimate_gas=lambda transaction: 500_000))

    assert bundle._deploy_tx_gas(w3, b"\x60\x00") == 550_000


def test_deploy_tx_gas_has_no_fixed_fallback():
    def fail(transaction):
        raise RuntimeError("no estimate")

    w3 = SimpleNamespace(eth=SimpleNamespace(estimate_gas=fail))

    with pytest.raises(RuntimeError, match="no estimate"):
        bundle._deploy_tx_gas(w3, b"\x60\x00")


def test_action_tx_gas_covers_stipend_overhead_and_calldata_upper_bound():
    action = _action(gas=50_000, data="0x" + "ab" * 33)

    # 6 head words + 33 data bytes padded to 64
    expected_calldata = bundle.CALLDATA_GAS_PER_BYTE * (6 * 32 + 64)
    assert bundle._action_tx_gas(action) == (
        50_000 + bundle.RESCUE_ACTION_OVERHEAD_GAS + expected_calldata
    )


def test_action_tx_gas_upper_bounds_real_calldata_cost():
    actions = [_action(gas=0, data="0x" + "ff" * 100) for _ in range(5)]
    calldata = encode_rescue_call(actions)
    exact_calldata_gas = sum(16 if byte else 4 for byte in calldata)

    budgeted = bundle._batch_tx_gas(actions, bundle.RESCUE_TX_FIXED_GAS)

    assert budgeted - bundle.RESCUE_TX_FIXED_GAS >= exact_calldata_gas


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


class EstimatingEth:
    def __init__(self, estimates, accounts=None):
        self.estimates = estimates
        self.accounts = accounts or {}
        self.requests = []

    def estimate_gas(self, transaction):
        self.requests.append(transaction)
        result = self.estimates[transaction["to"]]
        if isinstance(result, Exception):
            raise result
        return result

    def get_transaction_count(self, address):
        return self.accounts.get(address, (0, 0, b""))[0]

    def get_balance(self, address):
        return self.accounts.get(address, (0, 0, b""))[1]

    def get_code(self, address):
        return self.accounts.get(address, (0, 0, b""))[2]


def test_size_actions_raises_limits_to_estimate_with_margin():
    eth = EstimatingEth({"heavy": 140_000, "light": 30_000})
    actions = [
        {"to": "heavy", "data": "0x01", "gas": 75_000},
        {"to": "light", "data": "0x02", "gas": 75_000},
    ]

    sized, failures = bundle.size_actions(SimpleNamespace(eth=eth), "victim", actions)

    assert [action["gas"] for action in sized] == [210_000, 75_000]
    assert failures == {}
    assert eth.requests[0] == {"from": "victim", "to": "heavy", "data": "0x01"}


def test_size_actions_keeps_floor_and_reports_failed_estimates():
    eth = EstimatingEth({"gone": RuntimeError("execution reverted: wrong owner")})

    sized, failures = bundle.size_actions(
        SimpleNamespace(eth=eth),
        "victim",
        [{"to": "gone", "data": "0x", "gas": 75_000}],
    )

    assert sized[0]["gas"] == 75_000
    assert failures == {0: "execution reverted: wrong owner"}


def test_size_actions_caps_runaway_estimates():
    eth = EstimatingEth({"huge": 100_000_000})

    sized, _ = bundle.size_actions(
        SimpleNamespace(eth=eth), "victim", [{"to": "huge", "data": "0x", "gas": 1}]
    )

    assert sized[0]["gas"] == bundle.MAX_ACTION_GAS


def test_new_account_gas_is_zero_for_existing_safe():
    eth = EstimatingEth({}, accounts={"safe": (0, 1, b"")})

    assert bundle._new_account_gas(SimpleNamespace(eth=eth), "gas", "safe") == 0


def test_new_account_gas_uses_node_estimate_for_empty_safe():
    eth = EstimatingEth({"safe": 204_600}, accounts={"gas": (0, 10**18, b"")})

    assert bundle._new_account_gas(SimpleNamespace(eth=eth), "gas", "safe") == 183_600
    assert eth.requests == [{"from": "gas", "to": "safe", "value": 1}]


def test_new_account_gas_falls_back_when_sender_unfunded():
    eth = EstimatingEth({})

    assert (
        bundle._new_account_gas(SimpleNamespace(eth=eth), "gas", "safe")
        == bundle.NEW_ACCOUNT_GAS_FALLBACK
    )


def test_required_funding_accounts_for_optional_undelegation():
    prepared = [{"to": "target", "data": "0x", "gas": 50_000}]
    max_fee = 20
    effective_fee = 10
    victim_funding = 50_000 * max_fee + bundle.SWEEP_TX_GAS * effective_fee

    plain = bundle._required_funding(
        prepared, max_fee, effective_fee, needs_undelegation=False
    )
    delegated = bundle._required_funding(
        prepared, max_fee, effective_fee, needs_undelegation=True
    )

    assert plain == int(
        (victim_funding + bundle.FUNDING_TX_GAS * max_fee) * bundle.FUNDING_BUFFER
    )
    assert delegated == int(
        (victim_funding + (bundle.FUNDING_TX_GAS + bundle.UNDELEGATE_TX_GAS) * max_fee)
        * bundle.FUNDING_BUFFER
    )


def test_build_bundle_sets_priority_fee_on_every_transaction(monkeypatch):
    account = FakeAccount()
    w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, account=account))
    priority_fee = 3_750_000_000
    max_fee_per_gas = 28_750_000_000
    effective_fee_cap = 15_000_000_000

    monkeypatch.setattr(
        bundle, "_sign_7702_undelegation", lambda **kwargs: b"undelegate"
    )
    entries = bundle._build_bundle(
        w3,
        victim=FakeSigner("victim", "victim-key"),
        gas=FakeSigner("gas", "gas-key"),
        prepared=[
            {"to": "target-1", "data": "0x1234", "gas": 50_000},
            {"to": "target-2", "data": "0x5678", "gas": 70_000},
        ],
        safe_address="safe",
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=effective_fee_cap,
        victim_nonce=9,
        gas_nonce=2,
        victim_balance=123,
    )

    assert entries == [
        {"signed_transaction": b"undelegate"},
        {"signed_transaction": "signed-1"},
        {"signed_transaction": "signed-2"},
        {"signed_transaction": "signed-3"},
        {"signed_transaction": "signed-4"},
    ]
    txs = [tx for tx, _private_key in account.signed_txs]
    assert [tx["maxPriorityFeePerGas"] for tx in txs] == [priority_fee] * 4
    assert [tx["maxFeePerGas"] for tx in txs] == [
        max_fee_per_gas,
        max_fee_per_gas,
        max_fee_per_gas,
        effective_fee_cap,
    ]
    assert [tx["nonce"] for tx in txs] == [3, 10, 11, 12]
    assert txs[0]["value"] == (
        120_000 * max_fee_per_gas + bundle.SWEEP_TX_GAS * effective_fee_cap
    )
    assert txs[-1]["to"] == "safe"
    assert txs[-1]["value"] == 123 + 120_000 * (max_fee_per_gas - effective_fee_cap)
    assert [key for _tx, key in account.signed_txs] == [
        "gas-key",
        "victim-key",
        "victim-key",
        "victim-key",
    ]


def test_build_bundle_skips_undelegation_for_plain_eoa():
    account = FakeAccount()
    w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, account=account))

    entries = bundle._build_bundle(
        w3,
        victim=FakeSigner("victim", "victim-key"),
        gas=FakeSigner("gas", "gas-key"),
        prepared=[{"to": "target", "data": "0x1234", "gas": 50_000}],
        safe_address="safe",
        priority_fee=1,
        max_fee_per_gas=20,
        effective_fee_cap=10,
        victim_nonce=9,
        gas_nonce=2,
        victim_balance=0,
        needs_undelegation=False,
    )

    assert len(entries) == 3
    assert [tx["nonce"] for tx, _key in account.signed_txs] == [2, 9, 10]


def test_build_bundle_omits_zero_value_sweep():
    account = FakeAccount()
    w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, account=account))

    entries = bundle._build_bundle(
        w3,
        victim=FakeSigner("victim", "victim-key"),
        gas=FakeSigner("gas", "gas-key"),
        prepared=[{"to": "target", "data": "0x1234", "gas": 50_000}],
        safe_address="safe",
        priority_fee=1,
        max_fee_per_gas=20,
        effective_fee_cap=20,
        victim_nonce=9,
        gas_nonce=2,
        victim_balance=0,
        needs_undelegation=False,
    )

    assert len(entries) == 2
    assert [tx["to"] for tx, _key in account.signed_txs] == ["victim", "target"]


def test_build_bundle_sweep_pays_for_creating_safe_account():
    account = FakeAccount()
    w3 = SimpleNamespace(eth=SimpleNamespace(chain_id=1, account=account))

    bundle._build_bundle(
        w3,
        victim=FakeSigner("victim", "victim-key"),
        gas=FakeSigner("gas", "gas-key"),
        prepared=[{"to": "target", "data": "0x1234", "gas": 50_000}],
        safe_address="safe",
        priority_fee=1,
        max_fee_per_gas=20,
        effective_fee_cap=10,
        victim_nonce=9,
        gas_nonce=2,
        victim_balance=10**18,
        needs_undelegation=False,
        sweep_gas=204_600,
    )

    funding, _rescue, sweep = (tx for tx, _key in account.signed_txs)
    assert sweep["gas"] == 204_600
    assert funding["value"] == 50_000 * 20 + 204_600 * 10


def test_prepare_bundle_targets_configured_block_offset(monkeypatch):
    eth = SimpleNamespace(
        block_number=100,
        get_transaction_count=lambda address: 0,
        get_balance=lambda address: 0,
    )
    monkeypatch.setattr(bundle, "_compute_fees", lambda w3, fee: (1, 20))
    monkeypatch.setattr(bundle, "_max_next_block_effective_fee", lambda w3, fee: 10)
    monkeypatch.setattr(bundle, "has_7702_delegation", lambda w3, address: False)
    monkeypatch.setattr(bundle, "_new_account_gas", lambda w3, sender, safe: 0)
    monkeypatch.setattr(
        bundle, "_build_bundle", lambda *args: [{"signed_transaction": b"fund"}]
    )

    prepared = bundle.prepare_bundle(
        SimpleNamespace(eth=eth),
        SimpleNamespace(address="victim"),
        SimpleNamespace(address="gas"),
        [],
        "safe",
        Decimal(0),
    )

    assert prepared.target_block == 100 + bundle.TARGET_BLOCK_OFFSET


@pytest.fixture
def fake_7702_chain(monkeypatch):
    victim = Account.create()
    gas = Account.create()
    monkeypatch.setattr(bundle, "_compute_fees", lambda w3, fee: (1, 20))
    monkeypatch.setattr(bundle, "_max_next_block_effective_fee", lambda w3, fee: 10)
    monkeypatch.setattr(bundle, "_deploy_tx_gas", lambda w3, data: 800_000)
    monkeypatch.setattr(bundle, "_new_account_gas", lambda w3, sender, safe: 183_600)
    eth = SimpleNamespace(
        block_number=100,
        chain_id=1,
        get_transaction_count=lambda address: 7 if address == victim.address else 3,
    )
    return SimpleNamespace(eth=eth), victim, gas


def test_prepare_7702_bundle_pure_shape_and_nonces(fake_7702_chain):
    w3, victim, gas = fake_7702_chain

    prepared = bundle.prepare_7702_bundle(
        w3, victim, gas, [_action()], SAFE, Decimal(0)
    )

    assert [t.role for t in prepared.transactions] == ["deploy", "rescue-7702"]
    assert prepared.transactions[1].batch_start == 0
    assert prepared.victim_nonce == 7
    assert prepared.gas_nonce == 3
    assert prepared.rescue_contract == bundle.compute_create_address(gas.address, 3)

    deploy_tx = _decode(prepared.transactions[0].signed_transaction)
    rescue_tx = _decode(prepared.transactions[1].signed_transaction)
    assert deploy_tx["nonce"] == 3
    assert deploy_tx["to"] in (None, b"", HexBytes("0x"))
    assert rescue_tx["nonce"] == 4
    assert rescue_tx["to"] == HexBytes(victim.address)
    assert rescue_tx["gas"] == bundle._batch_tx_gas(
        [_action()], bundle._rescue_base_gas(183_600)
    )
    authorization = rescue_tx["authorizationList"][0]
    assert authorization["nonce"] == 7
    assert authorization["address"] == HexBytes(prepared.rescue_contract)
    assert (
        Account.recover_transaction(prepared.transactions[1].signed_transaction)
        == gas.address
    )


def test_prepare_7702_bundle_chunks_actions_and_tracks_batch_start(fake_7702_chain):
    w3, victim, gas = fake_7702_chain
    actions = [_action() for _ in range(bundle.MAX_ACTIONS_PER_RESCUE + 1)]

    prepared = bundle.prepare_7702_bundle(w3, victim, gas, actions, SAFE, Decimal(0))

    assert [t.role for t in prepared.transactions] == [
        "deploy",
        "rescue-7702",
        "rescue-7702",
    ]
    assert [t.batch_start for t in prepared.transactions] == [
        None,
        0,
        bundle.MAX_ACTIONS_PER_RESCUE,
    ]
    decoded = [_decode(t.signed_transaction) for t in prepared.transactions]
    assert [tx["nonce"] for tx in decoded] == [3, 4, 5]
    # only the first rescue tx carries the authorization
    assert "authorizationList" in decoded[1]
    assert "authorizationList" not in decoded[2]


def test_required_funding_7702_covers_deploy_and_batches():
    prepared = [_action(70_000), _action(50_000)]

    required = bundle._required_funding_7702(
        800_000, prepared, 20, bundle.RESCUE_TX_FIXED_GAS
    )

    expected_gas = 800_000 + bundle._batch_tx_gas(prepared, bundle.RESCUE_TX_FIXED_GAS)
    assert required == int(expected_gas * 20 * bundle.FUNDING_BUFFER)


def test_has_7702_delegation_recognizes_only_delegation_designator():
    victim = "0x0000000000000000000000000000000000000001"

    def has_delegation(code):
        w3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda address: code))
        return bundle.has_7702_delegation(w3, victim)

    assert has_delegation(b"") is False
    assert has_delegation(b"\xef\x01\x00" + b"\x11" * 20) is True
    with pytest.raises(ValueError, match="unexpected non-EIP-7702 code"):
        has_delegation(b"\x60\x00")


def test_sign_7702_undelegation_builds_type_4_clear_authorization():
    victim = Account.create()
    gas = Account.create()

    raw_tx = bundle._sign_7702_undelegation(
        chain_id=1,
        tx_nonce=3,
        authority_nonce=9,
        authority_key=victim.key,
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
    assert authorization["chainId"] == 1
    assert authorization["address"] == HexBytes(bundle.ZERO_ADDRESS)
    assert authorization["nonce"] == 9
    expected_authorization = Account.sign_authorization(
        {"chainId": 1, "address": bundle.ZERO_ADDRESS, "nonce": 9}, victim.key
    )
    assert Web3.to_checksum_address(expected_authorization.authority) == victim.address
    assert authorization["yParity"] == expected_authorization.y_parity
    assert authorization["r"] == expected_authorization.r
    assert authorization["s"] == expected_authorization.s
    assert Account.recover_transaction(raw_tx) == gas.address
