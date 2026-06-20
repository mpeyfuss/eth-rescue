from typing import Any

from eth_utils import is_address, to_checksum_address
import questionary

from rescue_scripts import ui

CANCEL_WORDS = {"back", "cancel", "exit"}


class PromptCancelled(Exception):
    """Raised when a cancel-aware prompt should abandon the current flow."""


def _ask(prompt: Any) -> Any:
    value = prompt.ask()
    if value is None:
        raise KeyboardInterrupt
    return value


def _raise_if_cancelled(value: str, allow_cancel: bool) -> None:
    if allow_cancel and value.casefold() in CANCEL_WORDS:
        raise PromptCancelled


def prompt_text(
    label: str,
    default: str | None = None,
    allow_cancel: bool = False,
) -> str:
    """Prompt for free-form text."""
    value = _ask(questionary.text(label, default=default or "")).strip()
    _raise_if_cancelled(value, allow_cancel)
    return value


def prompt_secret(label: str, allow_cancel: bool = False) -> str:
    """Prompt for hidden free-form text."""
    value = _ask(questionary.password(label)).strip()
    _raise_if_cancelled(value, allow_cancel)
    return value


def prompt_path(label: str, allow_cancel: bool = False) -> str:
    """Prompt for a file path."""
    value = _ask(questionary.path(label)).strip()
    _raise_if_cancelled(value, allow_cancel)
    return value


def pause(label: str = "Press Enter to continue...") -> None:
    questionary.press_any_key_to_continue(label).ask()


def prompt_select[T](label: str, choices: list[tuple[str, T]]) -> T:
    """Prompt for a selection using arrow-key navigation."""
    selected = _ask(
        questionary.select(
            label,
            choices=[
                questionary.Choice(title=title, value=value) for title, value in choices
            ],
        )
    )
    return selected


def prompt_address(label: str, allow_cancel: bool = False) -> str:
    """Prompt for an Ethereum address, re-asking until it's valid."""
    while True:
        value = prompt_text(label, allow_cancel=allow_cancel)
        if is_address(value):
            return to_checksum_address(value)
        ui.warning("That doesn't look like a valid Ethereum address. Try again.")


def prompt_int(label: str, allow_cancel: bool = False) -> int:
    """Prompt for a whole number (supports 0x hex), re-asking until valid."""
    while True:
        value = prompt_text(label, allow_cancel=allow_cancel)
        try:
            return int(value, 0) if value.lower().startswith("0x") else int(value)
        except ValueError:
            ui.warning("Please enter a whole number. Try again.")


def prompt_float(
    label: str,
    default: float | None = None,
    allow_cancel: bool = False,
) -> float:
    """Prompt for a decimal number, optionally accepting a default on empty input."""
    while True:
        value = prompt_text(
            label,
            default=str(default) if default is not None else None,
            allow_cancel=allow_cancel,
        )
        if not value and default is not None:
            return default
        try:
            return float(value)
        except ValueError:
            ui.warning("Please enter a number. Try again.")


def prompt_yes_no(label: str, default: bool | None = None) -> bool:
    """Prompt for a yes/no answer."""
    if default is None:
        return prompt_select(label, [("Yes", True), ("No", False)])
    return bool(_ask(questionary.confirm(label, default=default)))
