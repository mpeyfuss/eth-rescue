from typing import cast

import rlp
from eth_account.account import Account
from eth_account.signers.local import LocalAccount
from eth_keys import keys
from eth_utils import keccak, to_hex
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3.exceptions import TransactionNotFound

from rescue_scripts import ui
from rescue_scripts.calldata import build_calldata
from rescue_scripts.flashbots import FlashbotsWeb3, flashbot
from rescue_scripts.prompts import (
    pause,
    prompt_address,
    prompt_float,
    prompt_secret,
    prompt_select,
    prompt_yes_no,
)
from rescue_scripts.types import (
    BundleEntry,
    Network,
    PreparedAction,
    RescueData,
    SimulationResult,
)
from rescue_scripts.wizard import build_rescue_data

NETWORKS: dict[str, Network] = {
    "mainnet": {
        "label": "Ethereum mainnet",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "relay": "https://relay.flashbots.net",
    },
    "sepolia": {
        "label": "Sepolia (testnet)",
        "rpc": "https://ethereum-sepolia-rpc.publicnode.com",
        "relay": "https://relay-sepolia.flashbots.net",
    },
}

FUNDING_TX_GAS = 21000
UNDELEGATE_TX_GAS = 60000
SWEEP_TX_GAS = 21000
FUNDING_BUFFER = 1.15  # extra headroom on the gas wallet for fee fluctuation
MAX_BLOCK_ATTEMPTS = 25  # ~5 minutes of blocks before checking in with the user
SET_CODE_TX_TYPE = 0x04
SET_CODE_MAGIC = 0x05
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


# ---------------------------------------------------------------------------
# Step 1: set up accounts (private keys, no keystores)
# ---------------------------------------------------------------------------
def _load_key(label: str) -> LocalAccount:
    """Prompt for a private key (hidden input) and return the account."""
    while True:
        raw = prompt_secret(f"Enter the {label} private key")
        try:
            return Account.from_key(raw)
        except Exception:
            ui.warning("That doesn't look like a valid private key. Try again.")


def _setup_victim_account() -> LocalAccount:
    """The compromised wallet — its private key is what we're rescuing assets from.

    Always entered interactively (never read from disk or env).
    """
    return _load_key("compromised (victim) wallet")


def _setup_gas_account() -> LocalAccount:
    """The wallet that pays for the rescue. Entered interactively or created fresh."""
    ui.info("Gas wallet: this is the wallet that pays for the whole rescue.")
    choice = prompt_select(
        "How do you want to set up the gas wallet?",
        [
            ("I'll enter an existing private key", "existing"),
            ("Create a new gas wallet for me", "create"),
        ],
    )
    if choice == "existing":
        return _load_key("gas wallet")

    acct = Account.create()
    ui.callout(
        "New gas wallet created",
        [
            f"Address:     {acct.address}",
            f"Private key: {to_hex(acct.key)}",
            "",
            "Save this private key somewhere safe now.",
            "You will fund this address later and need the key for leftovers.",
        ],
        style="yellow",
    )
    pause("Press any key once you've saved the private key")
    return acct


def load_accounts() -> tuple[LocalAccount, LocalAccount, LocalAccount]:
    """Collect the (victim, gas, auth) accounts. Auth is an ephemeral signer."""
    ui.section("Step 1: Set up accounts")
    victim = _setup_victim_account()
    while True:
        gas = _setup_gas_account()
        if gas.address.lower() != victim.address.lower():
            break
        ui.warning("Gas wallet must be different from the compromised wallet.")
    auth = Account.create()  # ephemeral Flashbots signing identity; needs no funds
    ui.render_accounts(victim.address, gas.address)
    return victim, gas, auth


def choose_network() -> Network:
    """Pick the network to run against (mainnet, or Sepolia for testing)."""
    key = prompt_select(
        "Which network are you rescuing on?",
        [
            ("Ethereum mainnet", "mainnet"),
            ("Sepolia (testnet - for testing only)", "sepolia"),
        ],
    )
    network = NETWORKS[key]
    ui.success(f"Using {network['label']}.")
    return network


# ---------------------------------------------------------------------------
# Step 3: connect, estimate gas, preview cost
# ---------------------------------------------------------------------------
def connect(auth: LocalAccount, network: Network) -> FlashbotsWeb3:
    w3 = Web3(HTTPProvider(network["rpc"]))
    return flashbot(w3, auth, network["relay"])


