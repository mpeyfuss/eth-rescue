import pytest
from eth_utils import function_signature_to_4byte_selector

from eth_rescue import calldata
from eth_rescue.calldata import build_calldata

SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1"
VICTIM = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61"


def _selector(sig: str) -> str:
    return "0x" + function_signature_to_4byte_selector(sig).hex()


def test_build_calldata_raises_on_invalid_types():
    with pytest.raises(
        ValueError,
        match="invalid types were found",
    ):
        build_calldata("transfer(addy)", [SAFE])


def test_build_calldata_encodes_selector_and_args():
    data = build_calldata("transfer(address,uint256)", [SAFE, 1000])

    assert data.startswith(_selector("transfer(address,uint256)"))
    assert data == build_calldata("transfer(address,uint256)", [SAFE.lower(), 1000])
    assert (
        data
        == "0xa9059cbb00000000000000000000000074a7b842fdeb244c152aa5bc8b7fbae362091ee100000000000000000000000000000000000000000000000000000000000003e8"
    )


def test_build_calldata_handles_arrays():
    data = build_calldata("batch(uint256[])", [[1, 2, 3]])

    assert data.startswith(_selector("batch(uint256[])"))
    assert (
        data
        == "0x29ba162900000000000000000000000000000000000000000000000000000000000000200000000000000000000000000000000000000000000000000000000000000003000000000000000000000000000000000000000000000000000000000000000100000000000000000000000000000000000000000000000000000000000000020000000000000000000000000000000000000000000000000000000000000003"
    )


def test_build_calldata_raises_on_arg_count_mismatch():
    with pytest.raises(ValueError, match="expects 2 args, got 1"):
        build_calldata("transfer(address,uint256)", [SAFE])


def test_parse_types_keeps_nested_tuples_intact():
    types = calldata._parse_types("swap((address,uint256),uint256)")

    assert types == ["(address,uint256)", "uint256"]


def test_parse_types_empty_signature_returns_no_types():
    assert calldata._parse_types("pause()") == []
