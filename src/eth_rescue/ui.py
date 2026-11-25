from rich import box
from rich.console import Console
from rich.table import Table

from web3 import Web3

from eth_rescue.types import (
    ActionOutcome,
    PreparedBundle,
    RescueData,
    SimulationResult,
)

console = Console()


def section(text: str) -> None:
    console.print(f"\n[bold cyan]{text}[/bold cyan]")


def info(message: str) -> None:
    console.print(f"[cyan]INFO[/cyan] {message}")


def success(message: str) -> None:
    console.print(f"[green]OK[/green] {message}")


def warning(message: str) -> None:
    console.print(f"[yellow]WARN[/yellow] {message}")


def error(message: str) -> None:
    console.print(f"[red]ERROR[/red] {message}")


def render_rescue_plan(actions: list[RescueData]) -> None:
    table = Table(title="Rescue plan", box=box.ASCII, show_lines=True)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Action", overflow="fold")
    table.add_column("Contract", overflow="fold")
    table.add_column("Call", overflow="fold")

    for i, action in enumerate(actions, 1):
        desc = action.get("description") or action["function_signature"]
        table.add_row(
            str(i),
            desc,
            action["address"],
            f"{action['function_signature']} {action['args']}",
        )

    console.print()
    console.print(table)
    console.print()


def render_bundle(bundle: PreparedBundle) -> None:
    table = Table(title="Prepared bundle", box=box.ASCII)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Transaction")
    for i, transaction in enumerate(bundle.transactions, 1):
        table.add_row(str(i), transaction.role)
    console.print(table)
    info(
        f"Target block {bundle.target_block} @ max "
        f"{Web3.from_wei(bundle.max_fee_per_gas, 'gwei')} gwei; gas wallet needs "
        f"{Web3.from_wei(bundle.required_funding, 'ether')} ETH"
    )


def render_simulation_result(result: SimulationResult) -> None:
    table = Table(title="Bundle simulation", box=box.ASCII)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Tx hash", overflow="fold")
    table.add_column("Gas used", justify="right")
    table.add_column("Status", overflow="fold")

    for i, tx_result in enumerate(result.get("results", []), 1):
        error_text = tx_result.get("error") or tx_result.get("revert") or ""
        table.add_row(
            str(i),
            str(tx_result.get("txHash", "")),
            f"{int(tx_result.get('gasUsed', 0)):,}",
            error_text or "OK",
        )

    table.add_section()
    table.add_row(
        "",
        "Total gas used",
        f"{int(result.get('totalGasUsed', 0)):,}",
        f"Bundle hash: {result.get('bundleHash', '')}",
    )
    console.print(table)


def render_outcome_report(
    actions: list[RescueData], outcomes: list[ActionOutcome]
) -> None:
    styles = {
        "rescued": "[green]rescued[/green]",
        "failed": "[red]failed[/red]",
        "unverified": "[yellow]unverified[/yellow]",
    }
    table = Table(title="Rescue outcome", box=box.ASCII, show_lines=True)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Action", overflow="fold")
    table.add_column("Result")

    for outcome in outcomes:
        action = actions[outcome.index]
        desc = action.get("description") or action["function_signature"]
        table.add_row(
            str(outcome.index + 1), desc, styles.get(outcome.status, outcome.status)
        )

    console.print()
    console.print(table)
    if any(o.status == "unverified" for o in outcomes):
        warning(
            "Unverified actions could not be checked automatically; confirm them "
            "manually. They are not retried in the legacy fallback."
        )
    console.print()
