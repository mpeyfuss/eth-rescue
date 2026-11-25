from types import SimpleNamespace

from hexbytes import HexBytes
from web3.exceptions import TransactionNotFound

from eth_rescue import engine
from eth_rescue.bundle import BUNDLE_BLOCK_RANGE
from eth_rescue.contract import ACTION_RESULT_TOPIC
from eth_rescue.engine import ExitCode, Inclusion, RescueContext
from eth_rescue.types import BundleTransaction, PreparedBundle

VICTIM = SimpleNamespace(address="victim-addr", key="victim-key")
GAS = SimpleNamespace(address="gas-addr", key="gas-key")
PREPARED = [{"to": "t", "data": "0x", "gas": 21_000}]


def _bundle(entry=b"signed", required_funding=1000, target_block=101):
    return PreparedBundle(
        transactions=[BundleTransaction("undelegate", entry)],
        victim_nonce=7,
        gas_nonce=3,
        priority_fee=1,
        max_fee_per_gas=20_000_000_000,
        effective_fee_cap=2,
        target_block=target_block,
        required_funding=required_funding,
        victim_funding=0,
        sweep_value=0,
        expected_residual=0,
    )


class FakeSubmission:
    def __init__(self, outcome):
        self.outcome = outcome

    def wait(self):
        return None

    def receipts(self):
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome


class FakeRelay:
    def __init__(self, outcomes=(), simulation=None, simulation_error=None):
        self._outcomes = iter(outcomes)
        self.simulation = simulation or {
            "bundleHash": "0xbundle",
            "results": [{"txHash": "0xtx", "gasUsed": 21_000}],
            "totalGasUsed": 21_000,
        }
        self.simulation_error = simulation_error
        self.simulated = []
        self.sent = []

    def simulate(self, bundle, block_tag=None):
        self.simulated.append((bundle, block_tag))
        if self.simulation_error:
            raise self.simulation_error
        return self.simulation

    def send_bundle(self, bundle, target_block_number=None):
        self.sent.append((bundle, target_block_number))
        return FakeSubmission(next(self._outcomes))


class FakeEth:
    def __init__(self, balance=1_000_000, block_number=100):
        self._balance = balance
        self.block_number = block_number
        self.balance_calls = 0

    def get_balance(self, address):
        index = self.balance_calls
        self.balance_calls += 1
        if isinstance(self._balance, list):
            return self._balance[min(index, len(self._balance) - 1)]
        return self._balance


class FakeClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds


def _ctx(reporter, relay=None, eth=None, **overrides):
    clock = FakeClock()
    w3 = SimpleNamespace(eth=eth or FakeEth(), relay=relay or FakeRelay())
    fields = {
        "w3": w3,
        "victim": VICTIM,
        "gas": GAS,
        "safe": "safe",
        "reporter": reporter,
        "poll_interval": 12,
        "funding_timeout": 60,
        "sleep": clock.sleep,
        "clock": clock,
    }
    return RescueContext(**(fields | overrides))


def _preparer(*bundles, calls=None):
    remaining = iter(bundles) if len(bundles) > 1 else None

    def prepare(*args):
        if calls is not None:
            calls.append(args)
        return next(remaining) if remaining else bundles[0]

    return prepare


def _receipts(status=1):
    return [
        {"blockNumber": 123, "transactionHash": HexBytes("0xabc"), "status": status}
    ]


def _not_found():
    return TransactionNotFound("bundle transaction was not included")


def _included_first(window=BUNDLE_BLOCK_RANGE):
    return [_receipts()] + [_not_found()] * (window - 1)


# ---------------------------------------------------------------------------
# simulate_prepared_bundle
# ---------------------------------------------------------------------------
def test_simulate_prepared_bundle_succeeds_for_clean_result(reporter):
    relay = FakeRelay()

    outcome = engine.simulate_prepared_bundle(_ctx(reporter, relay), _bundle())

    assert outcome.ok
    assert relay.simulated == [([{"signed_transaction": b"signed"}], 101)]
    assert reporter.named("simulation") == [(relay.simulation,)]


