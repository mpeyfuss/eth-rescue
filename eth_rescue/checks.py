import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from eth_utils import to_checksum_address
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from eth_rescue.abi import (
    ERC20_BALANCE_OF_ABI,
    ERC721_OWNER_OF_ABI,
    ERC1155_BALANCE_OF_ABI,
    OWNABLE_OWNER_ABI,
)
from eth_rescue.types import ActionOutcome, CheckKind, RescueData

READ_ATTEMPTS = 3
READ_RETRY_DELAY = 2.0

# (kind, contract, recipient, token_id): actions moving the same balance share it.
BalanceKey = tuple[str, str, str, int | None]


@dataclass(frozen=True)
class Check:
    kind: CheckKind
    contract: str
    recipient: str
    token_id: int | None = None
    amount: int | None = None

    @property
    def balance_key(self) -> BalanceKey:
        return (self.kind, self.contract, self.recipient, self.token_id)


class ReadUnavailable(RuntimeError):
    """A post-rescue read failed for reasons unrelated to the asset (RPC errors)."""


class ReadReverted(RuntimeError):
    """The contract itself rejected the read, e.g. ownerOf for a burned token."""


def infer_check(data: RescueData) -> Check | None:
    """The recipient-side postcondition a typed action declares.

    Checks are recipient-side deliberately: victim-side deltas would
    misclassify an attacker draining the asset mid-race as a successful rescue.
    Custom calls declare none, whatever their signature looks like.
    """
    spec = data.get("check")
    if spec is None:
        return None
    return Check(
        spec["kind"],
        to_checksum_address(spec["contract"]),
        to_checksum_address(spec["recipient"]),
        token_id=spec.get("token_id"),
        amount=spec.get("amount"),
    )


def infer_checks(rescue_data: list[RescueData]) -> dict[int, Check]:
    checks: dict[int, Check] = {}
    for index, data in enumerate(rescue_data):
        check = infer_check(data)
        if check is not None:
            checks[index] = check
    return checks


def _read(
    call: Callable[[], Any],
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    for attempt in range(READ_ATTEMPTS):
        try:
            return call()
        except (ContractLogicError, BadFunctionCallOutput) as e:
            raise ReadReverted(str(e)) from e
        except Exception as e:
            if attempt == READ_ATTEMPTS - 1:
                raise ReadUnavailable(str(e)) from e
            sleep(READ_RETRY_DELAY)
    raise AssertionError("unreachable")


def _balance(w3: Web3, check: Check, block: int | str, sleep) -> int:
    if check.kind == "erc20":
        contract = w3.eth.contract(address=check.contract, abi=ERC20_BALANCE_OF_ABI)
        fn = contract.functions.balanceOf(check.recipient)
    else:
        contract = w3.eth.contract(address=check.contract, abi=ERC1155_BALANCE_OF_ABI)
        fn = contract.functions.balanceOf(check.recipient, check.token_id)
    return _read(lambda: fn.call(block_identifier=block), sleep)


def _owner(w3: Web3, check: Check, block: int, sleep) -> str:
    if check.kind == "erc721":
        contract = w3.eth.contract(address=check.contract, abi=ERC721_OWNER_OF_ABI)
        fn = contract.functions.ownerOf(check.token_id)
    else:
        contract = w3.eth.contract(address=check.contract, abi=OWNABLE_OWNER_ABI)
        fn = contract.functions.owner()
    return to_checksum_address(_read(lambda: fn.call(block_identifier=block), sleep))


def snapshot_checks(
    w3: Web3,
    checks: dict[int, Check],
    sleep: Callable[[float], None] = time.sleep,
) -> dict[BalanceKey, int | None]:
    """Recipient balances before the rescue for delta-based checks; None where
    the balance could not be read (those actions end up unverified)."""
    snapshot: dict[BalanceKey, int | None] = {}
    for check in checks.values():
        if check.kind not in ("erc20", "erc1155") or check.balance_key in snapshot:
            continue
        try:
            snapshot[check.balance_key] = _balance(w3, check, "latest", sleep)
        except (ReadUnavailable, ReadReverted):
            snapshot[check.balance_key] = None
    return snapshot


class _Balances:
    """Attributes each shared balance increase to actions in execution order, so
    two transfers of the same token cannot both claim one arrival."""

    def __init__(self, w3, snapshot, block, sleep):
        self.w3, self.snapshot, self.block, self.sleep = w3, snapshot, block, sleep
        self.remaining: dict[BalanceKey, int | None] = {}

    def claim(self, check: Check) -> int | None:
        """The amount of this action's transfer that arrived; None if unreadable."""
        key = check.balance_key
        if key not in self.remaining:
            baseline = self.snapshot.get(key)
            try:
                current = _balance(self.w3, check, self.block, self.sleep)
            except (ReadUnavailable, ReadReverted):
                current = None
            self.remaining[key] = (
                None if baseline is None or current is None else current - baseline
            )
        remaining = self.remaining[key]
        if remaining is None:
            return None
        got = max(0, min(remaining, check.amount or 0))
        self.remaining[key] = remaining - got
        return got


def _evaluate_balance(index: int, check: Check, balances: _Balances) -> ActionOutcome:
    got = balances.claim(check)
    wanted = check.amount or 0
    if got is None:
        return ActionOutcome(
            index, "unverified", "the safe's balance could not be read"
        )
    if got == wanted:
        return ActionOutcome(index, "rescued")
    if got == 0:
        return ActionOutcome(index, "failed", "nothing arrived at the safe")
    return ActionOutcome(index, "partial", f"only {got} of {wanted} arrived")


def _evaluate_owner(w3, index: int, check: Check, block, sleep) -> ActionOutcome:
    try:
        owner = _owner(w3, check, block, sleep)
    except ReadReverted:
        return ActionOutcome(
            index, "failed", "the owner could not be read from the contract"
        )
    except ReadUnavailable:
        return ActionOutcome(
            index, "unverified", "the owner could not be read (RPC error)"
        )
    if owner == check.recipient:
        return ActionOutcome(index, "rescued")
    return ActionOutcome(index, "failed", f"owned by {owner}")


def _evaluate_one(
    w3, index: int, check: Check | None, executed: bool | None, balances, block, sleep
) -> ActionOutcome:
    if executed is None:
        return ActionOutcome(index, "not_executed", "the call never ran")
    if executed is False:
        return ActionOutcome(index, "failed", "the call reverted")
    if check is None:
        return ActionOutcome(
            index, "unverified", "the call succeeded; confirm its effect manually"
        )
    if check.kind in ("erc20", "erc1155"):
        return _evaluate_balance(index, check, balances)
    return _evaluate_owner(w3, index, check, block, sleep)


def evaluate_checks(
    w3: Web3,
    checks: dict[int, Check],
    snapshot: dict[BalanceKey, int | None],
    executed: dict[int, bool | None],
    block: int,
    sleep: Callable[[float], None] = time.sleep,
) -> list[ActionOutcome]:
    """Classify each executed-or-not call in `executed` (call index -> EVM call
    result, None when it never ran), reading state at the inclusion block."""
    balances = _Balances(w3, snapshot, block, sleep)
    return [
        _evaluate_one(w3, index, checks.get(index), result, balances, block, sleep)
        for index, result in sorted(executed.items())
    ]
