from typing import Any, NotRequired, TypedDict


class RescueData(TypedDict):
    address: str
    function_signature: str
    args: list[Any]
    gas_estimate: NotRequired[int]
    description: NotRequired[str]