def test_simulate_prepared_bundle_reports_reverting_transaction(reporter):
    relay = FakeRelay(
        simulation={
            "bundleHash": "0xbundle",
            "results": [{"txHash": "0xtx", "gasUsed": 21_000, "revert": "nope"}],
        }
    )

    outcome = engine.simulate_prepared_bundle(_ctx(reporter, relay), _bundle())

    assert not outcome.ok
    assert reporter.messages("error") == ["Simulation failed at undelegate: nope"]


def test_simulate_prepared_bundle_reports_relay_exception(reporter):
    relay = FakeRelay(simulation_error=RuntimeError("relay unavailable"))

    outcome = engine.simulate_prepared_bundle(_ctx(reporter, relay), _bundle())

    assert not outcome.ok
    assert reporter.messages("error") == ["Bundle simulation failed: relay unavailable"]


def test_simulate_prepared_bundle_rejects_incomplete_result(reporter):
    relay = FakeRelay(simulation={"bundleHash": "0xbundle", "results": []})

    outcome = engine.simulate_prepared_bundle(_ctx(reporter, relay), _bundle())

    assert not outcome.ok
    assert reporter.messages("error") == [
        "Relay simulation response did not include every bundle transaction"
    ]


# ---------------------------------------------------------------------------
# send_with_retry
# ---------------------------------------------------------------------------
def test_send_with_retry_included_first_attempt(reporter):
    relay = FakeRelay(_included_first())
    bundle = _bundle()

    result = engine.send_with_retry(_ctx(reporter, relay), PREPARED, _preparer(bundle))

    assert result == Inclusion(bundle, _receipts())
    assert [target for _bundle, target in relay.sent] == [101, 102, 103, 104, 105]
    assert reporter.named("submitting") == [(101, 105)]


def test_send_with_retry_rebuilds_each_window_until_included(reporter):
    calls = []
    relay = FakeRelay([_not_found()] * BUNDLE_BLOCK_RANGE + _included_first())
    bundles = (
        _bundle(entry=b"first", target_block=101),
        _bundle(entry=b"second", target_block=106),
    )

    result = engine.send_with_retry(
        _ctx(reporter, relay), PREPARED, _preparer(*bundles, calls=calls)
    )

    assert isinstance(result, Inclusion)
    assert len(calls) == 2  # fees and nonces refreshed every window
    assert relay.sent[:BUNDLE_BLOCK_RANGE] == [
        ([{"signed_transaction": b"first"}], target) for target in range(101, 106)
    ]
    assert relay.sent[BUNDLE_BLOCK_RANGE:] == [
        ([{"signed_transaction": b"second"}], target) for target in range(106, 111)
    ]


def test_send_with_retry_rebuilds_when_target_arrives_during_simulation(reporter):
    relay = FakeRelay(_included_first())
    bundles = (
        _bundle(entry=b"stale", target_block=100),
        _bundle(entry=b"fresh", target_block=102),
    )

    result = engine.send_with_retry(
        _ctx(reporter, relay), PREPARED, _preparer(*bundles)
    )

    assert isinstance(result, Inclusion)
    assert relay.sent == [
        ([{"signed_transaction": b"fresh"}], target) for target in range(102, 107)
    ]


def test_send_with_retry_monitors_accepted_submissions_after_later_rejection(
    reporter,
):
    relay = FakeRelay(_included_first(BUNDLE_BLOCK_RANGE - 1))
    send_bundle = relay.send_bundle

    def reject_last_target(bundle, target_block_number=None):
        if len(relay.sent) == BUNDLE_BLOCK_RANGE - 1:
            raise RuntimeError("invalid inclusion")
        return send_bundle(bundle, target_block_number)

    relay.send_bundle = reject_last_target

    result = engine.send_with_retry(
        _ctx(reporter, relay), PREPARED, _preparer(_bundle())
    )

    assert isinstance(result, Inclusion)
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE - 1
    assert "later target was rejected" in reporter.messages("warning")[0]


def test_send_with_retry_rejects_incomplete_receipts(reporter):
    relay = FakeRelay([[]] * BUNDLE_BLOCK_RANGE)

    result = engine.send_with_retry(
        _ctx(reporter, relay), PREPARED, _preparer(_bundle())
    )

    assert result == ExitCode.NOT_INCLUDED


def test_send_with_retry_rejects_reverted_receipt(reporter):
    relay = FakeRelay([_receipts(status=0)] * BUNDLE_BLOCK_RANGE)

    result = engine.send_with_retry(
        _ctx(reporter, relay), PREPARED, _preparer(_bundle())
    )

    assert result == ExitCode.NOT_INCLUDED


