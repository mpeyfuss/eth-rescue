# Read .env as make variables (ACCOUNT, SENDER, ...); forge and cast load it themselves.
-include .env

CONTRACT_PATH := src/EthRescue.sol:EthRescue
VERIFY_DELAY ?= 30

ifdef ACCOUNT
SIGNER_ARGS ?= --account $(ACCOUNT)
else ifdef SENDER
SIGNER_ARGS ?= --trezor --sender $(SENDER)
else
SIGNER_ARGS ?= --trezor
endif

# $(call verify,<chain>,<verifier args>,<label>): ADDR overrides the predicted address.
define verify
set -e; \
addr="$(ADDR)"; \
if [ -z "$$addr" ]; then addr="$$($(PREDICT_ADDR))"; fi; \
if [ "$$(cast code "$$addr" --rpc-url $(1))" = "0x" ]; then \
	echo "No code at $$addr on $(1). If EthRescue changed since it was deployed, pass ADDR=<deployed address>." >&2; \
	exit 1; \
fi; \
echo "Verifying EthRescue at $$addr on $(1)$(3)..."; \
forge verify-contract "$$addr" $(CONTRACT_PATH) --chain $(1) $(2) --watch
endef

PREDICT_ADDR = forge script script/Deploy.s.sol --sig "predictedAddress(bytes)" "$$(forge inspect EthRescue bytecode)" --json | python3 -c "import json,sys; print(json.loads(sys.stdin.read().splitlines()[-1])['returns']['0']['value'])"

.PHONY: remove install update fmt clean build clean-build \
	test-quick test-std test-gas test-cov test-fuzz \
	predict dry-run-sepolia dry-run-mainnet \
	deploy-sepolia deploy-mainnet \
	verify-sepolia verify-mainnet \
	verify-sepolia-sourcify verify-mainnet-sourcify

########################################
# Dependencies
########################################
remove:
	rm -rf dependencies

install:
	forge soldeer install

update: remove install

########################################
# Format
########################################
fmt:
	forge fmt

########################################
# Build
########################################
clean:
	forge clean

build:
	forge build --sizes

clean-build: clean build

########################################
# Test
########################################
test-quick: build
	forge test --fuzz-runs 256

test-std: build
	forge test

test-gas: build
	forge test --gas-report

test-cov: build
	forge coverage --no-match-coverage "(script|test)"

test-fuzz: build
	forge test --fuzz-runs 10000

########################################
# Deployments
########################################
# Signer defaults to Trezor (`--trezor --sender $(SENDER)` when SENDER is set).
# Override with `ACCOUNT=<keystore>` or `SIGNER_ARGS="..."`.
# Examples:
#   make dry-run-sepolia
#   make deploy-sepolia
#   make deploy-sepolia ACCOUNT=deployer
#   make verify-sepolia

predict: build
	@$(PREDICT_ADDR)

dry-run-sepolia: build
	forge script script/Deploy.s.sol --rpc-url sepolia

dry-run-mainnet: build
	forge script script/Deploy.s.sol --rpc-url mainnet

deploy-sepolia: build
	forge script script/Deploy.s.sol --rpc-url sepolia $(SIGNER_ARGS) --broadcast
	sleep $(VERIFY_DELAY)
	$(MAKE) verify-sepolia

deploy-mainnet: build
	forge script script/Deploy.s.sol --rpc-url mainnet $(SIGNER_ARGS) --broadcast
	sleep $(VERIFY_DELAY)
	$(MAKE) verify-mainnet

verify-sepolia: build
	@$(call verify,sepolia,--license-type MIT)

verify-mainnet: build
	@$(call verify,mainnet,--license-type MIT)

verify-sepolia-sourcify: build
	@$(call verify,sepolia,--verifier sourcify, (Sourcify))

verify-mainnet-sourcify: build
	@$(call verify,mainnet,--verifier sourcify, (Sourcify))
