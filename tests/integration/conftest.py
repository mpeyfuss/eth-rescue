import json
import os
import shutil
import socket
import subprocess
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import pytest
from eth_account import Account
from eth_account.signers.local import LocalAccount
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3.exceptions import TransactionNotFound
from web3.types import TxReceipt

from eth_rescue.engine import RescueContext
from eth_rescue.networks import NETWORKS
from eth_rescue.types import BundleEntry, SimulationResult

# When forking, this must match the live chain's fork: Anvil defaults to its latest.
ANVIL_HARDFORK = os.environ.get("ANVIL_HARDFORK", "osaka")
ANVIL_STARTUP_TIMEOUT = 30
CONTRACT_OUT = Path(__file__).parent / "contracts" / "out" / "Fixtures.sol"
ETH_RESCUE_OUT = Path(__file__).parents[2] / "contracts" / "out" / "EthRescue.sol"


@dataclass(frozen=True)
class ForkTarget:
    rpc: str
    eth_rescue: str
    eth_rescue_codehash: str
    block: int | None = None


def _fork_targets() -> dict[str, ForkTarget]:
    # Sepolia is not a CLI network (its Flashbots relay rarely lands bundles), but
    # its EthRescue deployment can still be fork-tested.
    targets = {
        "sepolia": ForkTarget(
            rpc="https://ethereum-sepolia-rpc.publicnode.com",
            eth_rescue="0xe9Da4345F6AB43e05FF3DE7999A99Dc30710421A",
            eth_rescue_codehash="0x4066e42da7841bead9a6829d601b8219458298865f7562dc5a6a2563dd32474b",
        )
    }
    for name, network in NETWORKS.items():
        if network["eth_rescue"] and network["eth_rescue_codehash"]:
            targets[name] = ForkTarget(
                rpc=network["rpc"],
                eth_rescue=network["eth_rescue"],
                eth_rescue_codehash=network["eth_rescue_codehash"],
            )
    return targets


@pytest.fixture(scope="session")
def fork() -> ForkTarget | None:
    """The live network the suite runs on (`FORK_NETWORK`), or None for a fresh
    chain. Forked runs use the network's EthRescue deployment instead of a local one."""
    name = os.environ.get("FORK_NETWORK")
    if not name:
        return None
    target = _fork_targets().get(name)
    if target is None:
        pytest.fail(f"FORK_NETWORK={name} has no pinned EthRescue deployment")
    rpc = os.environ.get("FORK_RPC_URL") or target.rpc
    # One block for the session: every test sees the same state, and Anvil
    # caches a pinned block's state on disk between test processes.
    block = Web3(HTTPProvider(rpc, request_kwargs={"timeout": 10})).eth.block_number
    return replace(target, rpc=rpc, block=block)


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _chain_args(fork: ForkTarget | None) -> list[str]:
    if fork is None:
        return ["--chain-id", "31337"]
    return ["--fork-url", fork.rpc, "--fork-block-number", str(fork.block)]


