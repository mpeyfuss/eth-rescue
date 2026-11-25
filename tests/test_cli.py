import argparse
import json
from decimal import Decimal
from pathlib import Path

import pytest
from eth_account import Account
from eth_utils import to_hex

from eth_rescue import cli
from eth_rescue.cli import CliError, main, read_accounts
from eth_rescue.engine import ExitCode
from eth_rescue.plan import Plan, plan_json_schema

EXAMPLE = Path(__file__).parents[1] / "examples" / "full_plan.json"
VICTIM = Account.create()
GAS = Account.create()
SAFE = Account.create()


def _plan(**overrides) -> Plan:
    return Plan.model_validate(
        {
            "version": 1,
            "network": "sepolia",
            "victim": VICTIM.address,
            "safe": SAFE.address,
            "actions": [{"type": "ownership", "contract": SAFE.address}],
        }
        | overrides
    )


@pytest.fixture
def keys(monkeypatch):
    """Answer hidden prompts in order and pretend stdin is a terminal."""
    answers: list[str] = []
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: answers.pop(0))
    return answers


def test_validate_accepts_valid_plan(capsys):
    assert main(["validate", str(EXAMPLE)]) == ExitCode.OK
    assert "Plan is valid" in capsys.readouterr().out


def test_validate_rejects_invalid_plan(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text("[]")

    assert main(["validate", str(path)]) == ExitCode.INVALID
    assert "Invalid plan" in capsys.readouterr().out


def test_schema_prints_json_schema(capsys):
    assert main(["schema"]) == ExitCode.OK
    assert json.loads(capsys.readouterr().out) == plan_json_schema()


def test_gas_wallet_new_prints_address_and_key(capsys):
    assert main(["gas-wallet", "new"]) == ExitCode.OK
    output = capsys.readouterr().out
    assert "Address:" in output
    assert "Private key: 0x" in output


def test_run_rejects_invalid_plan_before_connecting(tmp_path, monkeypatch, capsys):
    path = tmp_path / "plan.json"
    path.write_text("{}")
    monkeypatch.setattr(cli, "connect", lambda *a, **k: pytest.fail("connected"))

    assert main(["run", str(path), "--json"]) == ExitCode.INVALID
    event = json.loads(capsys.readouterr().out.splitlines()[0])
    assert event["event"] == "message"
    assert event["level"] == "error"


def test_run_refuses_network_without_eth_rescue_before_connecting(monkeypatch, capsys):
    monkeypatch.setitem(cli.NETWORKS["mainnet"], "eth_rescue", None)
    monkeypatch.setattr(cli, "connect", lambda *a, **k: pytest.fail("connected"))
    monkeypatch.setattr(cli, "read_accounts", lambda plan: pytest.fail("read keys"))

    assert main(["run", str(EXAMPLE), "--json"]) == ExitCode.INVALID
    event = json.loads(capsys.readouterr().out.splitlines()[0])
    assert event["text"] == "EthRescue is not deployed on Ethereum mainnet yet"


def test_read_accounts_returns_matching_keys(keys):
    keys.extend([to_hex(VICTIM.key), to_hex(GAS.key)])

    victim, gas = read_accounts(_plan(gas_wallet=GAS.address))

    assert (victim.address, gas.address) == (VICTIM.address, GAS.address)


def test_read_accounts_requires_terminal(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    with pytest.raises(CliError, match="hidden prompt"):
        read_accounts(_plan())


def test_read_accounts_rejects_invalid_key(keys):
    keys.append("not-a-key")

    with pytest.raises(CliError, match="Compromised wallet private key is not valid"):
        read_accounts(_plan())


def test_read_accounts_rejects_key_for_other_victim(keys):
    keys.append(to_hex(GAS.key))

    with pytest.raises(CliError, match="plan's victim"):
        read_accounts(_plan())


def test_read_accounts_rejects_key_for_other_gas_wallet(keys):
    keys.extend([to_hex(VICTIM.key), to_hex(Account.create().key)])

    with pytest.raises(CliError, match="plan's gas_wallet"):
        read_accounts(_plan(gas_wallet=GAS.address))


def test_read_accounts_rejects_safe_as_gas_wallet(keys):
    keys.extend([to_hex(VICTIM.key), to_hex(SAFE.key)])

    with pytest.raises(CliError, match="must differ"):
        read_accounts(_plan())


@pytest.mark.parametrize("value", ["1", "1.25", "10"])
def test_gas_multiplier_accepts_bounded_decimals(value):
    assert cli.gas_multiplier(value) == Decimal(value)


@pytest.mark.parametrize("value", ["0.9", "10.5", "-2", "abc", "NaN"])
def test_gas_multiplier_rejects_out_of_range_or_non_numeric(value):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.gas_multiplier(value)


def test_rescue_commands_default_and_parse_gas_multiplier():
    parser = cli.build_parser()

    assert parser.parse_args(["simulate", "plan.json"]).gas_multiplier == Decimal("1.5")
    assert parser.parse_args(
        ["run", "plan.json", "--gas-multiplier", "3"]
    ).gas_multiplier == Decimal(3)
