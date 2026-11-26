import json
from pathlib import Path

import pytest

from eth_rescue.calldata import CalldataError, build_calldata, canonical_signature

DESTINATION = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1"
VECTORS = json.loads(
    (Path(__file__).parent / "fixtures" / "abi_vectors.json").read_text()
)


@pytest.mark.parametrize(
    "vector",
    [v for v in VECTORS if "calldata" in v],
    ids=lambda v: v["signature"],
)
def test_shared_vector_encodes(vector):
    assert build_calldata(vector["signature"], vector["args"]) == vector["calldata"]


@pytest.mark.parametrize(
    "vector",
    [v for v in VECTORS if "error" in v],
    ids=lambda v: v["error"],
)
def test_shared_vector_is_rejected(vector):
    with pytest.raises(CalldataError):
        build_calldata(vector["signature"], vector["args"])


def test_build_calldata_matches_known_erc20_transfer():
    assert (
        build_calldata("transfer(address,uint256)", [DESTINATION, 1000])
        == "0xa9059cbb00000000000000000000000074a7b842fdeb244c152aa5bc8b7fbae362091ee100000000000000000000000000000000000000000000000000000000000003e8"
    )


def test_alias_is_rejected_with_the_canonical_form():
    with pytest.raises(CalldataError, match=r"use transferPunk\(address,uint256\)"):
        build_calldata("transferPunk(address,uint)", [DESTINATION, "1"])


def test_canonical_signature_expands_aliases_and_nested_types():
    assert canonical_signature("f(uint[],(int,bool)[2])") == (
        "f(uint256[],(int256,bool)[2])"
    )


def test_error_names_the_offending_argument():
    with pytest.raises(CalldataError, match=r"args\[1\]\[0\]\[1\]"):
        build_calldata("f(uint256,(address,uint256)[])", ["1", [[DESTINATION, "x"]]])
