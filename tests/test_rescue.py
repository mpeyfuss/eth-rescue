from types import SimpleNamespace

from web3.exceptions import TransactionNotFound

from rescue_scripts import rescue


class FakeSigner:
    def __init__(self, address, key):
        self.address = address
        self.key = key


class FakeAccount:
    def __init__(self):
        self.signed_txs = []

    def sign_transaction(self, tx, private_key=None):
        self.signed_txs.append((tx, private_key))
        return SimpleNamespace(rawTransaction=f"signed-{len(self.signed_txs)}")


class FakeEth:
    block_number = 100

    def get_transaction_count(self, address):
        return {"victim": 7, "gas": 3}[address]


class FakeFlashbots:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def simulate(self, bundle, block_tag=None):
        self.calls.append((bundle, block_tag))
        if self.error:
            raise self.error
        return self.result


class FakeWeb3:
    def __init__(self, flashbots):
        self.eth = FakeEth()
        self.flashbots = flashbots

    def from_wei(self, value, unit):
        if unit == "gwei":
            return value // 1_000_000_000
        raise ValueError(unit)


class DummyStatus:
    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return None


def test_compute_fees_adds_extra_priority_fee():
    class FeeWeb3:
        class Eth:
            max_priority_fee = 1_500_000_000

            def get_block(self, block):
                assert block == "latest"
                return {"baseFeePerGas": 10_000_000_000}

        eth = Eth()

        def to_wei(self, value, unit):
            assert unit == "gwei"
            return int(value * 1_000_000_000)

    priority_fee, max_fee_per_gas = rescue._compute_fees(FeeWeb3(), 2.25)

    assert priority_fee == 3_750_000_000
    assert max_fee_per_gas == 28_750_000_000


def test_build_bundle_sets_priority_fee_on_every_transaction():
    account = FakeAccount()
    w3 = SimpleNamespace(
        eth=SimpleNamespace(
            chain_id=1,
            account=account,
        )
    )
    priority_fee = 3_750_000_000
    max_fee_per_gas = 28_750_000_000

    bundle = rescue._build_bundle(
        w3,
        victim=FakeSigner("victim", "victim-key"),
        gas=FakeSigner("gas", "gas-key"),
        prepared=[
            {"to": "target-1", "data": "0x1234", "gas": 50_000},
            {"to": "target-2", "data": "0x5678", "gas": 70_000},
        ],
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        victim_nonce=9,
        gas_nonce=2,
    )

    assert bundle == [
        {"signed_transaction": "signed-1"},
        {"signed_transaction": "signed-2"},
        {"signed_transaction": "signed-3"},
    ]
    txs = [tx for tx, _private_key in account.signed_txs]
    assert [tx["maxPriorityFeePerGas"] for tx in txs] == [priority_fee] * 3
    assert [tx["maxFeePerGas"] for tx in txs] == [max_fee_per_gas] * 3
    assert account.signed_txs[0][1] == "gas-key"
    assert account.signed_txs[1][1] == "victim-key"
    assert account.signed_txs[2][1] == "victim-key"


def test_simulate_bundle_returns_true_for_clean_result(monkeypatch):
    flashbots = FakeFlashbots(
        {
            "bundleHash": "0xbundle",
            "results": [{"txHash": "0xtx", "gasUsed": 21_000}],
            "totalGasUsed": 21_000,
        }
    )
    w3 = FakeWeb3(flashbots)

    monkeypatch.setattr(rescue, "_compute_fees", lambda w3, fee: (1, 20_000_000_000))
    monkeypatch.setattr(
        rescue,
        "_build_bundle",
        lambda *args: [{"signed_transaction": b"signed"}],
    )
    monkeypatch.setattr(rescue.ui, "section", lambda message: None)
    monkeypatch.setattr(rescue.ui, "info", lambda message: None)
    monkeypatch.setattr(rescue.ui, "success", lambda message: None)
    monkeypatch.setattr(rescue.ui, "render_simulation_result", lambda result: None)
    monkeypatch.setattr(
        rescue.ui.console,
        "status",
        lambda message: DummyStatus(),
    )

    assert rescue.simulate_bundle(
        w3,
        SimpleNamespace(address="victim"),
        SimpleNamespace(address="gas"),
        [{"to": "target", "gas": 21_000}],
        0.0,
    )
    assert flashbots.calls == [([{"signed_transaction": b"signed"}], 101)]


