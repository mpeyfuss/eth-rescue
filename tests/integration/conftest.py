import os
import selectors
import shutil
import socket
import subprocess
from collections.abc import Iterator

import pytest
from web3 import HTTPProvider, Web3

ANVIL_HARDFORK = os.environ.get("ANVIL_HARDFORK", "osaka")


def _available_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@pytest.fixture(scope="session")
def anvil_w3() -> Iterator[Web3]:
    external_url = os.environ.get("ANVIL_RPC_URL")
    if external_url:
        w3 = Web3(HTTPProvider(external_url))
        if not w3.is_connected():
            pytest.fail(f"Could not connect to ANVIL_RPC_URL={external_url}")
        yield w3
        return

    if os.environ.get("RUN_ANVIL_INTEGRATION") != "1":
        pytest.skip("set RUN_ANVIL_INTEGRATION=1 or ANVIL_RPC_URL to run Anvil tests")
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
            "--chain-id",
            "31337",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    url = f"http://127.0.0.1:{port}"
    w3 = Web3(HTTPProvider(url, request_kwargs={"timeout": 1}))
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output: list[str] = []
    events = selector.select(timeout=10)
    while events:
        line = process.stdout.readline()
        output.append(line)
        if "Listening on" in line:
            break
        events = selector.select(timeout=10)
    else:
        process.terminate()
        pytest.fail(
            f"Anvil did not start within 10 seconds using {ANVIL_HARDFORK}:\n"
            + "".join(output)
        )
    if not w3.is_connected():
        process.terminate()
        pytest.fail(f"Anvil announced startup but RPC is unavailable at {url}")

    try:
        yield w3
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


@pytest.fixture(autouse=True)
def isolate_chain(anvil_w3: Web3) -> Iterator[None]:
    snapshot = anvil_w3.provider.make_request("evm_snapshot", [])["result"]
    yield
    anvil_w3.provider.make_request("evm_revert", [snapshot])
