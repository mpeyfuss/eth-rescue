"""
Functions for string to abi encoded hex
"""

from typing import Any

from eth_abi import encode as abi_encode, is_encodable_type
from eth_abi.grammar import ABIType, TupleType, parse
from eth_utils import function_signature_to_4byte_selector


def _parse_types(function_signature: str) -> list[str]:
    """
    Extract the parameter types from a function signature such as
    `transferFrom(address,address,uint256)`.
    """
    start = function_signature.index("(")
    end = function_signature.rindex(")")
    param_str = function_signature[start : end + 1].strip()
    if param_str == "()":
        return []
    types = [param.to_type_str() for param in parse(param_str).components]
    invalid_types = [t for t in types if not is_encodable_type(t)]
    if len(invalid_types) > 0:
        raise ValueError(
            f'The following invalid types were found in "{function_signature}": {invalid_types}'
        )
    return types


def _to_int(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    if value.lower().startswith("0x"):
        return int(value, 16)
    return int(value)


def _to_bytes(value: Any) -> Any:
    if isinstance(value, str) and value.lower().startswith("0x"):
        return bytes.fromhex(value[2:])
    return value


def _coerce(abi_type: ABIType, value: Any) -> Any:
    """Convert JSON-friendly values (decimal/hex strings) into eth_abi types."""
    if abi_type.is_array:
        if not isinstance(value, list | tuple):
            raise ValueError(f"expected an array for {abi_type.to_type_str()}")
        return [_coerce(abi_type.item_type, item) for item in value]
    if isinstance(abi_type, TupleType):
        components = abi_type.components
        if not isinstance(value, list | tuple) or len(value) != len(components):
            raise ValueError(
                f"expected {len(components)} values for {abi_type.to_type_str()}"
            )
        return tuple(_coerce(c, item) for c, item in zip(components, value))
    match abi_type.base:
        case "uint" | "int":
            return _to_int(value)
        case "bytes":
            return _to_bytes(value)
        case _:
            return value


def build_calldata(function_signature: str, args: list[Any]) -> str:
    """
    Build ABI-encoded calldata for `function_signature` with `args`.
    Integers may be given as decimal or 0x strings and bytes as 0x strings.
    """
    selector = function_signature_to_4byte_selector(function_signature)
    types = _parse_types(function_signature)
    if len(types) != len(args):
        raise ValueError(
            f"{function_signature} expects {len(types)} args, got {len(args)}"
        )
    coerced = [_coerce(parse(t), arg) for t, arg in zip(types, args)]
    encoded = abi_encode(types, coerced)
    return "0x" + (selector + encoded).hex()
