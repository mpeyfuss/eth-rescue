import json
from types import SimpleNamespace

import pytest
import requests
from eth_account import Account, messages
from hexbytes import HexBytes
from web3 import Web3

from eth_rescue.relay import RelayClient, RelayError, RelayRPCError


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def _client():
    eth = SimpleNamespace(
        block_number=100,
        get_block=lambda number: {"timestamp": 1_000},
    )
    w3 = SimpleNamespace(
        eth=eth,
        keccak=lambda value: HexBytes(b"\x11" * 32),
    )
    return RelayClient(w3, Account.create(), "https://relay.example")


def test_send_bundle_signs_exact_json_rpc_body(monkeypatch):
    requests = []
    response = {"jsonrpc": "2.0", "id": 1, "result": {"bundleHash": "0xabc"}}
    monkeypatch.setattr(
        "eth_rescue.relay.requests.post",
        lambda url, **kwargs: requests.append((url, kwargs)) or Response(response),
    )
    w3 = SimpleNamespace(keccak=lambda value: HexBytes(b"\x11" * 32))
    signer = Account.create()
    client = RelayClient(w3, signer, "https://relay.example", builders=["builder"])

    submission = client.send_bundle(
        [{"signed_transaction": HexBytes("0x1234")}], target_block_number=42
    )

    url, request = requests[0]
    body = json.loads(request["data"])
    assert url == "https://relay.example"
    assert request["timeout"] == 15.0
    assert body["method"] == "eth_sendBundle"
    assert body["params"] == [
        {"txs": ["0x1234"], "blockNumber": "0x2a", "builders": ["builder"]}
    ]
    assert request["headers"]["X-Flashbots-Signature"].startswith(f"{signer.address}:")
    address, signature = request["headers"]["X-Flashbots-Signature"].split(":")
    digest = Web3.keccak(text=request["data"]).to_0x_hex()
    assert digest.startswith("0x")
    assert signature.startswith("0x")
    recovered = Account.recover_message(
        messages.encode_defunct(text=digest), signature=signature
    )
    assert address == recovered == signer.address
    assert submission.bundle_hash == "0xabc"


def test_send_bundle_omits_builders_when_not_configured(monkeypatch):
    request_params = []
    client = _client()
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, params: request_params.extend(params) or {"bundleHash": "0xabc"},
    )

    client.send_bundle(
        [{"signed_transaction": HexBytes("0x1234")}], target_block_number=42
    )

    assert request_params == [{"txs": ["0x1234"], "blockNumber": "0x2a"}]


def test_simulate_builds_expected_call_bundle_request(monkeypatch):
    client = _client()
    calls = []
    monkeypatch.setattr(
        client,
        "_request",
        lambda method, params: calls.append((method, params))
        or {
            "bundleHash": "0xbundle",
            "results": [{"txHash": "0xtx", "gasUsed": 21_000}, {"gasUsed": 30_000}],
        },
    )

    result = client.simulate(
        [
            {"signed_transaction": HexBytes("0x1234")},
            {"signed_transaction": HexBytes("0xabcd")},
        ],
        block_tag=103,
    )

    assert calls == [
        (
            "eth_callBundle",
            [
                {
                    "txs": ["0x1234", "0xabcd"],
                    "blockNumber": "0x67",
                    "stateBlockNumber": "latest",
                    "timestamp": 1_036,
                }
            ],
        )
    ]
    assert result["bundleHash"] == "0xbundle"
    assert result["totalGasUsed"] == 51_000


def test_simulate_rejects_past_target_block():
    client = _client()

    with pytest.raises(ValueError, match="cannot be in the past"):
        client.simulate([], block_tag=99)


def test_simulate_rejects_missing_transaction_results(monkeypatch):
    client = _client()
    monkeypatch.setattr(client, "_request", lambda method, params: {})

    with pytest.raises(RelayError, match="transaction results"):
        client.simulate([], block_tag=100)


def test_request_wraps_transport_and_invalid_json_errors(monkeypatch):
    client = _client()

    def raise_transport(*args, **kwargs):
        raise requests.ConnectionError("offline")

    monkeypatch.setattr("eth_rescue.relay.requests.post", raise_transport)
    with pytest.raises(RelayError, match="offline"):
        client._request("eth_test", [{}])

    class InvalidJSONResponse(Response):
        def json(self):
            raise ValueError("invalid json")

    monkeypatch.setattr(
        "eth_rescue.relay.requests.post",
        lambda *args, **kwargs: InvalidJSONResponse({}),
    )
    with pytest.raises(RelayError, match="invalid json"):
        client._request("eth_test", [{}])


def test_request_includes_sanitized_http_error_response(monkeypatch):
    client = _client()
    response = requests.Response()
    response.status_code = 403
    response.url = "https://relay.example"
    response._content = b"  request  blocked\nby policy  "

    monkeypatch.setattr(
        "eth_rescue.relay.requests.post", lambda *args, **kwargs: response
    )

    with pytest.raises(
        RelayError, match=r"403 Client Error.*Response: request blocked by policy"
    ):
        client._request("eth_test", [{}])


def test_request_raises_typed_rpc_error(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "eth_rescue.relay.requests.post",
        lambda *args, **kwargs: Response(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "bad bundle"},
            }
        ),
    )

    with pytest.raises(RelayRPCError, match="bad bundle") as error:
        client._request("eth_test", [{}])

    assert error.value.code == -32000


def test_request_rejects_response_without_result(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        "eth_rescue.relay.requests.post",
        lambda *args, **kwargs: Response({"jsonrpc": "2.0", "id": 1}),
    )

    with pytest.raises(RelayError, match="did not include a result"):
        client._request("eth_test", [{}])
