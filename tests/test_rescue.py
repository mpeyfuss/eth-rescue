from types import SimpleNamespace

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
