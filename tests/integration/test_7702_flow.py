from functools import partial

import pytest
from eth_account import Account
from eth_utils import to_canonical_address

from eth_rescue import checks, engine, templates
from eth_rescue.bundle import (
    MAX_ACTIONS_PER_RESCUE,
    _batch_tx_gas,
    _new_account_gas,
    _rescue_base_gas,
    prepare_7702_bundle,
    prepare_actions,
)
from eth_rescue.engine import ExitCode, Inclusion

pytestmark = pytest.mark.integration

DELEGATION_PREFIX = bytes.fromhex("ef0100")


def _confirm():
    return True


def _statuses(reporter):
    [(_actions, outcomes)] = reporter.named("outcomes")[-1:]
    return [outcome.status for outcome in outcomes]


def _asset_matrix(asset_contracts, victim, safe):
    return [
        templates.erc20_transfer(asset_contracts["erc20"].address, safe.address, 1_000),
        templates.erc721_transfer(
            asset_contracts["erc721"].address, victim.address, safe.address, 1
        ),
        templates.erc1155_transfer(
            asset_contracts["erc1155"].address, victim.address, safe.address, 7, 25
        ),
        templates.transfer_ownership(asset_contracts["ownable"].address, safe.address),
    ]


def test_run_rescues_asset_matrix_and_sweeps_all_eth(
    anvil_w3,
    rescue_accounts,
    asset_contracts,
    rescue_contract,
    sequential_relay,
    make_ctx,
    reporter,
):
    victim, gas, safe = rescue_accounts
    gas_nonce = anvil_w3.eth.get_transaction_count(gas.address)

    result = engine.run(
        make_ctx(victim, gas, safe),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=_confirm,
    )

    assert result == ExitCode.OK
    assert asset_contracts["erc20"].functions.balanceOf(safe.address).call() == 1_000
    assert asset_contracts["erc721"].functions.ownerOf(1).call() == safe.address
    assert asset_contracts["erc1155"].functions.balanceOf(safe.address, 7).call() == 25
    assert asset_contracts["ownable"].functions.owner().call() == safe.address
    assert _statuses(reporter) == ["rescued"] * 4
    [(bundle,)] = reporter.named("bundle")
    assert [t.role for t in bundle.transactions] == ["rescue-7702"]
    assert bundle.warnings == ()  # the type-4 whole-transaction estimate worked

    # entire victim balance swept to safe; victim left with nothing
    assert anvil_w3.eth.get_balance(victim.address) == 0
    assert anvil_w3.eth.get_balance(safe.address) == anvil_w3.to_wei(1, "ether")

    expected_code = DELEGATION_PREFIX + to_canonical_address(rescue_contract.address)
    assert bytes(anvil_w3.eth.get_code(victim.address)) == expected_code
    assert anvil_w3.eth.get_transaction_count(gas.address) == gas_nonce + 1


def test_continuation_batches_are_estimated_with_delegation_override(
    anvil_w3,
    rescue_accounts,
    asset_contracts,
    gas_burner,
    sequential_relay,
    make_ctx,
    reporter,
):
    victim, gas, safe = rescue_accounts
    erc20 = asset_contracts["erc20"]
    rescue_data = [
        templates.custom(gas_burner.address, "burn()", [], 30_000)
        for _ in range(MAX_ACTIONS_PER_RESCUE)
    ] + [templates.erc20_transfer(erc20.address, safe.address, 1_000)]

    result = engine.run(
        make_ctx(victim, gas, safe),
        rescue_data,
        confirm=_confirm,
        legacy_fallback=False,
    )

    assert result == ExitCode.UNRESCUED
    assert _statuses(reporter)[-1] == "rescued"
    [(bundle,)] = reporter.named("bundle")
    assert [t.role for t in bundle.transactions] == ["rescue-7702", "rescue-7702"]
    assert bundle.warnings == ()
    assert erc20.functions.balanceOf(safe.address).call() == 1_000


