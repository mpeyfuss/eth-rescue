from eth_utils import is_address, to_checksum_address


def prompt_address(label: str) -> str:
    """Prompt for an Ethereum address, re-asking until it's valid."""
    while True:
        value = input(f"{label}: ").strip()
        if is_address(value):
            return to_checksum_address(value)
        print("  ⚠️  That doesn't look like a valid Ethereum address. Try again.")


def prompt_int(label: str) -> int:
    """Prompt for a whole number (supports 0x hex), re-asking until valid."""
    while True:
        value = input(f"{label}: ").strip()
        try:
            return int(value, 0) if value.lower().startswith("0x") else int(value)
        except ValueError:
            print("  ⚠️  Please enter a whole number. Try again.")


def prompt_float(label: str, default: float | None = None) -> float:
    """Prompt for a decimal number, optionally accepting a default on empty input."""
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = input(f"{label}{suffix}: ").strip()
        if not value and default is not None:
            return default
        try:
            return float(value)
        except ValueError:
            print("  ⚠️  Please enter a number. Try again.")


def prompt_yes_no(label: str, default: bool | None = None) -> bool:
    """Prompt for a yes/no answer."""
    hint = "(y/n)" if default is None else ("(Y/n)" if default else "(y/N)")
    while True:
        value = input(f"{label} {hint}: ").strip().lower()
        if not value and default is not None:
            return default
        if value in ("y", "yes"):
            return True
        if value in ("n", "no"):
            return False
        print("  ⚠️  Please answer y or n.")


def prompt_choice(label: str, n: int) -> int:
    """Prompt for a menu choice in [1, n], re-asking until valid."""
    while True:
        value = input(f"{label} [1-{n}]: ").strip()
        if value.isdigit() and 1 <= int(value) <= n:
            return int(value)
        print(f"  ⚠️  Please choose a number between 1 and {n}.")
