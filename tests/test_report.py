import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from hexbytes import HexBytes

from eth_rescue import templates, ui
from eth_rescue.report import JsonReporter, RichReporter
from eth_rescue.types import ActionOutcome, BundleTransaction, PreparedBundle

EXAMPLE = Path(__file__).parents[1] / "examples" / "full_plan.json"
TOKEN = "0x" + "11" * 20
DESTINATION = "0x" + "22" * 20


GAS = "0x" + "33" * 20


def _bundle(*roles):
    return PreparedBundle(
        transactions=[BundleTransaction(role, HexBytes(b"\x01")) for role in roles],
        compromised_nonce=0,
        gas_nonce=0,
        priority_fee=0,
        max_fee_per_gas=564_574_168,
        effective_fee_cap=0,
        target_block=26_501_096,
        required_funding=813_416_668_691_515,
        compromised_funding=0,
        sweep_value=0,
    )


def _simulated(*errors):
    return {
        "results": [
            {"gasUsed": 21_000, **({"error": error} if error else {})}
            for error in errors
        ],
        "totalGasUsed": 21_000 * len(errors),
    }


def _events(stream):
    return [json.loads(line) for line in stream.getvalue().splitlines()]


def test_plain_strips_terminal_control_sequences():
    assert ui.plain("ok\x1b[2J\x1b]0;pwned\x07") == "ok[2J]0;pwned"


def test_plain_hides_signature_sized_hex():
    signed = "0x" + "ab" * 200

    assert ui.plain(f"bad tx {signed}") == "bad tx 0x…(200 bytes hidden)"
    assert ui.plain("0x" + "11" * 20) == "0x" + "11" * 20


def test_plain_bounds_length():
    assert len(ui.plain("x" * 10_000)) == ui.MAX_MESSAGE_LENGTH + 1


def test_json_reporter_sanitizes_messages():
    stream = io.StringIO()

    JsonReporter(stream).message("error", "relay said \x1b[31m0x" + "ff" * 100)

    [event] = _events(stream)
    assert "\x1b" not in event["text"]
    assert "100 bytes hidden" in event["text"]


def test_json_reporter_plan_keeps_large_ints_exact():
    stream = io.StringIO()
    amount = 2**256 - 1

    JsonReporter(stream).plan([templates.erc20_transfer(TOKEN, DESTINATION, amount)])

    [event] = _events(stream)
    assert event["actions"][0]["args"] == [DESTINATION, str(amount)]


def test_json_reporter_outcomes_include_detail():
    stream = io.StringIO()
    data = [templates.erc20_transfer(TOKEN, DESTINATION, 5)]

    JsonReporter(stream).outcomes(data, [ActionOutcome(0, "partial", "only 2 of 5")])

    [event] = _events(stream)
    [outcome] = event["outcomes"]
    assert (outcome["status"], outcome["detail"]) == ("partial", "only 2 of 5")


def _cli(*args):
    return subprocess.run(
        [str(Path(sys.executable).parent / "eth-rescue"), *args],
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_smoke_validate_and_schema():
    validated = _cli("validate", str(EXAMPLE))
    schema = _cli("schema")

    assert validated.returncode == 0, validated.stderr
    assert schema.returncode == 0, schema.stderr
    assert json.loads(schema.stdout)["title"]


def test_plain_redacts_url_paths_queries_and_credentials():
    assert (
        ui.plain(
            "400 Client Error for url: https://eth-mainnet.g.alchemy.com/v2/SECRET"
        )
        == "400 Client Error for url: https://eth-mainnet.g.alchemy.com/…"
    )
    assert ui.plain("https://user:SECRET@rpc.example?key=SECRET") == (
        "https://rpc.example/…"
    )
    assert ui.plain("https://relay.flashbots.net/") == "https://relay.flashbots.net"


def test_plain_redacts_bare_url_paths_from_connection_errors():
    text = (
        "HTTPSConnectionPool(host='mainnet.infura.io', port=443): Max retries "
        "exceeded with url: /v3/SECRET (Caused by timeout)"
    )

    assert "SECRET" not in ui.plain(text)
    assert "with url: /… (Caused by timeout)" in ui.plain(text)


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("(see https://rpc.example/v2/a)SECRET)", "(see https://rpc.example/…)"),
        ("(see https://rpc.example/v2/SECRET)", "(see https://rpc.example/…)"),
        ("(see https://rpc.example)", "(see https://rpc.example)"),
        ("https://rpc.example/x(1)/SECRET", "https://rpc.example/…"),
        ("with url: /v3/a)SECRET (Caused by", "with url: /… (Caused by"),
        ("(with url: /v3/SECRET)", "(with url: /…)"),
    ],
)
def test_plain_redacts_urls_past_a_closing_parenthesis(text, shown):
    assert ui.plain(text) == shown


