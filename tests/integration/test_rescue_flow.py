from decimal import Decimal

import pytest
from eth_account import Account
from web3 import Web3

from eth_rescue import engine, templates
from eth_rescue.bundle import (
    BUNDLE_BLOCK_RANGE,
    SET_CODE_TX_TYPE,
    _compute_fees,
    has_7702_delegation,
    prepare_actions,
    prepare_bundle,
)
from eth_rescue.engine import ExitCode, Inclusion

pytestmark = pytest.mark.integration


def _send_account_transaction(w3, account, transaction):
    signed = account.sign_transaction(transaction)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    return w3.eth.wait_for_transaction_receipt(tx_hash)


def _execute_rescue(ctx, rescue_data):
    prepared = engine.sized_actions(ctx, rescue_data)
    preview_bundle = ctx.prepare(prepare_bundle, prepared)
    assert engine.simulate_prepared_bundle(ctx, preview_bundle).ok
    assert isinstance(engine.send_with_retry(ctx, prepared, prepare_bundle), Inclusion)
    return preview_bundle


def _delegate_compromised(w3: Web3, compromised, gas, target: str) -> None:
    priority_fee, max_fee_per_gas = _compute_fees(
        w3, w3.eth.get_block("latest"), Decimal(0)
    )
    authorization = Account.sign_authorization(
        {"chainId": w3.eth.chain_id, "address": target, "nonce": 0}, compromised.key
    )
    signed = gas.sign_transaction(
        {
            "type": SET_CODE_TX_TYPE,
            "chainId": w3.eth.chain_id,
            "nonce": w3.eth.get_transaction_count(gas.address),
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee_per_gas,
            "gas": 200_000,  # setting a delegation costs ~64k under EIP-8037
            "to": gas.address,
            "value": 0,
            "data": b"",
            "accessList": [],
            "authorizationList": [authorization],
        }
    )
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1


def test_plain_compromised_rescues_asset_matrix_and_sweeps_eth(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx
):
    compromised, gas, destination = rescue_accounts
    erc20 = asset_contracts["erc20"]
    erc721 = asset_contracts["erc721"]
    erc1155 = asset_contracts["erc1155"]
    ownable = asset_contracts["ownable"]
    compromised_nonce = anvil_w3.eth.get_transaction_count(compromised.address)
    gas_nonce = anvil_w3.eth.get_transaction_count(gas.address)
    gas_balance = anvil_w3.eth.get_balance(gas.address)

    bundle = _execute_rescue(
        make_ctx(compromised, gas, destination),
        [
            templates.erc20_transfer(erc20.address, destination.address, 1_000),
            templates.erc721_transfer(
                erc721.address, compromised.address, destination.address, 1
            ),
            templates.erc1155_transfer(
                erc1155.address, compromised.address, destination.address, 7, 25
            ),
            templates.transfer_ownership(ownable.address, destination.address),
        ],
    )

    assert [transaction.role for transaction in bundle.transactions] == [
        "fund",
        "rescue",
        "rescue",
        "rescue",
        "rescue",
        "sweep",
    ]
    assert erc20.functions.balanceOf(destination.address).call() == 1_000
    assert erc721.functions.ownerOf(1).call() == destination.address
    assert erc1155.functions.balanceOf(destination.address, 7).call() == 25
    assert ownable.functions.owner().call() == destination.address
    assert anvil_w3.eth.get_balance(destination.address) == bundle.sweep_value
    assert (
        anvil_w3.eth.get_transaction_count(compromised.address) == compromised_nonce + 5
    )
    assert anvil_w3.eth.get_transaction_count(gas.address) == gas_nonce + 1
    assert (
        0 <= anvil_w3.eth.get_balance(compromised.address) < bundle.compromised_funding
    )
    assert (
        0
        < gas_balance - anvil_w3.eth.get_balance(gas.address)
        < bundle.required_funding
    )
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE
    assert len(relay.landed[0]) == len(bundle.transactions)
    assert all(receipt["status"] == 1 for receipt in relay.landed[0])