def test_send_with_retry_stops_after_max_blocks(reporter):
    relay = FakeRelay([_not_found()] * 12)

    result = engine.send_with_retry(
        _ctx(reporter, relay, max_blocks=12), PREPARED, _preparer(_bundle())
    )

    assert result == ExitCode.NOT_INCLUDED
    assert len(relay.sent) == 12  # 5 + 5 + a final window of 2
    assert reporter.named("submitting")[-1] == (101, 102)
    assert reporter.messages("error") == [
        "Not included after 12 blocks. Nothing was rescued."
    ]


def test_send_with_retry_blocks_submission_when_resimulation_fails(reporter):
    relay = FakeRelay(
        simulation={"bundleHash": "0x", "results": [{"error": "nonce too low"}]}
    )

    result = engine.send_with_retry(
        _ctx(reporter, relay), PREPARED, _preparer(_bundle())
    )

    assert result == ExitCode.SIMULATION_FAILED
    assert relay.sent == []


def test_send_with_retry_waits_for_refund_when_fees_rise(reporter):
    relay = FakeRelay(_included_first())
    eth = FakeEth(balance=[500, 1500])

    result = engine.send_with_retry(
        _ctx(reporter, relay, eth=eth), PREPARED, _preparer(_bundle())
    )

    assert isinstance(result, Inclusion)
    assert reporter.named("funding") == [("gas-addr", 1000, 1500)]


def test_send_with_retry_gives_up_when_refund_never_arrives(reporter):
    eth = FakeEth(balance=0)

    result = engine.send_with_retry(
        _ctx(reporter, eth=eth), PREPARED, _preparer(_bundle())
    )

    assert result == ExitCode.UNFUNDED


# ---------------------------------------------------------------------------
# wait_for_funding
# ---------------------------------------------------------------------------
def test_wait_for_funding_returns_immediately_when_funded(reporter):
    ctx = _ctx(reporter, eth=FakeEth(balance=5000))

    assert engine.wait_for_funding(ctx, 1000)
    assert reporter.named("funding") == [("gas-addr", 1000, 5000)]


def test_wait_for_funding_polls_every_interval_until_funded(reporter):
    ctx = _ctx(reporter, eth=FakeEth(balance=[0, 500, 1500]))

    assert engine.wait_for_funding(ctx, 1000)
    assert [balance for _a, _r, balance in reporter.named("funding")] == [
        0,
        500,
        1500,
    ]
    assert ctx.clock() == 24


def test_wait_for_funding_times_out(reporter):
    ctx = _ctx(reporter, eth=FakeEth(balance=0), funding_timeout=30)

    assert not engine.wait_for_funding(ctx, 1000)
    assert len(reporter.named("funding")) == 4  # t=0, 12, 24, 36
    assert reporter.messages("error") == ["Gas wallet was not funded in time."]


# ---------------------------------------------------------------------------
# call_results
# ---------------------------------------------------------------------------
def _action_log(address, index, success):
    return {
        "address": address,
        "topics": [HexBytes(ACTION_RESULT_TOPIC), HexBytes(index.to_bytes(32))],
        "data": HexBytes(int(success).to_bytes(32)),
    }


def test_call_results_maps_logs_across_batches():
    victim = "0x" + "11" * 20
    bundle = PreparedBundle(
        transactions=[
            BundleTransaction("deploy", b"d"),
            BundleTransaction("rescue-7702", b"r1", batch_start=0),
            BundleTransaction("rescue-7702", b"r2", batch_start=2),
        ],
        victim_nonce=0,
        gas_nonce=0,
        priority_fee=0,
        max_fee_per_gas=0,
        effective_fee_cap=0,
        target_block=0,
        required_funding=0,
        victim_funding=0,
        sweep_value=0,
        expected_residual=0,
    )
    spoofed = _action_log("0x" + "99" * 20, 0, True)
    receipts = [
        {"logs": [_action_log(victim, 0, True)]},
        {"logs": [_action_log(victim, 0, True), _action_log(victim, 1, False)]},
        {"logs": [spoofed, _action_log(victim, 0, False)]},
    ]

    results = engine.call_results(Inclusion(bundle, receipts), victim)

    assert results == {0: True, 1: False, 2: False}
