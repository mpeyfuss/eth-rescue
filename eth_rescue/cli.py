import argparse
import getpass
import math
import os
import sys
import warnings
from decimal import Decimal, InvalidOperation
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import NoReturn
from urllib.parse import urlsplit

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import to_hex

from eth_rescue import engine, ui
from eth_rescue.bundle import (
    DEFAULT_GAS_MULTIPLIER,
    MAX_GAS_MULTIPLIER,
    MIN_GAS_MULTIPLIER,
)
from eth_rescue.engine import HIGH_PRIORITY_FEE_GWEI, ExitCode, RescueContext
from eth_rescue.networks import NETWORKS, connect
from eth_rescue.plan import Plan, PlanError, expand, load_plan, render_schema
from eth_rescue.report import JsonReporter, Reporter, RichReporter

INTERRUPTED = 130
RPC_ENV = "ETH_RESCUE_RPC_URL"
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


class CliError(Exception):
    pass


class ArgumentParser(argparse.ArgumentParser):
    """Usage errors exit 1 (invalid input); argparse's own 2 means
    "simulation failed" here."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(ExitCode.INVALID, f"{self.prog}: error: {message}\n")


def _validate(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
    except PlanError as e:
        ui.error(f"Invalid plan {args.plan}:\n{e}")
        return ExitCode.INVALID
    ui.info(f"Network:    {plan.network}")
    ui.info(f"Victim:     {plan.victim}")
    ui.info(f"Safe:       {plan.safe}")
    ui.info(f"Gas wallet: {plan.gas_wallet or '(not set)'}")
    ui.info(f"Extra priority fee: {plan.options.priority_fee_gwei} gwei")
    if Decimal(plan.options.priority_fee_gwei) > HIGH_PRIORITY_FEE_GWEI:
        ui.warning(
            "options.priority_fee_gwei is unusually high and is paid on every gas "
            "unit; check it for a typo."
        )
    ui.render_rescue_plan(expand(plan))
    ui.success("Plan is valid.")
    return ExitCode.OK


def _schema(_: argparse.Namespace) -> int:
    sys.stdout.write(render_schema())
    return ExitCode.OK


def _write_new_key(path: Path, key: str) -> None:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as e:
        raise CliError(f"{path} already exists; not overwriting it") from e
    except OSError as e:
        raise CliError(f"could not create {path}: {e.strerror or e}") from e
    with os.fdopen(fd, "w") as file:
        file.write(key + "\n")


def _new_gas_wallet(args: argparse.Namespace) -> int:
    account = Account.create()
    key = to_hex(account.key)
    if args.out is not None:
        try:
            _write_new_key(args.out, key)
        except CliError as e:
            ui.error(str(e))
            return ExitCode.INVALID
    # Plain lines, not a rich panel, so the key never wraps or picks up borders.
    print(f"Address:     {account.address}")
    if args.out is None:
        print(f"Private key: {key}")
        ui.warning(
            "Save the private key now; it is not stored anywhere. Put the address "
            "in your plan as gas_wallet, and keep the key to recover any unused "
            "balance."
        )
    else:
        ui.success(
            f"Private key written to {args.out} (readable only by you). Put the "
            "address in your plan as gas_wallet, and keep the file to recover any "
            "unused balance."
        )
    return ExitCode.OK


def gas_multiplier(value: str) -> Decimal:
    try:
        multiplier = Decimal(value)
    except InvalidOperation as e:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from e
    if not multiplier.is_finite() or not (
        MIN_GAS_MULTIPLIER <= multiplier <= MAX_GAS_MULTIPLIER
    ):
        raise argparse.ArgumentTypeError(
            f"must be between {MIN_GAS_MULTIPLIER} and {MAX_GAS_MULTIPLIER}"
        )
    return multiplier


def funding_timeout(value: str) -> float:
    try:
        seconds = float(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not a number: {value!r}") from e
    if not math.isfinite(seconds) or seconds < 0:
        raise argparse.ArgumentTypeError("must be a finite number of seconds >= 0")
    return seconds


def _hidden_input(prompt: str) -> str:
    """getpass that refuses to fall back to echoing the key."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", getpass.GetPassWarning)
        try:
            return getpass.getpass(prompt)
        except getpass.GetPassWarning as e:
            raise CliError(
                "this terminal cannot hide typed input; run in a regular terminal"
            ) from e
        except EOFError as e:
            raise CliError("no private key was entered") from e


