import json

import pytest

from eth_rescue import cli
from eth_rescue.contract import runtime_codehash
from eth_rescue.engine import ExitCode, RescueContext
from eth_rescue.networks import connect

pytestmark = pytest.mark.integration


@pytest.fixture
def anvil_network(anvil_w3, rescue_contract, relay, monkeypatch):
    """Point the CLI's mainnet entry at Anvil: its chain, its EthRescue and the
    block-building relay double. Blocks advance only when the engine sleeps."""
    code = bytes(anvil_w3.eth.get_code(rescue_contract.address))
    network = cli.NETWORKS["mainnet"] | {
        "eth_rescue": rescue_contract.address,
        "eth_rescue_codehash": runtime_codehash(code),
        "chain_id": anvil_w3.eth.chain_id,
    }
    monkeypatch.setitem(cli.NETWORKS, "mainnet", network)

    def connect_to_anvil(network, auth, rpc=None):
        w3 = connect(network, auth, rpc=rpc)
        w3.relay = relay
        return w3

    monkeypatch.setattr(cli, "connect", connect_to_anvil)
    monkeypatch.setattr(
        cli,
        "RescueContext",
        lambda **fields: RescueContext(
            **fields,
            sleep=lambda seconds: relay.mine_block(),
            poll_interval=0,
            head_poll_interval=0,
        ),
    )
    return anvil_w3.provider.endpoint_uri


def _type_keys(monkeypatch, *accounts):
    keys = [account.key.to_0x_hex() for account in accounts]
    monkeypatch.setattr(cli.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: keys.pop(0))


def _plan(tmp_path, victim, gas, safe, asset_contracts):
    path = tmp_path / "plan.json"
    path.write_text(
        json.dumps(
            {
                "version": 1,
                "network": "mainnet",
                "victim": victim.address,
                "safe": safe.address,
                "gas_wallet": gas.address,
                "options": {"max_blocks": 10},
                "actions": [
                    {
                        "type": "erc20",
                        "contract": asset_contracts["erc20"].address,
                        "amount": "1000",
                    },
                    {
                        "type": "erc721",
                        "contract": asset_contracts["erc721"].address,
                        "token_id": "1",
                    },
                    {
                        "type": "erc1155",
                        "contract": asset_contracts["erc1155"].address,
                        "token_id": "7",
                        "amount": "25",
                    },
                    {
                        "type": "ownership",
                        "contract": asset_contracts["ownable"].address,
                    },
                ],
            }
        )
    )
    return path


def test_cli_run_yes_rescues_everything(
    anvil_network, rescue_accounts, asset_contracts, monkeypatch, tmp_path, capsys
):
    victim, gas, safe = rescue_accounts
    _type_keys(monkeypatch, victim, gas)
    plan = _plan(tmp_path, victim, gas, safe, asset_contracts)

    code = cli.main(["run", str(plan), "--yes", "--json", "--rpc", anvil_network])

    events = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert code == ExitCode.OK, events
    [outcomes] = [e["outcomes"] for e in events if e["event"] == "outcomes"]
    assert [o["status"] for o in outcomes] == ["rescued"] * 4
    assert [o["action"] for o in outcomes] == [0, 1, 2, 3]
    assert events[-1]["text"] == "Rescue complete."
    assert asset_contracts["erc721"].functions.ownerOf(1).call() == safe.address
    assert asset_contracts["ownable"].functions.owner().call() == safe.address


def test_cli_simulate_sends_nothing(
    anvil_network,
    rescue_accounts,
    asset_contracts,
    relay,
    monkeypatch,
    tmp_path,
    capsys,
):
    victim, gas, safe = rescue_accounts
    _type_keys(monkeypatch, victim, gas)
    plan = _plan(tmp_path, victim, gas, safe, asset_contracts)

    code = cli.main(["simulate", str(plan), "--json", "--rpc", anvil_network])

    assert code == ExitCode.OK
    assert relay.simulated and relay.sent == []
    assert asset_contracts["erc721"].functions.ownerOf(1).call() == victim.address
