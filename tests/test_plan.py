import json
from pathlib import Path
from typing import get_args

import pytest

from eth_rescue import templates
from eth_rescue.plan import (
    NetworkName,
    PlanError,
    expand,
    load_plan,
    parse_plan,
    render_schema,
)
from eth_rescue.networks import NETWORKS

ROOT = Path(__file__).parents[1]
EXAMPLES = sorted((ROOT / "examples").glob("*.json"))
SCHEMA_PATH = ROOT / "schema" / "plan.v1.schema.json"

VICTIM = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61"
SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1"
GAS = "0x2546BcD3c84621e976D8185a91A922aE77ECEc30"
TOKEN = "0x5509102d66eEC09A994f978c80535572890f1Fef"
AUCTION_HOUSE = "0x6f66b95a0C512f3497FB46660E0BC3B94B989F8d"


def _plan(actions: list[dict], **overrides) -> str:
    return json.dumps(
        {
            "version": 1,
            "network": "mainnet",
            "victim": VICTIM,
            "safe": SAFE,
            "actions": actions,
        }
        | overrides
    )


ERC721 = {"type": "erc721", "contract": TOKEN, "token_id": "78"}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_examples_load_and_expand(path):
    assert expand(load_plan(path))


def test_committed_schema_is_fresh():
    assert SCHEMA_PATH.read_text() == render_schema(), "run `make schema`"


def test_network_names_match_configured_networks():
    assert set(get_args(NetworkName)) == set(NETWORKS)


def test_defaults_applied():
    plan = parse_plan(_plan([ERC721]))

    assert plan.gas_wallet is None
    assert plan.options.priority_fee_gwei == "0"
    assert plan.options.max_blocks == 25
    assert plan.options.legacy_fallback is True


def test_addresses_are_checksummed():
    plan = parse_plan(_plan([ERC721], victim=VICTIM.lower()))

    assert plan.victim == VICTIM


def test_erc721_fills_victim_safe_and_default_gas():
    [call] = expand(parse_plan(_plan([ERC721])))

    assert call["address"] == TOKEN
    assert call["args"] == [VICTIM, SAFE, 78]
    assert call["gas_estimate"] == templates.GAS_ERC721


def test_gas_override_and_label_applied():
    action = ERC721 | {"gas": 99_000, "label": "My NFT"}
    [call] = expand(parse_plan(_plan([action])))

    assert call["gas_estimate"] == 99_000
    assert call["description"] == "My NFT"


def test_transient_delist_expands_to_delist_then_transfer():
    action = {
        "type": "transient_delist_erc721",
        "auction_house": AUCTION_HOUSE,
        "contract": TOKEN,
        "token_id": "5",
    }
    delist, transfer = expand(parse_plan(_plan([action])))

    assert delist["address"] == AUCTION_HOUSE
    assert delist["args"] == [TOKEN, 5]
    assert transfer["args"] == [VICTIM, SAFE, 5]


def test_custom_action_passes_args_through():
    action = {
        "type": "custom",
        "target": TOKEN,
        "signature": "transferPunk(address,uint256)",
        "args": [SAFE, "78"],
        "gas": 75_000,
    }
    [call] = expand(parse_plan(_plan([action])))

    assert call["function_signature"] == "transferPunk(address,uint256)"
    assert call["args"] == [SAFE, "78"]
    assert call["gas_estimate"] == 75_000


def test_max_uint256_amount_accepted():
    action = {"type": "erc20", "contract": TOKEN, "amount": str(2**256 - 1)}

    [call] = expand(parse_plan(_plan([action])))

    assert call["args"][1] == 2**256 - 1


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"options": {"legacy_fallback": "false"}}, id="bool-as-string"),
        pytest.param({"options": {"legacy_fallback": 0}}, id="bool-as-number"),
        pytest.param({"options": {"max_blocks": "25"}}, id="int-as-string"),
        pytest.param({"options": {"max_blocks": True}}, id="int-as-bool"),
    ],
)
def test_options_are_not_coerced(overrides):
    with pytest.raises(PlanError, match="options"):
        parse_plan(_plan([ERC721], **overrides))


