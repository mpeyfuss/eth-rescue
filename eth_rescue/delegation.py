"""Replays EIP-7702 authorization rules over a block's transactions, to tell
which code an account ran at each point of the block."""

from collections.abc import Mapping, Sequence
from typing import Any

from eth_account.typed_transactions.set_code_transaction import Authorization
from eth_keys import keys
from eth_keys.exceptions import BadSignature
from eth_utils import to_canonical_address, to_checksum_address

from eth_rescue.bundle import ZERO_ADDRESS

SECP256K1_HALF_N = (
    0xFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFEBAAEDCE6AF48A03BBFD25E8CD0364141 // 2
)
MAX_AUTHORIZATION_NONCE = 2**64 - 1


def _int(value: Any) -> int:
    return value if isinstance(value, int) else int.from_bytes(bytes(value))


def authorization_signer(authorization: Mapping[str, Any]) -> str | None:
    """The address that signed an authorization, or None for a signature
    EIP-7702 would reject."""
    y_parity, r, s = (_int(authorization[k]) for k in ("yParity", "r", "s"))
    if s > SECP256K1_HALF_N:
        return None
    try:
        message = Authorization(
            _int(authorization["chainId"]),
            to_canonical_address(authorization["address"]),
            _int(authorization["nonce"]),
        ).hash()
        public_key = keys.Signature(
            vrs=(y_parity, r, s)
        ).recover_public_key_from_msg_hash(message)
    except (BadSignature, ValueError):
        return None
    return public_key.to_checksum_address()


def _applies(
    authorization: Mapping[str, Any], authority: str, chain_id: int, nonce: int
) -> bool:
    return (
        _int(authorization["chainId"]) in (0, chain_id)
        and _int(authorization["nonce"]) == nonce
        and nonce <= MAX_AUTHORIZATION_NONCE
        and authorization_signer(authorization) == authority
    )


def replay_delegates(
    transactions: Sequence[Mapping[str, Any]],
    authority: str,
    chain_id: int,
    nonce: int,
    delegate: str | None,
) -> list[str | None]:
    """The delegate `authority` runs while each transaction executes, starting
    from its `nonce` and `delegate` before the first one.

    A transaction from the authority uses its nonce before the transaction's
    authorizations are processed, and an authorization applies only at the
    authority's current nonce. Assumes the authority holds no other code.
    """
    authority = to_checksum_address(authority)
    delegates: list[str | None] = []
    for transaction in transactions:
        if to_checksum_address(transaction["from"]) == authority:
            nonce += 1
        for authorization in transaction.get("authorizationList") or ():
            if _applies(authorization, authority, chain_id, nonce):
                nonce += 1
                target = to_checksum_address(authorization["address"])
                delegate = None if target == ZERO_ADDRESS else target
        delegates.append(delegate)
    return delegates