def _read_key(label: str) -> LocalAccount:
    raw = _hidden_input(f"{label} private key (hidden): ").strip()
    try:
        return Account.from_key(raw)
    except ValueError as e:
        raise CliError(f"{label} private key is not valid") from e


def read_accounts(plan: Plan) -> tuple[LocalAccount, LocalAccount]:
    if not sys.stdin.isatty():
        raise CliError("private keys are entered at a hidden prompt; run in a terminal")
    victim = _read_key("Compromised wallet")
    if victim.address != plan.victim:
        raise CliError(
            f"compromised wallet key is for {victim.address}, "
            f"but the plan's victim is {plan.victim}"
        )
    gas = _read_key("Gas wallet")
    if plan.gas_wallet is not None and gas.address != plan.gas_wallet:
        raise CliError(
            f"gas wallet key is for {gas.address}, "
            f"but the plan's gas_wallet is {plan.gas_wallet}"
        )
    if gas.address in (plan.victim, plan.safe):
        raise CliError("gas wallet must differ from the victim and safe wallets")
    return victim, gas


def _confirm() -> bool:
    if not sys.stdin.isatty():
        return False
    sys.stderr.write("Send the rescue bundle now? [y/N] ")
    sys.stderr.flush()
    try:
        return input().strip().lower() in {"y", "yes"}
    except (EOFError, KeyboardInterrupt):
        sys.stderr.write("\n")
        return False


def _check_rpc_url(url: str, reporter: Reporter) -> None:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise CliError("the RPC URL must be an http(s) URL")
    if parts.scheme == "http" and parts.hostname not in LOCAL_HOSTS:
        reporter.message(
            "warning",
            "The RPC URL is plain http: anyone on the network path can read and "
            "alter its answers. Prefer https.",
        )


def _context(
    args: argparse.Namespace, reporter: Reporter
) -> tuple[Plan, RescueContext]:
    plan = load_plan(args.plan)
    network = NETWORKS[plan.network]
    if network["eth_rescue"] is None or network["eth_rescue_codehash"] is None:
        raise CliError(f"EthRescue is not deployed on {network['label']} yet")
    if args.rpc:
        _check_rpc_url(args.rpc, reporter)
    # A throwaway relay identity: Flashbots reputation is not worth linking runs.
    w3 = connect(network, Account.create(), rpc=args.rpc or None)
    reporter.message("info", f"Connected to {network['label']}.")
    victim, gas = read_accounts(plan)
    return plan, RescueContext(
        w3=w3,
        victim=victim,
        gas=gas,
        safe=plan.safe,
        reporter=reporter,
        rescue_contract=network["eth_rescue"],
        rescue_codehash=network["eth_rescue_codehash"],
        network=network["label"],
        extra_priority_fee_gwei=Decimal(plan.options.priority_fee_gwei),
        gas_multiplier=args.gas_multiplier,
        max_blocks=plan.options.max_blocks,
        funding_timeout=args.funding_timeout,
    )


def _after_stop(reporter: Reporter, ctx: RescueContext) -> None:
    if not ctx.progress.sent:
        reporter.message("warning", "Nothing was sent.")
        return
    reporter.message(
        "warning",
        "A bundle was already submitted and may still be included. Check the gas "
        f"wallet {ctx.gas.address} on a block explorer before running again.",
    )


