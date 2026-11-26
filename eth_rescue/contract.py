from dataclasses import dataclass

from eth_abi import decode as abi_decode, encode as abi_encode
from eth_account import Account
from eth_utils import keccak, to_hex
from web3 import Web3

from eth_rescue.types import PreparedAction

REQUEST_TYPE = "(address,address,uint256,bytes32)"
ACTIONS_TYPE = "(address,uint256,bytes)[]"
RESCUE_SELECTOR = keccak(text=f"rescue({REQUEST_TYPE},{ACTIONS_TYPE},bytes)")[:4]
ACTION_RESULT_TOPIC = keccak(text="ActionResult(uint256,bool)")
RESCUE_REQUEST_TYPEHASH = keccak(
    text="RescueRequest(address rescuer,address safe,uint256 deadline,bytes32 actionsHash)"
)
TYPEHASH_SELECTOR = keccak(text="RESCUE_REQUEST_TYPEHASH()")[:4]
DELEGATION_DESIGNATOR_PREFIX = b"\xef\x01\x00"

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


def runtime_codehash(code: bytes) -> str:
    return to_hex(keccak(code))


def require_rescue_contract(
    w3: Web3, address: str, codehash: str | None = None
) -> None:
    """Refuse to sign a delegation to anything but the reviewed EthRescue build.

    `codehash` is the keccak of the deployment's runtime code (it includes the
    EIP-712 immutables, so it is specific to one deployment). Without a pin, as
    in local tests, the contract must at least answer like EthRescue.
    """
    code = bytes(w3.eth.get_code(address))
    if not code:
        raise RescueContractError(f"No EthRescue contract is deployed at {address}")
    if code.startswith(DELEGATION_DESIGNATOR_PREFIX):
        raise RescueContractError(f"{address} is a delegated account, not EthRescue")
    if codehash is not None:
        if runtime_codehash(code).lower() != codehash.lower():
            raise RescueContractError(
                f"The contract at {address} is not the expected EthRescue build"
            )
        return
    try:
        typehash = bytes(
            w3.eth.call({"to": address, "data": to_hex(TYPEHASH_SELECTOR)})
        )
    except Exception as e:
        raise RescueContractError(f"The contract at {address} is not EthRescue") from e
    if typehash != RESCUE_REQUEST_TYPEHASH:
        raise RescueContractError(f"The contract at {address} is not EthRescue")


def decode_rescue_results(data: bytes) -> list[bool]:
    return list(abi_decode(["bool[]"], data)[0])


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
