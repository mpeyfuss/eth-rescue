from eth_rescue.types import RescueData

# Gas-limit floors per action type, used as-is when a live estimate fails and
# raised by estimates otherwise. Sized for Amsterdam (EIP-8037), where a transfer
# to a fresh holder pays ~98k state gas per new storage slot (measured: ERC-20
# 139.5k, ERC-1155 141k vs ~50k on Osaka). Overestimating only costs funding
# headroom; unused gas is refunded.
GAS_ERC20 = 200_000
GAS_ERC721 = 250_000
GAS_ERC1155 = 200_000
GAS_OWNERSHIP = 60_000
GAS_TRANSIENT_DELIST = 300_000
GAS_CRYPTOPUNK = 250_000
GAS_GENERIC = 250_000
# Most gas one action may be given, in a plan or from a live estimate.
MAX_ACTION_GAS = 8_000_000


def erc20_transfer(contract: str, to: str, amount: int) -> RescueData:
    """Transfer ERC20 tokens (amount in base units, e.g. wei) to a destination wallet."""
    return {
        "address": contract,
        "function_signature": "transfer(address,uint256)",
        "args": [to, amount],
        "gas_estimate": GAS_ERC20,
        "description": f"ERC20 transfer of {amount} (base units) to {to}",
        "check": {
            "kind": "erc20",
            "contract": contract,
            "recipient": to,
            "amount": amount,
        },
    }


def erc721_transfer(
    contract: str, from_compromised: str, to: str, token_id: int
) -> RescueData:
    return {
        "address": contract,
        "function_signature": "transferFrom(address,address,uint256)",
        "args": [from_compromised, to, token_id],
        "gas_estimate": GAS_ERC721,
        "description": f"ERC721 token #{token_id} -> {to}",
        "check": {
            "kind": "erc721",
            "contract": contract,
            "recipient": to,
            "token_id": token_id,
        },
    }


def cryptopunk_transfer(contract: str, to: str, punk_index: int) -> RescueData:
    """CryptoPunks predate ERC-721: `transferPunk` moves a punk and cancels any
    open sale offer on it."""
    return {
        "address": contract,
        "function_signature": "transferPunk(address,uint256)",
        "args": [to, punk_index],
        "gas_estimate": GAS_CRYPTOPUNK,
        "description": f"CryptoPunk #{punk_index} -> {to}",
        "check": {
            "kind": "cryptopunk",
            "contract": contract,
            "recipient": to,
            "token_id": punk_index,
        },
    }


def superrare_v1_transfer(contract: str, to: str, token_id: int) -> RescueData:
    """SuperRare V1 follows a pre-final ERC-721 draft with `transfer` and no
    `transferFrom`; `ownerOf` still works for the check."""
    return {
        "address": contract,
        "function_signature": "transfer(address,uint256)",
        "args": [to, token_id],
        "gas_estimate": GAS_ERC721,
        "description": f"SuperRare V1 token #{token_id} -> {to}",
        "check": {
            "kind": "erc721",
            "contract": contract,
            "recipient": to,
            "token_id": token_id,
        },
    }


def transient_auction_house_erc721_rescue(
    auction_house: str,
    contract: str,
    from_compromised: str,
    to: str,
    token_id: int,
) -> list[RescueData]:
    """Delist an escrowed ERC721 from Transient and transfer it to safety."""
    return [
        {
            "address": auction_house,
            "function_signature": "delist(address,uint256)",
            "args": [contract, token_id],
            "gas_estimate": GAS_TRANSIENT_DELIST,
            "description": f"Delist ERC721 token #{token_id} from Transient Auction House",
        },
        erc721_transfer(contract, from_compromised, to, token_id),
    ]


def erc1155_transfer(
    contract: str, from_compromised: str, to: str, token_id: int, amount: int
) -> RescueData:
    return {
        "address": contract,
        "function_signature": "safeTransferFrom(address,address,uint256,uint256,bytes)",
        "args": [from_compromised, to, token_id, amount, "0x"],
        "gas_estimate": GAS_ERC1155,
        "description": f"ERC1155 token #{token_id} x{amount} -> {to}",
        "check": {
            "kind": "erc1155",
            "contract": contract,
            "recipient": to,
            "token_id": token_id,
            "amount": amount,
        },
    }


def transfer_ownership(contract: str, to: str) -> RescueData:
    return {
        "address": contract,
        "function_signature": "transferOwnership(address)",
        "args": [to],
        "gas_estimate": GAS_OWNERSHIP,
        "description": f"transferOwnership of {contract} -> {to}",
        "check": {"kind": "ownership", "contract": contract, "recipient": to},
    }


def custom(
    contract: str, function_signature: str, args: list, gas_estimate: int = GAS_GENERIC
) -> RescueData:
    """No check: its effect is only known from its call result."""
    return {
        "address": contract,
        "function_signature": function_signature,
        "args": args,
        "gas_estimate": gas_estimate,
        "description": f"custom {function_signature} on {contract}",
        "custom": True,
    }
