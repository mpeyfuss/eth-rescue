import re
from typing import Any

from rich import box
from rich.console import Console
from rich.markup import escape
from rich.table import Table
from rich.text import Text
from web3 import Web3

from eth_rescue.types import (
    ActionOutcome,
    PreparedBundle,
    RescueData,
    SimulationResult,
)

console = Console()
# Warnings and errors stay visible when stdout is redirected.
err_console = Console(stderr=True)

# 65 bytes or more of hex is a signature, a signed transaction or raw calldata:
# never useful in a message, and possibly sensitive.
LONG_HEX_RE = re.compile(r"0x[0-9a-fA-F]{130,}")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
# RPC URLs often carry an API key in the path, query or userinfo, and HTTP
# client errors repeat them; only the scheme and host are kept. Everything up to
# whitespace or a quote is treated as part of the URL, since ")" is legal in one.
URL_RE = re.compile(
    r"\b([a-z][a-z0-9+.-]*://)(?:[^\s/@]+@)?([^\s/?#'\")]+)([^\s'\"]*)", re.I
)
# urllib3 errors name the path on its own ("with url: /v2/<key>").
URL_PATH_RE = re.compile(r"(\burl: )(/[^\s'\"]*)", re.I)
MAX_MESSAGE_LENGTH = 2_000


def _closing_paren(rest: str) -> str:
    """A trailing ")" that closes a parenthesis opened before the URL."""
    return ")" if rest.endswith(")") and rest.count(")") > rest.count("(") else ""


def _redact_url(match: re.Match[str]) -> str:
    scheme, host, rest = match.groups()
    close = _closing_paren(rest)
    rest = rest[: len(rest) - len(close)]
    return f"{scheme}{host}{'' if rest in ('', '/') else '/…'}{close}"


def _redact_path(match: re.Match[str]) -> str:
    prefix, path = match.groups()
    return f"{prefix}/…{_closing_paren(path)}"


def plain(text: Any) -> str:
    """Untrusted text (labels, provider and relay errors) made safe to print:
    no terminal control sequences, no URL paths or credentials, no long hex
    blobs, bounded length."""
    cleaned = CONTROL_RE.sub("", str(text))
    cleaned = URL_RE.sub(_redact_url, cleaned)
    cleaned = URL_PATH_RE.sub(_redact_path, cleaned)
    cleaned = LONG_HEX_RE.sub(
        lambda m: f"0x…({(len(m.group()) - 2) // 2} bytes hidden)", cleaned
    )
    if len(cleaned) > MAX_MESSAGE_LENGTH:
        cleaned = cleaned[:MAX_MESSAGE_LENGTH] + "…"
    return cleaned


def _literal(text: Any) -> Text:
    return Text(plain(text))


def section(text: str) -> None:
    console.print(f"\n[bold cyan]{escape(plain(text))}[/bold cyan]")


def info(message: str) -> None:
    console.print(f"[cyan]INFO[/cyan] {escape(plain(message))}")


def success(message: str) -> None:
    console.print(f"[green]OK[/green] {escape(plain(message))}")


def warning(message: str) -> None:
    err_console.print(f"[yellow]WARN[/yellow] {escape(plain(message))}")


def error(message: str) -> None:
    err_console.print(f"[red]ERROR[/red] {escape(plain(message))}")


def ether(wei: int) -> str:
    return f"{Web3.from_wei(wei, 'ether')} ETH"


def _title(action: RescueData) -> str:
    return action.get("description") or action["function_signature"]


def describe_call(action: RescueData) -> str:
    return f"{action['function_signature']} {action['args']}"


def render_rescue_plan(actions: list[RescueData]) -> None:
    table = Table(title="Rescue plan", box=box.ASCII, show_lines=True)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Action", overflow="fold")
    table.add_column("Contract", overflow="fold")
    table.add_column("Call", overflow="fold")

    for i, action in enumerate(actions, 1):
        table.add_row(
            str(i),
            _literal(_title(action)),
            _literal(action["address"]),
            _literal(describe_call(action)),
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
        f"{ether(bundle.required_funding)}"
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
            _literal(tx_result.get("txHash", "")),
            f"{int(tx_result.get('gasUsed', 0)):,}",
            _literal(error_text or "OK"),
        )

    table.add_section()
    table.add_row(
        "",
        "Total gas used",
        f"{int(result.get('totalGasUsed', 0)):,}",
        _literal(f"Bundle hash: {result.get('bundleHash', '')}"),
    )
    console.print(table)


STATUS_STYLES = {
    "rescued": "green",
    "partial": "yellow",
    "unverified": "yellow",
    "failed": "red",
    "not_executed": "red",
}
STATUS_LABELS = {"not_executed": "not executed"}


def render_outcome_report(
    actions: list[RescueData], outcomes: list[ActionOutcome]
) -> None:
    table = Table(title="Rescue outcome", box=box.ASCII, show_lines=True)
    table.add_column("#", justify="right", style="bold")
    table.add_column("Action", overflow="fold")
    table.add_column("Result")
    table.add_column("Detail", overflow="fold")

    for outcome in outcomes:
        label = STATUS_LABELS.get(outcome.status, outcome.status)
        table.add_row(
            str(outcome.index + 1),
            _literal(_title(actions[outcome.index])),
            Text(label, style=STATUS_STYLES.get(outcome.status, "")),
            _literal(outcome.detail or ""),
        )

    console.print()
    console.print(table)
    if any(o.status == "unverified" for o in outcomes):
        warning(
            "Unverified actions ran but could not be checked automatically; confirm "
            "them manually. They are never retried automatically."
        )
    if any(o.status == "partial" for o in outcomes):
        warning(
            "Partial actions are not retried automatically, so nothing is sent "
            "twice. Follow each one's detail, and rerun with a new plan if needed."
        )
    console.print()


def render_final_state(victim_balance: int, delegation: str | None) -> None:
    info(
        f"ETH left in the compromised wallet: {Web3.from_wei(victim_balance, 'ether')}"
    )
    if delegation is None:
        info("The compromised wallet has no EIP-7702 delegation.")
    else:
        warning(f"The compromised wallet is delegated to {delegation}.")
