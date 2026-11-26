import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from web3 import Web3
from web3.exceptions import Web3RPCError

from eth_rescue import rpc
from eth_rescue.rpc import MAX_WAIT, BackoffHTTPProvider, _CappedRetry


class ScriptedRPC:
    """Answers each request with the next scripted (status, headers, reply);
    a reply is a JSON-RPC result, or an error dict under "error"."""

    def __init__(self, script):
        self.script = list(script)
        self.requests = 0
        server = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                server.requests += 1
                status, headers, reply = server.script.pop(0)
                payload = {"jsonrpc": "2.0", "id": body["id"]}
                if isinstance(reply, dict) and "error" in reply:
                    payload |= reply
                else:
                    payload["result"] = reply
                data = json.dumps(payload).encode()
                self.send_response(status)
                for name, value in headers.items():
                    self.send_header(name, value)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def serve():
    servers = []

    def start(*script):
        servers.append(ScriptedRPC(script))
        return servers[-1]

    yield start
    for server in servers:
        server.httpd.shutdown()


def _w3(server, sleeps):
    return Web3(BackoffHTTPProvider(server.url, sleep=sleeps.append))


def test_http_429_is_retried_after_its_retry_after(serve):
    server = serve((429, {"Retry-After": "0"}, None), (200, {}, "0x10"))

    assert _w3(server, []).eth.block_number == 16
    assert server.requests == 2


def test_unavailable_node_is_retried(serve):
    server = serve((503, {}, None), (200, {}, "0x10"))

    assert _w3(server, []).eth.block_number == 16
    assert server.requests == 2


def test_jsonrpc_rate_limit_error_backs_off_and_retries(serve):
    limited = {"error": {"code": -32005, "message": "limit exceeded"}}
    server = serve((200, {}, limited), (200, {}, limited), (200, {}, "0x10"))
    sleeps = []

    assert _w3(server, sleeps).eth.block_number == 16
    assert server.requests == 3
    assert len(sleeps) == 2
    assert sleeps[0] < sleeps[1] <= MAX_WAIT + rpc.BACKOFF_JITTER


def test_other_jsonrpc_errors_are_not_retried(serve):
    reverted = {"error": {"code": -32000, "message": "execution reverted"}}
    server = serve((200, {}, reverted))
    sleeps = []

    with pytest.raises(Web3RPCError, match="execution reverted"):
        _w3(server, sleeps).eth.block_number

    assert server.requests == 1
    assert sleeps == []


def test_persistent_jsonrpc_rate_limit_gives_up(serve):
    limited = {"error": {"code": 429, "message": "too many requests"}}
    server = serve(*[(200, {}, limited)] * (rpc.RETRIES + 1))

    with pytest.raises(Web3RPCError, match="too many requests"):
        _w3(server, []).eth.block_number

    assert server.requests == rpc.RETRIES + 1


def test_retry_after_is_capped():
    assert _CappedRetry().parse_retry_after("3600") == MAX_WAIT
    assert _CappedRetry().parse_retry_after("2") == 2
