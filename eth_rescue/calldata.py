"""
ABI encoding for plan calls.

The plan's function signatures must be canonical (`name(type,...)` with full
type names, no spaces, parameter names or aliases) because the selector is
the hash of that exact text. The web builder applies the same rules, and
`tests/fixtures/abi_vectors.json` holds the shared cases both sides must agree on.
"""

import re
from typing import Any

from eth_abi import encode as abi_encode, is_encodable_type
from eth_abi.exceptions import ABITypeError, EncodingError, ParseError
from eth_abi.grammar import ABIType, BasicType, TupleType, normalize, parse
from eth_utils import function_signature_to_4byte_selector, is_checksum_address
from eth_utils import to_checksum_address

SIGNATURE_RE = re.compile(r"^([A-Za-z_$][A-Za-z0-9_$]*)\((.*)\)$", re.DOTALL)
SUPPORTED_BASES = {"address", "bool", "string", "bytes", "uint", "int"}
INTEGER_RE = re.compile(r"^(-?[0-9]+|0x[0-9a-fA-F]+)$")
HEX_BYTES_RE = re.compile(r"^0x([0-9a-fA-F]{2})*$")
ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


class CalldataError(ValueError):
    pass


def _check_supported(abi_type: ABIType) -> None:
    if isinstance(abi_type, TupleType):
        for component in abi_type.components:
            _check_supported(component)
        return
    assert isinstance(abi_type, BasicType)
    if abi_type.base not in SUPPORTED_BASES:
        raise CalldataError(f"unsupported type {abi_type.to_type_str()}")
    try:
        abi_type.validate()
    except ABITypeError as e:
        raise CalldataError(f"invalid type {abi_type.to_type_str()}") from e


def _parse_params(params: str) -> list[ABIType]:
    if params == "":
        return []
    try:
        return list(parse(f"({params})").components)
    except (ParseError, ValueError) as e:
        raise CalldataError(f"could not parse parameter types ({params})") from e


def canonical_signature(signature: str) -> str:
    """The signature with aliases expanded, e.g. `f(uint)` -> `f(uint256)`."""
    match = SIGNATURE_RE.match(signature)
    if match is None:
        raise CalldataError(
            f"{signature!r} is not a function signature like name(type,...)"
        )
    name, params = match.groups()
    types = [normalize(t.to_type_str()) for t in _parse_params(params)]
    return f"{name}({','.join(types)})"


def parse_signature(signature: str) -> list[ABIType]:
    """Parameter types of a canonical signature; rejects anything else."""
    canonical = canonical_signature(signature)
    if canonical != signature:
        raise CalldataError(
            f"signature must be canonical: use {canonical} instead of {signature}"
        )
    types = _parse_params(SIGNATURE_RE.match(signature).group(2))
    for abi_type in types:
        _check_supported(abi_type)
        if not is_encodable_type(abi_type.to_type_str()):
            raise CalldataError(f"unsupported type {abi_type.to_type_str()}")
    return types


def _to_int(value: Any, path: str) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and INTEGER_RE.match(value):
        return int(value, 16) if value.startswith("0x") else int(value)
    raise CalldataError(f"{path}: expected an integer or a decimal/0x string")


def _to_bytes(value: Any, size: int | None, path: str) -> bytes:
    if not isinstance(value, str) or not HEX_BYTES_RE.match(value):
        raise CalldataError(f"{path}: expected a 0x hex string of whole bytes")
    data = bytes.fromhex(value[2:])
    if size is not None and len(data) != size:
        raise CalldataError(f"{path}: expected exactly {size} bytes, got {len(data)}")
    return data


def _to_address(value: Any, path: str) -> str:
    if not isinstance(value, str) or not ADDRESS_RE.match(value):
        raise CalldataError(f"{path}: expected a 0x address with 40 hex characters")
    digits = value[2:]
    mixed_case = digits != digits.lower() and digits != digits.upper()
    if mixed_case and not is_checksum_address(value):
        raise CalldataError(f"{path}: invalid address checksum")
    return to_checksum_address(value)


def _coerce(abi_type: ABIType, value: Any, path: str) -> Any:
    """Convert a JSON plan value into what eth_abi encodes, rejecting anything
    the web builder would treat differently."""
    if abi_type.is_array:
        if not isinstance(value, list):
            raise CalldataError(
                f"{path}: expected an array for {abi_type.to_type_str()}"
            )
        size = abi_type.arrlist[-1]
        if size and len(value) != size[0]:
            raise CalldataError(f"{path}: expected {size[0]} items, got {len(value)}")
        return [
            _coerce(abi_type.item_type, item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if isinstance(abi_type, TupleType):
        components = abi_type.components
        if not isinstance(value, list) or len(value) != len(components):
            raise CalldataError(
                f"{path}: expected {len(components)} values for {abi_type.to_type_str()}"
            )
        return tuple(
            _coerce(component, item, f"{path}[{index}]")
            for index, (component, item) in enumerate(zip(components, value))
        )
    match abi_type.base:
        case "uint" | "int":
            return _to_int(value, path)
        case "bytes":
            return _to_bytes(value, int(abi_type.sub) if abi_type.sub else None, path)
        case "address":
            return _to_address(value, path)
        case "bool":
            if not isinstance(value, bool):
                raise CalldataError(f"{path}: expected true or false")
            return value
        case "string":
            if not isinstance(value, str):
                raise CalldataError(f"{path}: expected a string")
            return value
    raise CalldataError(f"{path}: unsupported type {abi_type.to_type_str()}")


def build_calldata(function_signature: str, args: list[Any]) -> str:
    """ABI-encode a call. Integers may be JSON integers or decimal/0x strings,
    bytes are 0x strings, and arrays and tuples are JSON arrays."""
    types = parse_signature(function_signature)
    if len(types) != len(args):
        raise CalldataError(
            f"{function_signature} expects {len(types)} args, got {len(args)}"
        )
    coerced = [
        _coerce(abi_type, arg, f"args[{index}]")
        for index, (abi_type, arg) in enumerate(zip(types, args))
    ]
    try:
        encoded = abi_encode([t.to_type_str() for t in types], coerced)
    except EncodingError as e:
        raise CalldataError(f"args do not encode: {e}") from e
    selector = function_signature_to_4byte_selector(function_signature)
    return "0x" + (selector + encoded).hex()
