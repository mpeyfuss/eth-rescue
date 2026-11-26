import itertools
from decimal import Decimal
from types import SimpleNamespace

import pytest
from eth_utils import keccak
from hexbytes import HexBytes
from web3.exceptions import TransactionNotFound

from eth_rescue import engine, templates
from eth_rescue.bundle import BUNDLE_BLOCK_RANGE
from eth_rescue.contract import ACTION_RESULT_TOPIC
from eth_rescue.engine import ExitCode, Inclusion, RescueContext
from eth_rescue.types import ActionOutcome, BundleTransaction, PreparedBundle

VICTIM = SimpleNamespace(address="0x" + "11" * 20, key=b"\x01" * 32)
GAS = SimpleNamespace(address="0x" + "33" * 20, key=b"\x03" * 32)
SAFE = "0x" + "22" * 20
TARGET = "0x" + "44" * 20
BAD_TARGET = "0x" + "66" * 20
BLOCK_TIME = 12
_serial = itertools.count()


# ---------------------------------------------------------------------------
# a tiny chain + relay simulator
# ---------------------------------------------------------------------------
class Chain:
    """Blocks advance with the fake clock; bundles land in their target block."""

    def __init__(self, gas_balance=10**18, block=100):
        self.block_number = block
        self.chain_id = 1
        self.balances = {GAS.address: gas_balance}
        self.code = {}
        self.gas_nonce = 3
        self.receipts = {}
        self.visible_from = {}
        self.receipt_lag = 0
        self.mining = True
        self.now = 0.0
        self.relay = None

    def block_hash(self, number):
        return number.to_bytes(32, "big")

    def tick(self, seconds):
        before = int(self.now // BLOCK_TIME)
        self.now += seconds
        if not self.mining:
            return
        for _ in range(int(self.now // BLOCK_TIME) - before):
            self.block_number += 1
            if self.relay is not None:
                self.relay.on_block(self.block_number)

    def include(self, entries, block, results):
        for position, (entry, (status, logs)) in enumerate(zip(entries, results)):
            tx_hash = bytes(keccak(entry["signed_transaction"]))
            self.receipts[tx_hash] = {
                "blockNumber": block,
                "blockHash": HexBytes(self.block_hash(block)),
                "transactionIndex": position,
                "transactionHash": HexBytes(tx_hash),
                "status": status,
                "logs": logs,
            }
            self.visible_from[tx_hash] = block + self.receipt_lag
        self.gas_nonce += 1

    def get_transaction_receipt(self, tx_hash):
        key = bytes(tx_hash)
        if key not in self.receipts or self.block_number < self.visible_from[key]:
            raise TransactionNotFound("not found")
        return self.receipts[key]

    def get_block(self, number):
        return {"number": number, "hash": HexBytes(self.block_hash(number))}

    def get_balance(self, address):
        return self.balances.get(address, 0)

    def get_code(self, address):
        return self.code.get(address, b"")


class Relay:
    def __init__(self, chain, land=lambda entries, block: True, outcome=None):
        self.chain = chain
        chain.relay = self
        self.land = land
        # entries -> [(status, logs)] per transaction
        self.outcome = outcome or (lambda entries: [(1, [])] * len(entries))
        self.sim_error = lambda signed: None
        self.send_error = None
        self.simulated = []
        self.sent = []

    def simulate(self, entries, block_tag=None):
        self.simulated.append((entries, block_tag))
        results = []
        for entry in entries:
            signed = entry["signed_transaction"]
            result = {"txHash": "0x" + keccak(signed).hex(), "gasUsed": 21_000}
            if error := self.sim_error(signed):
                result["error"] = error
            results.append(result)
        return {"bundleHash": "0xb", "results": results, "totalGasUsed": 21_000}

    def send_bundle(self, entries, target_block_number=None):
        self.sent.append((entries, target_block_number))
        if self.send_error:
            raise self.send_error

    def on_block(self, block):
        for entries, target in self.sent:
            first = bytes(keccak(entries[0]["signed_transaction"]))
            if first in self.chain.receipts:
                continue
            if target == block and self.land(entries, block):
                self.chain.include(entries, block, self.outcome(entries))
                return


def _make_bundle(chain, transactions, required=1000, predicted=(), target=None):
    return PreparedBundle(
        transactions=transactions,
        victim_nonce=7,
        gas_nonce=chain.gas_nonce,
        priority_fee=1,
        max_fee_per_gas=20,
        effective_fee_cap=10,
        target_block=chain.block_number + 1 if target is None else target,
        required_funding=required,
        victim_funding=0,
        sweep_value=0,
        expected_residual=0,
        predicted=tuple(predicted),
    )


def _signed(role, to=""):
    return HexBytes(f"{next(_serial)}|{role}|{to}".encode())


def preparer_7702(chain, required=1000, predicted=None):
    def prepare(w3, victim, gas, prepared, safe, extra, rescue_contract=None):
        transaction = BundleTransaction(
            "rescue-7702",
            _signed("rescue-7702"),
            batch_start=0,
            batch_size=len(prepared),
        )
        return _make_bundle(
            chain,
            [transaction],
            required,
            predicted if predicted is not None else [True] * len(prepared),
        )

    return prepare


def preparer_legacy(chain, required=1000):
    def prepare(w3, victim, gas, prepared, safe, extra):
        transactions = [BundleTransaction("fund", _signed("fund"))] + [
            BundleTransaction("rescue", _signed("rescue", action["to"]), action_index=i)
            for i, action in enumerate(prepared)
        ]
        return _make_bundle(chain, transactions, required)

    return prepare


def action_logs(*results, address=VICTIM.address):
    return [
        {
            "address": address,
            "topics": [HexBytes(ACTION_RESULT_TOPIC), HexBytes(i.to_bytes(32))],
            "data": HexBytes(int(ok).to_bytes(32)),
        }
        for i, ok in enumerate(results)
    ]


@pytest.fixture
def chain():
    return Chain()


@pytest.fixture
def relay(chain):
    return Relay(chain)


def _ctx(reporter, chain, relay, **overrides):
    fields = {
        "w3": SimpleNamespace(eth=chain, relay=relay),
        "victim": VICTIM,
        "gas": GAS,
        "safe": SAFE,
        "reporter": reporter,
        "rescue_contract": "0x" + "55" * 20,
        "network": "Testnet",
        "poll_interval": BLOCK_TIME,
        "head_poll_interval": 1,
        "head_timeout": 60,
        "funding_timeout": 60,
        "max_blocks": 10,
        "sleep": chain.tick,
        "clock": lambda: chain.now,
    }
    return RescueContext(**(fields | overrides))


PREPARED = [{"to": TARGET, "data": "0x", "gas": 50_000}]


# ---------------------------------------------------------------------------
# simulate_prepared_bundle
# ---------------------------------------------------------------------------
def test_simulation_succeeds_for_clean_result(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    bundle = preparer_legacy(chain)(None, None, None, PREPARED, SAFE, 0)

    outcome = engine.simulate_prepared_bundle(ctx, bundle)

    assert outcome
    assert relay.simulated == [(bundle.entries, bundle.target_block)]


def test_simulation_failure_names_the_plan_action(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    relay.sim_error = lambda signed: "reverted" if b"rescue" in signed else None
    bundle = preparer_legacy(chain)(None, None, None, PREPARED, SAFE, 0)

    outcome = engine.simulate_prepared_bundle(ctx, bundle, indices=[4])

    assert not outcome
    assert reporter.messages("error") == ["Simulation failed at action #5: reverted"]


def test_simulation_rejects_a_different_transaction_hash(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    bundle = preparer_legacy(chain)(None, None, None, PREPARED, SAFE, 0)
    original = relay.simulate

    def swapped(entries, block_tag=None):
        result = original(entries, block_tag)
        result["results"][0]["txHash"] = "0x" + "00" * 32
        return result

    relay.simulate = swapped

    assert not engine.simulate_prepared_bundle(ctx, bundle)
    assert "different transaction" in reporter.messages("error")[0]


def test_simulation_rejects_incomplete_result(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    relay.simulate = lambda entries, block_tag=None: {"results": []}
    bundle = preparer_legacy(chain)(None, None, None, PREPARED, SAFE, 0)

    assert not engine.simulate_prepared_bundle(ctx, bundle)


def test_simulation_reports_relay_exception(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)

    def boom(entries, block_tag=None):
        raise RuntimeError("relay down")

    relay.simulate = boom
    bundle = preparer_legacy(chain)(None, None, None, PREPARED, SAFE, 0)

    assert not engine.simulate_prepared_bundle(ctx, bundle)
    assert "relay down" in reporter.messages("error")[0]


# ---------------------------------------------------------------------------
# send_with_retry
# ---------------------------------------------------------------------------
def test_included_in_first_window(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert isinstance(result, Inclusion)
    assert result.block == 101
    assert [target for _entries, target in relay.sent] == list(
        range(101, 101 + BUNDLE_BLOCK_RANGE)
    )


def test_rebuilds_each_window_until_included(reporter, chain):
    relay = Relay(chain, land=lambda entries, block: block >= 107)
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert isinstance(result, Inclusion)
    assert result.block == 107
    first_window = {bytes(e[0]["signed_transaction"]) for e, _t in relay.sent[:5]}
    second_window = {bytes(e[0]["signed_transaction"]) for e, _t in relay.sent[5:]}
    assert len(first_window) == len(second_window) == 1
    assert first_window != second_window


def test_stops_after_max_blocks_submitted(reporter, chain):
    relay = Relay(chain, land=lambda entries, block: False)
    ctx = _ctx(reporter, chain, relay, max_blocks=7)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert result is ExitCode.NOT_INCLUDED
    assert len(relay.sent) == 7
    assert "Not included after 7 blocks" in reporter.messages("error")[-1]


def test_status_zero_receipt_is_still_an_inclusion(reporter, chain):
    relay = Relay(chain, outcome=lambda entries: [(1, []), (0, [])])
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert isinstance(result, Inclusion)
    assert [r["status"] for r in result.receipts] == [1, 0]


def test_lagging_receipts_are_found_instead_of_resubmitting(reporter, chain, relay):
    chain.receipt_lag = 7
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert isinstance(result, Inclusion)
    assert result.block == 101
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE


def test_foreign_gas_nonce_change_stops_without_resubmitting(reporter, chain):
    relay = Relay(chain, land=lambda entries, block: False)
    ctx = _ctx(reporter, chain, relay)
    original = relay.on_block

    def drain_gas_wallet(block):
        original(block)
        if block == 103:
            chain.gas_nonce += 1

    relay.on_block = drain_gas_wallet

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert result is ExitCode.NOT_INCLUDED
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE
    assert "Cannot tell whether the rescue landed" in reporter.messages("error")[0]


def test_half_visible_bundle_is_ambiguous_and_never_resubmitted(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    original = chain.include

    def include_first_only(entries, block, results):
        original(entries[:1], block, results[:1])

    chain.include = include_first_only

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert result is ExitCode.NOT_INCLUDED
    assert "only some of the bundle" in reporter.messages("error")[0]
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE


def test_reorged_block_is_ambiguous(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    chain.block_hash = lambda number: (number + (1 if number == 101 else 0)).to_bytes(
        32, "big"
    )
    original = chain.include

    def include_then_reorg(entries, block, results):
        original(entries, block, results)
        chain.block_hash = lambda number: number.to_bytes(32, "big")

    chain.include = include_then_reorg

    assert engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain)) is (
        ExitCode.NOT_INCLUDED
    )
    assert "no longer canonical" in reporter.messages("error")[0]


def test_stalled_head_stops_with_explicit_error(reporter, chain, relay):
    chain.mining = False
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert result is ExitCode.NOT_INCLUDED
    assert "stopped advancing" in reporter.messages("error")[0]


def test_failed_send_is_still_monitored(reporter, chain, relay):
    relay.send_error = RuntimeError("timeout")
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert isinstance(result, Inclusion)
    assert any("did not confirm" in m for m in reporter.messages("warning"))


def test_stale_target_is_rebuilt_not_failed(reporter, chain, relay):
    ctx = _ctx(reporter, chain, relay)
    legacy = preparer_legacy(chain)
    calls = iter([True, False])

    def prepare(*args):
        bundle = legacy(*args)
        if next(calls, False):
            return PreparedBundle(**(bundle.__dict__ | {"target_block": 100}))
        return bundle

    result = engine.send_with_retry(ctx, PREPARED, prepare)

    assert isinstance(result, Inclusion)
    assert relay.simulated and relay.simulated[0][1] == 101
    assert any("rebuilding" in m for m in reporter.messages("warning"))


def test_failed_resimulation_blocks_submission(reporter, chain, relay):
    relay.sim_error = lambda signed: "reverted"
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert result is ExitCode.SIMULATION_FAILED
    assert relay.sent == []


def test_waits_for_top_up_when_fees_rise(reporter, chain, relay):
    chain.balances[GAS.address] = 10
    ctx = _ctx(reporter, chain, relay)
    original = chain.tick

    def top_up(seconds):
        original(seconds)
        chain.balances[GAS.address] = 10**18

    ctx = _ctx(reporter, chain, relay, sleep=top_up)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert isinstance(result, Inclusion)


def test_gives_up_when_top_up_never_arrives(reporter, chain, relay):
    chain.balances[GAS.address] = 10
    ctx = _ctx(reporter, chain, relay)

    result = engine.send_with_retry(ctx, PREPARED, preparer_legacy(chain))

    assert result is ExitCode.UNFUNDED
    assert relay.sent == []


# ---------------------------------------------------------------------------
# wait_for_funding
# ---------------------------------------------------------------------------
def test_wait_for_funding_returns_immediately_when_funded(reporter, chain, relay):
    assert engine.wait_for_funding(_ctx(reporter, chain, relay), 1000)
    assert chain.now == 0


def test_wait_for_funding_times_out(reporter, chain, relay):
    chain.balances[GAS.address] = 0
    ctx = _ctx(reporter, chain, relay)

    assert not engine.wait_for_funding(ctx, 1000)
    assert chain.now == 60
    assert reporter.messages("error") == ["Gas wallet was not funded in time."]


# ---------------------------------------------------------------------------
# execution_results
# ---------------------------------------------------------------------------
def _inclusion(transactions, receipts):
    bundle = _make_bundle(Chain(), transactions)
    return Inclusion(bundle, receipts)


def test_execution_results_maps_logs_across_batches_to_plan_indices():
    transactions = [
        BundleTransaction("rescue-7702", b"a", batch_start=0, batch_size=2),
        BundleTransaction("rescue-7702", b"b", batch_start=2, batch_size=1),
    ]
    spoofed = action_logs(True, address="0x" + "99" * 20)
    receipts = [
        {"status": 1, "logs": action_logs(True, False)},
        {"status": 1, "logs": spoofed + action_logs(True)},
    ]

    results = engine.execution_results(
        _inclusion(transactions, receipts), VICTIM.address.upper(), [3, 5, 8]
    )

    assert results == {3: True, 5: False, 8: True}


def test_execution_results_unlogged_or_reverted_batch_never_ran():
    transactions = [
        BundleTransaction("rescue-7702", b"a", batch_start=0, batch_size=2),
        BundleTransaction("rescue-7702", b"b", batch_start=2, batch_size=1),
    ]
    receipts = [
        {"status": 1, "logs": action_logs(True)},
        {"status": 0, "logs": []},
    ]

    results = engine.execution_results(
        _inclusion(transactions, receipts), VICTIM.address, [0, 1, 2]
    )

    assert results == {0: True, 1: None, 2: None}


def test_execution_results_ignores_out_of_batch_and_malformed_logs():
    transactions = [BundleTransaction("rescue-7702", b"a", batch_start=0, batch_size=1)]
    malformed = {
        "address": VICTIM.address,
        "topics": [ACTION_RESULT_TOPIC],
        "data": b"",
    }
    receipts = [{"status": 1, "logs": [malformed] + action_logs(True, True)}]

    results = engine.execution_results(
        _inclusion(transactions, receipts), VICTIM.address, [0]
    )

    assert results == {0: True}


def test_execution_results_legacy_uses_receipt_status():
    transactions = [
        BundleTransaction("fund", b"f"),
        BundleTransaction("rescue", b"a", action_index=0),
        BundleTransaction("rescue", b"b", action_index=1),
    ]
    receipts = [{"status": 1, "logs": []}, {"status": 0, "logs": []}, {"status": 1}]

    results = engine.execution_results(
        _inclusion(transactions, receipts), VICTIM.address, [2, 6]
    )

    assert results == {2: False, 6: True}


# ---------------------------------------------------------------------------
# run / simulate
# ---------------------------------------------------------------------------
def _custom(target=TARGET):
    return templates.custom(target, "poke()", [])


@pytest.fixture
def offline(monkeypatch, chain):
    monkeypatch.setattr(engine, "require_rescue_contract", lambda *args: None)
    monkeypatch.setattr(
        engine, "size_actions", lambda w3, sender, prepared, multiplier: (prepared, {})
    )
    monkeypatch.setattr(engine, "prepare_bundle", preparer_legacy(chain))


def _use_7702(monkeypatch, chain, **kwargs):
    monkeypatch.setattr(engine, "prepare_7702_bundle", preparer_7702(chain, **kwargs))


def test_run_phase_1_success(reporter, chain, monkeypatch, offline):
    _use_7702(monkeypatch, chain)
    relay = Relay(chain, outcome=lambda entries: [(1, action_logs(True, True))])
    ctx = _ctx(reporter, chain, relay)

    code = engine.run(ctx, [_custom(), _custom()], confirm=lambda: True)

    assert code is ExitCode.OK
    [(outcomes_data, outcomes)] = reporter.named("outcomes")
    assert [o.status for o in outcomes] == ["unverified", "unverified"]
    assert reporter.named("final_state") == [(0, None)]


def test_run_retries_only_failed_actions_in_phase_2(
    reporter, chain, monkeypatch, offline
):
    _use_7702(monkeypatch, chain)

    def outcome(entries):
        if b"rescue-7702" in bytes(entries[0]["signed_transaction"]):
            return [(1, action_logs(True, False))]
        return [(1, [])] * len(entries)

    relay = Relay(chain, outcome=outcome)
    confirms = []
    ctx = _ctx(reporter, chain, relay)

    code = engine.run(
        ctx,
        [_custom(), _custom(BAD_TARGET)],
        confirm=lambda: confirms.append(1) or True,
    )

    assert code is ExitCode.OK
    assert confirms == [1]
    phase_2_sends = [
        e for e, _t in relay.sent if b"|fund|" in bytes(e[0]["signed_transaction"])
    ]
    assert phase_2_sends and all(len(entries) == 2 for entries in phase_2_sends)
    assert BAD_TARGET.encode() in bytes(phase_2_sends[0][1]["signed_transaction"])


def test_phase_2_drops_actions_that_fail_simulation(
    reporter, chain, monkeypatch, offline
):
    _use_7702(monkeypatch, chain)
    relay = Relay(
        chain,
        outcome=lambda entries: (
            [(1, action_logs(False, False))]
            if len(entries) == 1
            and b"rescue-7702" in bytes(entries[0]["signed_transaction"])
            else [(1, [])] * len(entries)
        ),
    )
    relay.sim_error = (
        lambda signed: "reverted" if BAD_TARGET.encode() in signed else None
    )
    ctx = _ctx(reporter, chain, relay)

    code = engine.run(ctx, [_custom(), _custom(BAD_TARGET)], confirm=lambda: True)

    assert code is ExitCode.UNRESCUED
    final = reporter.named("outcomes")[-1][1]
    assert [(o.index, o.status) for o in final] == [(0, "unverified"), (1, "failed")]
    assert any("Leaving out action #2" in m for m in reporter.messages("warning"))


def test_run_declined_sends_nothing(reporter, chain, monkeypatch, offline):
    _use_7702(monkeypatch, chain)
    relay = Relay(chain)
    ctx = _ctx(reporter, chain, relay)

    assert engine.run(ctx, [_custom()], confirm=lambda: False) is ExitCode.DECLINED
    assert relay.sent == []


def test_run_without_fallback_reports_failure(reporter, chain, monkeypatch, offline):
    _use_7702(monkeypatch, chain)
    relay = Relay(chain, outcome=lambda entries: [(1, action_logs(False))])
    ctx = _ctx(reporter, chain, relay)

    code = engine.run(ctx, [_custom()], confirm=lambda: True, legacy_fallback=False)

    assert code is ExitCode.UNRESCUED
    assert len({t for _e, t in relay.sent}) == BUNDLE_BLOCK_RANGE


def test_simulate_fails_when_dry_run_predicts_a_revert(
    reporter, chain, monkeypatch, offline
):
    _use_7702(monkeypatch, chain, predicted=[True, False])
    relay = Relay(chain)
    ctx = _ctx(reporter, chain, relay)

    code = engine.simulate(ctx, [_custom(), _custom()])

    assert code is ExitCode.SIMULATION_FAILED
    assert relay.sent == []
    assert any("Action #2 reverts" in m for m in reporter.messages("warning"))


def test_simulate_succeeds_without_sending(reporter, chain, monkeypatch, offline):
    _use_7702(monkeypatch, chain)
    relay = Relay(chain)

    assert engine.simulate(_ctx(reporter, chain, relay), [_custom()]) is ExitCode.OK
    assert relay.sent == []


def test_preflight_rejects_gas_wallet_with_code(reporter, chain, relay, offline):
    chain.code[GAS.address] = b"\xef\x01\x00" + b"\x55" * 20

    assert engine.run(
        _ctx(reporter, chain, relay), [_custom()], confirm=lambda: True
    ) is (ExitCode.INVALID)
    assert "gas wallet has code" in reporter.messages("error")[0]


def test_preflight_rejects_action_targeting_victim(reporter, chain, relay, offline):
    ctx = _ctx(reporter, chain, relay)

    assert engine.simulate(ctx, [_custom(VICTIM.address)]) is ExitCode.INVALID


def test_preflight_warns_about_foreign_delegation(
    reporter, chain, monkeypatch, offline
):
    _use_7702(monkeypatch, chain)
    chain.code[VICTIM.address] = b"\xef\x01\x00" + b"\x99" * 20
    relay = Relay(chain)

    engine.simulate(_ctx(reporter, chain, relay), [_custom()])

    assert any("likely by the attacker" in m for m in reporter.messages("warning"))


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (_custom(), "unverified", ExitCode.OK),
        (templates.erc20_transfer(TARGET, SAFE, 1), "unverified", ExitCode.UNRESCUED),
        (templates.erc20_transfer(TARGET, SAFE, 1), "rescued", ExitCode.OK),
        (templates.erc20_transfer(TARGET, SAFE, 2), "partial", ExitCode.UNRESCUED),
    ],
)
def test_finish_exit_code(reporter, chain, relay, data, status, code):
    ctx = _ctx(reporter, chain, relay)

    assert engine._finish(ctx, [data], [ActionOutcome(0, status)]) is code


def test_sized_actions_uses_context_gas_multiplier(reporter, chain, relay, monkeypatch):
    seen = []

    def fake_size(w3, sender, prepared, multiplier):
        seen.append((sender, multiplier))
        return [action | {"gas": 300_000} for action in prepared], {}

    monkeypatch.setattr(engine, "size_actions", fake_size)
    ctx = _ctx(reporter, chain, relay, gas_multiplier=Decimal(3))

    prepared = engine.sized_actions(
        ctx, [templates.custom(TARGET, "run()", [], 100_000)]
    )

    assert seen == [(VICTIM.address, Decimal(3))]
    assert prepared[0]["gas"] == 300_000
    assert "raised from 100,000 to 300,000" in reporter.messages("info")[0]


def test_phase_2_inclusion_unknown_exits_4(reporter, chain, monkeypatch, offline):
    _use_7702(monkeypatch, chain)

    def land(entries, block):
        return b"rescue-7702" in bytes(entries[0]["signed_transaction"])

    relay = Relay(chain, land=land, outcome=lambda entries: [(1, action_logs(False))])
    ctx = _ctx(reporter, chain, relay, max_blocks=5)

    code = engine.run(ctx, [_custom()], confirm=lambda: True)

    assert code is ExitCode.NOT_INCLUDED
    assert "legacy fallback did not complete" in reporter.messages("error")[-2]
