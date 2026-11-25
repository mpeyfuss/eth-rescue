.PHONY: fmt lint schema test test-unit test-integration test-all

fmt:
	uv run ruff format .

lint:
	uv run ruff check . --fix

schema:
	uv run eth-rescue schema > schema/plan.v1.schema.json

test: test-unit

test-unit:
	uv run pytest -m "not integration"

# Every integration run covers the live fork and the next one (Amsterdam /
# Glamsterdam, with EIP-8037 state gas) so gas-schedule breakage shows up early.
test-integration:
	forge build --root contracts
	forge build --root tests/integration/contracts
	RUN_ANVIL_INTEGRATION=1 ANVIL_HARDFORK=osaka uv run pytest -m integration
	RUN_ANVIL_INTEGRATION=1 ANVIL_HARDFORK=amsterdam uv run pytest -m integration

test-all: test-unit test-integration
