from types import SimpleNamespace

import pytest
from eth_account import Account
from eth_utils import to_canonical_address

from eth_rescue import engine, templates
from eth_rescue.bundle import (
    BUNDLE_BLOCK_RANGE,
    MAX_ACTIONS_PER_RESCUE,
    _batch_tx_gas,
    _rescue_base_gas,
    prepare_actions,
    safe_receive_gas,
)
from eth_rescue.engine import ExitCode

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
    relay,
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
    relay,
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
    rescue_accounts, asset_contracts, relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts

    result = engine.run(
        make_ctx(victim, gas, safe, rescue_contract="0x" + "55" * 20),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=_confirm,
    )

    assert result == ExitCode.INVALID
    assert "No EthRescue contract" in reporter.messages("error")[0]
    assert relay.simulated == []


def test_run_reports_failed_transfers_without_fallback(
    anvil_w3,
    rescue_accounts,
    asset_contracts,
    code_rejecting_erc20,
    false_returning_erc20,
    relay,
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
    relay,
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
    phase_1, phase_2, final = (outcomes for _a, outcomes in reporter.named("outcomes"))
    assert [o.status for o in phase_1] == ["failed"]
    assert [o.status for o in phase_2] == ["rescued"]
    assert [o.status for o in final] == ["rescued"]
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
    relay,
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
    base_gas = _rescue_base_gas(safe_receive_gas(anvil_w3, safe.address))
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


def _spend_victim_nonce(w3, victim):
    """An attacker transaction from the compromised key, mined just ahead of
    the bundle: it consumes the nonce the bundle was signed for."""

    def front_run():
        signed = victim.sign_transaction(
            {
                "to": victim.address,
                "value": 0,
                "gas": 21_000,
                "maxFeePerGas": w3.eth.get_block("latest")["baseFeePerGas"] * 2 + 10**9,
                "maxPriorityFeePerGas": 10**9,
                "nonce": w3.eth.get_transaction_count(victim.address),
                "chainId": w3.eth.chain_id,
            }
        )
        w3.eth.wait_for_transaction_receipt(
            w3.eth.send_raw_transaction(signed.raw_transaction)
        )

    return front_run


def test_nonce_race_leaves_calls_unexecuted_and_fallback_rescues_them(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx, reporter
):
    """The skipped authorization runs nothing, so even the uncheckable delist
    is retried rather than reported as an unverified success."""
    victim, gas, safe = rescue_accounts
    erc721 = asset_contracts["erc721"]
    auction = asset_contracts["auction"]
    for call in (
        erc721.functions.approve(auction.address, 1),
        auction.functions.list(erc721.address, 1),
    ):
        signed = victim.sign_transaction(
            call.build_transaction(
                {
                    "from": victim.address,
                    "nonce": anvil_w3.eth.get_transaction_count(victim.address),
                }
            )
        )
        anvil_w3.eth.wait_for_transaction_receipt(
            anvil_w3.eth.send_raw_transaction(signed.raw_transaction)
        )
    relay.front_run = _spend_victim_nonce(anvil_w3, victim)

    result = engine.run(
        make_ctx(victim, gas, safe),
        templates.transient_auction_house_erc721_rescue(
            auction.address, erc721.address, victim.address, safe.address, 1
        ),
        confirm=_confirm,
    )

    assert result == ExitCode.OK
    phase_1, _phase_2, final = (outcomes for _a, outcomes in reporter.named("outcomes"))
    assert [o.status for o in phase_1] == ["not_executed", "not_executed"]
    assert [o.status for o in final] == ["unverified", "rescued"]
    assert erc721.functions.ownerOf(1).call() == safe.address
    assert relay.front_run is None


def test_fallback_failure_exits_5(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts
    erc20 = asset_contracts["erc20"]

    result = engine.run(
        make_ctx(victim, gas, safe),
        [
            templates.erc20_transfer(erc20.address, safe.address, 400),
            templates.erc20_transfer(erc20.address, safe.address, 1_000),
        ],
        confirm=_confirm,
    )

    assert result == ExitCode.UNRESCUED
    assert _statuses(reporter) == ["rescued", "failed"]
    assert erc20.functions.balanceOf(safe.address).call() == 400
    assert any("Leaving out action #2" in m for m in reporter.messages("warning"))


def test_never_included_exits_4_after_max_blocks(
    rescue_accounts, asset_contracts, relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts
    relay.lands = lambda bundle, block: False

    result = engine.run(
        make_ctx(victim, gas, safe, max_blocks=7),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=_confirm,
    )

    assert result == ExitCode.NOT_INCLUDED
    assert len(relay.sent) == 7
    assert reporter.named("outcomes") == []


def test_simulate_exits_2_when_an_action_would_revert(
    rescue_accounts, asset_contracts, relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts
    erc20 = asset_contracts["erc20"]

    result = engine.simulate(
        make_ctx(victim, gas, safe),
        [templates.erc20_transfer(erc20.address, safe.address, 1_001)],
    )

    assert result == ExitCode.SIMULATION_FAILED
    assert relay.sent == []


def test_contract_safe_receives_the_sweep_in_both_phases(
    anvil_w3,
    rescue_accounts,
    asset_contracts,
    code_rejecting_erc20,
    receiver,
    relay,
    make_ctx,
):
    victim, gas, _safe = rescue_accounts
    safe = SimpleNamespace(address=receiver.address)
    erc20 = asset_contracts["erc20"]

    result = engine.run(
        make_ctx(victim, gas, safe),
        [
            templates.erc20_transfer(erc20.address, safe.address, 1_000),
            templates.erc20_transfer(code_rejecting_erc20.address, safe.address, 500),
        ],
        confirm=_confirm,
    )

    assert result == ExitCode.OK
    assert code_rejecting_erc20.functions.balanceOf(safe.address).call() == 500
    assert anvil_w3.eth.get_balance(victim.address) < anvil_w3.to_wei(0.001, "ether")
    assert receiver.functions.received().call() == anvil_w3.eth.get_balance(
        safe.address
    )
    assert receiver.functions.received().call() > anvil_w3.to_wei(0.99, "ether")


def test_reverted_transaction_still_counts_as_inclusion(
    anvil_w3, rescue_accounts, switch, relay, make_ctx, reporter
):
    victim, gas, safe = rescue_accounts
    relay.allow_reverts = True
    funder = anvil_w3.eth.accounts[0]
    relay.front_run = lambda: anvil_w3.eth.wait_for_transaction_receipt(
        switch.functions.toggle().transact({"from": funder})
    )

    result = engine.run(
        make_ctx(victim, gas, safe),
        [templates.custom(switch.address, "poke()", [], 50_000)],
        confirm=_confirm,
        legacy_fallback=False,
    )

    assert result == ExitCode.UNRESCUED
    assert _statuses(reporter) == ["failed"]
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE
    assert len(relay.landed) == 1


def test_run_declined_sends_nothing(rescue_accounts, asset_contracts, relay, make_ctx):
    victim, gas, safe = rescue_accounts

    result = engine.run(
        make_ctx(victim, gas, safe),
        _asset_matrix(asset_contracts, victim, safe),
        confirm=lambda: False,
    )

    assert result == ExitCode.DECLINED
    assert len(relay.simulated) == 1
    assert relay.sent == []


def test_simulate_sends_nothing(rescue_accounts, asset_contracts, relay, make_ctx):
    victim, gas, safe = rescue_accounts

    result = engine.simulate(
        make_ctx(victim, gas, safe), _asset_matrix(asset_contracts, victim, safe)
    )

    assert result == ExitCode.OK
    assert len(relay.simulated) == 1
    assert relay.sent == []


def test_run_rejects_victim_with_contract_code(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx, reporter
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
    assert relay.simulated == []


def test_run_waits_for_funding_then_gives_up(
    rescue_accounts, asset_contracts, relay, make_ctx, reporter
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
    assert relay.simulated == []
