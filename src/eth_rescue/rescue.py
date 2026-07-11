from eth_account.account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import is_address, to_checksum_address, to_hex
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3.exceptions import TransactionNotFound

from rescue_scripts import ui
from rescue_scripts.calldata import build_calldata
from rescue_scripts.relay import BUILDERS, RelayClient, RelayWeb3
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
    BundleTransaction,
    Network,
    PreparedAction,
    PreparedBundle,
    RescueData,
    SimulationFailure,
    SimulationOutcome,
)
from rescue_scripts.templates import GAS_GENERIC
from rescue_scripts.wizard import build_rescue_data, revise_rescue_data

NETWORKS: dict[str, Network] = {
    "mainnet": {
        "label": "Ethereum mainnet",
        "rpc": "https://ethereum-rpc.publicnode.com",
        "relay": "https://relay.flashbots.net",
        "chain_id": 1,
        "builders": BUILDERS,
    },
    "sepolia": {
        "label": "Sepolia (testnet)",
        "rpc": "https://ethereum-sepolia-rpc.publicnode.com",
        "relay": "https://relay-sepolia.flashbots.net",
        "chain_id": 11155111,
        "builders": None,
    },
}

FUNDING_TX_GAS = 21000
UNDELEGATE_TX_GAS = 60000
SWEEP_TX_GAS = 21000
FUNDING_BUFFER = 1.15  # extra headroom on the gas wallet for fee fluctuation
MAX_BLOCK_ATTEMPTS = 25  # ~5 minutes of blocks before checking in with the user
TARGET_BLOCK_OFFSET = 1
BUNDLE_BLOCK_RANGE = 5  # target current +1 through current +5 (relay maximum)
SET_CODE_TX_TYPE = 0x04
DELEGATION_DESIGNATOR_PREFIX = b"\xef\x01\x00"
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
    if not prompt_yes_no(
        "I have securely saved this private key and cleared visible terminal history",
        default=False,
    ):
        raise KeyboardInterrupt
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
            ("Sepolia (testnet)", "sepolia"),
        ],
    )
    network = NETWORKS[key]
    ui.success(f"Using {network['label']}.")
    return network


# ---------------------------------------------------------------------------
# Step 3: connect, estimate gas, preview cost
# ---------------------------------------------------------------------------
def connect(auth: LocalAccount, network: Network) -> RelayWeb3:
    w3 = RelayWeb3(HTTPProvider(network["rpc"]))
    if not w3.is_connected():
        raise ConnectionError(f"Could not connect to {network['label']} RPC")
    w3.relay = RelayClient(w3, auth, network["relay"], builders=network["builders"])
    return w3


def validate_accounts_and_destination(
    victim: LocalAccount, gas: LocalAccount, safe_address: str
) -> str:
    if victim.address.lower() == gas.address.lower():
        raise ValueError("Gas wallet must be different from the compromised wallet")
    if not is_address(safe_address):
        raise ValueError("Safe wallet is not a valid Ethereum address")
    safe_address = to_checksum_address(safe_address)
    if safe_address.lower() in {victim.address.lower(), gas.address.lower()}:
        raise ValueError("Safe wallet must differ from victim and gas wallets")
    return safe_address


def validate_network(w3: Web3, network: Network) -> None:
    if w3.eth.chain_id != network["chain_id"]:
        raise ConnectionError(
            f"RPC chain ID {w3.eth.chain_id} does not match expected {network['chain_id']}"
        )


def _compute_fees(w3: Web3, extra_priority_fee_gwei: float) -> tuple[int, int]:
    """Return (priority_fee, max_fee_per_gas) from the latest block."""
    if extra_priority_fee_gwei < 0:
        raise ValueError("Extra priority fee cannot be negative")
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
    if not rescue_data:
        raise ValueError("No rescue actions provided")
    prepared: list[PreparedAction] = []
    for data in rescue_data:
        fallback = data.get("gas_estimate", GAS_GENERIC)
        tx_data = build_calldata(data["function_signature"], data["args"])
        target = to_checksum_address(data["address"])
        gas = _estimate_gas(w3, victim, target, tx_data, fallback)
        prepared.append({"to": target, "data": tx_data, "gas": gas})
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
    needs_undelegation: bool = True,
) -> int:
    """Total ETH the gas wallet must hold for sponsor txs and victim funding."""
    if effective_fee_cap is None:
        effective_fee_cap = max_fee_per_gas
    victim_funding = _victim_funding_value(prepared, max_fee_per_gas, effective_fee_cap)
    sponsor_gas = FUNDING_TX_GAS
    if needs_undelegation:
        sponsor_gas += UNDELEGATE_TX_GAS
    sponsor_cost = sponsor_gas * max_fee_per_gas
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
    authorization = Account.sign_authorization(
        {
            "chainId": chain_id,
            "address": ZERO_ADDRESS,
            "nonce": authority_nonce,
        },
        authority_key,
    )
    signed = Account.sign_transaction(
        {
            "type": SET_CODE_TX_TYPE,
            "chainId": chain_id,
            "nonce": tx_nonce,
            "maxPriorityFeePerGas": priority_fee,
            "maxFeePerGas": max_fee_per_gas,
            "gas": UNDELEGATE_TX_GAS,
            "to": sponsor_address,
            "value": 0,
            "data": b"",
            "accessList": [],
            "authorizationList": [authorization],
        },
        sponsor_key,
    )
    return signed.raw_transaction


