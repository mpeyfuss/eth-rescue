import argparse
import getpass
import math
import sys
import warnings
from decimal import Decimal, InvalidOperation

from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import to_hex

from eth_rescue import engine, ui
from eth_rescue.bundle import (
    DEFAULT_GAS_MULTIPLIER,
    MAX_GAS_MULTIPLIER,
    MIN_GAS_MULTIPLIER,
)
from eth_rescue.engine import ExitCode, RescueContext
from eth_rescue.networks import NETWORKS, connect
from eth_rescue.plan import Plan, PlanError, expand, load_plan, render_schema
from eth_rescue.report import JsonReporter, Reporter, RichReporter

INTERRUPTED = 130
INTERRUPTED_MESSAGE = (
    "Interrupted. A bundle that was already submitted may still be included."
)


class CliError(Exception):
    pass


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
    ui.render_rescue_plan(expand(plan))
    ui.success("Plan is valid.")
    return ExitCode.OK


def _schema(_: argparse.Namespace) -> int:
    sys.stdout.write(render_schema())
    return ExitCode.OK


def _new_gas_wallet(_: argparse.Namespace) -> int:
    account = Account.create()
    # Plain lines, not a rich panel, so the key never wraps or picks up borders.
    print(f"Address:     {account.address}")
    print(f"Private key: {to_hex(account.key)}")
    ui.warning(
        "Save the private key now; it is not stored anywhere. Put the address in "
        "your plan as gas_wallet, and keep the key to recover any unused balance."
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
    except EOFError:
        return False


def _context(
    args: argparse.Namespace, reporter: Reporter
) -> tuple[Plan, RescueContext]:
    plan = load_plan(args.plan)
    network = NETWORKS[plan.network]
    if network["eth_rescue"] is None or network["eth_rescue_codehash"] is None:
        raise CliError(f"EthRescue is not deployed on {network['label']} yet")
    w3 = connect(network, Account.create(), rpc=args.rpc)
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


def _rescue(args: argparse.Namespace) -> int:
    reporter: Reporter = JsonReporter() if args.json else RichReporter()
    try:
        plan, ctx = _context(args, reporter)
    except (PlanError, CliError, ConnectionError) as e:
        reporter.message("error", str(e))
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
        reporter.message("warning", INTERRUPTED_MESSAGE)
        return INTERRUPTED


def _add_rescue_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("plan", help="path to plan.json")
    parser.add_argument("--rpc", help="override the network's public RPC URL")
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="eth-rescue",
        description="Rescue assets from a compromised Ethereum account.",
    )
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
    gas_wallet_commands.add_parser(
        "new", help="generate a fresh gas wallet"
    ).set_defaults(handler=_new_gas_wallet)

    schema = commands.add_parser("schema", help="print the plan JSON Schema")
    schema.set_defaults(handler=_schema)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return int(args.handler(args))
    except KeyboardInterrupt:
        # Outside a rescue (e.g. at a key prompt); --json output stays NDJSON.
        if getattr(args, "json", False):
            JsonReporter().message("warning", INTERRUPTED_MESSAGE)
        else:
            ui.warning(INTERRUPTED_MESSAGE)
        return INTERRUPTED


def run() -> None:
    sys.exit(main())
