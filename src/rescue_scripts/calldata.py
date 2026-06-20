from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes


def _split_top_level_types(types_str: str) -> list[str]:
    """
    Split a comma-separated list of ABI types at the top level only, so that
    nested tuples `(a,b)` and arrays survive intact.
    """
    types: list[str] = []
    depth = 0
    current = ""
    for ch in types_str:
        if ch == "(" or ch == "[":
            depth += 1
            current += ch
        elif ch == ")" or ch == "]":
            depth -= 1
            current += ch
        elif ch == "," and depth == 0:
            types.append(current.strip())
            current = ""
        else:
            current += ch
    if current.strip():
        types.append(current.strip())
    return types


def _parse_types(function_signature: str) -> list[str]:
    """
    Extract the parameter types from a function signature such as
    `transferFrom(address,address,uint256)`.
    """
    start = function_signature.index("(")
    end = function_signature.rindex(")")
    inner = function_signature[start + 1 : end].strip()
    if not inner:
        return []
    return _split_top_level_types(inner)


def _coerce(abi_type: str, value):
    """
    Coerce a JSON / human-supplied value into the Python type that
    `eth_abi.encode` expects for the given ABI type.
    """
    # arrays: coerce each element using the base type
    if abi_type.endswith("]"):
        base = abi_type[: abi_type.rindex("[")]
        return [_coerce(base, v) for v in value]

    if abi_type.startswith("bytes"):
        if isinstance(value, (bytes, bytearray)):
            return bytes(value)
        return bytes(HexBytes(value))

    if abi_type.startswith("uint") or abi_type.startswith("int"):
        if isinstance(value, str):
            return int(value, 0)
        return int(value)

    if abi_type == "bool":
        if isinstance(value, str):
            return value.strip().lower() in ("true", "1", "yes")
        return bool(value)

    # address and string are passed through as-is
    return value


def build_calldata(function_signature: str, args: list) -> str:
    """
    Build ABI-encoded calldata for `function_signature` with `args`.

    Pure-Python replacement for `cast calldata` (no Foundry required).
    """
    selector = function_signature_to_4byte_selector(function_signature)
    types = _parse_types(function_signature)
    if len(types) != len(args):
        raise ValueError(
            f"{function_signature} expects {len(types)} args, got {len(args)}"
        )
    coerced = [_coerce(t, a) for t, a in zip(types, args)]
    encoded = encode(types, coerced)
    return "0x" + (selector + encoded).hex()