def _has_7702_delegation(w3: Web3, address: str) -> bool:
    code = bytes(w3.eth.get_code(address))
    if not code:
        return False
    if len(code) == 23 and code.startswith(DELEGATION_DESIGNATOR_PREFIX):
        return True
    raise ValueError(f"Victim account {address} has unexpected non-EIP-7702 code")


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
    needs_undelegation: bool = True,
) -> list[BundleEntry]:
    chain_id = w3.eth.chain_id
    rescue_cost = sum(a["gas"] * max_fee_per_gas for a in prepared)
    rescue_fee_cap = sum(a["gas"] * effective_fee_cap for a in prepared)
    sweep_value = max(0, victim_balance + rescue_cost - rescue_fee_cap)

    nonce_offset = int(needs_undelegation)
    entries: list[BundleEntry] = []
    if needs_undelegation:
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
        entries.append({"signed_transaction": undelegate_tx})
    funding_tx = {
        "to": victim.address,
        "value": _victim_funding_value(prepared, max_fee_per_gas, effective_fee_cap),
        "gas": FUNDING_TX_GAS,
        "maxFeePerGas": max_fee_per_gas,
        "maxPriorityFeePerGas": priority_fee,
        "nonce": gas_nonce + nonce_offset,
        "chainId": chain_id,
    }
    rescue_txs = [
        {
            "to": a["to"],
            "data": a["data"],
            "gas": a["gas"],
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": priority_fee,
            "nonce": victim_nonce + nonce_offset + i,
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
        "nonce": victim_nonce + nonce_offset + len(prepared),
        "chainId": chain_id,
    }
    signed = [w3.eth.account.sign_transaction(funding_tx, private_key=gas.key)]
    signed += [
        w3.eth.account.sign_transaction(tx, private_key=victim.key) for tx in rescue_txs
    ]
    if sweep_value:
        signed.append(w3.eth.account.sign_transaction(sweep_tx, private_key=victim.key))
    entries.extend({"signed_transaction": s.raw_transaction} for s in signed)
    return entries


def prepare_bundle(
    w3: Web3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: float,
) -> PreparedBundle:
    """Build one immutable bundle for the next block from current chain state."""
    victim_nonce = w3.eth.get_transaction_count(victim.address)
    gas_nonce = w3.eth.get_transaction_count(gas.address)
    priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee_gwei)
    effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
    target_block = w3.eth.block_number + TARGET_BLOCK_OFFSET
    victim_balance = w3.eth.get_balance(victim.address)
    needs_undelegation = _has_7702_delegation(w3, victim.address)
    entries = _build_bundle(
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
        victim_balance,
        needs_undelegation,
    )
    victim_funding = _victim_funding_value(prepared, max_fee_per_gas, effective_fee_cap)
    rescue_cost = sum(action["gas"] * max_fee_per_gas for action in prepared)
    rescue_fee_cap = sum(action["gas"] * effective_fee_cap for action in prepared)
    sweep_value = max(0, victim_balance + rescue_cost - rescue_fee_cap)
    roles: list[BundleTransaction] = []
    entry_index = 0
    if needs_undelegation:
        roles.append(BundleTransaction("undelegate", entries[0]["signed_transaction"]))
        entry_index = 1
    roles.append(BundleTransaction("fund", entries[entry_index]["signed_transaction"]))
    entry_index += 1
    for action_index, entry in enumerate(
        entries[entry_index : entry_index + len(prepared)]
    ):
        roles.append(
            BundleTransaction(
                "rescue", entry["signed_transaction"], action_index=action_index
            )
        )
    entry_index += len(prepared)
    if len(entries) > entry_index:
        roles.append(BundleTransaction("sweep", entries[-1]["signed_transaction"]))
    return PreparedBundle(
        transactions=roles,
        victim_nonce=victim_nonce,
        gas_nonce=gas_nonce,
        priority_fee=priority_fee,
        max_fee_per_gas=max_fee_per_gas,
        effective_fee_cap=effective_fee_cap,
        target_block=target_block,
        required_funding=_required_funding(
            prepared,
            max_fee_per_gas,
            effective_fee_cap,
            needs_undelegation,
        ),
        victim_funding=victim_funding,
        sweep_value=sweep_value,
        expected_residual=0,
    )