def _compute_fees(w3: Web3, extra_priority_fee_gwei: float) -> tuple[int, int]:
    """Return (priority_fee, max_fee_per_gas) from the latest block."""
    base_fee = int(w3.eth.get_block("latest")["baseFeePerGas"] * 1.25)
    priority_fee = w3.eth.max_priority_fee + w3.to_wei(extra_priority_fee_gwei, "gwei")
    max_fee_per_gas = 2 * base_fee + priority_fee
    return priority_fee, max_fee_per_gas


def _max_next_block_effective_fee(w3: Web3, priority_fee: int) -> int:
    latest_base_fee = int(w3.eth.get_block("latest")["baseFeePerGas"])
    return latest_base_fee + latest_base_fee // 8 + 1 + priority_fee


def prepare_actions(
    w3: Web3, victim: str, rescue_data: list[RescueData]
) -> list[PreparedAction]:
    """Encode calldata and estimate gas once per action."""
    if not isinstance(rescue_data, list) or not rescue_data:
        raise ValueError("No rescue actions provided")
    prepared: list[PreparedAction] = []
    for data in rescue_data:
        tx_data = build_calldata(data["function_signature"], data["args"])
        gas = _estimate_gas(w3, victim, data["address"], tx_data, data["gas_estimate"])
        prepared.append({"to": data["address"], "data": tx_data, "gas": gas})
    return prepared


def _estimate_gas(w3: Web3, victim: str, to: str, data: str, fallback: int) -> int:
    """Estimate gas via the RPC with a buffer, falling back to a default on failure."""
    try:
        estimate = w3.eth.estimate_gas(
            {"from": victim, "to": to, "data": data, "value": 0}
        )
        return int(estimate * 1.25)
    except Exception as e:
        ui.warning(f"Could not estimate gas for {to} ({e}); using fallback {fallback}")
        return fallback


def _victim_funding_value(
    prepared: list[PreparedAction],
    max_fee_per_gas: int,
    effective_fee_cap: int,
) -> int:
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    return rescue_cost + SWEEP_TX_GAS * effective_fee_cap


def _required_funding(
    prepared: list[PreparedAction],
    max_fee_per_gas: int,
    effective_fee_cap: int | None = None,
) -> int:
    """Total ETH the gas wallet must hold for sponsor txs and victim funding."""
    if effective_fee_cap is None:
        effective_fee_cap = max_fee_per_gas
    victim_funding = _victim_funding_value(prepared, max_fee_per_gas, effective_fee_cap)
    sponsor_cost = (UNDELEGATE_TX_GAS + FUNDING_TX_GAS) * max_fee_per_gas
    return int((victim_funding + sponsor_cost) * FUNDING_BUFFER)


def preview(w3: Web3, prepared: list[PreparedAction], max_fee_per_gas: int) -> None:
    ui.render_cost_preview(w3, prepared, max_fee_per_gas)


# ---------------------------------------------------------------------------
# Step 4: fund the gas wallet (with refresh)
# ---------------------------------------------------------------------------
def wait_for_funding(w3: Web3, gas_address: str, required: int) -> None:
    ui.section("Step 4: Fund the gas wallet")
    ui.callout(
        "Funding required",
        [
            f"Send at least {w3.from_wei(required, 'ether')} ETH to:",
            gas_address,
            "",
            "This wallet pays for the whole rescue.",
            "The amount includes a safety buffer.",
        ],
    )
    while True:
        balance = w3.eth.get_balance(gas_address)
        ui.info(
            f"Current balance: {w3.from_wei(balance, 'ether')} ETH "
            f"/ needed {w3.from_wei(required, 'ether')} ETH"
        )
        if balance >= required:
            ui.success("Gas wallet funded.")
            return
        pause("Send funds, then press any key to re-check (Ctrl+C to abort)")


# ---------------------------------------------------------------------------
# Step 5: simulate, build, sign, send (retry across blocks)
# ---------------------------------------------------------------------------
def _address_bytes(address: str) -> bytes:
    return bytes(HexBytes(Web3.to_checksum_address(address)))


def _sign_hash(private_key: bytes, message_hash: bytes) -> tuple[int, int, int]:
    signature = keys.PrivateKey(bytes(private_key)).sign_msg_hash(message_hash)
    return signature.v, signature.r, signature.s


def _signed_raw_transaction(signed) -> HexBytes:
    raw = getattr(signed, "rawTransaction", None)
    if raw is None:
        raw = signed.raw_transaction
    return cast(HexBytes, raw)


