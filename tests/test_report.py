import io
import json
import subprocess
import sys
from pathlib import Path

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