def simulate_prepared_bundle(
    w3: RelayWeb3, bundle: PreparedBundle
) -> SimulationOutcome:
    """Simulate the exact immutable bundle that may subsequently be submitted."""
    ui.info(
        f"Simulating bundle for block {bundle.target_block} "
        f"@ {w3.from_wei(bundle.max_fee_per_gas, 'gwei')} gwei ..."
    )
    try:
        with ui.console.status("Running Flashbots simulation..."):
            result = w3.relay.simulate(bundle.entries, block_tag=bundle.target_block)
    except Exception as e:
        failure = SimulationFailure(f"Bundle simulation failed: {e}")
        ui.error(failure.message)
        return SimulationOutcome(False, bundle=bundle, failures=(failure,))

    if len(result["results"]) != len(bundle.transactions):
        failure = SimulationFailure(
            "Relay simulation response did not include every bundle transaction"
        )
        ui.error(failure.message)
        return SimulationOutcome(
            False, bundle=bundle, result=result, failures=(failure,)
        )

    ui.render_simulation_result(result)
    failures: list[SimulationFailure] = []
    for index, tx_result in enumerate(result["results"]):
        message = tx_result.get("error") or tx_result.get("revert")
        if not message:
            continue
        transaction = bundle.transactions[index]
        failures.append(
            SimulationFailure(
                str(message),
                transaction_index=index,
                role=transaction.role,
                action_index=transaction.action_index,
            )
        )
    if failures:
        for failure in failures:
            label = (
                f"action #{failure.action_index + 1}"
                if failure.action_index is not None
                else failure.role or "unknown transaction"
            )
            ui.error(f"Simulation failed at {label}: {failure.message}")
        return SimulationOutcome(
            False, bundle=bundle, result=result, failures=tuple(failures)
        )

    ui.success("Simulation succeeded. The bundle executed without reported reverts.")
    return SimulationOutcome(True, bundle=bundle, result=result)


def simulate_bundle(
    w3: RelayWeb3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: float,
) -> SimulationOutcome:
    """Simulate the exact funding + rescue bundle before asking to send it."""
    ui.section("Step 5: Simulate the rescue bundle")
    try:
        bundle = prepare_bundle(
            w3, victim, gas, prepared, safe_address, extra_priority_fee_gwei
        )
    except Exception as e:
        failure = SimulationFailure(f"Could not prepare bundle: {e}")
        ui.error(failure.message)
        return SimulationOutcome(False, failures=(failure,))
    return simulate_prepared_bundle(w3, bundle)