def _sign_7702_undelegation(
    *,
    chain_id: int,
    tx_nonce: int,
    authority_nonce: int,
    authority_key: bytes,
    sponsor_key: bytes,
    sponsor_address: str,
    priority_fee: int,
    max_fee_per_gas: int,
) -> HexBytes:
    zero_address = _address_bytes(ZERO_ADDRESS)
    auth_message = keccak(
        bytes([SET_CODE_MAGIC]) + rlp.encode([chain_id, zero_address, authority_nonce])
    )
    auth_y_parity, auth_r, auth_s = _sign_hash(authority_key, auth_message)
    authorization = [
        chain_id,
        zero_address,
        authority_nonce,
        auth_y_parity,
        auth_r,
        auth_s,
    ]

    unsigned_payload = [
        chain_id,
        tx_nonce,
        priority_fee,
        max_fee_per_gas,
        UNDELEGATE_TX_GAS,
        _address_bytes(sponsor_address),
        0,
        b"",
        [],
        [authorization],
    ]
    tx_message = keccak(bytes([SET_CODE_TX_TYPE]) + rlp.encode(unsigned_payload))
    tx_y_parity, tx_r, tx_s = _sign_hash(sponsor_key, tx_message)
    return HexBytes(
        bytes([SET_CODE_TX_TYPE])
        + rlp.encode(unsigned_payload + [tx_y_parity, tx_r, tx_s])
    )


def _safe_victim_balance(w3: Web3, address: str) -> int:
    try:
        return w3.eth.get_balance(address)
    except Exception:
        return 0


def _build_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    priority_fee: int,
    max_fee_per_gas: int,
    effective_fee_cap: int,
    victim_nonce: int,
    gas_nonce: int,
    victim_balance: int,
) -> list[BundleEntry]:
    chain_id = w3.eth.chain_id
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    rescue_fee_cap = sum(a["gas"] * effective_fee_cap for a in prepared)
    sweep_value = max(0, victim_balance + rescue_cost - rescue_fee_cap)

    undelegate_tx = _sign_7702_undelegation(
        chain_id=chain_id,
        tx_nonce=gas_nonce,
        authority_nonce=victim_nonce,
        authority_key=victim.key,
        sponsor_key=gas.key,
        sponsor_address=gas.address,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
    )
    funding_tx = {
        "to": victim.address,
        "value": _victim_funding_value(prepared, max_fee_per_gas, effective_fee_cap),
        "gas": FUNDING_TX_GAS,
        "maxFeePerGas": max_fee_per_gas,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": gas_nonce + 1,
        "chainId": chain_id,
    }
    rescue_txs = [
        {
            "to": a["to"],
            "data": a["data"],
            "gas": a["gas"],
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": priority_fee,
            "nonce": victim_nonce + 1 + i,
            "chainId": chain_id,
        }
        for i, a in enumerate(prepared)
    ]
    sweep_tx = {
        "to": safe_address,
        "value": sweep_value,
        "gas": SWEEP_TX_GAS,
        "maxFeePerGas": effective_fee_cap,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": victim_nonce + 1 + len(prepared),
        "chainId": chain_id,
    }
    signed = [w3.eth.account.sign_transaction(funding_tx, private_key=gas.key)]
    signed += [
        w3.eth.account.sign_transaction(tx, private_key=victim.key) for tx in rescue_txs
    ]
    if sweep_value:
        signed.append(w3.eth.account.sign_transaction(sweep_tx, private_key=victim.key))
    return [{"signed_transaction": undelegate_tx}] + [
        {"signed_transaction": _signed_raw_transaction(s)} for s in signed
    ]


def _simulation_has_failures(result: SimulationResult) -> bool:
    return any(
        bool(tx_result.get("error") or tx_result.get("revert"))
        for tx_result in result.get("results", [])
    )