def test_simulate_bundle_returns_false_for_revert(monkeypatch):
    errors = []
    flashbots = FakeFlashbots(
        {
            "bundleHash": "0xbundle",
            "results": [{"txHash": "0xtx", "gasUsed": 21_000, "revert": "nope"}],
            "totalGasUsed": 21_000,
        }
    )
    w3 = FakeWeb3(flashbots)

    monkeypatch.setattr(rescue, "_compute_fees", lambda w3, fee: (1, 20_000_000_000))
    monkeypatch.setattr(rescue, "_build_bundle", lambda *args: [])
    monkeypatch.setattr(rescue.ui, "section", lambda message: None)
    monkeypatch.setattr(rescue.ui, "info", lambda message: None)
    monkeypatch.setattr(rescue.ui, "error", errors.append)
    monkeypatch.setattr(rescue.ui, "render_simulation_result", lambda result: None)
    monkeypatch.setattr(
        rescue.ui.console,
        "status",
        lambda message: DummyStatus(),
    )

    assert not rescue.simulate_bundle(
        w3,
        SimpleNamespace(address="victim"),
        SimpleNamespace(address="gas"),
        [{"to": "target", "gas": 21_000}],
        0.0,
    )
    assert errors == ["Simulation completed, but one or more transactions failed."]


def test_simulate_bundle_returns_false_for_rpc_exception(monkeypatch):
    errors = []
    w3 = FakeWeb3(FakeFlashbots(error=RuntimeError("relay unavailable")))

    monkeypatch.setattr(rescue, "_compute_fees", lambda w3, fee: (1, 20_000_000_000))
    monkeypatch.setattr(rescue, "_build_bundle", lambda *args: [])
    monkeypatch.setattr(rescue.ui, "section", lambda message: None)
    monkeypatch.setattr(rescue.ui, "info", lambda message: None)
    monkeypatch.setattr(rescue.ui, "error", errors.append)
    monkeypatch.setattr(
        rescue.ui.console,
        "status",
        lambda message: DummyStatus(),
    )

    assert not rescue.simulate_bundle(
        w3,
        SimpleNamespace(address="victim"),
        SimpleNamespace(address="gas"),
        [{"to": "target", "gas": 21_000}],
        0.0,
    )
    assert errors == ["Bundle simulation failed: relay unavailable"]


# ---------------------------------------------------------------------------
# send_with_retry / wait_for_funding (Layer 2: faked chain surface)
# ---------------------------------------------------------------------------
VICTIM = SimpleNamespace(address="victim-addr", key="victim-key")
GAS = SimpleNamespace(address="gas-addr", key="gas-key")


def _receipts():
    return [
        SimpleNamespace(
            blockNumber=123,
            transactionHash=SimpleNamespace(hex=lambda: "0xabc"),
        )
    ]


