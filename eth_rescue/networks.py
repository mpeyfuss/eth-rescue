from eth_account.signers.local import LocalAccount

from eth_rescue.relay import BUILDERS, RelayClient, RelayWeb3
from eth_rescue.rpc import BackoffHTTPProvider
from eth_rescue.types import Network

# `eth_rescue` is the EthRescue deployment victims delegate to and
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
        "eth_rescue": "0xe9Da4345F6AB43e05FF3DE7999A99Dc30710421A",
        "eth_rescue_codehash": (
            "0x3e437fbd77867905e360820ecdc27ba719b11b80e2ff1b3f7972d3df599c7382"
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
