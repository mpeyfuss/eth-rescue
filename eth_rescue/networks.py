from eth_account.signers.local import LocalAccount

from eth_rescue.relay import BUILDERS, RelayClient, RelayWeb3
from eth_rescue.rpc import BackoffHTTPProvider
from eth_rescue.types import Network

# `eth_rescue` is the EthRescue deployment compromised wallets delegate to and
# `eth_rescue_codehash` the keccak of its runtime code (deployment-specific,
# since EIP-712 immutables are embedded). The CLI refuses to run on a network
# without both.
NETWORKS: dict[str, Network] = {
    "mainnet": {
        "label": "Ethereum mainnet",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "relay": "https://relay.flashbots.net",
        "chain_id": 1,
        "builders": BUILDERS,
        "eth_rescue": "0xdC7eC38149c572a29Ac89813eda47140Ac8cD969",
        "eth_rescue_codehash": (
            "0x5635918452603f8714e9e5f7348cc9b7f4d397529d7a92ba2ed818db0e7048fa"
        ),
    },
}


def connect(network: Network, auth: LocalAccount, rpc: str | None = None) -> RelayWeb3:
    w3 = RelayWeb3(BackoffHTTPProvider(rpc or network["rpc"]))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to {network['label']} RPC")
    if w3.eth.chain_id != network["chain_id"]:
        raise ConnectionError(
            f"RPC chain ID {w3.eth.chain_id} does not match expected {network['chain_id']}"
        )
    w3.relay = RelayClient(w3, auth, network["relay"], builders=network["builders"])
    return w3
