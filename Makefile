.PHONY: test test-unit test-integration test-all

test: test-unit

test-unit:
	uv run pytest -m "not integration"

test-integration:
	forge build --root tests/integration/contracts
	RUN_ANVIL_INTEGRATION=1 uv run pytest -m integration

test-all:
	RUN_ANVIL_INTEGRATION=1 uv run pytest