@pytest.fixture
def anvil_w3(fork: ForkTarget | None) -> Iterator[Web3]:
    if os.environ.get("RUN_ANVIL_INTEGRATION") != "1":
        pytest.skip("set RUN_ANVIL_INTEGRATION=1 to run Anvil tests")
    executable = shutil.which("anvil")
    if executable is None:
        pytest.skip("anvil is not installed")

    port = _available_port()
    process = subprocess.Popen(
        [
            executable,
            "--hardfork",
            ANVIL_HARDFORK,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            *_chain_args(fork),
            "--enable-tx-gas-limit",
            "--order",
            "fifo",
            # Bundles fund the victim mid-block; balances are still enforced
            # when the block executes, as a builder would.
            "--disable-pool-balance-checks",
            "--quiet",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 10}))
    deadline = time.monotonic() + ANVIL_STARTUP_TIMEOUT
    while time.monotonic() < deadline:
        if process.poll() is not None:
            output, _ = process.communicate()
            pytest.fail(
                f"Anvil exited with code {process.returncode} while starting with "
                f"{ANVIL_HARDFORK}:\n{output}"
            )
        if w3.is_connected():
            break
        time.sleep(0.05)
    else:
        process.terminate()
        try:
            output, _ = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            output, _ = process.communicate(timeout=5)
        pytest.fail(
            f"Anvil did not start within {ANVIL_STARTUP_TIMEOUT} seconds using "
            f"{ANVIL_HARDFORK}:\n" + output
        )

    if fork is not None:
        # Anvil's well-known dev accounts carry live 7702 delegations on public chains.
        w3.provider.make_request("anvil_setCode", [w3.eth.accounts[0], "0x"])

    try:
        yield w3
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class BlockRelay:
    """Anvil relay double with Flashbots semantics: a bundle lands atomically, in
    order, in its target block, or not at all. Blocks are mined only when the
    engine sleeps (see `make_ctx`), so the engine's block-by-block polling runs
    for real. `allow_reverts` lands bundles with failed transactions (status 0);
    `front_run` runs once, mined in the block before the next landing attempt.

    Bundle blocks are built with `anvil_reorg`, which executes a given
    transaction list as one block. The mempool cannot do this: it holds back a
    victim transaction whose nonce an earlier authorization in the same block
    bumps.
    """

    def __init__(self, w3: Web3):
        self.w3 = w3
        self.simulated: list[list[BundleEntry]] = []
        self.sent: list[tuple[list[BundleEntry], int]] = []
        self.landed: list[list[TxReceipt]] = []
        self.lands: Callable[[list[BundleEntry], int], bool] = lambda b, n: True
        self.allow_reverts = False
        self.front_run: Callable[[], None] | None = None

    def _rpc(self, method: str, params: list | None = None) -> Any:
        response = self.w3.provider.make_request(method, params or [])
        if "error" in response:
            raise RuntimeError(f"{method} failed: {response['error']}")
        return response["result"]

    def _mine_with(
        self, bundle: list[BundleEntry]
    ) -> tuple[list[dict[str, Any]], list[TxReceipt]]:
        """Mine `bundle` alone as the next block; per-transaction results."""
        raw = [HexBytes(entry["signed_transaction"]) for entry in bundle]
        hashes = [HexBytes(keccak(tx)) for tx in raw]
        results: list[dict[str, Any]] = [{"txHash": h.to_0x_hex()} for h in hashes]
        self._rpc("evm_mine")
        if not raw:
            return results, []
        try:
            self._rpc("anvil_reorg", [1, [[tx.to_0x_hex(), 0] for tx in raw]])
        except RuntimeError as error:
            return [{"error": str(error)} for _ in raw], []
        receipts: list[TxReceipt] = []
        for tx_hash, result in zip(hashes, results):
            try:
                receipt = self.w3.eth.get_transaction_receipt(tx_hash)
            except TransactionNotFound:
                result["error"] = "transaction was not mined"
                continue
            receipts.append(receipt)
            result["gasUsed"] = receipt["gasUsed"]
            if receipt["status"] != 1:
                result["revert"] = "transaction reverted"
        return results, receipts

    def simulate(
        self, bundle: list[BundleEntry], block_tag: int | str | None = None
    ) -> SimulationResult:
        del block_tag
        self.simulated.append(bundle)
        snapshot = self._rpc("evm_snapshot")
        try:
            results, _receipts = self._mine_with(bundle)
        finally:
            if not self._rpc("evm_revert", [snapshot]):
                raise RuntimeError("Anvil failed to revert relay simulation snapshot")
        return {
            "bundleHash": "0xsimulation",
            "results": results,
            "totalGasUsed": sum(result.get("gasUsed", 0) for result in results),
        }

    def send_bundle(self, bundle: list[BundleEntry], target_block_number: int) -> None:
        self.sent.append((bundle, target_block_number))

    def _try_land(self, bundle: list[BundleEntry]) -> bool:
        snapshot = self._rpc("evm_snapshot")
        results, receipts = self._mine_with(bundle)
        failed = any(
            "error" in result or ("revert" in result and not self.allow_reverts)
            for result in results
        )
        if failed:
            self._rpc("evm_revert", [snapshot])
            return False
        self.landed.append(receipts)
        return True

    def mine_block(self) -> None:
        if self.front_run is not None:
            front_run, self.front_run = self.front_run, None
            front_run()  # automine: lands in its own block
            return
        number = self.w3.eth.block_number + 1
        for bundle, target in self.sent:
            if target == number and self.lands(bundle, number):
                if self._try_land(bundle):
                    return
        self._rpc("evm_mine")


def _artifact(name: str, out: Path = CONTRACT_OUT) -> tuple[list[dict[str, Any]], str]:
    path = out / f"{name}.json"
    if not path.exists():
        pytest.fail("integration contracts are not compiled; run make test-integration")
    artifact = json.loads(path.read_text())
    return artifact["abi"], artifact["bytecode"]["object"]


def _deploy(w3: Web3, name: str, out: Path = CONTRACT_OUT):
    abi, bytecode = _artifact(name, out)
    factory = w3.eth.contract(abi=abi, bytecode=bytecode)
    tx_hash = factory.constructor().transact({"from": w3.eth.accounts[0]})
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash)
    assert receipt["status"] == 1
    return w3.eth.contract(address=receipt["contractAddress"], abi=abi)


