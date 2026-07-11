import json
from types import SimpleNamespace

from eth_account import Account
from hexbytes import HexBytes

from rescue_scripts.relay import RelayClient


class Response:
    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


def test_send_bundle_signs_exact_json_rpc_body(monkeypatch):
    requests = []
    response = {"jsonrpc": "2.0", "id": 1, "result": {"bundleHash": "0xabc"}}
    monkeypatch.setattr(
        "rescue_scripts.relay.requests.post",
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
    assert request["headers"]["X-Flashbots-Signature"].startswith(
        f"{signer.address}:"
    )
    assert submission.bundle_hash == "0xabc"