def test_json_reporter_redacts_urls():
    stream = io.StringIO()

    JsonReporter(stream).message("error", "failed: https://rpc.example/v2/SECRET")

    [event] = _events(stream)
    assert "SECRET" not in event["text"]


def test_warnings_and_errors_go_to_stderr(capsys):
    ui.info("progress")
    ui.warning("careful")
    ui.error("broken")

    captured = capsys.readouterr()
    assert "progress" in captured.out
    assert "careful" in captured.err and "broken" in captured.err
    assert "careful" not in captured.out and "broken" not in captured.out


@pytest.mark.parametrize(
    ("wei", "round_up", "shown"),
    [
        (0, False, "0 ETH"),
        (10**15, False, "0.001 ETH"),
        (821_272_627_694_656, True, "0.0008213 ETH"),
        (821_272_627_694_656, False, "0.0008213 ETH"),
        (81_639_484_781_964, False, "0.00008164 ETH"),
        (12_345_678_900_000_000_000, False, "12.3457 ETH"),
        (1, True, "0.000000000000000001 ETH"),
    ],
)
def test_ether_shows_about_four_significant_digits(wei, round_up, shown):
    assert ui.ether(wei, round_up=round_up) == shown


def test_ether_rounding_up_never_shows_less_than_needed():
    assert ui.ether(100_000_100_000_000, round_up=True) == "0.0001001 ETH"
    assert ui.ether(100_000_100_000_000) == "0.0001 ETH"


def test_rich_funding_prints_an_unchanged_status_once(capsys):
    reporter = RichReporter()

    for _ in range(3):
        reporter.funding(GAS, 821_272_627_694_656, 0)
    reporter.funding(GAS, 821_272_627_694_656, 10**15)
    reporter.funding(GAS, 813_416_668_691_515, 10**15)

    out = capsys.readouterr()
    assert out.err.count("Send at least 0.0008213 ETH") == 1
    assert out.out.count("Gas wallet funded (0.001 ETH)") == 1


def test_rich_funding_prints_again_in_a_new_phase(capsys):
    reporter = RichReporter()

    reporter.funding(GAS, 1, 10**15)
    reporter.phase("Phase 2: legacy fallback")
    reporter.funding(GAS, 1, 10**15)

    assert capsys.readouterr().out.count("Gas wallet funded") == 2


def test_rich_recheck_that_still_succeeds_is_one_line(capsys):
    reporter = RichReporter()

    reporter.simulation(_simulated(None), _bundle("rescue-7702"), recheck=False)
    first = capsys.readouterr().out
    reporter.simulation(_simulated(None), _bundle("rescue-7702"), recheck=True)
    again = capsys.readouterr().out

    assert "rescue-7702" in first and "Simulation succeeded." in first
    assert again.strip() == "OK Re-simulated for block 26501096: still succeeds."


def test_rich_recheck_that_fails_shows_the_table(capsys):
    RichReporter().simulation(
        _simulated(None, "execution reverted"), _bundle("fund", "rescue"), recheck=True
    )

    out = capsys.readouterr().out
    assert "execution reverted" in out and "Simulation succeeded." not in out


def test_rich_bundle_is_one_line(capsys):
    RichReporter().bundle(_bundle("undelegate", "fund", "rescue", "sweep"))

    assert (
        capsys.readouterr().out.split()
        == (
            "INFO Bundle for block 26501096: undelegate → fund → rescue → sweep. "
            "Max fee 0.5646 gwei; the gas wallet needs 0.0008135 ETH."
        ).split()
    )


def test_rich_interim_outcomes_are_left_to_the_combined_report(capsys):
    data = [templates.erc20_transfer(TOKEN, DESTINATION, 5)]

    RichReporter().outcomes(data, [ActionOutcome(0, "rescued")], interim=True)

    assert capsys.readouterr().out == ""


def test_json_reporter_marks_rechecks_and_interim_outcomes():
    stream = io.StringIO()
    reporter = JsonReporter(stream)
    data = [templates.erc20_transfer(TOKEN, DESTINATION, 5)]

    reporter.simulation(_simulated(None), _bundle("rescue-7702"), recheck=True)
    reporter.outcomes(data, [ActionOutcome(0, "rescued")], interim=True)

    simulation, outcomes = _events(stream)
    assert (simulation["recheck"], simulation["target_block"]) == (True, 26_501_096)
    assert outcomes["interim"] is True
