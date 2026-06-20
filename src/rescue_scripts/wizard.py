import json
import os
from datetime import datetime

from rescue_scripts import templates
from rescue_scripts.prompts import (
    prompt_address,
    prompt_choice,
    prompt_int,
    prompt_yes_no,
)
from rescue_scripts.types import RescueData

CONFIG_DIR = "configs"


def _load_config() -> list[RescueData]:
    """Power-user path: load an existing JSON config of rescue actions."""
    while True:
        path = input("Path to JSON config file: ").strip()
        try:
            with open(path, "r") as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                raise ValueError("config must be a non-empty JSON list of actions")
            print(f"  ✅ Loaded {len(data)} action(s) from {path}")
            return data
        except (OSError, ValueError, json.JSONDecodeError) as e:
            print(f"  ⚠️  Could not load config: {e}")


def _build_action(safe_wallet: str, victim_hint: str) -> RescueData:
    """Ask the user which kind of rescue and gather the inputs for one action."""
    print("\nWhat do you want to rescue?")
    print("  1) ERC721 NFT")
    print("  2) ERC1155 NFT")
    print("  3) ERC20 token")
    print("  4) Contract ownership")
    print("  5) Custom (advanced: function signature + args)")
    choice = prompt_choice("Choose an option", 5)

    if choice == 1:
        contract = prompt_address("NFT contract address")
        token_id = prompt_int("Token ID")
        return templates.erc721_transfer(contract, victim_hint, safe_wallet, token_id)

    if choice == 2:
        contract = prompt_address("NFT contract address")
        token_id = prompt_int("Token ID")
        amount = prompt_int("Amount to transfer")
        return templates.erc1155_transfer(
            contract, victim_hint, safe_wallet, token_id, amount
        )

    if choice == 3:
        contract = prompt_address("Token contract address")
        print(
            "  (amount is in base units / wei, e.g. 1 token with 18 decimals "
            "= 1000000000000000000)"
        )
        amount = prompt_int("Amount to transfer (base units)")
        return templates.erc20_transfer(contract, safe_wallet, amount)

    if choice == 4:
        contract = prompt_address("Contract address to transfer ownership of")
        return templates.transfer_ownership(contract, safe_wallet)

    # choice == 5
    contract = prompt_address("Target contract address")
    sig = input("Function signature (e.g. transfer(address,uint256)): ").strip()
    raw_args = input('Args as JSON array (e.g. ["0xabc...", 78]): ').strip()
    args = json.loads(raw_args) if raw_args else []
    return templates.custom(contract, sig, args)


def print_plan(actions: list[RescueData]) -> None:
    """Render the list of rescue actions in plain English."""
    print("\n=== Rescue plan ===")
    for i, a in enumerate(actions, 1):
        desc = a.get("description") or a["function_signature"]
        print(f"  {i}. {desc}")
        print(f"       contract: {a['address']}")
        print(f"       call:     {a['function_signature']} {a['args']}")
    print("===================\n")


def save_config(actions: list[RescueData]) -> None:
    """Offer to save the built plan to a JSON file for future reuse."""
    if not prompt_yes_no("Save this plan to a file for future use?", default=True):
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    default_name = f"rescue-{datetime.now():%Y%m%d-%H%M%S}.json"
    name = input(f"File name [{default_name}]: ").strip() or default_name
    if not name.endswith(".json"):
        name += ".json"
    path = (
        name
        if os.path.isabs(name) or os.sep in name
        else os.path.join(CONFIG_DIR, name)
    )
    with open(path, "w") as f:
        json.dump(actions, f, indent=2)
    print(f"  ✅ Saved plan to {path}")


def build_rescue_data(victim_address: str = "") -> list[RescueData]:
    """
    Step 1 of the flow: produce the list of rescue actions, either by loading a
    saved JSON config or by walking the guided wizard. Wizard-built plans can be
    saved for next time.
    """
    print("How would you like to set up the rescue?")
    print("  1) Guided wizard (recommended)")
    print("  2) Load a saved JSON config file")
    if prompt_choice("Choose an option", 2) == 2:
        return _load_config()

    victim_hint = victim_address or prompt_address(
        "Compromised (victim) wallet address"
    )
    safe_wallet = prompt_address("Safe wallet to send everything to")

    actions: list[RescueData] = []
    while True:
        actions.append(_build_action(safe_wallet, victim_hint))
        if not prompt_yes_no("Add another action to this bundle?", default=False):
            break

    print_plan(actions)
    save_config(actions)
    return actions
