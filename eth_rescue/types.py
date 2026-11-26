from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from eth_utils import keccak
from hexbytes import HexBytes


CheckKind = Literal["erc20", "erc721", "erc1155", "ownership"]


class CheckSpec(TypedDict):
    """Recipient-side postcondition of a typed action. Custom calls have none."""

    kind: CheckKind
    contract: str
    recipient: str
    token_id: NotRequired[int]
    amount: NotRequired[int]


class RescueData(TypedDict):
    address: str
    function_signature: str
    args: list[Any]
    # Gas floor for the action; live estimates can only raise it.
    gas_estimate: NotRequired[int]
    description: NotRequired[str]
    check: NotRequired[CheckSpec]
    custom: NotRequired[bool]
    # Position in the plan's `actions` and the step within its expansion, so
    # reports and the fallback can refer back to what the user wrote.
    plan_action: NotRequired[int]
    plan_step: NotRequired[int]


class Network(TypedDict):
    label: str
    rpc: str
    relay: str
    chain_id: int
    builders: list[str] | None
    eth_rescue: str | None
    eth_rescue_codehash: str | None


class PreparedAction(TypedDict):
    """A rescue action with encoded calldata and a fixed gas limit."""

    to: str  # ethereum address
    data: str  # 0x prepended hex string
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


TransactionRole = Literal["rescue-7702", "undelegate", "fund", "rescue", "sweep"]


@dataclass(frozen=True)
class BundleTransaction:
    role: TransactionRole
    signed_transaction: HexBytes
    action_index: int | None = None
    # the calls a rescue-7702 transaction covers: batch_start .. batch_start + batch_size - 1
    batch_start: int = 0
    batch_size: int = 0

    @property
    def hash(self) -> HexBytes:
        return HexBytes(keccak(self.signed_transaction))


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
    warnings: tuple[str, ...] = ()
    # Dry-run call result per action (unsigned eth_call), None where unavailable.
    predicted: tuple[bool | None, ...] = ()

    @property
    def entries(self) -> list[BundleEntry]:
        return [
            {"signed_transaction": transaction.signed_transaction}
            for transaction in self.transactions
        ]


# rescued: verified on-chain. partial: some but not all of the amount arrived.
# unverified: executed, but nothing could confirm the effect. failed: executed
# with no effect (reverted, or the check saw nothing arrive). not_executed: the
# call never ran (reverted transaction, skipped authorization, or never sent).
OutcomeStatus = Literal["rescued", "partial", "unverified", "failed", "not_executed"]
# Only outcomes with evidence of no effect are retried automatically.
RETRYABLE: frozenset[OutcomeStatus] = frozenset({"failed", "not_executed"})


@dataclass(frozen=True)
class ActionOutcome:
    index: int
    status: OutcomeStatus
    detail: str | None = None


@dataclass(frozen=True)
class SimulationFailure:
    message: str
    role: TransactionRole | None = None
    action_index: int | None = None


@dataclass(frozen=True)
class SimulationOutcome:
    ok: bool
    bundle: PreparedBundle | None = None
    failures: tuple[SimulationFailure, ...] = ()

    def __bool__(self) -> bool:
        return self.ok
