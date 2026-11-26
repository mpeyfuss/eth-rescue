import json
import sys
from contextlib import AbstractContextManager, nullcontext
from typing import Any, Literal, Protocol, TextIO

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
    def final_state(self, victim_balance: int, delegation: str | None) -> None: ...
    def waiting(self, text: str) -> AbstractContextManager[Any]: ...


def _json_safe(value: Any) -> Any:
    """Integers become decimal strings so JS consumers keep full precision."""
    if isinstance(value, bool) or value is None:
        return value
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list | tuple):
        return [_json_safe(item) for item in value]
    return value


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
            ui.success(f"Gas wallet funded ({ui.ether(balance)}).")
            return
        ui.warning(
            f"Send at least {ui.ether(required - balance)} to {address} "
            f"(balance {ui.ether(balance)} / needs {ui.ether(required)}). "
            "Re-checking every block."
        )

    def simulation(self, result: SimulationResult) -> None:
        ui.render_simulation_result(result)

    def submitting(self, first_block: int, last_block: int) -> None:
        ui.info(f"Submitting bundle for blocks {first_block}-{last_block} ...")

    def included(self, receipts: list[TxReceipt]) -> None:
        ui.success(f"Bundle included in block {receipts[0]['blockNumber']}.")
        for receipt in receipts:
            status = "" if receipt["status"] == 1 else " (reverted)"
            ui.info(f"  {receipt['transactionHash'].to_0x_hex()}{status}")

    def outcomes(
        self, actions: list[RescueData], outcomes: list[ActionOutcome]
    ) -> None:
        ui.render_outcome_report(actions, outcomes)

    def final_state(self, victim_balance: int, delegation: str | None) -> None:
        ui.render_final_state(victim_balance, delegation)

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
        self._emit("message", level=level, text=ui.plain(text))

    def phase(self, title: str) -> None:
        self._emit("phase", title=title)

    def plan(self, actions: list[RescueData]) -> None:
        self._emit(
            "plan",
            actions=[
                {
                    "index": index,
                    "action": action.get("plan_action"),
                    "step": action.get("plan_step"),
                    "target": action["address"],
                    "signature": action["function_signature"],
                    "args": _json_safe(action["args"]),
                    "description": action.get("description"),
                }
                for index, action in enumerate(actions)
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
            statuses=[r["status"] for r in receipts],
        )

    def outcomes(
        self, actions: list[RescueData], outcomes: list[ActionOutcome]
    ) -> None:
        self._emit(
            "outcomes",
            outcomes=[
                {
                    "index": outcome.index,
                    "action": actions[outcome.index].get("plan_action"),
                    "step": actions[outcome.index].get("plan_step"),
                    "status": outcome.status,
                    "detail": outcome.detail,
                    "description": actions[outcome.index].get("description"),
                }
                for outcome in outcomes
            ],
        )

    def final_state(self, victim_balance: int, delegation: str | None) -> None:
        self._emit(
            "final_state",
            victim_balance=str(victim_balance),
            victim_delegation=delegation,
        )

    def waiting(self, text: str) -> AbstractContextManager[Any]:
        return nullcontext()