def simulate_bundle(
    w3: FlashbotsWeb3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: float,
) -> bool:
    """Simulate the exact funding + rescue bundle before asking to send it."""
    ui.section("Step 5: Simulate the rescue bundle")
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)
    priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)
    effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
    target_block = w3.eth.block_number + 1
    bundle = _build_bundle(
        w3,
        victim,
        gas,
        prepared,
        safe_address,
        priority_fee,
        max_fee_per_gas,
        effective_fee_cap,
        victim_nonce,
        gas_nonce,
        _safe_victim_balance(w3, victim.address),
    )

    ui.info(
        f"Simulating bundle for block {target_block} "
        f"@ {w3.from_wei(max_fee_per_gas, 'gwei')} gwei ..."
    )
    try:
        with ui.console.status("Running Flashbots simulation..."):
            result = cast(
                SimulationResult,
                w3.flashbots.simulate(bundle, block_tag=target_block),
            )
    except Exception as e:
        ui.error(f"Bundle simulation failed: {e}")
        return False

    ui.render_simulation_result(result)
    if _simulation_has_failures(result):
        ui.error("Simulation completed, but one or more transactions failed.")
        return False

    ui.success("Simulation succeeded. The bundle executed without reported reverts.")
    return True


def send_with_retry(
    w3: FlashbotsWeb3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: float,
) -> bool:
    """Resend the bundle each block (refreshing fees) until included or aborted."""
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)

    while True:
        for attempt in range(1, MAX_BLOCK_ATTEMPTS + 1):
            priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)
            effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)

            # ensure the gas wallet can still cover the (possibly higher) fees
            needed = _required_funding(prepared, max_fee_per_gas, effective_fee_cap)
            if w3.eth.get_balance(gas.address) < needed:
                ui.warning("Gas wallet balance dropped below requirement (fees rose).")
                wait_for_funding(w3, gas.address, needed)

            bundle = _build_bundle(
                w3,
                victim,
                gas,
                prepared,
                safe_address,
                priority_fee,
                max_fee_per_gas,
                effective_fee_cap,
                victim_nonce,
                gas_nonce,
                _safe_victim_balance(w3, victim.address),
            )
            target_block = w3.eth.block_number + 1
            result = w3.flashbots.send_bundle(bundle, target_block_number=target_block)
            ui.info(
                f"Attempt {attempt}/{MAX_BLOCK_ATTEMPTS} -> block {target_block} "
                f"@ {w3.from_wei(max_fee_per_gas, 'gwei')} gwei ..."
            )
            with ui.console.status("Waiting for bundle result..."):
                result.wait()
            try:
                receipts = result.receipts()
                ui.success("Bundle included.")
                ui.info(f"Block: {receipts[0].blockNumber}")
                ui.info(f"Tx hashes: {[r.transactionHash.hex() for r in receipts]}")
                return True
            except TransactionNotFound:
                continue

        if not prompt_yes_no(
            f"\nNot included after {MAX_BLOCK_ATTEMPTS} blocks. Keep trying?",
            default=True,
        ):
            ui.error("Aborted by user. No rescue transactions were included.")
            return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run() -> None:
    ui.title("Whitehat Rescue - guided setup")

    # Choose network (mainnet or Sepolia testnet)
    network = choose_network()

    # Step 1: set up accounts (need the victim address to build the plan)
    victim, gas, auth = load_accounts()
    w3 = connect(auth, network)

    # Step 2: build or load the plan
    ui.section("Step 2: Build the rescue plan")
    safe_wallet = prompt_address("Safe wallet to receive rescued assets and ETH")
    rescue_data = build_rescue_data(victim.address, safe_wallet)

    # Step 3: estimate gas + preview cost
    extra_priority_fee = prompt_float("Extra priority fee to add (gwei)", default=0.0)
    with ui.console.status("Preparing actions and estimating gas..."):
        prepared = prepare_actions(w3, victim.address, rescue_data)
        priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee)
        effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
    preview(w3, prepared, max_fee_per_gas)

    if not prompt_yes_no("\nProceed to funding?", default=True):
        ui.warning("Aborted. Nothing was sent.")
        return

    # Step 4: fund the gas wallet (with refresh loop)
    wait_for_funding(
        w3,
        gas.address,
        _required_funding(prepared, max_fee_per_gas, effective_fee_cap),
    )

    # Step 5: simulate, confirm, and send (retry across blocks)
    simulation_ok = simulate_bundle(
        w3, victim, gas, prepared, safe_wallet, extra_priority_fee
    )
    if not simulation_ok and not prompt_yes_no(
        "Simulation did not pass cleanly. Continue anyway?",
        default=False,
    ):
        ui.warning("Aborted. Nothing was sent.")
        return

    ui.section("Step 6: Send the rescue bundle")
    if not prompt_yes_no("Send the rescue bundle now?", default=True):
        ui.warning("Aborted. Nothing was sent.")
        return
    send_with_retry(w3, victim, gas, prepared, safe_wallet, extra_priority_fee)