@pytest.fixture
def rescue_accounts(anvil_w3: Web3) -> tuple[LocalAccount, LocalAccount, LocalAccount]:
    victim = Account.create()
    gas = Account.create()
    safe = Account.create()
    funder = anvil_w3.eth.accounts[0]
    for address, amount in (
        (victim.address, anvil_w3.to_wei(1, "ether")),
        (gas.address, anvil_w3.to_wei(10, "ether")),
    ):
        tx_hash = anvil_w3.eth.send_transaction(
            {"from": funder, "to": address, "value": amount}
        )
        anvil_w3.eth.wait_for_transaction_receipt(tx_hash)
    return victim, gas, safe


@pytest.fixture
def relay(anvil_w3: Web3) -> BlockRelay:
    relay = BlockRelay(anvil_w3)
    anvil_w3.relay = relay
    return relay


@pytest.fixture
def code_rejecting_erc20(anvil_w3: Web3, rescue_accounts):
    victim, _gas, _safe = rescue_accounts
    token = _deploy(anvil_w3, "FixtureCodeRejectingERC20")
    receipt = anvil_w3.eth.wait_for_transaction_receipt(
        token.functions.mint(victim.address, 500).transact(
            {"from": anvil_w3.eth.accounts[0]}
        )
    )
    assert receipt["status"] == 1
    return token


@pytest.fixture
def false_returning_erc20(anvil_w3: Web3, rescue_accounts):
    victim, _gas, _safe = rescue_accounts
    token = _deploy(anvil_w3, "FixtureFalseReturningERC20")
    receipt = anvil_w3.eth.wait_for_transaction_receipt(
        token.functions.mint(victim.address, 500).transact(
            {"from": anvil_w3.eth.accounts[0]}
        )
    )
    assert receipt["status"] == 1
    return token


@pytest.fixture
def asset_contracts(anvil_w3: Web3, rescue_accounts):
    victim, _gas, _safe = rescue_accounts
    contracts = {
        "erc20": _deploy(anvil_w3, "FixtureERC20"),
        "erc721": _deploy(anvil_w3, "FixtureERC721"),
        "erc1155": _deploy(anvil_w3, "FixtureERC1155"),
        "ownable": _deploy(anvil_w3, "FixtureOwnable"),
        "auction": _deploy(anvil_w3, "FixtureAuctionHouse"),
        "delegate": _deploy(anvil_w3, "FixtureDelegateTarget"),
    }
    funder = anvil_w3.eth.accounts[0]
    setup = (
        contracts["erc20"].functions.mint(victim.address, 1_000),
        contracts["erc721"].functions.mint(victim.address, 1),
        contracts["erc1155"].functions.mint(victim.address, 7, 25),
        contracts["ownable"].functions.transferOwnership(victim.address),
    )
    for call in setup:
        receipt = anvil_w3.eth.wait_for_transaction_receipt(
            call.transact({"from": funder})
        )
        assert receipt["status"] == 1
    return contracts


@pytest.fixture
def rescue_contract(anvil_w3: Web3, fork: ForkTarget | None):
    """The EthRescue deployment victims delegate to: the live one when forking."""
    if fork is None:
        return _deploy(anvil_w3, "EthRescue", ETH_RESCUE_OUT)
    abi, _bytecode = _artifact("EthRescue", ETH_RESCUE_OUT)
    return anvil_w3.eth.contract(address=fork.eth_rescue, abi=abi)


@pytest.fixture
def make_ctx(anvil_w3: Web3, reporter, rescue_contract, relay, fork):
    """Build a RescueContext whose every sleep mines one block through the relay;
    extra fields override defaults."""

    def make(victim, gas, safe, **overrides) -> RescueContext:
        fields = {
            "w3": anvil_w3,
            "victim": victim,
            "gas": gas,
            "safe": safe.address,
            "reporter": reporter,
            "rescue_contract": rescue_contract.address,
            "rescue_codehash": fork.eth_rescue_codehash if fork else None,
            "poll_interval": 0,
            "funding_timeout": 0,
            "sleep": lambda seconds: relay.mine_block(),
        }
        return RescueContext(**(fields | overrides))

    return make


@pytest.fixture
def gas_burner(anvil_w3: Web3):
    return _deploy(anvil_w3, "FixtureGasBurner")


@pytest.fixture
def receiver(anvil_w3: Web3):
    """A contract safe whose receive() needs more than the 2,300-gas stipend."""
    return _deploy(anvil_w3, "FixtureReceiver")


@pytest.fixture
def switch(anvil_w3: Web3):
    return _deploy(anvil_w3, "FixtureSwitch")