def test_run_refuses_network_address_without_code(
    rescue_accounts, asset_contracts, sequential_relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts

    result = engine.run(
        make_ctx(victim, gas, safe, rescue_contract="0x" + "55" * 20),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=_confirm,
    )

    assert result == ExitCode.INVALID
    assert "No EthRescue contract" in reporter.messages("error")[0]
    assert sequential_relay.simulated == []


def test_run_reports_failed_transfers_without_fallback(
    anvil_w3,
    rescue_accounts,
    asset_contracts,
    code_rejecting_erc20,
    false_returning_erc20,
    sequential_relay,
    make_ctx,
    reporter,
):
    victim, gas, safe = rescue_accounts
    erc20 = asset_contracts["erc20"]

    result = engine.run(
        make_ctx(victim, gas, safe),
        [
            templates.erc20_transfer(erc20.address, safe.address, 1_000),
            templates.erc20_transfer(code_rejecting_erc20.address, safe.address, 500),
            templates.erc20_transfer(false_returning_erc20.address, safe.address, 500),
        ],
        confirm=_confirm,
        legacy_fallback=False,
    )

    assert result == ExitCode.UNRESCUED
    assert _statuses(reporter) == ["rescued", "failed", "failed"]
    # the good token still moved despite the two failures in the same call
    assert erc20.functions.balanceOf(safe.address).call() == 1_000


def test_run_legacy_fallback_recovers_code_rejecting_token(
    anvil_w3,
    rescue_accounts,
    code_rejecting_erc20,
    sequential_relay,
    make_ctx,
    reporter,
):
    victim, gas, safe = rescue_accounts

    result = engine.run(
        make_ctx(victim, gas, safe),
        [templates.erc20_transfer(code_rejecting_erc20.address, safe.address, 500)],
        confirm=_confirm,
    )

    assert result == ExitCode.OK
    phase_1, phase_2 = (outcomes for _a, outcomes in reporter.named("outcomes"))
    assert [o.status for o in phase_1] == ["failed"]
    assert [o.status for o in phase_2] == ["rescued"]
    legacy_bundle = reporter.named("bundle")[-1][0]
    assert [t.role for t in legacy_bundle.transactions] == [
        "undelegate",
        "fund",
        "rescue",
        "sweep",
    ]
    assert bytes(anvil_w3.eth.get_code(victim.address)) == b""
    assert code_rejecting_erc20.functions.balanceOf(safe.address).call() == 500


def test_gas_burning_actions_cannot_starve_later_actions_or_sweep(
    anvil_w3,
    rescue_accounts,
    asset_contracts,
    gas_burner,
    sequential_relay,
    make_ctx,
    reporter,
):
    victim, gas, safe = rescue_accounts
    erc20 = asset_contracts["erc20"]
    burner_count = MAX_ACTIONS_PER_RESCUE + 2  # forces a continuation batch
    rescue_data = [
        templates.custom(gas_burner.address, "burn()", [], 30_000)
        for _ in range(burner_count)
    ] + [templates.erc20_transfer(erc20.address, safe.address, 1_000)]
    # Burner estimates revert, so each keeps its 30k floor in the constant budget.
    first_batch = prepare_actions(rescue_data[:MAX_ACTIONS_PER_RESCUE])
    base_gas = _rescue_base_gas(_new_account_gas(anvil_w3, gas.address, safe.address))
    constant_budget = _batch_tx_gas(first_batch, base_gas)

    result = engine.run(
        make_ctx(victim, gas, safe),
        rescue_data,
        confirm=_confirm,
        legacy_fallback=False,
    )

    assert result == ExitCode.UNRESCUED
    assert _statuses(reporter) == ["failed"] * burner_count + ["rescued"]
    assert erc20.functions.balanceOf(safe.address).call() == 1_000
    assert anvil_w3.eth.get_balance(victim.address) == 0
    [(receipts,)] = reporter.named("included")
    assert len(receipts) == 2  # two rescue batches

    # Fork tripwire on the constant budget (the live estimate may raise the real
    # limit): the first batch is all full-burn actions, so its non-stipend cost
    # is exact. Fail well before a repricing exhausts the constants.
    stipends = MAX_ACTIONS_PER_RESCUE * 30_000
    assert receipts[0]["gasUsed"] - stipends <= 0.75 * (constant_budget - stipends)


def test_nonce_race_skips_authorization_and_rescues_nothing(
    anvil_w3, rescue_accounts, asset_contracts, sequential_relay, make_ctx
):
    victim, gas, safe = rescue_accounts
    erc20 = asset_contracts["erc20"]
    ctx = make_ctx(victim, gas, safe)
    rescue_data = [templates.erc20_transfer(erc20.address, safe.address, 1_000)]
    prepared = prepare_actions(rescue_data)
    check_map = checks.infer_checks(rescue_data)
    snapshot = checks.snapshot_checks(anvil_w3, check_map)
    bundle = ctx.prepare(
        partial(prepare_7702_bundle, rescue_contract=ctx.rescue_contract), prepared
    )

    # The attacker spends the victim's nonce before the bundle lands.
    attacker_tx = victim.sign_transaction(
        {
            "to": victim.address,
            "value": 0,
            "gas": 21_000,
            "maxFeePerGas": bundle.max_fee_per_gas,
            "maxPriorityFeePerGas": bundle.priority_fee,
            "nonce": bundle.victim_nonce,
            "chainId": anvil_w3.eth.chain_id,
        }
    )
    anvil_w3.eth.wait_for_transaction_receipt(
        anvil_w3.eth.send_raw_transaction(attacker_tx.raw_transaction)
    )
    gas_balance = anvil_w3.eth.get_balance(gas.address)

    receipts = sequential_relay.send_bundle(bundle.entries, bundle.target_block)
    receipts = receipts.receipts()

    # The rescue transaction succeeds, but the stale authorization is skipped,
    # so the call hits a plain EOA and nothing moves.
    assert [receipt["status"] for receipt in receipts] == [1]
    assert bytes(anvil_w3.eth.get_code(victim.address)) == b""
    assert erc20.functions.balanceOf(victim.address).call() == 1_000
    assert anvil_w3.eth.get_balance(gas.address) < gas_balance
    assert engine.call_results(Inclusion(bundle, receipts), victim.address) == {}
    outcomes = checks.evaluate_checks(anvil_w3, check_map, snapshot, 1)
    assert [o.status for o in outcomes] == ["failed"]


def test_run_declined_sends_nothing(
    rescue_accounts, asset_contracts, sequential_relay, make_ctx
):
    victim, gas, safe = rescue_accounts

    result = engine.run(
        make_ctx(victim, gas, safe),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=lambda: False,
    )

    assert result == ExitCode.DECLINED
    assert len(sequential_relay.simulated) == 1
    assert sequential_relay.sent == []


def test_simulate_sends_nothing(
    rescue_accounts, asset_contracts, sequential_relay, make_ctx
):
    victim, gas, safe = rescue_accounts

    result = engine.simulate(
        make_ctx(victim, gas, safe), _asset_matrix(asset_contracts, victim, safe)
    )

    assert result == ExitCode.OK
    assert len(sequential_relay.simulated) == 1
    assert sequential_relay.sent == []


def test_run_rejects_victim_with_contract_code(
    anvil_w3, rescue_accounts, asset_contracts, sequential_relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts
    anvil_w3.provider.make_request("anvil_setCode", [victim.address, "0x6000"])

    result = engine.run(
        make_ctx(victim, gas, safe),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=_confirm,
    )

    assert result == ExitCode.INVALID
    assert "unexpected non-EIP-7702 code" in reporter.messages("error")[0]
    assert sequential_relay.simulated == []


def test_run_waits_for_funding_then_gives_up(
    rescue_accounts, asset_contracts, sequential_relay, make_ctx, reporter
):
    victim, _gas, safe = rescue_accounts
    unfunded = Account.create()

    result = engine.run(
        make_ctx(victim, unfunded, safe),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=_confirm,
    )

    assert result == ExitCode.UNFUNDED
    [(address, required, balance)] = reporter.named("funding")
    assert (address, balance) == (unfunded.address, 0)
    assert required > 0
    assert sequential_relay.simulated == []
