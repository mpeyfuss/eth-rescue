# Ethereum Rescue

`eth-rescue` is an interactive Ethereum CLI for recovering assets and contract control from an account whose private key or seed phrase has been compromised.

When a compromised account is watched by a sweeper, sending it ETH for gas can give the attacker an opportunity to take the funds first. This tool instead sends an ordered private Flashbots bundle that can clear an EIP-7702 delegation when necessary, fund the compromised account, execute one or more rescue actions, and sweep a conservative amount of ETH to a safe wallet.

> [!WARNING]
> A clean relay simulation is required before submission, but it does not guarantee inclusion or recovery. An attacker with the same private key can change the nonce or move assets first, builders may decline the bundle, and private submission cannot make a contested account trustworthy. Rehearse on Sepolia with disposable accounts before attempting a mainnet rescue.

> [!CAUTION]
> This is experimental software provided “as is,” without warranty of any kind. Use it entirely at your own risk. To the fullest extent permitted by law, the authors, maintainers, and contributors are not liable for lost funds or assets, failed or partial rescues, transaction fees, compromised credentials, or any other damages arising from its use. See the [MIT License](LICENSE) for the governing terms.

## User guide

### Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). `uv` will obtain a compatible Python version when needed.
- Access to the public RPC and Flashbots relay endpoints configured by the tool

Foundry is not required to run a rescue. The CLI encodes calldata in Python. Foundry and Anvil are only required for the local integration tests.

### Install and run

