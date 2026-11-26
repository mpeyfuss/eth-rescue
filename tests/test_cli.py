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
COMPROMISED = Account.create()
GAS = Account.create()
DESTINATION = Account.create()


def _plan(**overrides) -> Plan:
    return Plan.model_validate(
        {
            "version": 1,
            "network": "mainnet",
            "compromised": COMPROMISED.address,
            "destination": DESTINATION.address,
            "actions": [{"type": "ownership", "contract": DESTINATION.address}],
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
    assert "Invalid plan" in capsys.readouterr().err


def test_validate_json_prints_ndjson_events(capsys):
    assert main(["validate", "--json", str(EXAMPLE)]) == ExitCode.OK
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert "plan" in {event["event"] for event in events}
    assert events[-1] == {
        "event": "message",
        "level": "success",
        "text": "Plan is valid.",
    }


def test_validate_json_reports_invalid_plan_as_an_event(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text("[]")

    assert main(["validate", "--json", str(path)]) == ExitCode.INVALID
    [event] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert event["level"] == "error"
    assert event["text"].startswith("Invalid plan")


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
    monkeypatch.setitem(cli.NETWORKS["mainnet"], "eth_rescue", "0x" + "55" * 20)
    monkeypatch.setitem(cli.NETWORKS["mainnet"], "eth_rescue_codehash", None)
    monkeypatch.setattr(cli, "connect", lambda *a, **k: pytest.fail("connected"))
    monkeypatch.setattr(cli, "read_accounts", lambda plan: pytest.fail("read keys"))

    assert main(["run", str(EXAMPLE), "--json"]) == ExitCode.INVALID
    event = json.loads(capsys.readouterr().out.splitlines()[0])
    assert event["text"] == "EthRescue is not deployed on Ethereum mainnet yet"


def test_read_accounts_returns_matching_keys(keys):
    keys.extend([to_hex(COMPROMISED.key), to_hex(GAS.key)])

    compromised, gas = read_accounts(_plan(gas_wallet=GAS.address))

    assert (compromised.address, gas.address) == (COMPROMISED.address, GAS.address)


def test_read_accounts_requires_terminal(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    with pytest.raises(CliError, match="hidden prompt"):
        read_accounts(_plan())


def test_read_accounts_rejects_invalid_key(keys):
    keys.append("not-a-key")

    with pytest.raises(CliError, match="Compromised wallet private key is not valid"):
        read_accounts(_plan())


def test_read_accounts_rejects_key_for_other_compromised(keys):
    keys.append(to_hex(GAS.key))

    with pytest.raises(CliError, match="plan's compromised"):
        read_accounts(_plan())


def test_read_accounts_rejects_key_for_other_gas_wallet(keys):
    keys.extend([to_hex(COMPROMISED.key), to_hex(Account.create().key)])

    with pytest.raises(CliError, match="plan's gas_wallet"):
        read_accounts(_plan(gas_wallet=GAS.address))


def test_read_accounts_rejects_destination_as_gas_wallet(keys):
    keys.extend([to_hex(COMPROMISED.key), to_hex(DESTINATION.key)])

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


def test_hidden_prompt_refuses_to_echo_the_key(monkeypatch):
    def echoing(prompt):
        cli.warnings.warn("echo", cli.getpass.GetPassWarning)
        return "0x" + "11" * 32

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", echoing)

    with pytest.raises(CliError, match="cannot hide typed input"):
        read_accounts(_plan())


def test_hidden_prompt_eof_is_a_clean_error(monkeypatch):
    def eof(prompt):
        raise EOFError

    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", eof)

    with pytest.raises(CliError, match="no private key"):
        read_accounts(_plan())


def test_confirm_treats_eof_as_no(monkeypatch):
    def eof():
        raise EOFError

    monkeypatch.setattr("builtins.input", eof)

    assert cli._confirm() is False


@pytest.mark.parametrize("value", ["inf", "nan", "-1", "abc"])
def test_funding_timeout_rejects_non_finite_or_negative(value):
    with pytest.raises(argparse.ArgumentTypeError):
        cli.funding_timeout(value)


def test_funding_timeout_accepts_zero():
    assert cli.funding_timeout("0") == 0


def test_interrupt_at_key_prompt_says_nothing_was_sent(monkeypatch, capsys):
    def interrupt(plan):
        raise KeyboardInterrupt

    monkeypatch.setattr(cli, "connect", lambda *a, **k: object())
    monkeypatch.setattr(cli, "read_accounts", interrupt)

    assert main(["run", str(EXAMPLE), "--json"]) == ExitCode.INTERRUPTED
    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert events[-1]["text"] == "Interrupted. Nothing was sent."


def test_mainnet_deployment_is_pinned():
    mainnet = cli.NETWORKS["mainnet"]

    assert mainnet["eth_rescue"] == "0xdC7eC38149c572a29Ac89813eda47140Ac8cD969"
    assert mainnet["eth_rescue_codehash"] == (
        "0x5635918452603f8714e9e5f7348cc9b7f4d397529d7a92ba2ed818db0e7048fa"
    )


# ---------------------------------------------------------------------------
# argument parsing
# ---------------------------------------------------------------------------
def test_usage_errors_exit_invalid_not_simulation_failed(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["simulate", str(EXAMPLE), "--yes"])

    assert exit_info.value.code == ExitCode.INVALID
    assert "unrecognized arguments: --yes" in capsys.readouterr().err


def test_run_help_lists_every_exit_code(capsys):
    with pytest.raises(SystemExit):
        main(["run", "--help"])

    help_text = capsys.readouterr().out
    assert set(cli.RUN_EXIT_CODES) == set(ExitCode)
    for code in ExitCode:
        assert f"  {code.value:>3}  " in help_text


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out.startswith("eth-rescue ")


def test_rpc_defaults_to_environment_variable(monkeypatch):
    monkeypatch.setenv(cli.RPC_ENV, "https://rpc.example/v2/key")

    args = cli.build_parser().parse_args(["run", "plan.json"])

    assert args.rpc == "https://rpc.example/v2/key"


def test_plain_http_rpc_warns(reporter):
    cli._check_rpc_url("http://rpc.example", reporter)
    cli._check_rpc_url("http://127.0.0.1:8545", reporter)

    assert len(reporter.messages("warning")) == 1
    assert "plain http" in reporter.messages("warning")[0]


@pytest.mark.parametrize("url", ["rpc.example", "ws://rpc.example", "https://"])
def test_non_http_rpc_is_rejected(reporter, url):
    with pytest.raises(CliError, match="http"):
        cli._check_rpc_url(url, reporter)


# ---------------------------------------------------------------------------
# gas-wallet new --out
# ---------------------------------------------------------------------------
def test_gas_wallet_new_writes_key_to_private_file(tmp_path, capsys):
    path = tmp_path / "gas.key"

    assert main(["gas-wallet", "new", "--out", str(path)]) == ExitCode.OK

    output = capsys.readouterr().out
    key = path.read_text().strip()
    assert path.stat().st_mode & 0o777 == 0o600
    assert key not in output
    assert "Private key:" not in output
    assert f"Address:     {Account.from_key(key).address}" in output


def test_gas_wallet_new_never_overwrites_a_file(tmp_path, capsys):
    path = tmp_path / "gas.key"
    path.write_text("keep me")

    assert main(["gas-wallet", "new", "--out", str(path)]) == ExitCode.INVALID

    assert path.read_text() == "keep me"
    assert "already exists" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# confirmation
# ---------------------------------------------------------------------------
def _answer(value):
    def answer():
        if isinstance(value, BaseException):
            raise value
        return value

    return answer


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ("y", True),
        (" YES ", True),
        ("n", False),
        ("", False),
        ("yep", False),
        (EOFError(), False),
        (KeyboardInterrupt(), False),
    ],
)
def test_confirm_answers(monkeypatch, answer, expected):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", _answer(answer))

    assert cli._confirm() is expected


def test_confirm_without_terminal_is_no(monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)
    monkeypatch.setattr("builtins.input", lambda: pytest.fail("prompted"))

    assert cli._confirm() is False


# ---------------------------------------------------------------------------
# rescue dispatch and failures
# ---------------------------------------------------------------------------
@pytest.fixture
def dispatch(monkeypatch):
    """Connect and read keys without a network or terminal; record what the
    engine is called with."""
    calls = {}
    monkeypatch.setattr(cli, "connect", lambda *a, **k: object())
    monkeypatch.setattr(
        cli, "read_accounts", lambda plan: (Account.create(), Account.create())
    )

    def fake_run(ctx, rescue_data, *, confirm, legacy_fallback):
        calls["run"] = (ctx, rescue_data, confirm, legacy_fallback)
        return ExitCode.OK

    def fake_simulate(ctx, rescue_data, legacy_fallback):
        calls["simulate"] = (ctx, rescue_data, legacy_fallback)
        return ExitCode.OK

    monkeypatch.setattr(cli.engine, "run", fake_run)
    monkeypatch.setattr(cli.engine, "simulate", fake_simulate)
    return calls


def test_run_passes_options_to_the_engine(dispatch, monkeypatch):
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: False)

    code = main(
        [
            "run",
            str(EXAMPLE),
            "--yes",
            "--gas-multiplier",
            "2",
            "--funding-timeout",
            "60",
        ]
    )

    assert code == ExitCode.OK
    ctx, rescue_data, confirm, legacy_fallback = dispatch["run"]
    plan = cli.load_plan(EXAMPLE)
    assert confirm() is True
    assert legacy_fallback is plan.options.legacy_fallback
    assert ctx.gas_multiplier == Decimal(2)
    assert ctx.funding_timeout == 60
    assert ctx.max_blocks == plan.options.max_blocks
    assert ctx.extra_priority_fee_gwei == Decimal(plan.options.priority_fee_gwei)
    assert ctx.rescue_contract == cli.NETWORKS["mainnet"]["eth_rescue"]
    assert len(rescue_data) == len(cli.expand(plan))


def test_run_without_yes_asks(dispatch):
    assert main(["run", str(EXAMPLE)]) == ExitCode.OK

    assert dispatch["run"][2] is cli._confirm


def test_simulate_routes_to_engine_simulate(dispatch):
    assert main(["simulate", str(EXAMPLE)]) == ExitCode.OK

    assert "simulate" in dispatch and "run" not in dispatch


def _crash(sent, error):
    def run(ctx, rescue_data, *, confirm, legacy_fallback):
        ctx.progress.sent = sent
        raise error

    return run


@pytest.mark.parametrize(
    ("sent", "code"), [(False, ExitCode.INVALID), (True, ExitCode.NOT_INCLUDED)]
)
def test_unexpected_error_mid_rescue_is_reported_cleanly(
    dispatch, monkeypatch, capsys, sent, code
):
    error = RuntimeError("502 for url: https://rpc.example/v2/SECRET")
    monkeypatch.setattr(cli.engine, "run", _crash(sent, error))

    assert main(["run", str(EXAMPLE), "--json"]) == code

    texts = [json.loads(line)["text"] for line in capsys.readouterr().out.splitlines()]
    assert "Stopped by an unexpected error" in texts[-2]
    assert "SECRET" not in " ".join(texts)
    if sent:
        assert "may still be included" in texts[-1]
    else:
        assert texts[-1] == "Nothing was sent."


def test_interrupt_after_submission_names_the_gas_wallet(dispatch, monkeypatch, capsys):
    monkeypatch.setattr(cli.engine, "run", _crash(True, KeyboardInterrupt()))

    assert main(["run", str(EXAMPLE), "--json"]) == ExitCode.INTERRUPTED

    texts = [json.loads(line)["text"] for line in capsys.readouterr().out.splitlines()]
    assert texts[-2] == "Interrupted."
    assert "may still be included" in texts[-1]
    assert "Check the gas wallet 0x" in texts[-1]


def test_network_error_while_connecting_is_clean(monkeypatch, capsys):
    def connect(*args, **kwargs):
        raise OSError("Max retries exceeded with url: /v2/SECRET")

    monkeypatch.setattr(cli, "connect", connect)

    assert main(["run", str(EXAMPLE), "--json"]) == ExitCode.INVALID
    [event] = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert event["text"].startswith("Could not reach the network")
    assert "SECRET" not in event["text"]
