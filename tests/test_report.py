import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from eth_rescue import templates, ui
from eth_rescue.report import JsonReporter
from eth_rescue.types import ActionOutcome

EXAMPLE = Path(__file__).parents[1] / "examples" / "full_plan.json"
TOKEN = "0x" + "11" * 20
SAFE = "0x" + "22" * 20


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

    JsonReporter(stream).plan([templates.erc20_transfer(TOKEN, SAFE, amount)])

    [event] = _events(stream)
    assert event["actions"][0]["args"] == [SAFE, str(amount)]


def test_json_reporter_outcomes_include_detail():
    stream = io.StringIO()
    data = [templates.erc20_transfer(TOKEN, SAFE, 5)]

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
