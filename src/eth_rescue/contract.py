from dataclasses import dataclass

from eth_abi import encode as abi_encode
from eth_account import Account
from eth_utils import keccak
from web3 import Web3

from eth_rescue.types import PreparedAction

REQUEST_TYPE = "(address,address,uint256,bytes32)"
ACTIONS_TYPE = "(address,uint256,bytes)[]"
RESCUE_SELECTOR = keccak(text=f"rescue({REQUEST_TYPE},{ACTIONS_TYPE},bytes)")[:4]
ACTION_RESULT_TOPIC = keccak(text="ActionResult(uint256,bool)")

EIP712_DOMAIN_NAME = "EthRescue"
EIP712_DOMAIN_VERSION = "1"
RESCUE_REQUEST_TYPES = {
    "RescueRequest": [
        {"name": "rescuer", "type": "address"},
        {"name": "safe", "type": "address"},
        {"name": "deadline", "type": "uint256"},
        {"name": "actionsHash", "type": "bytes32"},
    ]
}


class RescueContractError(ValueError):
    pass


@dataclass(frozen=True)
class RescueRequest:
    rescuer: str
    safe: str
    deadline: int
    actions_hash: bytes

    def as_tuple(self) -> tuple[str, str, int, bytes]:
        return (self.rescuer, self.safe, self.deadline, self.actions_hash)


def require_rescue_contract(w3: Web3, address: str) -> None:
    if not w3.eth.get_code(address):
        raise RescueContractError(f"No EthRescue contract is deployed at {address}")


def _abi_actions(prepared: list[PreparedAction]) -> list[tuple[str, int, bytes]]:
    return [
        (action["to"], action["gas"], bytes.fromhex(action["data"][2:]))
        for action in prepared
    ]


def actions_hash(prepared: list[PreparedAction]) -> bytes:
    return keccak(abi_encode([ACTIONS_TYPE], [_abi_actions(prepared)]))


def sign_rescue_request(
    victim_key: bytes, chain_id: int, victim_address: str, request: RescueRequest
) -> bytes:
    """EIP-712 signature by the victim key; the domain's verifying contract is
    the victim itself, because rescue() runs as the victim's delegated code."""
    signed = Account.sign_typed_data(
        victim_key,
        domain_data={
            "name": EIP712_DOMAIN_NAME,
            "version": EIP712_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": victim_address,
        },
        message_types=RESCUE_REQUEST_TYPES,
        message_data={
            "rescuer": request.rescuer,
            "safe": request.safe,
            "deadline": request.deadline,
            "actionsHash": request.actions_hash,
        },
    )
    return bytes(signed.signature)


def encode_rescue_call(
    request: RescueRequest, prepared: list[PreparedAction], signature: bytes
) -> bytes:
    return RESCUE_SELECTOR + abi_encode(
        [REQUEST_TYPE, ACTIONS_TYPE, "bytes"],
        [request.as_tuple(), _abi_actions(prepared), signature],
    )