def _rescue(args: argparse.Namespace) -> int:
    reporter: Reporter = JsonReporter() if args.json else RichReporter()
    try:
        plan, ctx = _context(args, reporter)
    except (PlanError, CliError, ConnectionError) as e:
        reporter.message("error", str(e))
        return ExitCode.INVALID
    except Exception as e:  # HTTP and RPC errors from the provider
        reporter.message("error", f"Could not reach the network: {e}")
        return ExitCode.INVALID

    rescue_data = expand(plan)
    try:
        if args.command == "simulate":
            return engine.simulate(ctx, rescue_data, plan.options.legacy_fallback)
        return engine.run(
            ctx,
            rescue_data,
            confirm=(lambda: True) if args.yes else _confirm,
            legacy_fallback=plan.options.legacy_fallback,
        )
    except KeyboardInterrupt:
        reporter.message("warning", "Interrupted.")
        _after_stop(reporter, ctx)
        return INTERRUPTED
    except Exception as e:
        reporter.message("error", f"Stopped by an unexpected error: {e}")
        _after_stop(reporter, ctx)
        return ExitCode.NOT_INCLUDED if ctx.progress.sent else ExitCode.INVALID


def _add_rescue_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan", help="path to plan.json")
    parser.add_argument(
        "--rpc",
        default=os.environ.get(RPC_ENV),
        metavar="URL",
        help=(
            "override the network's public RPC URL. Prefer setting "
            f"{RPC_ENV}, which keeps an API key out of your shell history"
        ),
    )
    parser.add_argument(
        "--json", action="store_true", help="print NDJSON events instead of text"
    )
    parser.add_argument(
        "--funding-timeout",
        type=funding_timeout,
        default=engine.DEFAULT_FUNDING_TIMEOUT,
        metavar="SECONDS",
        help="how long to wait for the gas wallet to be funded (default: 1800)",
    )
    parser.add_argument(
        "--gas-multiplier",
        type=gas_multiplier,
        default=DEFAULT_GAS_MULTIPLIER,
        metavar="X",
        help=(
            "multiply each action's live gas estimate by X "
            f"(default: {DEFAULT_GAS_MULTIPLIER}; {MIN_GAS_MULTIPLIER}-"
            f"{MAX_GAS_MULTIPLIER}). The plan's gas is always the minimum"
        ),
    )
    parser.set_defaults(handler=_rescue)


def _version() -> str:
    try:
        return version("eth-rescue")
    except PackageNotFoundError:
        return "unknown"


def build_parser() -> argparse.ArgumentParser:
    parser = ArgumentParser(
        prog="eth-rescue",
        description="Rescue assets from a compromised Ethereum account.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {_version()}")
    commands = parser.add_subparsers(dest="command", required=True)

    validate = commands.add_parser(
        "validate", help="check a plan file without touching the network"
    )
    validate.add_argument("plan", help="path to plan.json")
    validate.set_defaults(handler=_validate)

    simulate = commands.add_parser(
        "simulate", help="build and simulate the rescue bundle without sending it"
    )
    _add_rescue_arguments(simulate)

    run = commands.add_parser("run", help="simulate, confirm, and submit the rescue")
    _add_rescue_arguments(run)
    run.add_argument(
        "--yes", action="store_true", help="skip the send confirmation prompt"
    )

    gas_wallet = commands.add_parser("gas-wallet", help="gas wallet utilities")
    gas_wallet_commands = gas_wallet.add_subparsers(dest="gas_command", required=True)
    new_gas_wallet = gas_wallet_commands.add_parser(
        "new", help="generate a fresh gas wallet"
    )
    new_gas_wallet.add_argument(
        "--out",
        type=Path,
        metavar="FILE",
        help="write the private key to a new file readable only by you, "
        "instead of printing it",
    )
    new_gas_wallet.set_defaults(handler=_new_gas_wallet)

    schema = commands.add_parser("schema", help="print the plan JSON Schema")
    schema.set_defaults(handler=_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        # Outside a rescue (e.g. at a key prompt); --json output stays NDJSON.
        message = "Interrupted. Nothing was sent."
        if getattr(args, "json", False):
            JsonReporter().message("warning", message)
        else:
            ui.warning(message)
        return INTERRUPTED


def run() -> None:
    sys.exit(main())
