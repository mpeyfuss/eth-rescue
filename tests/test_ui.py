from rich.console import Console

from rescue_scripts import ui


class FakeWeb3:
    def from_wei(self, value, unit):
        if unit == "gwei":
            return value // 1_000_000_000
        if unit == "ether":
            return value / 10**18
        raise ValueError(unit)


def test_render_rescue_plan_outputs_action_table(monkeypatch):
    console = Console(record=True, width=140)
    monkeypatch.setattr(ui, "console", console)

    ui.render_rescue_plan(
        [
            {
                "address": "0x3333333333333333333333333333333333333333",
                "function_signature": "transfer(address,uint256)",
                "args": ["0x1111111111111111111111111111111111111111", 1000],
                "description": "ERC20 transfer",
            }
        ]
    )

    output = console.export_text()
    assert "Rescue plan" in output
    assert "ERC20 transfer" in output
    assert "transfer(address,uint256)" in output


def test_render_cost_preview_outputs_totals(monkeypatch):
    console = Console(record=True, width=140)
    monkeypatch.setattr(ui, "console", console)

    ui.render_cost_preview(
        FakeWeb3(),
        [{"to": "0x3333333333333333333333333333333333333333", "gas": 21_000}],
        20_000_000_000,
    )

    output = console.export_text()
    assert "Plan & cost preview" in output
    assert "20 gwei" in output
    assert "21,000" in output
    assert "Total rescue actions" in output


def test_render_simulation_result_outputs_status(monkeypatch):
    console = Console(record=True, width=140)
    monkeypatch.setattr(ui, "console", console)

    ui.render_simulation_result(
        {
            "bundleHash": "0xbundle",
            "results": [{"txHash": "0xtx", "gasUsed": 21_000}],
            "totalGasUsed": 21_000,
        }
    )

    output = console.export_text()
    assert "Bundle simulation" in output
    assert "0xtx" in output
    assert "OK" in output
    assert "0xbundle" in output
