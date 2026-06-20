import json

import pytest

from rescue_scripts import wizard

SAFE = "0x1111111111111111111111111111111111111111"
VICTIM = "0x2222222222222222222222222222222222222222"
CONTRACT = "0x3333333333333333333333333333333333333333"


def test_build_erc721_action(monkeypatch):
    info = []

    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "erc721")
    monkeypatch.setattr(
        wizard,
        "prompt_address",
        lambda label, allow_cancel=False: CONTRACT,
    )
    monkeypatch.setattr(wizard, "prompt_int", lambda label, allow_cancel=False: 123)
    monkeypatch.setattr(wizard.ui, "info", info.append)

    action = wizard._build_action(SAFE, VICTIM)

    assert action["address"] == CONTRACT
    assert action["function_signature"] == "transferFrom(address,address,uint256)"
    assert action["args"] == [VICTIM, SAFE, 123]
    assert info == ["While adding an action, type cancel, back, or exit to abandon it."]


def test_build_erc1155_action(monkeypatch):
    ints = iter([123, 4])

    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "erc1155")
    monkeypatch.setattr(
        wizard,
        "prompt_address",
        lambda label, allow_cancel=False: CONTRACT,
    )
    monkeypatch.setattr(
        wizard,
        "prompt_int",
        lambda label, allow_cancel=False: next(ints),
    )

    action = wizard._build_action(SAFE, VICTIM)

    assert (
        action["function_signature"]
        == "safeTransferFrom(address,address,uint256,uint256,bytes)"
    )
    assert action["args"] == [VICTIM, SAFE, 123, 4, "0x"]


def test_build_erc20_action(monkeypatch):
    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "erc20")
    monkeypatch.setattr(
        wizard,
        "prompt_address",
        lambda label, allow_cancel=False: CONTRACT,
    )
    monkeypatch.setattr(wizard, "prompt_int", lambda label, allow_cancel=False: 1000)
    monkeypatch.setattr(wizard.ui, "info", lambda message: None)

    action = wizard._build_action(SAFE, VICTIM)

    assert action["function_signature"] == "transfer(address,uint256)"
    assert action["args"] == [SAFE, 1000]


def test_build_ownership_action(monkeypatch):
    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "ownership")
    monkeypatch.setattr(
        wizard,
        "prompt_address",
        lambda label, allow_cancel=False: CONTRACT,
    )

    action = wizard._build_action(SAFE, VICTIM)

    assert action["function_signature"] == "transferOwnership(address)"
    assert action["args"] == [SAFE]


def test_build_custom_action_retries_invalid_json_args(monkeypatch):
    texts = iter(["setValue(uint256)", "not json", "[42]"])
    warnings = []

    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "custom")
    monkeypatch.setattr(
        wizard,
        "prompt_address",
        lambda label, allow_cancel=False: CONTRACT,
    )
    monkeypatch.setattr(
        wizard,
        "prompt_text",
        lambda label, default=None, allow_cancel=False: next(texts),
    )
    monkeypatch.setattr(wizard.ui, "warning", warnings.append)

    action = wizard._build_action(SAFE, VICTIM)

    assert action["function_signature"] == "setValue(uint256)"
    assert action["args"] == [42]
    assert len(warnings) == 1


def test_validate_actions_accepts_well_formed_list():
    actions = [
        {
            "address": CONTRACT,
            "function_signature": "transfer(address,uint256)",
            "args": [SAFE, 1000],
        }
    ]

    assert wizard._validate_actions(actions) == actions


@pytest.mark.parametrize(
    "data, message",
    [
        ([], "non-empty"),
        ({"address": CONTRACT}, "non-empty"),
        (["not-an-object"], "must be a JSON object"),
        ([{"address": CONTRACT, "args": []}], "missing keys"),
        (
            [{"address": CONTRACT, "function_signature": "f()", "args": "nope"}],
            "must be a JSON array",
        ),
    ],
)
def test_validate_actions_rejects_bad_shapes(data, message):
    with pytest.raises(ValueError, match=message):
        wizard._validate_actions(data)


def test_load_config_retries_invalid_then_loads_valid(tmp_path, monkeypatch):
    bad = tmp_path / "missing.json"
    good = tmp_path / "good.json"
    expected = [
        {
            "address": CONTRACT,
            "function_signature": "transfer(address,uint256)",
            "args": [SAFE, 1000],
        }
    ]
    good.write_text(json.dumps(expected))
    paths = iter([str(bad), str(good)])
    warnings = []

    monkeypatch.setattr(wizard, "prompt_path", lambda label: next(paths))
    monkeypatch.setattr(wizard.ui, "warning", warnings.append)
    monkeypatch.setattr(wizard.ui, "success", lambda message: None)

    assert wizard._load_config() == expected
    assert len(warnings) == 1


def test_build_action_can_cancel_from_action_type(monkeypatch):
    warnings = []

    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "cancel")
    monkeypatch.setattr(wizard.ui, "warning", warnings.append)

    assert wizard._build_action(SAFE, VICTIM) is None
    assert warnings == ["Cancelled current action."]


def test_build_action_can_cancel_mid_action(monkeypatch):
    warnings = []

    def cancel_address(label, allow_cancel=False):
        raise wizard.PromptCancelled

    monkeypatch.setattr(wizard, "prompt_select", lambda label, choices: "erc721")
    monkeypatch.setattr(wizard, "prompt_address", cancel_address)
    monkeypatch.setattr(wizard.ui, "warning", warnings.append)

    assert wizard._build_action(SAFE, VICTIM) is None
    assert warnings == ["Cancelled current action."]


def test_build_rescue_data_returns_to_menu_after_cancel(monkeypatch):
    selections = iter(["wizard", "finish"])
    actions = [
        None,
        {
            "address": CONTRACT,
            "function_signature": "transfer(address,uint256)",
            "args": [SAFE, 1000],
        },
    ]

    monkeypatch.setattr(
        wizard,
        "prompt_select",
        lambda label, choices: next(selections),
    )
    monkeypatch.setattr(wizard, "prompt_address", lambda label: SAFE)
    monkeypatch.setattr(wizard, "_build_action", lambda safe, victim: actions.pop(0))
    monkeypatch.setattr(wizard, "print_plan", lambda actions: None)
    monkeypatch.setattr(wizard, "save_config", lambda actions: None)

    assert wizard.build_rescue_data(VICTIM) == [
        {
            "address": CONTRACT,
            "function_signature": "transfer(address,uint256)",
            "args": [SAFE, 1000],
        }
    ]