def test_delegated_compromised_is_undelegated_before_rescue(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx
):
    compromised, gas, destination = rescue_accounts
    erc20 = asset_contracts["erc20"]
    _delegate_compromised(
        anvil_w3, compromised, gas, asset_contracts["delegate"].address
    )
    assert has_7702_delegation(anvil_w3, compromised.address)
    starting_nonce = anvil_w3.eth.get_transaction_count(compromised.address)

    bundle = _execute_rescue(
        make_ctx(compromised, gas, destination),
        [templates.erc20_transfer(erc20.address, destination.address, 1_000)],
    )

    assert [transaction.role for transaction in bundle.transactions] == [
        "undelegate",
        "fund",
        "rescue",
        "sweep",
    ]
    assert anvil_w3.eth.get_code(compromised.address) == b""
    assert erc20.functions.balanceOf(destination.address).call() == 1_000
    assert anvil_w3.eth.get_transaction_count(compromised.address) == starting_nonce + 3
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE


def test_delist_then_transfer_rescues_escrowed_erc721(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx
):
    compromised, gas, destination = rescue_accounts
    erc721 = asset_contracts["erc721"]
    auction = asset_contracts["auction"]
    approve = erc721.functions.approve(auction.address, 1).build_transaction(
        {
            "from": compromised.address,
            "nonce": anvil_w3.eth.get_transaction_count(compromised.address),
        }
    )
    assert _send_account_transaction(anvil_w3, compromised, approve)["status"] == 1
    listing = auction.functions.list(erc721.address, 1).build_transaction(
        {
            "from": compromised.address,
            "nonce": anvil_w3.eth.get_transaction_count(compromised.address),
        }
    )
    assert _send_account_transaction(anvil_w3, compromised, listing)["status"] == 1
    assert erc721.functions.ownerOf(1).call() == auction.address

    bundle = _execute_rescue(
        make_ctx(compromised, gas, destination),
        templates.transient_auction_house_erc721_rescue(
            auction.address, erc721.address, compromised.address, destination.address, 1
        ),
    )

    assert [
        transaction.action_index
        for transaction in bundle.transactions
        if transaction.role == "rescue"
    ] == [0, 1]
    assert (
        auction.functions.seller(erc721.address, 1).call()
        == "0x0000000000000000000000000000000000000000"
    )
    assert erc721.functions.ownerOf(1).call() == destination.address
    assert len(relay.sent) == BUNDLE_BLOCK_RANGE


def test_reverting_action_rolls_back_simulation_and_blocks_submission(
    anvil_w3, rescue_accounts, asset_contracts, relay, make_ctx
):
    compromised, gas, destination = rescue_accounts
    erc20 = asset_contracts["erc20"]
    ctx = make_ctx(compromised, gas, destination)
    prepared = prepare_actions(
        [templates.erc20_transfer(erc20.address, destination.address, 1_001)]
    )
    bundle = ctx.prepare(prepare_bundle, prepared)
    before = {
        "compromised_balance": anvil_w3.eth.get_balance(compromised.address),
        "gas_balance": anvil_w3.eth.get_balance(gas.address),
        "compromised_nonce": anvil_w3.eth.get_transaction_count(compromised.address),
        "gas_nonce": anvil_w3.eth.get_transaction_count(gas.address),
        "token_balance": erc20.functions.balanceOf(compromised.address).call(),
        "code": anvil_w3.eth.get_code(compromised.address),
    }

    outcome = engine.simulate_prepared_bundle(ctx, bundle)

    assert not outcome.ok
    failure = outcome.failures[0]
    assert failure.role == "rescue"
    assert failure.action_index == 0
    assert before == {
        "compromised_balance": anvil_w3.eth.get_balance(compromised.address),
        "gas_balance": anvil_w3.eth.get_balance(gas.address),
        "compromised_nonce": anvil_w3.eth.get_transaction_count(compromised.address),
        "gas_nonce": anvil_w3.eth.get_transaction_count(gas.address),
        "token_balance": erc20.functions.balanceOf(compromised.address).call(),
        "code": anvil_w3.eth.get_code(compromised.address),
    }
    assert (
        engine.send_with_retry(ctx, prepared, prepare_bundle)
        == ExitCode.SIMULATION_FAILED
    )
    assert relay.sent == []
