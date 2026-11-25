import json
import sys
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Literal, Protocol, TextIO

from web3 import Web3
from web3.types import TxReceipt

from eth_rescue import ui
from eth_rescue.types import ActionOutcome, PreparedBundle, RescueData, SimulationResult

Level = Literal["info", "success", "warning", "error"]


class Reporter(Protocol):
    def message(self, level: Level, text: str) -> None: ...
    def phase(self, title: str) -> None: ...
    def plan(self, actions: list[RescueData]) -> None: ...
    def bundle(self, bundle: PreparedBundle) -> None: ...
    def funding(self, address: str, required: int, balance: int) -> None: ...
    def simulation(self, result: SimulationResult) -> None: ...
    def submitting(self, first_block: int, last_block: int) -> None: ...
    def included(self, receipts: list[TxReceipt]) -> None: ...
    def outcomes(
        self, actions: list[RescueData], outcomes: list[ActionOutcome]
    ) -> None: ...
    def waiting(self, text: str) -> AbstractContextManager[Any]: ...


def _ether(wei: int) -> str:
    return f"{Web3.from_wei(wei, 'ether')} ETH"


class RichReporter:
    def message(self, level: Level, text: str) -> None:
        getattr(ui, level)(text)

    def phase(self, title: str) -> None:
        ui.section(title)

    def plan(self, actions: list[RescueData]) -> None:
        ui.render_rescue_plan(actions)

    def bundle(self, bundle: PreparedBundle) -> None:
        ui.render_bundle(bundle)

    def funding(self, address: str, required: int, balance: int) -> None:
        if balance >= required:
            ui.success(f"Gas wallet funded ({_ether(balance)}).")
            return
        ui.warning(
            f"Send at least {_ether(required - balance)} to {address} "
            f"(balance {_ether(balance)} / needs {_ether(required)}). "
            "Re-checking every block."
        )

    def simulation(self, result: SimulationResult) -> None:
        ui.render_simulation_result(result)

    def submitting(self, first_block: int, last_block: int) -> None:
        ui.info(f"Submitting bundle for blocks {first_block}-{last_block} ...")

    def included(self, receipts: list[TxReceipt]) -> None:
        ui.success(f"Bundle included in block {receipts[0]['blockNumber']}.")
        for receipt in receipts:
            ui.info(f"  {receipt['transactionHash'].to_0x_hex()}")

    def outcomes(
        self, actions: list[RescueData], outcomes: list[ActionOutcome]
    ) -> None:
        ui.render_outcome_report(actions, outcomes)

    def waiting(self, text: str) -> AbstractContextManager[Any]:
        return ui.console.status(text)


class JsonReporter:
    """Newline-delimited JSON events; wei amounts are decimal strings."""

    def __init__(self, stream: TextIO | None = None):
        self.stream = stream or sys.stdout

    def _emit(self, event: str, **fields: Any) -> None:
        self.stream.write(json.dumps({"event": event, **fields}) + "\n")
        self.stream.flush()

    def message(self, level: Level, text: str) -> None:
        self._emit("message", level=level, text=text)

    def phase(self, title: str) -> None:
        self._emit("phase", title=title)

    def plan(self, actions: list[RescueData]) -> None:
        self._emit(
            "plan",
            actions=[
                {
                    "target": action["address"],
                    "signature": action["function_signature"],
                    "description": action.get("description"),
                }
                for action in actions
            ],
        )

    def bundle(self, bundle: PreparedBundle) -> None:
        self._emit(
            "bundle",
            transactions=[transaction.role for transaction in bundle.transactions],
            target_block=bundle.target_block,
            max_fee_per_gas=str(bundle.max_fee_per_gas),
            required_funding=str(bundle.required_funding),
        )

    def funding(self, address: str, required: int, balance: int) -> None:
        self._emit(
            "funding",
            address=address,
            required=str(required),
            balance=str(balance),
            funded=balance >= required,
        )

    def simulation(self, result: SimulationResult) -> None:
        self._emit("simulation", result=result)

    def submitting(self, first_block: int, last_block: int) -> None:
        self._emit("submitting", first_block=first_block, last_block=last_block)

    def included(self, receipts: list[TxReceipt]) -> None:
        self._emit(
            "included",
            block=receipts[0]["blockNumber"],
            transactions=[r["transactionHash"].to_0x_hex() for r in receipts],
        )

    def outcomes(
        self, actions: list[RescueData], outcomes: list[ActionOutcome]
    ) -> None:
        self._emit(
            "outcomes",
            outcomes=[
                {
                    "index": outcome.index,
                    "status": outcome.status,
                    "description": actions[outcome.index].get("description"),
                }
                for outcome in outcomes
            ],
        )

    def waiting(self, text: str) -> AbstractContextManager[Any]:
        return nullcontext()
