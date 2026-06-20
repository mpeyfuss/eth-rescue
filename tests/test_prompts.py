import pytest

from rescue_scripts import prompts


class DummyPrompt:
    def __init__(self, value):
        self.value = value

    def ask(self):
        return self.value


def test_prompt_address_retries_and_checksums(monkeypatch):
    values = iter(["not-an-address", "0xde709f2102306220921060314715629080e2fb77"])
    warnings = []

    monkeypatch.setattr(
        prompts,
        "prompt_text",
        lambda label, default=None, allow_cancel=False: next(values),
    )
    monkeypatch.setattr(prompts.ui, "warning", warnings.append)

    assert prompts.prompt_address("Wallet") == "0xde709f2102306220921060314715629080e2fb77"
    assert warnings == ["That doesn't look like a valid Ethereum address. Try again."]


def test_prompt_int_retries_and_accepts_hex(monkeypatch):
    values = iter(["abc", "0x10"])
    warnings = []

    monkeypatch.setattr(
        prompts,
        "prompt_text",
        lambda label, default=None, allow_cancel=False: next(values),
    )
    monkeypatch.setattr(prompts.ui, "warning", warnings.append)

    assert prompts.prompt_int("Token ID") == 16
    assert warnings == ["Please enter a whole number. Try again."]


def test_prompt_float_uses_default(monkeypatch):
    monkeypatch.setattr(
        prompts,
        "prompt_text",
        lambda label, default=None, allow_cancel=False: "",
    )

    assert prompts.prompt_float("Priority fee", default=0.25) == 0.25


def test_prompt_float_retries_invalid(monkeypatch):
    values = iter(["nan-not", "1.5"])
    warnings = []

    monkeypatch.setattr(
        prompts,
        "prompt_text",
        lambda label, default=None, allow_cancel=False: next(values),
    )
    monkeypatch.setattr(prompts.ui, "warning", warnings.append)

    assert prompts.prompt_float("Priority fee") == 1.5
    assert warnings == ["Please enter a number. Try again."]


def test_prompt_yes_no_with_default_uses_confirm(monkeypatch):
    monkeypatch.setattr(
        prompts.questionary,
        "confirm",
        lambda label, default=None: DummyPrompt(False),
    )

    assert prompts.prompt_yes_no("Continue?", default=True) is False


def test_prompt_select_returns_selected_value(monkeypatch):
    captured = {}

    def fake_select(label, choices):
        captured["label"] = label
        captured["choices"] = choices
        return DummyPrompt(choices[1].value)

    monkeypatch.setattr(prompts.questionary, "select", fake_select)

    assert prompts.prompt_select("Pick one", [("First", 1), ("Second", 2)]) == 2
    assert captured["label"] == "Pick one"
    assert [choice.title for choice in captured["choices"]] == ["First", "Second"]


def test_prompt_cancellation_raises_keyboard_interrupt():
    with pytest.raises(KeyboardInterrupt):
        prompts._ask(DummyPrompt(None))


def test_prompt_text_can_cancel(monkeypatch):
    captured = {}

    def fake_text(label, default=""):
        captured["label"] = label
        return DummyPrompt("cancel")

    monkeypatch.setattr(
        prompts.questionary,
        "text",
        fake_text,
    )

    with pytest.raises(prompts.PromptCancelled):
        prompts.prompt_text("Contract", allow_cancel=True)
    assert captured["label"] == "Contract"


def test_prompt_text_does_not_cancel_unless_enabled(monkeypatch):
    monkeypatch.setattr(
        prompts.questionary,
        "text",
        lambda label, default="": DummyPrompt("cancel"),
    )

    assert prompts.prompt_text("Contract") == "cancel"
