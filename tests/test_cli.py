import json
from pathlib import Path

from eth_rescue.cli import EXIT_INVALID, EXIT_OK, main
from eth_rescue.plan import plan_json_schema

EXAMPLE = Path(__file__).parents[1] / "examples" / "full_plan.json"


def test_validate_accepts_valid_plan(capsys):
    assert main(["validate", str(EXAMPLE)]) == EXIT_OK
    assert "Plan is valid" in capsys.readouterr().out


def test_validate_rejects_invalid_plan(tmp_path, capsys):
    path = tmp_path / "plan.json"
    path.write_text("[]")

    assert main(["validate", str(path)]) == EXIT_INVALID
    assert "Invalid plan" in capsys.readouterr().out


def test_schema_prints_json_schema(capsys):
    assert main(["schema"]) == EXIT_OK
    assert json.loads(capsys.readouterr().out) == plan_json_schema()
