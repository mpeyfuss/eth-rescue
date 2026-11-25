from types import SimpleNamespace

import pytest
from eth_abi import decode
from eth_account import Account
from eth_account.messages import encode_typed_data
from eth_utils import keccak, to_checksum_address

from eth_rescue import contract

VICTIM = Account.from_key("0x" + "0a" * 32)
REQUEST = contract.RescueRequest(
    rescuer="0x" + "11" * 20,
    safe="0x" + "22" * 20,
    deadline=1_700_000_000,
    actions_hash=keccak(b"actions"),
)


def test_rescue_selector_matches_signature():
    assert (
        contract.RESCUE_SELECTOR
        == keccak(
            text="rescue((address,address,uint256,bytes32),(address,uint256,bytes)[],bytes)"
        )[:4]
    )


def test_actions_hash_is_keccak_of_abi_encoded_actions():
    prepared = [{"to": "0x" + "33" * 20, "data": "0xdeadbeef", "gas": 70_000}]

    expected = keccak(
        bytes.fromhex(
            "0000000000000000000000000000000000000000000000000000000000000020"
            "0000000000000000000000000000000000000000000000000000000000000001"
            "0000000000000000000000000000000000000000000000000000000000000020"
            "0000000000000000000000003333333333333333333333333333333333333333"
            "0000000000000000000000000000000000000000000000000000000000011170"
            "0000000000000000000000000000000000000000000000000000000000000060"
            "0000000000000000000000000000000000000000000000000000000000000004"
            "deadbeef00000000000000000000000000000000000000000000000000000000"
        )
    )
    assert contract.actions_hash(prepared) == expected


def test_sign_rescue_request_recovers_to_victim_over_victim_domain():
    signature = contract.sign_rescue_request(VICTIM.key, 1, VICTIM.address, REQUEST)

    message = encode_typed_data(
        domain_data={
            "name": "EthRescue",
            "version": "1",
            "chainId": 1,
            "verifyingContract": VICTIM.address,
        },
        message_types=contract.RESCUE_REQUEST_TYPES,
        message_data={
            "rescuer": REQUEST.rescuer,
            "safe": REQUEST.safe,
            "deadline": REQUEST.deadline,
            "actionsHash": REQUEST.actions_hash,
        },
    )
    assert len(signature) == 65
    assert Account.recover_message(message, signature=signature) == VICTIM.address


def test_signatures_differ_across_chains():
    assert contract.sign_rescue_request(
        VICTIM.key, 1, VICTIM.address, REQUEST
    ) != contract.sign_rescue_request(VICTIM.key, 11155111, VICTIM.address, REQUEST)


def test_encode_rescue_call_round_trips():
    prepared = [
        {"to": "0x" + "33" * 20, "data": "0xdeadbeef", "gas": 70_000},
        {"to": "0x" + "44" * 20, "data": "0x", "gas": 40_000},
    ]
    signature = b"\x01" * 65

    calldata = contract.encode_rescue_call(REQUEST, prepared, signature)

    assert calldata[:4] == contract.RESCUE_SELECTOR
    request, actions, decoded_signature = decode(
        [contract.REQUEST_TYPE, contract.ACTIONS_TYPE, "bytes"], calldata[4:]
    )
    assert (to_checksum_address(request[0]), request[2]) == (
        to_checksum_address(REQUEST.rescuer),
        REQUEST.deadline,
    )
    assert request[3] == REQUEST.actions_hash
    target, stipend, action_data = actions[0]
    assert to_checksum_address(target) == to_checksum_address(prepared[0]["to"])
    assert (stipend, action_data) == (70_000, bytes.fromhex("deadbeef"))
    assert actions[1][2] == b""
    assert decoded_signature == signature


def test_require_rescue_contract_accepts_deployed_code():
    w3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda address: b"\x60\x00"))

    contract.require_rescue_contract(w3, "0x" + "55" * 20)


def test_require_rescue_contract_rejects_empty_address():
    w3 = SimpleNamespace(eth=SimpleNamespace(get_code=lambda address: b""))

    with pytest.raises(contract.RescueContractError, match="No EthRescue contract"):
        contract.require_rescue_contract(w3, "0x" + "55" * 20)
