from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from hexbytes import HexBytes


class RescueData(TypedDict):
    address: str
    function_signature: str
    args: list[Any]
    gas_estimate: NotRequired[int]
    description: NotRequired[str]


class Network(TypedDict):
    label: str
    rpc: str
    relay: str
    chain_id: int


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


class CallBundleResult(TypedDict):
    bundleHash: str
    results: list[SimulationTxResult]


class SendBundleResult(TypedDict):
    bundleHash: str


TransactionRole = Literal["undelegate", "fund", "rescue", "sweep"]


@dataclass(frozen=True)
class BundleTransaction:
    role: TransactionRole
    signed_transaction: HexBytes
    action_index: int | None = None


@dataclass(frozen=True)
class PreparedBundle:
    transactions: list[BundleTransaction]
    victim_nonce: int
    gas_nonce: int
    priority_fee: int
    max_fee_per_gas: int
    effective_fee_cap: int
    target_block: int
    required_funding: int
    victim_funding: int
    sweep_value: int
    expected_residual: int

    @property
    def entries(self) -> list[BundleEntry]:
        return [
            {"signed_transaction": transaction.signed_transaction}
            for transaction in self.transactions
        ]


@dataclass(frozen=True)
class SimulationFailure:
    message: str
    transaction_index: int | None = None
    role: TransactionRole | None = None
    action_index: int | None = None


@dataclass(frozen=True)
class SimulationOutcome:
    ok: bool
    bundle: PreparedBundle | None = None
    result: SimulationResult | None = None
    failures: tuple[SimulationFailure, ...] = ()

    def __bool__(self) -> bool:
        return self.ok
