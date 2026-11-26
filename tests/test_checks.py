from types import SimpleNamespace

import pytest
from web3.exceptions import ContractLogicError

from eth_rescue import checks, templates

SAFE = "0x000000000000000000000000000000000000BEEF"
OTHER = "0x000000000000000000000000000000000000dEaD"
TOKEN = "0x0000000000000000000000000000000000000001"
BLOCK = 123


class FakeChain:
    """Contract reads keyed by function name; records each read's block."""

    def __init__(self, results):
        self.results = results
        self.blocks = []

    def _fn(self, name, *args):
        def call(block_identifier=None):
            self.blocks.append(block_identifier)
            result = self.results[name]
            return result(*args) if callable(result) else result

        return SimpleNamespace(call=call)

    def contract(self, address, abi):
        functions = SimpleNamespace(
            balanceOf=lambda *args: self._fn("balanceOf", *args),
            ownerOf=lambda token_id: self._fn("ownerOf", token_id),
            owner=lambda: self._fn("owner"),
        )
        return SimpleNamespace(functions=functions)


def _w3(results):
    chain = FakeChain(results)
    return SimpleNamespace(eth=chain), chain


def _no_sleep(_seconds):
    pass


def _evaluate(w3, rescue_data, snapshot, executed=None):
    check_map = checks.infer_checks(rescue_data)
    if executed is None:
        executed = dict.fromkeys(range(len(rescue_data)), True)
    return checks.evaluate_checks(
        w3, check_map, snapshot, executed, BLOCK, sleep=_no_sleep
    )


def _key(data):
    return checks.infer_check(data).balance_key


def test_infer_check_reads_the_template_declaration():
    assert checks.infer_check(templates.erc20_transfer(TOKEN, SAFE, 5)).kind == "erc20"
    assert (
        checks.infer_check(templates.erc721_transfer(TOKEN, OTHER, SAFE, 9)).kind
        == "erc721"
    )
    assert checks.infer_check(templates.transfer_ownership(TOKEN, SAFE)).kind == (
        "ownership"
    )


def test_custom_call_with_a_typed_signature_has_no_check():
    data = templates.custom(TOKEN, "transfer(address,uint256)", [SAFE, 5])

    assert checks.infer_check(data) is None


def test_erc20_uses_snapshot_delta_read_at_inclusion_block():
    data = templates.erc20_transfer(TOKEN, SAFE, 100)
    w3, chain = _w3({"balanceOf": 250})

    [outcome] = _evaluate(w3, [data], {_key(data): 200})
    assert (outcome.status, outcome.detail) == ("partial", "only 50 of 100 arrived")
    assert chain.blocks == [BLOCK]

    chain.results["balanceOf"] = 300
    assert _evaluate(w3, [data], {_key(data): 200})[0].status == "rescued"

    chain.results["balanceOf"] = 200
    assert _evaluate(w3, [data], {_key(data): 200})[0].status == "failed"


def test_shared_balance_increase_is_claimed_once_in_execution_order():
    first = templates.erc20_transfer(TOKEN, SAFE, 100)
    second = templates.erc20_transfer(TOKEN, SAFE, 100)
    w3, _chain = _w3({"balanceOf": 1_100})

    outcomes = _evaluate(w3, [first, second], {_key(first): 1_000})

    assert [o.status for o in outcomes] == ["rescued", "failed"]


def test_unreadable_snapshot_makes_balance_action_unverified():
    data = templates.erc20_transfer(TOKEN, SAFE, 100)
    w3, _chain = _w3({"balanceOf": 300})

    [outcome] = _evaluate(w3, [data], {_key(data): None})

    assert outcome.status == "unverified"


def test_snapshot_records_none_when_balance_unreadable():
    data = templates.erc20_transfer(TOKEN, SAFE, 100)

    def down(*_args):
        raise ConnectionError("rpc down")

    w3, _chain = _w3({"balanceOf": down})

    snapshot = checks.snapshot_checks(w3, checks.infer_checks([data]), sleep=_no_sleep)

    assert snapshot == {_key(data): None}


def test_erc721_checks_owner():
    data = templates.erc721_transfer(TOKEN, OTHER, SAFE, 9)

    w3, _chain = _w3({"ownerOf": SAFE})
    assert _evaluate(w3, [data], {})[0].status == "rescued"

    w3, _chain = _w3({"ownerOf": OTHER})
    assert _evaluate(w3, [data], {})[0].status == "failed"


def test_owner_read_revert_is_failure_but_rpc_error_is_unverified():
    data = templates.transfer_ownership(TOKEN, SAFE)

    def reverts():
        raise ContractLogicError("execution reverted")

    def down():
        raise ConnectionError("rpc down")

    w3, _chain = _w3({"owner": reverts})
    assert _evaluate(w3, [data], {})[0].status == "failed"

    w3, chain = _w3({"owner": down})
    assert _evaluate(w3, [data], {})[0].status == "unverified"
    assert len(chain.blocks) == checks.READ_ATTEMPTS


def test_read_retries_transient_errors():
    data = templates.transfer_ownership(TOKEN, SAFE)
    answers = iter([ConnectionError("blip"), SAFE])

    def flaky():
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer

    w3, _chain = _w3({"owner": flaky})

    assert _evaluate(w3, [data], {})[0].status == "rescued"


@pytest.mark.parametrize(
    ("executed", "status"),
    [(None, "not_executed"), (False, "failed"), (True, "unverified")],
)
def test_call_result_decides_status_before_any_check(executed, status):
    data = templates.custom(TOKEN, "delist(address,uint256)", [OTHER, 1])
    w3, chain = _w3({})

    [outcome] = _evaluate(w3, [data], {}, {0: executed})

    assert outcome.status == status
    assert chain.blocks == []


def test_reverted_typed_action_is_failed_without_reading_state():
    data = templates.erc20_transfer(TOKEN, SAFE, 100)
    w3, chain = _w3({"balanceOf": 10**9})

    [outcome] = _evaluate(w3, [data], {_key(data): 0}, {0: False})

    assert outcome.status == "failed"
    assert chain.blocks == []


def test_unknown_check_kind_is_unverified_not_a_crash():
    w3, _chain = _w3({})
    check = checks.Check(kind="erc404", contract=TOKEN, recipient=SAFE)

    [outcome] = checks.evaluate_checks(
        w3, {0: check}, {}, {0: True}, BLOCK, sleep=_no_sleep
    )

    assert outcome.status == "unverified"
