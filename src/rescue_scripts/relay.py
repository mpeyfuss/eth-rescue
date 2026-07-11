import json
import time
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

import requests
from eth_account import Account, messages
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt

from rescue_scripts.types import (
    BundleEntry,
    CallBundleResult,
    SendBundleResult,
    SimulationResult,
)

SECONDS_PER_BLOCK = 12

BUILDERS = [
    "flashbots",
    "rsync",
    "beaverbuild.org",
    "builder0x69",
    "Titan",
    "payload",
    "bobthebuilder",
]


class RPCErrorData(TypedDict):
    code: int
    message: str


class RPCResponse[T](TypedDict):
    jsonrpc: str
    id: int
    result: NotRequired[T]
    error: NotRequired[RPCErrorData]


class RelayError(RuntimeError):
    pass


class RelayRPCError(RelayError):
    def __init__(self, code: int | None, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class BundleSubmission:
    w3: Web3
    transaction_hashes: tuple[HexBytes, ...]
    target_block_number: int
    bundle_hash: str

    def wait(self) -> None:
        while self.w3.eth.block_number < self.target_block_number:
            time.sleep(1)

    def receipts(self) -> list[TxReceipt]:
        self.wait()
        return [
            self.w3.eth.get_transaction_receipt(transaction_hash)
            for transaction_hash in self.transaction_hashes
        ]


class RelayClient:
    def __init__(
        self,
        w3: Web3,
        signature_account: LocalAccount,
        endpoint_uri: str,
        *,
        builders: list[str] | None = None,
        timeout: float = 15.0,
    ):
        self.w3 = w3
        self.signature_account = signature_account
        self.endpoint_uri = endpoint_uri
        self.builders = builders or BUILDERS
        self.timeout = timeout
        self._request_id = 0

    def _raw_transactions(self, bundle: list[BundleEntry]) -> list[HexBytes]:
        return [HexBytes(entry["signed_transaction"]) for entry in bundle]

    def _request(self, method: str, params: list[dict[str, Any]]) -> Any:
        self._request_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": self._request_id,
                "method": method,
                "params": params,
            },
            separators=(",", ":"),
        ).encode()
        digest = Web3.keccak(body).hex()
        signed = Account.sign_message(
            messages.encode_defunct(text=digest),
            private_key=self.signature_account.key,
        )

        try:
            response = requests.post(
                self.endpoint_uri,
                data=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Flashbots-Signature": (
                        f"{self.signature_account.address}:"
                        f"{signed.signature.to_0x_hex()}"
                    ),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            payload: RPCResponse[Any] = response.json()
        except requests.RequestException as e:
            raise RelayError(f"Relay request failed: {e}") from e

        if "error" in payload:
            error = payload["error"]
            raise RelayRPCError(error.get("code"), error["message"])

        return payload["result"]

    def simulate(
        self, bundle: list[BundleEntry], block_tag: int | str | None = None
    ) -> SimulationResult:
        block_number = (
            self.w3.eth.block_number
            if block_tag is None or block_tag == "latest"
            else int(block_tag)
        )

        latest_block = self.w3.eth.block_number

        if block_number < latest_block:
            raise ValueError("Simulation target block cannot be in the past")

        timestamp = (
            int(self.w3.eth.get_block(latest_block)["timestamp"])
            + (block_number - latest_block) * SECONDS_PER_BLOCK
        )

        raw_transactions = self._raw_transactions(bundle)
        result: CallBundleResult = self._request(
            "eth_callBundle",
            [
                {
                    "txs": [tx.to_0x_hex() for tx in raw_transactions],
                    "blockNumber": hex(block_number),
                    "stateBlockNumber": hex(block_number - 1),
                    "timestamp": timestamp,
                }
            ],
        )

        tx_results = result["results"]
        simulation: SimulationResult = {
            "bundleHash": result.get("bundleHash", ""),
            "results": tx_results,
            "totalGasUsed": sum(int(tx.get("gasUsed", 0)) for tx in tx_results),
        }

        return simulation

    def send_bundle(
        self, bundle: list[BundleEntry], target_block_number: int
    ) -> BundleSubmission:
        raw_transactions = self._raw_transactions(bundle)
        result: SendBundleResult = self._request(
            "eth_sendBundle",
            [
                {
                    "txs": [tx.to_0x_hex() for tx in raw_transactions],
                    "blockNumber": hex(target_block_number),
                    "builders": self.builders,
                }
            ],
        )
        return BundleSubmission(
            self.w3,
            tuple(self.w3.keccak(tx) for tx in raw_transactions),
            target_block_number,
            result["bundleHash"],
        )


class RelayWeb3(Web3):
    relay: RelayClient
