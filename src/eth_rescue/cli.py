import argparse
import sys

from eth_rescue import ui
from eth_rescue.plan import PlanError, expand, load_plan, render_schema

EXIT_OK = 0
EXIT_INVALID = 1


def _validate(args: argparse.Namespace) -> int:
    try:
        plan = load_plan(args.plan)
    except PlanError as e:
        ui.error(f"Invalid plan {args.plan}:\n{e}")
        return EXIT_INVALID
    ui.info(f"Network:    {plan.network}")
    ui.info(f"Victim:     {plan.victim}")
    ui.info(f"Safe:       {plan.safe}")
    ui.info(f"Gas wallet: {plan.gas_wallet or '(not set)'}")
    ui.render_rescue_plan(expand(plan))
    ui.success("Plan is valid.")
    return EXIT_OK


def _schema(_: argparse.Namespace) -> int:
    sys.stdout.write(render_schema())
    return EXIT_OK


def _wizard(_: argparse.Namespace) -> int:
    from eth_rescue.rescue import run as run_wizard

    run_wizard()
    return EXIT_OK


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

    schema = commands.add_parser("schema", help="print the plan JSON Schema")
    schema.set_defaults(handler=_schema)

    wizard = commands.add_parser(
        "wizard", help="legacy guided flow (removed once `run` lands)"
    )
    wizard.set_defaults(handler=_wizard)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.handler(args)


def run() -> None:
    sys.exit(main())