First, [install uv](https://docs.astral.sh/uv/getting-started/installation/) for your operating system. Then choose either a one-time run or a persistent installation; cloning the repository is not required.

Run the latest version directly in an isolated environment:

```sh
uvx eth-rescue
```

Or install it as a persistent command:

```sh
uv tool install eth-rescue
eth-rescue
```

If `uv` reports that its tool directory is not on `PATH`, run `uv tool update-shell`, restart your shell, and then run `eth-rescue`.

### Before you start

Prepare three distinct account roles:

- **Compromised wallet:** holds the assets or authority being rescued. You must have its private key.
- **Gas wallet:** pays for the optional undelegation, victim funding, and rescue transactions. Enter an existing private key or let the CLI create a new wallet.
- **Safe wallet:** receives rescued assets and the ETH sweep. It must differ from both the compromised and gas wallets.

The Flashbots signing identity is generated automatically and needs no funds. Compromised and gas-wallet private keys are entered through hidden interactive prompts and held in memory; the tool does not read keys from keystores or `.env` files and does not write them to disk.

If the CLI generates a gas wallet, it displays the private key once. Save it securely before continuing and retain it until any unused gas-wallet balance has been recovered. The CLI does not include private keys in JSON plans; `configs/` and `.env` are git-ignored.

### Supported networks

- Ethereum mainnet
- Sepolia testnet

The selection controls the expected chain ID, public RPC endpoint, Flashbots relay, and builder routing. The CLI refuses to continue when the connected RPC reports the wrong chain.

### Rescue workflow

1. **Choose the network.** Use Sepolia for a rehearsal or Ethereum mainnet for the live rescue.
2. **Set up accounts.** Enter the compromised-wallet key, then enter or generate the separate gas wallet. Enter the safe-wallet address.
3. **Build the plan.** Load a user-authored JSON plan or add one or more ordered actions with the guided wizard. Wizard-built plans can be reviewed before continuing.
4. **Preview fees and funding.** Enter an optional extra priority fee. The CLI estimates each action with a 25% gas buffer, uses a conservative action-specific fallback if estimation fails, verifies whether the victim has an EIP-7702 delegation, and shows the maximum estimated cost.
5. **Fund the gas wallet.** The CLI shows the required balance, including a safety buffer, and waits for you to fund the gas wallet. It rechecks only when you press a key.
6. **Simulate.** A bundle is built from fresh fees, balances, and both account nonces for the next block. The exact signed transactions must simulate successfully through the relay before submission is offered.
7. **Submit and monitor.** After explicit confirmation, the CLI submits the bundle for the relay's allowed block range. Each new attempt rebuilds and simulates with current chain state. After 25 missed blocks, it asks whether to keep trying.

When the compromised account has an EIP-7702 delegation, the first bundle transaction is a gas-wallet-sponsored type-4 transaction that authorizes the victim to clear its delegation. Plain externally owned accounts skip that transaction. Unexpected contract code on the victim is rejected rather than overwritten.

The remaining order is gas wallet funds victim, victim executes every rescue action, then victim sweeps a conservative guaranteed amount of ETH to the safe wallet. The sweep is omitted when its value would be zero. Unused-gas savings can remain in the compromised wallet, so treat any residual balance as at risk.

If simulation fails, the CLI identifies a failing rescue action when possible. You can remove or rebuild actions, change the extra priority fee, retry against fresh state, or cancel. No bundle is broadcast without a clean simulation and a separate send confirmation. A bundle that was already submitted may still be considered by its target builders even if a later retry or interaction is cancelled.

### Supported actions

The guided wizard can add multiple actions in execution order:

- **ERC-20:** calls `transfer(address,uint256)`; amounts are integer base units, not display units.
- **ERC-721:** calls `transferFrom(address,address,uint256)` from the compromised wallet.
- **ERC-1155:** calls `safeTransferFrom(address,address,uint256,uint256,bytes)`.
- **Contract ownership:** calls `transferOwnership(address)`.
- **Transient Auction House ERC-721:** looks up `ownerOf(tokenId)`, requires the owner to be the auction-house address recognized by this version of the tool, then orders `delist(address,uint256)` before the ERC-721 transfer.
- **Custom:** accepts a target address, canonical function signature, and JSON array of ABI-compatible arguments.

Entering `cancel`, `back`, or `exit` at a cancel-aware action prompt abandons that action and returns to plan construction.

### JSON plans

Advanced users can choose **Load a saved JSON config file** to supply a user-authored, ordered, non-empty JSON array. Each action has this shape:

```json
{
  "address": "0xTargetContractAddress",
  "function_signature": "transfer(address,uint256)",
  "args": ["0xSafeWalletAddress", 1000000000000000000],
  "gas_estimate": 70000
}
```

| Field | Requirement |
| --- | --- |
| `address` | Required valid Ethereum contract address. It is converted to checksum form when loaded. |
| `function_signature` | Required string containing `(` and ending in `)`, such as `transfer(address,uint256)`. |
| `args` | Required JSON array in the same order as the function parameters. Token amounts must be integer base units. |
| `gas_estimate` | Optional positive integer used only as the fallback when RPC estimation fails. Defaults to the generic fallback. |

The loader rejects an empty plan, malformed JSON, missing required fields, invalid addresses or signatures, non-array arguments, and invalid fallback gas values. ABI encoding errors such as an argument-count or type mismatch are reported while preparing the plan.

Example plans:

- [ERC-20](examples/erc-20.json)
- [ERC-721](examples/erc_721.json)
- [ERC-1155](examples/erc_1155.json)
- [CryptoPunk custom call](examples/cryptopunk.json)
- [Contract ownership](examples/contract_ownership.json)

The examples contain placeholder addresses and amounts; review and replace every value before use.

### Sepolia rehearsal checklist

Use disposable accounts and test assets:

1. Fund a victim with test ETH and a test asset, then configure an unrelated gas wallet and safe wallet.
2. Complete a transfer and confirm the receipt order: optional undelegation, funding, rescue actions, then sweep.
3. Force an action revert and confirm submission remains blocked until the action is edited or removed.
4. Change the victim nonce between attempts and confirm the next bundle is rebuilt and simulated with the new nonce.
5. Cancel after a failed simulation and confirm nothing was submitted.

A successful rehearsal checks current endpoint compatibility and operating procedure; it cannot guarantee mainnet inclusion or prevent a nonce race.

## Developer guide

### Clone and set up

Contributors should work from a clone:

```sh
git clone https://github.com/mpeyfuss/eth-rescue.git
cd eth-rescue
uv sync
uv run eth-rescue
```

The project requires Python 3.13 or newer. `uv sync` creates the project environment and installs the locked runtime and development dependencies.

### Repository layout

The main package is under `src/eth_rescue/`. Fast tests live directly under `tests/`; Anvil-backed tests and their Solidity fixtures live under `tests/integration/`. Example rescue plans live under `examples/`.

### Development commands

Common commands:

```sh
make fmt               # format Python with Ruff
make lint              # run Ruff lint with automatic fixes
make test-unit         # unit tests; this is also the default `make test`
make test-integration  # compile fixtures and run Anvil-backed tests
make test-all          # run unit and enabled integration tests
```

### Integration tests

The integration suite requires `forge` and `anvil` from [Foundry](https://book.getfoundry.sh/getting-started/installation). `make test-integration` compiles the fixture contracts, sets `RUN_ANVIL_INTEGRATION=1`, and starts a fresh Anvil process for each test. It defaults to the Osaka execution fork; override it only when intentionally checking another fork:

```sh
ANVIL_HARDFORK=<fork-name> make test-integration
```

The integration tests exercise EIP-7702 undelegation, funding, ERC-20/721/1155 transfers, ownership transfer, auction delisting, ETH sweeping, simulation rollback, signing, and transaction ordering. Their local relay double snapshots Anvil for simulation and broadcasts transactions sequentially for execution.

That double does **not** model Flashbots privacy, builder selection, same-block execution, bundle competition, exact builder base fees, or atomic all-or-nothing inclusion. The local suite does not contact the configured public RPC or relay endpoints.

## License

This project is available under the [MIT License](LICENSE).
