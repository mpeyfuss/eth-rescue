from getpass import getpass

from eth_account.account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import to_hex
from web3 import HTTPProvider, Web3
from web3.exceptions import TransactionNotFound

from rescue_scripts.calldata import build_calldata
from rescue_scripts.flashbots import FlashbotsWeb3, flashbot
from rescue_scripts.prompts import prompt_choice, prompt_float, prompt_yes_no
from rescue_scripts.templates import GAS_GENERIC
from rescue_scripts.types import RescueData
from rescue_scripts.wizard import build_rescue_data

NETWORKS = {
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
FUNDING_BUFFER = 1.15  # extra headroom on the gas wallet for fee fluctuation
MAX_BLOCK_ATTEMPTS = 25  # ~5 minutes of blocks before checking in with the user


# ---------------------------------------------------------------------------
# Step 1: set up accounts (private keys, no keystores)
# ---------------------------------------------------------------------------
def _load_key(label: str) -> LocalAccount:
    """Prompt for a private key (hidden input) and return the account."""
    while True:
        raw = getpass(f"Enter the {label} private key: ").strip()
        try:
            return Account.from_key(raw)
        except Exception:
            print("  ⚠️  That doesn't look like a valid private key. Try again.")


def _setup_victim_account() -> LocalAccount:
    """The compromised wallet — its private key is what we're rescuing assets from.

    Always entered interactively (never read from disk or env).
    """
    return _load_key("compromised (victim) wallet")


def _setup_gas_account() -> LocalAccount:
    """The wallet that pays for the rescue. Entered interactively or created fresh."""
    print("\nGas wallet — this is the wallet that pays for the whole rescue:")
    print("  1) I'll enter an existing private key")
    print("  2) Create a new gas wallet for me")
    if prompt_choice("Choose an option", 2) == 1:
        return _load_key("gas wallet")

    acct = Account.create()
    print("\n  🔑 New gas wallet created:")
    print(f"     Address:     {acct.address}")
    print(f"     Private key: {to_hex(acct.key)}")
    print("  ⚠️  SAVE this private key somewhere safe NOW. You'll fund the address")
    print("      above in a later step, and you need the key to access any leftovers.")
    input("  Press Enter once you've saved the private key... ")
    return acct


def load_accounts() -> tuple[LocalAccount, LocalAccount, LocalAccount]:
    """Collect the (victim, gas, auth) accounts. Auth is an ephemeral signer."""
    print("\n--- Step 1: Set up accounts ---")
    victim = _setup_victim_account()
    gas = _setup_gas_account()
    auth = Account.create()  # ephemeral Flashbots signing identity; needs no funds
    print(f"\n  Victim (compromised) wallet: {victim.address}")
    print(f"  Gas (funding) wallet:        {gas.address}")
    return victim, gas, auth


def choose_network() -> dict:
    """Pick the network to run against (mainnet, or Sepolia for testing)."""
    print("Which network are you rescuing on?")
    print("  1) Ethereum mainnet")
    print("  2) Sepolia (testnet — for testing only)")
    network = NETWORKS["mainnet"] if prompt_choice("Choose an option", 2) == 1 else NETWORKS["sepolia"]
    print(f"  🌐 Using {network['label']}.")
    return network


# ---------------------------------------------------------------------------
# Step 3: connect, estimate gas, preview cost
# ---------------------------------------------------------------------------
def connect(auth: LocalAccount, network: dict) -> FlashbotsWeb3:
    w3: FlashbotsWeb3 = Web3(HTTPProvider(network["rpc"]))
    flashbot(w3, auth, network["relay"])
    return w3


def _compute_fees(w3: Web3, extra_priority_fee_gwei: float) -> tuple[int, int]:
    """Return (priority_fee, max_fee_per_gas) from the latest block."""
    base_fee = int(w3.eth.get_block("latest")["baseFeePerGas"] * 1.25)
    priority_fee = w3.eth.max_priority_fee + w3.to_wei(extra_priority_fee_gwei, "gwei")
    max_fee_per_gas = 2 * base_fee + priority_fee
    return priority_fee, max_fee_per_gas


def prepare_actions(w3: Web3, victim: str, rescue_data: list[RescueData]) -> list[dict]:
    """Encode calldata and estimate gas once per action."""
    if not isinstance(rescue_data, list) or not rescue_data:
        raise ValueError("No rescue actions provided")
    prepared = []
    for data in rescue_data:
        tx_data = build_calldata(data["function_signature"], data["args"])
        gas = _estimate_gas(
            w3, victim, data["address"], tx_data, data.get("gas_estimate", GAS_GENERIC)
        )
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
        print(f"  ⚠️  Could not estimate gas for {to} ({e}); using fallback {fallback}")
        return fallback


def _required_funding(prepared: list[dict], max_fee_per_gas: int) -> int:
    """Total ETH the gas wallet must hold: rescue gas + funding tx gas + buffer."""
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    return int((rescue_cost + FUNDING_TX_GAS * max_fee_per_gas) * FUNDING_BUFFER)


def preview(w3: Web3, prepared: list[dict], max_fee_per_gas: int) -> None:
    print("\n--- Step 3: Plan & cost preview ---")
    print(f"  Network gas (maxFeePerGas): {w3.from_wei(max_fee_per_gas, 'gwei')} gwei")
    total = 0
    for i, a in enumerate(prepared, 1):
        cost = a["gas"] * max_fee_per_gas
        total += cost
        print(
            f"  {i}. {a['to']}  gas≈{a['gas']:,}  (≈{w3.from_wei(cost, 'ether')} ETH)"
        )
    print(f"  Estimated rescue cost: ≈{w3.from_wei(total, 'ether')} ETH")


# ---------------------------------------------------------------------------
# Step 4: fund the gas wallet (with refresh)
# ---------------------------------------------------------------------------
def wait_for_funding(w3: Web3, gas_address: str, required: int) -> None:
    print("\n--- Step 4: Fund the gas wallet ---")
    print(
        f"  Send at least {w3.from_wei(required, 'ether')} ETH to the gas wallet:\n"
        f"    {gas_address}\n"
        "  This wallet pays for the whole rescue. (Includes a safety buffer.)"
    )
    while True:
        balance = w3.eth.get_balance(gas_address)
        print(
            f"  Current balance: {w3.from_wei(balance, 'ether')} ETH "
            f"/ needed {w3.from_wei(required, 'ether')} ETH"
        )
        if balance >= required:
            print("  ✅ Gas wallet funded.")
            return
        input("  Send funds, then press Enter to re-check (Ctrl+C to abort)... ")


# ---------------------------------------------------------------------------
# Step 5: build, sign, send (retry across blocks)
# ---------------------------------------------------------------------------
def _build_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[dict],
    priority_fee: int,
    max_fee_per_gas: int,
    victim_nonce: int,
    gas_nonce: int,
) -> list[dict]:
    chain_id = w3.eth.chain_id
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    funding_tx = {
        "to": victim.address,
        "value": rescue_cost,
        "gas": FUNDING_TX_GAS,
        "maxFeePerGas": max_fee_per_gas,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": gas_nonce,
        "chainId": chain_id,
    }
    rescue_txs = [
        {
            "to": a["to"],
            "data": a["data"],
            "gas": a["gas"],
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": priority_fee,
            "nonce": victim_nonce + i,
            "chainId": chain_id,
        }
        for i, a in enumerate(prepared)
    ]
    signed = [w3.eth.account.sign_transaction(funding_tx, private_key=gas.key)]
    signed += [
        w3.eth.account.sign_transaction(tx, private_key=victim.key) for tx in rescue_txs
    ]
    return [{"signed_transaction": s.rawTransaction} for s in signed]