def test_action_gas_is_not_coerced():
    with pytest.raises(PlanError, match="gas"):
        parse_plan(_plan([ERC721 | {"gas": "21000"}]))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("{not json", "Invalid JSON"),
        (_plan([]), "actions"),
        (_plan([ERC721], version=2), "version"),
        (_plan([ERC721], network="goerli"), "network"),
        (_plan([ERC721], extra=True), "extra"),
        (
            _plan([ERC721], victim="0xD068c6a6db349a8ce9c7ad3706391e53417abf61"),
            "checksum",
        ),
        (_plan([ERC721], safe=VICTIM), "safe must differ"),
        (
            _plan([ERC721], safe=NETWORKS["mainnet"]["eth_rescue"].lower()),
            "EthRescue contract",
        ),
        (_plan([ERC721], gas_wallet=SAFE), "gas_wallet must differ"),
        (_plan([ERC721 | {"token_id": 78}]), "token_id"),
        (_plan([ERC721 | {"token_id": "-1"}]), "token_id"),
        (_plan([ERC721 | {"gas": 0}]), "gas"),
        (
            _plan([{"type": "erc20", "contract": TOKEN, "amount": str(2**256)}]),
            "uint256",
        ),
        (_plan([{"type": "nft", "contract": TOKEN}]), "type"),
        (_plan([{"type": "erc20", "contract": TOKEN, "amount": "0"}]), "amount"),
        (
            _plan(
                [
                    {
                        "type": "erc1155",
                        "contract": TOKEN,
                        "token_id": "1",
                        "amount": "000",
                    }
                ]
            ),
            "amount",
        ),
        (
            _plan([ERC721], safe="0x0000000000000000000000000000000000000000"),
            "zero address",
        ),
        (_plan([ERC721 | {"contract": VICTIM}]), "compromised wallet"),
        (
            _plan(
                [
                    {
                        "type": "custom",
                        "target": VICTIM,
                        "signature": "f()",
                        "args": [],
                        "gas": 50_000,
                    }
                ]
            ),
            "compromised wallet",
        ),
        (
            _plan(
                [
                    {
                        "type": "custom",
                        "target": TOKEN,
                        "signature": "f(uint)",
                        "args": ["1"],
                        "gas": 50_000,
                    }
                ]
            ),
            "canonical",
        ),
        (
            _plan(
                [
                    {
                        "type": "custom",
                        "target": TOKEN,
                        "signature": "f(uint256)",
                        "args": [],
                    }
                ]
            ),
            "gas",
        ),
    ],
    ids=[
        "invalid-json",
        "empty-actions",
        "unknown-version",
        "unknown-network",
        "unknown-field",
        "bad-checksum",
        "safe-is-victim",
        "safe-is-ethrescue",
        "gas-is-safe",
        "numeric-token-id",
        "negative-token-id",
        "zero-gas",
        "amount-overflow",
        "unknown-action-type",
        "zero-erc20-amount",
        "zero-erc1155-amount",
        "zero-safe",
        "victim-as-contract",
        "victim-as-custom-target",
        "non-canonical-signature",
        "custom-without-gas",
    ],
)
def test_invalid_plans_rejected(text, message):
    with pytest.raises(PlanError, match=message):
        parse_plan(text)


def test_unencodable_custom_action_reports_index():
    bad = {
        "type": "custom",
        "target": TOKEN,
        "signature": "transfer(address,uint256)",
        "args": [SAFE],
        "gas": 50_000,
    }

    with pytest.raises(PlanError, match=r"actions\.1: .*expects 2 args, got 1"):
        parse_plan(_plan([ERC721, bad]))


def test_load_plan_missing_file(tmp_path):
    with pytest.raises(PlanError, match="could not read"):
        load_plan(tmp_path / "missing.json")


def test_transient_delist_accepts_gas_override():
    action = {
        "type": "transient_delist_erc721",
        "auction_house": AUCTION_HOUSE,
        "contract": TOKEN,
        "token_id": "5",
        "gas": 400_000,
    }

    delist, _transfer = expand(parse_plan(_plan([action])))

    assert delist["gas_estimate"] == 400_000


def test_multi_call_labels_name_each_call():
    action = {
        "type": "transient_delist_erc721",
        "auction_house": AUCTION_HOUSE,
        "contract": TOKEN,
        "token_id": "5",
        "label": "Listed NFT",
    }

    delist, transfer = expand(parse_plan(_plan([action])))

    assert delist["description"] == "Listed NFT (delist)"
    assert transfer["description"] == "Listed NFT (transferFrom)"
    assert (delist["plan_action"], delist["plan_step"]) == (0, 0)
    assert (transfer["plan_action"], transfer["plan_step"]) == (0, 1)


def test_custom_bool_argument_is_strict():
    action = {
        "type": "custom",
        "target": TOKEN,
        "signature": "setApprovalForAll(address,bool)",
        "args": [SAFE, "true"],
        "gas": 50_000,
    }

    with pytest.raises(PlanError, match="expected true or false"):
        parse_plan(_plan([action]))

    action["args"] = [SAFE, True]
    [call] = expand(parse_plan(_plan([action])))
    assert call["args"] == [SAFE, True]


def test_load_plan_rejects_invalid_utf8(tmp_path):
    path = tmp_path / "plan.json"
    path.write_bytes(b"\xff\xfe{}")

    with pytest.raises(PlanError, match="UTF-8"):
        load_plan(path)


def test_zero_token_id_is_still_valid():
    action = {"type": "erc1155", "contract": TOKEN, "token_id": "0", "amount": "1"}

    [call] = expand(parse_plan(_plan([action])))

    assert call["args"][2:4] == [0, 1]


def test_validation_messages_drop_pydantic_prefix():
    with pytest.raises(PlanError) as error:
        parse_plan(_plan([ERC721], safe=VICTIM))

    assert str(error.value) == "safe must differ from victim"


def test_web_builder_knows_each_network_ethrescue_deployment():
    draft = (ROOT / "web" / "src" / "plan" / "draft.ts").read_text()

    for name, network in NETWORKS.items():
        assert f'value: "{name}"' in draft
        assert f'ethRescue: "{network["eth_rescue"]}"' in draft