def send_with_retry(
    w3: RelayWeb3,
    victim: LocalAccount,
    gas: LocalAccount,
    prepared: list[PreparedAction],
    safe_address: str,
    extra_priority_fee_gwei: float,
) -> bool:
    """Build and simulate once per multi-block submission window."""
    while True:
        for batch_start in range(1, MAX_BLOCK_ATTEMPTS + 1, BUNDLE_BLOCK_RANGE):
            batch_size = min(BUNDLE_BLOCK_RANGE, MAX_BLOCK_ATTEMPTS - batch_start + 1)
            batch_end = batch_start + batch_size - 1
            try:
                bundle = prepare_bundle(
                    w3,
                    victim,
                    gas,
                    prepared,
                    safe_address,
                    extra_priority_fee_gwei,
                )
                gas_balance = w3.eth.get_balance(gas.address)
            except Exception as e:
                ui.error(f"Could not prepare the next bundle: {e}")
                return False

            if gas_balance < bundle.required_funding:
                ui.warning("Gas wallet balance dropped below requirement (fees rose).")
                wait_for_funding(w3, gas.address, bundle.required_funding)
                continue

            simulation = simulate_prepared_bundle(w3, bundle)
            if not simulation:
                ui.error(
                    "Submission blocked because the exact bundle did not simulate cleanly."
                )
                return False

            if w3.eth.block_number >= bundle.target_block:
                ui.warning(
                    f"Target block {bundle.target_block} arrived during simulation; "
                    "rebuilding for a later block."
                )
                continue

            ui.info(
                f"Attempts {batch_start}-{batch_end}/"
                f"{MAX_BLOCK_ATTEMPTS} -> blocks {bundle.target_block}-"
                f"{bundle.target_block + batch_size - 1} "
                f"@ {w3.from_wei(bundle.max_fee_per_gas, 'gwei')} gwei ..."
            )
            submissions = []
            for block_offset in range(batch_size):
                try:
                    submissions.append(
                        w3.relay.send_bundle(
                            bundle.entries,
                            target_block_number=bundle.target_block + block_offset,
                        )
                    )
                except Exception as e:
                    if not submissions:
                        ui.error(f"Relay submission failed: {e}")
                        return False
                    ui.warning(
                        f"A later target was rejected ({e}); monitoring the "
                        f"{len(submissions)} accepted submission(s)."
                    )
                    break

            for result in submissions:
                try:
                    with ui.console.status("Waiting for bundle result..."):
                        result.wait()
                    receipts = result.receipts()
                    if len(receipts) != len(bundle.transactions):
                        raise RuntimeError(
                            "Relay submission did not return every transaction receipt"
                        )
                    if any(receipt["status"] != 1 for receipt in receipts):
                        raise RuntimeError("One or more rescue transactions reverted")
                    ui.success("Bundle included.")
                    ui.info(f"Block: {receipts[0].blockNumber}")
                    ui.info(f"Tx hashes: {[r.transactionHash.hex() for r in receipts]}")
                    return True
                except TransactionNotFound:
                    continue
                except Exception as e:
                    ui.error(f"Relay submission failed: {e}")
                    return False

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
    try:
        w3 = connect(auth, network)
        validate_network(w3, network)
    except Exception as e:
        ui.error(f"Network setup failed: {e}")
        return

    # Step 2: build or load the plan
    ui.section("Step 2: Build the rescue plan")
    safe_wallet = prompt_address("Safe wallet to receive rescued assets and ETH")
    try:
        safe_wallet = validate_accounts_and_destination(victim, gas, safe_wallet)
    except ValueError as e:
        ui.error(str(e))
        return
    rescue_data = build_rescue_data(w3, victim.address, safe_wallet)

    # Step 3: estimate gas + preview cost
    extra_priority_fee = prompt_float("Extra priority fee to add (gwei)", default=0.0)
    try:
        with ui.console.status("Preparing actions and estimating gas..."):
            prepared = prepare_actions(w3, victim.address, rescue_data)
            priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee)
            effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
            needs_undelegation = _has_7702_delegation(w3, victim.address)
    except Exception as e:
        ui.error(f"Could not prepare the rescue plan: {e}")
        return
    preview(w3, prepared, max_fee_per_gas)
    ui.warning(
        "Funding uses conservative gas limits. Unused-gas savings may remain in the "
        "victim wallet after the guaranteed sweep amount is sent."
    )

    if not prompt_yes_no("\nProceed to funding?", default=True):
        ui.warning("Aborted. Nothing was sent.")
        return

    # Step 4: fund the gas wallet (with refresh loop)
    wait_for_funding(
        w3,
        gas.address,
        _required_funding(
            prepared,
            max_fee_per_gas,
            effective_fee_cap,
            needs_undelegation,
        ),
    )

    # Step 5: simulate, confirm, and send (retry across blocks)
    while True:
        simulation = simulate_bundle(
            w3, victim, gas, prepared, safe_wallet, extra_priority_fee
        )
        if simulation:
            ui.section("Step 6: Send the rescue bundle")
            if not prompt_yes_no("Send the rescue bundle now?", default=True):
                ui.warning("Aborted. Nothing was sent.")
                return
            if send_with_retry(
                w3, victim, gas, prepared, safe_wallet, extra_priority_fee
            ):
                return
            ui.warning(
                "The bundle was not included. You can revise and simulate again; "
                "nothing will be sent without another clean simulation."
            )
            failing_action = None
        else:
            failing_action = next(
                (
                    failure.action_index
                    for failure in simulation.failures
                    if failure.action_index is not None
                ),
                None,
            )

        correction = prompt_select(
            "What would you like to change before the next simulation?",
            [
                ("Edit or remove rescue actions", "plan"),
                ("Change the extra priority fee", "fee"),
                ("Retry with fresh chain state", "retry"),
                ("Cancel without sending", "cancel"),
            ],
        )
        if correction == "cancel":
            ui.warning("Aborted. Nothing was sent.")
            return
        if correction == "fee":
            extra_priority_fee = prompt_float(
                "Extra priority fee to add (gwei)", default=extra_priority_fee
            )
        elif correction == "plan":
            revised = revise_rescue_data(
                w3, rescue_data, safe_wallet, victim.address, failing_action
            )
            if revised is None:
                ui.warning("Aborted. Nothing was sent.")
                return
            rescue_data = revised
        try:
            with ui.console.status("Rebuilding and estimating the rescue plan..."):
                prepared = prepare_actions(w3, victim.address, rescue_data)
                priority_fee, max_fee_per_gas = _compute_fees(w3, extra_priority_fee)
                effective_fee_cap = _max_next_block_effective_fee(w3, priority_fee)
            preview(w3, prepared, max_fee_per_gas)
            wait_for_funding(
                w3,
                gas.address,
                _required_funding(
                    prepared,
                    max_fee_per_gas,
                    effective_fee_cap,
                    _has_7702_delegation(w3, victim.address),
                ),
            )
        except Exception as e:
            ui.error(f"Could not rebuild the rescue plan: {e}")
            return
