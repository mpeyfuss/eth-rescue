from typing import Any, NotRequired, TypedDict

from hexbytes import HexBytes


class RescueData(TypedDict):
    address: str
    function_signature: str
    args: list[Any]
    gas_estimate: int
    description: str


class Network(TypedDict):
    label: str
    rpc: str
    relay: str


class PreparedAction(TypedDict):
    """A rescue action with encoded calldata and an estimated gas limit."""

    to: str
    data: str
    gas: int


class BundleEntry(TypedDict):
    signed_transaction: HexBytes


class SimulationTxResult(TypedDict):
    txHash: NotRequired[str]
    gasUsed: NotRequired[int]
    error: NotRequired[str]
    revert: NotRequired[str]


class SimulationResult(TypedDict):
    bundleHash: NotRequired[str]
    totalGasUsed: NotRequired[int]
    results: NotRequired[list[SimulationTxResult]]
