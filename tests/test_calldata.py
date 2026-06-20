import pytest
from eth_utils import function_signature_to_4byte_selector

from rescue_scripts import calldata
from rescue_scripts.calldata import build_calldata

SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1"
VICTIM = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61"


def _selector(sig: str) -> str:
    return "0x" + function_signature_to_4byte_selector(sig).hex()


def test_build_calldata_encodes_selector_and_args():
    data = build_calldata("transfer(address,uint256)", [SAFE, 1000])

    assert data.startswith(_selector("transfer(address,uint256)"))
    assert data == build_calldata("transfer(address,uint256)", [SAFE.lower(), 1000])


def test_build_calldata_coerces_hex_string_integers():
    from_int = build_calldata("setValue(uint256)", [255])
    from_hex = build_calldata("setValue(uint256)", ["0xff"])

    assert from_int == from_hex


def test_build_calldata_coerces_bool_and_bytes():
    data = build_calldata("configure(bool,bytes)", ["true", "0xabcd"])

    assert data.startswith(_selector("configure(bool,bytes)"))
    assert build_calldata("configure(bool,bytes)", [True, b"\xab\xcd"]) == data


def test_build_calldata_handles_arrays():
    data = build_calldata("batch(uint256[])", [[1, "0x2", 3]])

    assert data.startswith(_selector("batch(uint256[])"))


def test_build_calldata_raises_on_arg_count_mismatch():
    with pytest.raises(ValueError, match="expects 2 args, got 1"):
        build_calldata("transfer(address,uint256)", [SAFE])


def test_parse_types_keeps_nested_tuples_intact():
    types = calldata._parse_types("swap((address,uint256),uint256)")

    assert types == ["(address,uint256)", "uint256"]


def test_parse_types_empty_signature_returns_no_types():
    assert calldata._parse_types("pause()") == []
