from eth_account.signers.local import LocalAccount
from web3 import HTTPProvider

from eth_rescue.relay import BUILDERS, RelayClient, RelayWeb3
from eth_rescue.types import Network

# `eth_rescue` is the EthRescue deployment victims delegate to; None until it
# is deployed on that network.
NETWORKS: dict[str, Network] = {
    "mainnet": {
        "label": "Ethereum mainnet",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "relay": "https://relay.flashbots.net",
        "chain_id": 1,
        "builders": BUILDERS,
        "eth_rescue": None,
    },
    "sepolia": {
        "label": "Sepolia (testnet)",
        "rpc": "https://ethereum-sepolia-rpc.publicnode.com",
        "relay": "https://relay-sepolia.flashbots.net",
        "chain_id": 11155111,
        "builders": None,
        "eth_rescue": None,
    },
}


def connect(network: Network, auth: LocalAccount, rpc: str | None = None) -> RelayWeb3:
    w3 = RelayWeb3(HTTPProvider(rpc or network["rpc"]))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to {network['label']} RPC")
    if w3.eth.chain_id != network["chain_id"]:
        raise ConnectionError(
            f"RPC chain ID {w3.eth.chain_id} does not match expected {network['chain_id']}"
        )
    w3.relay = RelayClient(w3, auth, network["relay"], builders=network["builders"])
    return w3
