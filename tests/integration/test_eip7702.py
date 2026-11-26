import pytest
from eth_account import Account
from web3 import Web3

from eth_rescue.bundle import _sign_7702_undelegation


@pytest.mark.integration
def test_anvil_accepts_sponsored_7702_clear_authorization(anvil_w3: Web3):
    compromised = Account.create()
    sponsor = Account.create()
    funder = anvil_w3.eth.accounts[0]
    # A real compromised wallet is never an empty account; under EIP-8037 an empty
    # authority would add 183,600 state gas to the authorization.
    for address in (sponsor.address, compromised.address):
        funding_hash = anvil_w3.eth.send_transaction(
            {"from": funder, "to": address, "value": anvil_w3.to_wei(1, "ether")}
        )
        anvil_w3.eth.wait_for_transaction_receipt(funding_hash)

    delegate_to = "0x" + "77" * 20
    authorization = Account.sign_authorization(
        {"chainId": anvil_w3.eth.chain_id, "address": delegate_to, "nonce": 0},
        compromised.key,
    )
    delegate = sponsor.sign_transaction(
        {
            "type": 4,
            "chainId": anvil_w3.eth.chain_id,
            "nonce": 0,
            "maxPriorityFeePerGas": 1_000_000_000,
            "maxFeePerGas": 2_000_000_000,
            "gas": 200_000,
            "to": sponsor.address,
            "value": 0,
            "data": b"",
            "accessList": [],
            "authorizationList": [authorization],
        }
    )
    anvil_w3.eth.wait_for_transaction_receipt(
        anvil_w3.eth.send_raw_transaction(delegate.raw_transaction)
    )
    assert bytes(anvil_w3.eth.get_code(compromised.address)) == (
        bytes.fromhex("ef0100") + bytes.fromhex(delegate_to[2:])
    )

    raw_transaction = _sign_7702_undelegation(
        chain_id=anvil_w3.eth.chain_id,
        tx_nonce=1,
        authority_nonce=1,
        authority_key=compromised.key,
        sponsor_key=sponsor.key,
        sponsor_address=sponsor.address,
        priority_fee=1_000_000_000,
        max_fee_per_gas=2_000_000_000,
    )
    transaction_hash = anvil_w3.eth.send_raw_transaction(raw_transaction)
    receipt = anvil_w3.eth.wait_for_transaction_receipt(transaction_hash)

    assert receipt["status"] == 1
    assert anvil_w3.eth.get_transaction_count(compromised.address) == 2
    assert anvil_w3.eth.get_code(compromised.address) == b""
    assert anvil_w3.eth.get_transaction_count(sponsor.address) == 2
