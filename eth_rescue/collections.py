"""Collections whose contracts predate ERC-721 and need their own action type,
because the generic ERC-721 action (`transferFrom`) does not work on them.

Keyed by network, then by the plan action type that rescues the collection.
"""

from typing import Literal

CollectionType = Literal["cryptopunk", "superrare_v1"]

COLLECTION_CONTRACTS: dict[str, dict[CollectionType, str]] = {
    "mainnet": {
        "cryptopunk": "0xb47e3cd837dDF8e4c57F05d70Ab865de6e193BBB",
        "superrare_v1": "0x41A322b28D0fF354040e2CbC676F0320d8c8850d",
    },
}

COLLECTION_NAMES: dict[CollectionType, str] = {
    "cryptopunk": "CryptoPunks",
    "superrare_v1": "SuperRare V1",
}


def collection_contract(network: str, collection: CollectionType) -> str:
    return COLLECTION_CONTRACTS[network][collection]


def collection_for(network: str, address: str | None) -> CollectionType | None:
    """The collection action type that owns `address` on `network`, if any."""
    if not address:
        return None
    for collection, contract in COLLECTION_CONTRACTS.get(network, {}).items():
        if contract.lower() == address.lower():
            return collection
    return None
