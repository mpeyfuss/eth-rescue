import pytest
from eth_account import Account
from web3 import Web3

from eth_rescue.rescue import _sign_7702_undelegation


@pytest.mark.integration
def test_anvil_accepts_sponsored_7702_clear_authorization(anvil_w3: Web3):
    victim = Account.create()
    sponsor = Account.create()
    funder = anvil_w3.eth.accounts[0]
    funding_hash = anvil_w3.eth.send_transaction(
        {"from": funder, "to": sponsor.address, "value": anvil_w3.to_wei(1, "ether")}
    )
    anvil_w3.eth.wait_for_transaction_receipt(funding_hash)

    raw_transaction = _sign_7702_undelegation(
        chain_id=anvil_w3.eth.chain_id,
        tx_nonce=0,
        authority_nonce=0,
        authority_key=victim.key,
        sponsor_key=sponsor.key,
        sponsor_address=sponsor.address,
        priority_fee=1_000_000_000,
        max_fee_per_gas=2_000_000_000,
    )
    transaction_hash = anvil_w3.eth.send_raw_transaction(raw_transaction)
    receipt = anvil_w3.eth.wait_for_transaction_receipt(transaction_hash)

    assert receipt["status"] == 1
    assert anvil_w3.eth.get_transaction_count(victim.address) == 1
    assert anvil_w3.eth.get_code(victim.address) == b""
    assert anvil_w3.eth.get_transaction_count(sponsor.address) == 1
