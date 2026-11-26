import json
from pathlib import Path

import pytest
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue.contract import runtime_codehash

ETH_RESCUE_ARTIFACT = (
    Path(__file__).parents[2] / "contracts" / "out" / "EthRescue.sol" / "EthRescue.json"
)

pytestmark = pytest.mark.integration


@pytest.fixture
def forked(fork):
    if fork is None:
        pytest.skip("set FORK_NETWORK to check a live deployment")
    return fork


def _without_immutables(code: bytes, references: dict) -> bytes:
    masked = bytearray(code)
    for ranges in references.values():
        for ref in ranges:
            start, length = ref["start"], ref["length"]
            masked[start : start + length] = bytes(length)
    return bytes(masked)


def test_deployment_matches_pinned_codehash(anvil_w3: Web3, forked):
    code = bytes(anvil_w3.eth.get_code(forked.eth_rescue))
    assert runtime_codehash(code) == forked.eth_rescue_codehash


def test_deployment_is_the_current_build(anvil_w3: Web3, forked):
    """The deployed runtime code is this checkout's EthRescue, apart from the
    EIP-712 immutables (chain id, address, domain separator)."""
    artifact = json.loads(ETH_RESCUE_ARTIFACT.read_text())
    compiled = artifact["deployedBytecode"]
    references = compiled["immutableReferences"]
    deployed = bytes(anvil_w3.eth.get_code(forked.eth_rescue))

    assert _without_immutables(deployed, references) == _without_immutables(
        bytes(HexBytes(compiled["object"])), references
    )
