import json
import os
from datetime import datetime

from rescue_scripts import templates
from rescue_scripts import ui
from rescue_scripts.prompts import (
    PromptCancelled,
    prompt_address,
    prompt_int,
    prompt_path,
    prompt_select,
    prompt_text,
    prompt_yes_no,
)
from rescue_scripts.types import RescueData

CONFIG_DIR = "configs"


def _load_config() -> list[RescueData]:
    """Power-user path: load an existing JSON config of rescue actions."""
    while True:
        path = prompt_path("Path to JSON config file")
        try:
            with open(path) as f:
                data = json.load(f)
            if not isinstance(data, list) or not data:
                raise ValueError("config must be a non-empty JSON list of actions")
            ui.success(f"Loaded {len(data)} action(s) from {path}")
            return data
        except (OSError, ValueError, json.JSONDecodeError) as e:
            ui.warning(f"Could not load config: {e}")


def _build_action(safe_wallet: str, victim_hint: str) -> RescueData | None:
    """Ask the user which kind of rescue and gather the inputs for one action."""
    ui.info("While adding an action, type cancel, back, or exit to abandon it.")
    choice = prompt_select(
        "What do you want to rescue?",
        [
            ("ERC721 NFT", "erc721"),
            ("ERC1155 NFT", "erc1155"),
            ("ERC20 token", "erc20"),
            ("Contract ownership", "ownership"),
            ("Custom (advanced: function signature + args)", "custom"),
            ("Cancel current action", "cancel"),
        ],
    )

    if choice == "cancel":
        ui.warning("Cancelled current action.")
        return None

    try:
        if choice == "erc721":
            contract = prompt_address("NFT contract address", allow_cancel=True)
            token_id = prompt_int("Token ID", allow_cancel=True)
            return templates.erc721_transfer(
                contract, victim_hint, safe_wallet, token_id
            )

        if choice == "erc1155":
            contract = prompt_address("NFT contract address", allow_cancel=True)
            token_id = prompt_int("Token ID", allow_cancel=True)
            amount = prompt_int("Amount to transfer", allow_cancel=True)
            return templates.erc1155_transfer(
                contract, victim_hint, safe_wallet, token_id, amount
            )

        if choice == "erc20":
            contract = prompt_address("Token contract address", allow_cancel=True)
            ui.info(
                "Amount is in base units / wei. Example: 1 token with 18 decimals "
                "is 1000000000000000000."
            )
            amount = prompt_int("Amount to transfer (base units)", allow_cancel=True)
            return templates.erc20_transfer(contract, safe_wallet, amount)

        if choice == "ownership":
            contract = prompt_address(
                "Contract address to transfer ownership of",
                allow_cancel=True,
            )
            return templates.transfer_ownership(contract, safe_wallet)

        contract = prompt_address("Target contract address", allow_cancel=True)
        sig = prompt_text(
            "Function signature",
            default="transfer(address,uint256)",
            allow_cancel=True,
        )
        while True:
            raw_args = prompt_text(
                'Args as JSON array (e.g. ["0xabc...", 78])',
                default="[]",
                allow_cancel=True,
            )
            try:
                args = json.loads(raw_args) if raw_args else []
                if not isinstance(args, list):
                    raise ValueError("args must be a JSON array")
                break
            except (ValueError, json.JSONDecodeError) as e:
                ui.warning(f"Could not parse args: {e}")
        return templates.custom(contract, sig, args)
    except PromptCancelled:
        ui.warning("Cancelled current action.")
        return None


def print_plan(actions: list[RescueData]) -> None:
    """Render the list of rescue actions in plain English."""
    ui.render_rescue_plan(actions)


def save_config(actions: list[RescueData]) -> None:
    """Offer to save the built plan to a JSON file for future reuse."""
    if not prompt_yes_no("Save this plan to a file for future use?", default=True):
        return
    os.makedirs(CONFIG_DIR, exist_ok=True)
    default_name = f"rescue-{datetime.now():%Y%m%d-%H%M%S}.json"
    name = prompt_text("File name", default=default_name) or default_name
    if not name.endswith(".json"):
        name += ".json"
    path = (
        name
        if os.path.isabs(name) or os.sep in name
        else os.path.join(CONFIG_DIR, name)
    )
    with open(path, "w") as f:
        json.dump(actions, f, indent=2)
    ui.success(f"Saved plan to {path}")


def build_rescue_data(victim_address: str = "") -> list[RescueData]:
    """
    Step 1 of the flow: produce the list of rescue actions, either by loading a
    saved JSON config or by walking the guided wizard. Wizard-built plans can be
    saved for next time.
    """
    setup_mode = prompt_select(
        "How would you like to set up the rescue?",
        [
            ("Guided wizard (recommended)", "wizard"),
            ("Load a saved JSON config file", "config"),
        ],
    )
    if setup_mode == "config":
        return _load_config()

    victim_hint = victim_address or prompt_address(
        "Compromised (victim) wallet address"
    )
    safe_wallet = prompt_address("Safe wallet to send everything to")

    actions: list[RescueData] = []
    while True:
        if actions:
            next_step = prompt_select(
                "What next?",
                [
                    ("Add another action", "add"),
                    ("Review current plan", "review"),
                    ("Finish this plan", "finish"),
                ],
            )
            if next_step == "review":
                print_plan(actions)
                continue
            if next_step == "finish":
                break

        action = _build_action(safe_wallet, victim_hint)
        if action is not None:
            actions.append(action)

    print_plan(actions)
    save_config(actions)
    return actions
