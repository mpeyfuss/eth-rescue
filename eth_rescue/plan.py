import json
from pathlib import Path
from typing import Annotated, Any, Literal

from eth_utils import to_checksum_address
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    PositiveInt,
    ValidationError,
    model_validator,
)

from eth_rescue import templates
from eth_rescue.templates import MAX_ACTION_GAS
from eth_rescue.calldata import (
    CalldataError,
    build_calldata,
    has_valid_checksum,
    parse_signature,
)
from eth_rescue.types import RescueData

MAX_UINT256 = 2**256 - 1


class PlanError(ValueError):
    pass


def _checksum(value: str) -> str:
    if not has_valid_checksum(value):
        raise ValueError("invalid address checksum")
    if int(value, 16) == 0:
        raise ValueError("must not be the zero address")
    return to_checksum_address(value)


def _canonical_signature(value: str) -> str:
    try:
        parse_signature(value)
    except CalldataError as e:
        raise ValueError(str(e)) from e
    return value


def _check_uint256(value: str) -> str:
    if int(value) > MAX_UINT256:
        raise ValueError("value exceeds uint256")
    return value


Address = Annotated[
    str, Field(pattern=r"^0x[0-9a-fA-F]{40}$"), AfterValidator(_checksum)
]
# Decimal strings: JS numbers lose precision above 2**53.
Uint256 = Annotated[
    str, Field(pattern=r"^[0-9]{1,78}$"), AfterValidator(_check_uint256)
]
# A zero transfer would always "arrive", so its check could never fail.
Amount = Annotated[
    str,
    Field(pattern=r"^0*[1-9][0-9]*$", max_length=78),
    AfterValidator(_check_uint256),
]
Gwei = Annotated[str, Field(pattern=r"^[0-9]+(\.[0-9]{1,9})?$")]
Gas = Annotated[int, Field(gt=0, le=MAX_ACTION_GAS)]
NetworkName = Literal["mainnet"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _Action(_Model):
    label: str | None = None


class _SingleCallAction(_Action):
    gas: Gas | None = None


class Erc20Action(_SingleCallAction):
    type: Literal["erc20"]
    contract: Address
    amount: Amount


class Erc721Action(_SingleCallAction):
    type: Literal["erc721"]
    contract: Address
    token_id: Uint256


class Erc1155Action(_SingleCallAction):
    type: Literal["erc1155"]
    contract: Address
    token_id: Uint256
    amount: Amount


class OwnershipAction(_SingleCallAction):
    type: Literal["ownership"]
    contract: Address


class TransientDelistErc721Action(_SingleCallAction):
    type: Literal["transient_delist_erc721"]
    auction_house: Address
    contract: Address
    token_id: Uint256


class CustomAction(_Action):
    type: Literal["custom"]
    target: Address
    signature: Annotated[
        str,
        Field(pattern=r"^[A-Za-z_$][A-Za-z0-9_$]*\(.*\)$"),
        AfterValidator(_canonical_signature),
    ]
    args: list[JsonValue]
    gas: Gas


Action = Annotated[
    Erc20Action
    | Erc721Action
    | Erc1155Action
    | OwnershipAction
    | TransientDelistErc721Action
    | CustomAction,
    Field(discriminator="type"),
]


class Options(_Model):
    priority_fee_gwei: Gwei = "0"
    max_blocks: PositiveInt = 25
    legacy_fallback: bool = True


class Plan(_Model):
    version: Literal[1]
    network: NetworkName
    victim: Address
    safe: Address
    gas_wallet: Address | None = None
    options: Options = Field(default_factory=Options)
    actions: list[Action] = Field(min_length=1)

    @model_validator(mode="after")
    def _distinct_wallets(self) -> "Plan":
        if self.safe == self.victim:
            raise ValueError("safe must differ from victim")
        if self.gas_wallet in (self.victim, self.safe):
            raise ValueError("gas_wallet must differ from victim and safe")
        for index, action in enumerate(self.actions):
            for field in ("contract", "auction_house", "target"):
                if getattr(action, field, None) == self.victim:
                    # EthRescue runs as the victim and rejects calls to itself.
                    raise ValueError(
                        f"actions.{index}.{field} must not be the compromised wallet"
                    )
        return self


def _template_calls(action: Action, plan: Plan) -> list[RescueData]:
    match action:
        case Erc20Action():
            return [
                templates.erc20_transfer(action.contract, plan.safe, int(action.amount))
            ]
        case Erc721Action():
            return [
                templates.erc721_transfer(
                    action.contract, plan.victim, plan.safe, int(action.token_id)
                )
            ]
        case Erc1155Action():
            return [
                templates.erc1155_transfer(
                    action.contract,
                    plan.victim,
                    plan.safe,
                    int(action.token_id),
                    int(action.amount),
                )
            ]
        case OwnershipAction():
            return [templates.transfer_ownership(action.contract, plan.safe)]
        case TransientDelistErc721Action():
            return templates.transient_auction_house_erc721_rescue(
                action.auction_house,
                action.contract,
                plan.victim,
                plan.safe,
                int(action.token_id),
            )
        case CustomAction():
            return [
                templates.custom(
                    action.target, action.signature, list(action.args), action.gas
                )
            ]


def _expand_action(action: Action, plan: Plan, index: int) -> list[RescueData]:
    calls = _template_calls(action, plan)
    gas = getattr(action, "gas", None)
    for step, call in enumerate(calls):
        call["plan_action"] = index
        call["plan_step"] = step
        if gas is not None:
            call["gas_estimate"] = gas
        if action.label:
            name = call["function_signature"].split("(")[0]
            call["description"] = (
                f"{action.label} ({name})" if len(calls) > 1 else action.label
            )
    return calls


def expand(plan: Plan) -> list[RescueData]:
    return [
        call
        for index, action in enumerate(plan.actions)
        for call in _expand_action(action, plan, index)
    ]


def _check_encodable(plan: Plan) -> None:
    for call in expand(plan):
        try:
            build_calldata(call["function_signature"], call["args"])
        except CalldataError as e:
            raise PlanError(f"actions.{call['plan_action']}: {e}") from e


def _format_validation_error(error: ValidationError) -> str:
    lines = []
    for detail in error.errors():
        location = ".".join(str(part) for part in detail["loc"])
        message = detail["msg"].removeprefix("Value error, ")
        lines.append(f"{location}: {message}" if location else message)
    return "\n".join(lines)


def parse_plan(text: str) -> Plan:
    try:
        plan = Plan.model_validate_json(text)
    except ValidationError as e:
        raise PlanError(_format_validation_error(e)) from e
    _check_encodable(plan)
    return plan


def load_plan(path: str | Path) -> Plan:
    try:
        raw = Path(path).read_bytes()
    except OSError as e:
        raise PlanError(f"could not read {path}: {e.strerror or e}") from e
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise PlanError(f"could not read {path}: not valid UTF-8 text") from e
    return parse_plan(text)


def plan_json_schema() -> dict[str, Any]:
    return Plan.model_json_schema()


def render_schema() -> str:
    return json.dumps(plan_json_schema(), indent=2) + "\n"