def send_with_retry(
    w3: FlashbotsWeb3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[dict],
    extra_priority_fee_gwei: float,
) -> bool:
    """Resend the bundle each block (refreshing fees) until included or aborted."""
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)

    while True:
        for attempt in range(1, MAX_BLOCK_ATTEMPTS + 1):
            priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)

            # ensure the gas wallet can still cover the (possibly higher) fees
            needed = _required_funding(prepared, max_fee_per_gas)
            if w3.eth.get_balance(gas.address) < needed:
                print("  ⚠️  Gas wallet balance dropped below requirement (fees rose).")
                wait_for_funding(w3, gas.address, needed)

            bundle = _build_bundle(
                w3,
                victim,
                gas,
                prepared,
                priority_fee,
                max_fee_per_gas,
                victim_nonce,
                gas_nonce,
            )
            target_block = w3.eth.block_number + 1
            result = w3.flashbots.send_bundle(bundle, target_block_number=target_block)
            print(
                f"  Attempt {attempt}/{MAX_BLOCK_ATTEMPTS} → block {target_block} "
                f"@ {w3.from_wei(max_fee_per_gas, 'gwei')} gwei ..."
            )
            result.wait()
            try:
                receipts = result.receipts()
                print("\n🚀 Bundle included!")
                print(f"🔗 Block: {receipts[0].blockNumber}")
                print(f"🫆 Tx hashes: {[r.transactionHash.hex() for r in receipts]}")
                return True
            except TransactionNotFound:
                continue

        if not prompt_yes_no(
            f"\nNot included after {MAX_BLOCK_ATTEMPTS} blocks. Keep trying?",
            default=True,
        ):
            print("❌ Aborted by user. No rescue transactions were included.")
            return False


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------
def run() -> None:
    print("\n🛟  Whitehat Rescue — guided setup\n")

    # Choose network (mainnet or Sepolia testnet)
    network = choose_network()

    # Step 1: set up accounts (need the victim address to build the plan)
    victim, gas, auth = load_accounts()
    w3 = connect(auth, network)

    # Step 2: build or load the plan
    print("\n--- Step 2: Build the rescue plan ---")
    rescue_data = build_rescue_data(victim.address)

    # Step 3: estimate gas + preview cost
    extra_priority_fee = prompt_float("Extra priority fee to add (gwei)", default=0.0)
    prepared = prepare_actions(w3, victim.address, rescue_data)
    _, max_fee_per_gas = _compute_fees(w3, extra_priority_fee)
    preview(w3, prepared, max_fee_per_gas)

    if not prompt_yes_no("\nProceed to funding?", default=True):
        print("Aborted. Nothing was sent.")
        return

    # Step 4: fund the gas wallet (with refresh loop)
    wait_for_funding(w3, gas.address, _required_funding(prepared, max_fee_per_gas))

    # Step 5: confirm and send (retry across blocks)
    print("\n--- Step 5: Send the rescue bundle ---")
    if not prompt_yes_no("Send the rescue bundle now?", default=True):
        print("Aborted. Nothing was sent.")
        return
    send_with_retry(w3, victim, gas, prepared, extra_priority_fee)
