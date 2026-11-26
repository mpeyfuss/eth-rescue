import json
from dataclasses import dataclass
from typing import Any, NotRequired, TypedDict

import requests
from eth_account import Account, messages
from eth_account.signers.local import LocalAccount
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue.types import (
    BundleEntry,
    CallBundleResult,
    SendBundleResult,
    SimulationResult,
)

SECONDS_PER_BLOCK = 12
MAX_ERROR_LENGTH = 300

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


class RelayTimeout(RelayError):
    """The request may or may not have reached the relay."""


class RelayRPCError(RelayError):
    def __init__(self, code: int | None, message: str):
        self.code = code
        super().__init__(message)


@dataclass(frozen=True)
class BundleSubmission:
    transaction_hashes: tuple[HexBytes, ...]
    target_block_number: int
    bundle_hash: str


def _error_message(response: requests.Response) -> str:
    """Only the relay's JSON-RPC error message: response bodies can echo the
    submitted request, which holds signed transactions."""
    try:
        payload = response.json()
        message = payload["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return ""
    return str(message)[:MAX_ERROR_LENGTH]


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
        self.builders = builders
        self.timeout = timeout
        self._request_id = 0

    def _raw_transactions(self, bundle: list[BundleEntry]) -> list[HexBytes]:
        return [HexBytes(entry["signed_transaction"]) for entry in bundle]

    def _request(self, method: str, params: list[dict[str, Any]]) -> Any:
        self._request_id += 1
        body = json.dumps(
            {
                "jsonrpc": "2.0",
                "method": method,
                "params": params,
                "id": self._request_id,
            },
            separators=(",", ":"),
        )
        digest = Web3.keccak(text=body).to_0x_hex()
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
                    "X-Flashbots-Signature": f"{self.signature_account.address}:{signed.signature.to_0x_hex()}",  # fmt: skip
                },
                timeout=self.timeout,
            )
        except requests.Timeout as e:
            raise RelayTimeout("Relay request timed out") from e
        except requests.RequestException as e:
            raise RelayError(f"Relay request failed: {type(e).__name__}") from e

        if not response.ok:
            detail = _error_message(response)
            suffix = f": {detail}" if detail else ""
            raise RelayError(
                f"Relay request failed with HTTP {response.status_code}{suffix}"
            )

        try:
            payload: RPCResponse[Any] = response.json()
        except ValueError as e:
            raise RelayError("Relay response was not JSON") from e

        if "error" in payload:
            error = payload["error"]
            raise RelayRPCError(
                error.get("code"), str(error.get("message", ""))[:MAX_ERROR_LENGTH]
            )

        if "result" not in payload:
            raise RelayError("Relay response did not include a result")
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
                    "stateBlockNumber": "latest",
                    "timestamp": timestamp,
                }
            ],
        )

        tx_results = result.get("results")
        if not isinstance(tx_results, list):
            raise RelayError(
                "Relay simulation response did not include transaction results"
            )
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
        params = {
            "txs": [tx.to_0x_hex() for tx in raw_transactions],
            "blockNumber": hex(target_block_number),
        }
        if self.builders is not None:
            params["builders"] = self.builders
        result: SendBundleResult = self._request("eth_sendBundle", [params])
        return BundleSubmission(
            tuple(self.w3.keccak(tx) for tx in raw_transactions),
            target_block_number,
            result["bundleHash"],
        )


class RelayWeb3(Web3):
    relay: RelayClient