class FakeSendResult:
    def __init__(self, outcome):
        self.outcome = outcome
        self.waited = 0

    def wait(self):
        self.waited += 1

    def receipts(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeFlashbotsSender:
    def __init__(self, outcomes):
        self._outcomes = iter(outcomes)
        self.sent = []

    def send_bundle(self, bundle, target_block_number=None):
        outcome = next(self._outcomes)
        self.sent.append((bundle, target_block_number))
        return FakeSendResult(outcome)


class FakeEthSend:
    def __init__(self, balance, victim_nonce=7, gas_nonce=3, block_number=100):
        self._balance = balance
        self.nonces = {VICTIM.address: victim_nonce, GAS.address: gas_nonce}
        self.block_number = block_number
        self.balance_calls = 0

    def get_transaction_count(self, address):
        return self.nonces[address]

    def get_balance(self, address):
        i = self.balance_calls
        self.balance_calls += 1
        if isinstance(self._balance, list):
            return self._balance[min(i, len(self._balance) - 1)]
        return self._balance


class FakeWeb3Send:
    def __init__(self, eth, flashbots=None):
        self.eth = eth
        self.flashbots = flashbots

    def from_wei(self, value, unit):
        if unit == "gwei":
            return value // 1_000_000_000
        if unit == "ether":
            return value / 10**18
        raise ValueError(unit)


def _patch_send(monkeypatch, max_fee=20_000_000_000, required=1000):
    monkeypatch.setattr(rescue, "_compute_fees", lambda w3, fee: (1, max_fee))
    monkeypatch.setattr(
        rescue, "_build_bundle", lambda *args: [{"signed_transaction": b"signed"}]
    )
    monkeypatch.setattr(rescue, "_required_funding", lambda prepared, mfpg: required)
    for name in ("section", "info", "success", "warning", "error"):
        monkeypatch.setattr(rescue.ui, name, lambda *a, **k: None)
    monkeypatch.setattr(rescue.ui.console, "status", lambda message: DummyStatus())


def _run_send(monkeypatch, flashbots, balance=1_000_000):
    w3 = FakeWeb3Send(FakeEthSend(balance), flashbots)
    return rescue.send_with_retry(w3, VICTIM, GAS, [{"to": "t", "gas": 21_000}], 0.0)


def test_send_with_retry_returns_true_when_included_first_attempt(monkeypatch):
    _patch_send(monkeypatch)
    flashbots = FakeFlashbotsSender([_receipts()])

    assert _run_send(monkeypatch, flashbots) is True
    assert flashbots.sent == [([{"signed_transaction": b"signed"}], 101)]


def test_send_with_retry_loops_until_included(monkeypatch):
    fee_calls = []
    _patch_send(monkeypatch)
    monkeypatch.setattr(
        rescue,
        "_compute_fees",
        lambda w3, fee: (fee_calls.append(1), (1, 20_000_000_000))[1],
    )
    flashbots = FakeFlashbotsSender(
        [TransactionNotFound(), TransactionNotFound(), _receipts()]
    )

    assert _run_send(monkeypatch, flashbots) is True
    assert len(flashbots.sent) == 3
    assert len(fee_calls) == 3  # fees refreshed every attempt


def test_send_with_retry_stops_after_max_attempts_when_user_declines(monkeypatch):
    _patch_send(monkeypatch)
    confirms = []
    monkeypatch.setattr(
        rescue, "prompt_yes_no", lambda *a, **k: confirms.append(1) or False
    )
    flashbots = FakeFlashbotsSender([TransactionNotFound()] * rescue.MAX_BLOCK_ATTEMPTS)

    assert _run_send(monkeypatch, flashbots) is False
    assert len(flashbots.sent) == rescue.MAX_BLOCK_ATTEMPTS
    assert len(confirms) == 1


def test_send_with_retry_keeps_trying_when_user_confirms(monkeypatch):
    _patch_send(monkeypatch)
    confirms = []
    monkeypatch.setattr(
        rescue, "prompt_yes_no", lambda *a, **k: confirms.append(1) or True
    )
    flashbots = FakeFlashbotsSender(
        [TransactionNotFound()] * rescue.MAX_BLOCK_ATTEMPTS + [_receipts()]
    )

    assert _run_send(monkeypatch, flashbots) is True
    assert len(flashbots.sent) == rescue.MAX_BLOCK_ATTEMPTS + 1
    assert len(confirms) == 1


def test_send_with_retry_refunds_when_balance_below_requirement(monkeypatch):
    _patch_send(monkeypatch, required=1000)
    refunds = []
    monkeypatch.setattr(rescue, "wait_for_funding", lambda *a: refunds.append(a[1:]))
    flashbots = FakeFlashbotsSender([_receipts()])
    w3 = FakeWeb3Send(FakeEthSend(balance=[500]), flashbots)

    assert (
        rescue.send_with_retry(w3, VICTIM, GAS, [{"to": "t", "gas": 21_000}], 0.0)
        is True
    )
    assert refunds == [(GAS.address, 1000)]


def test_wait_for_funding_returns_immediately_when_funded(monkeypatch):
    pauses = []
    monkeypatch.setattr(rescue, "pause", lambda *a: pauses.append(1))
    for name in ("section", "callout", "info", "success"):
        monkeypatch.setattr(rescue.ui, name, lambda *a, **k: None)
    w3 = FakeWeb3Send(FakeEthSend(balance=5000))

    rescue.wait_for_funding(w3, GAS.address, required=1000)

    assert pauses == []


def test_wait_for_funding_waits_until_balance_reaches_requirement(monkeypatch):
    pauses = []
    successes = []
    monkeypatch.setattr(rescue, "pause", lambda *a: pauses.append(1))
    monkeypatch.setattr(rescue.ui, "success", successes.append)
    for name in ("section", "callout", "info"):
        monkeypatch.setattr(rescue.ui, name, lambda *a, **k: None)
    w3 = FakeWeb3Send(FakeEthSend(balance=[0, 500, 1500]))

    rescue.wait_for_funding(w3, GAS.address, required=1000)

    assert len(pauses) == 2
    assert successes == ["Gas wallet funded."]
