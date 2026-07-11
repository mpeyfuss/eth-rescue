# Whitehat Rescue Scripts
A compilation of whitehat rescue scripts for Ethereum.

## Background
When a private key or seedphrase is compromised, bad actors typically attach a sweeper bot to the account. So whenever any ETH is sent to the compromised wallet, the sweeper bot takes those funds before you can use the ETH for gas on any transactions to save NFTs, contract ownership, or any other onchain action.

These scripts use private Flashbots bundles so the ordered rescue transactions are considered
together by participating builders. A clean relay simulation is required before submission, but
no rescue can eliminate every risk from an actively contested private key.

## Getting Started
1. Make sure you have [uv](https://docs.astral.sh/uv/) installed
2. Run the rescue:
   - From a clone: `uv sync` then `uv run eth-rescue`
   - As an installed tool: see below

The tool asks for everything it needs as you go — **no keystore files and no `.env`**. Private
keys are only ever entered interactively (hidden input) and kept in memory; they are never read
from or written to disk. You can also have it **generate a brand new gas wallet** for you. No
Foundry required either — calldata is encoded in pure Python.

The Flashbots signing key is always generated automatically — you never provide it.

## Install as a uv tool
The project exposes a `eth-rescue` command, so it can be installed and run anywhere with
[uv](https://docs.astral.sh/uv/):

```sh
# Run once without installing (from a clone)
uvx --from . eth-rescue

# Install the command globally, then run it from any folder
uv tool install eth-rescue
eth-rescue
```

The tool writes saved plans relative to the directory you run it from — so run `eth-rescue`
from your working folder.

## How the wizard works
`eth-rescue` walks you through the whole rescue, step by step:

**Pick a network.** Choose Ethereum mainnet, or **Sepolia** to rehearse the whole flow on a
testnet first (it just swaps the RPC and Flashbots relay URLs — everything else is identical).

**Step 1 — Set up accounts.** Enter the **compromised wallet's private key** (hidden input),
then set up the **gas wallet** that pays for the rescue: either enter an existing private key
or have the tool **create a new wallet** for you (it shows the address + key to save). The
Flashbots signing key is generated automatically.

**Step 2 — Build the rescue plan.** Enter the safe wallet to receive rescued assets and
leftover ETH. Then either load a previously saved plan, or use the guided wizard to add one
or more actions:

- **ERC721 NFT** — move an NFT (contract + token id)
- **ERC1155 NFT** — move semi-fungible tokens (contract + token id + amount)
- **ERC20 token** — move tokens (contract + amount in base units / wei)
- **Contract ownership** — `transferOwnership` of a contract you control
- **Custom (advanced)** — any function signature + JSON args

You can add multiple actions to one bundle, and at the end you're offered to **save the plan**
to `configs/` so you can reuse it next time (saved plans are git-ignored).

**Step 3 — Plan & cost preview.** The tool verifies the RPC chain, auto-estimates gas for
each action (with conservative fallbacks), and shows the estimated total cost.

**Step 4 — Fund the gas wallet.** The **gas wallet pays for the entire rescue**, including
the EIP-7702 undelegation transaction, the victim funding transaction, and the rescue bundle.
The tool tells you exactly how much ETH to send (including a safety buffer) and to which
address, then waits — send the funds and press Enter to re-check the balance until it's
funded.

**Step 5 — Simulate and send.** The bundle is EIP-7702 undelegate victim → fund victim →
run all rescue actions → sweep a conservative guaranteed amount of ETH to the safe wallet.
The exact signed bundle must simulate cleanly before it is submitted. A fresh bundle is built
and simulated for every target block, refreshing fees, balances, and both account nonces. If a
simulation fails, the wizard identifies the failing action and lets you edit the plan, change
the fee, retry with fresh chain state, or cancel without broadcasting.

Gas funding is deliberately conservative. When an action uses less than its gas limit, the
unused-gas savings can remain in the compromised wallet after the guaranteed sweep. Treat any
such remainder as still at risk.

Flashbots cannot guarantee inclusion, and an attacker controlling the same key can change the
victim nonce or move assets before a bundle lands. Repeated simulation and nonce refresh reduce
stale-bundle risk but do not turn a contested account into a trusted one.

## Advanced: JSON config
Power users can drive everything from a JSON file. Choose **Load a saved JSON config file** in
step 1 and point it at a config shaped like the files in `examples/` (`address`,
`function_signature`, `args`, and an optional `gas_estimate`).

## Sepolia rehearsal checklist

Before using mainnet, rehearse with disposable Sepolia accounts:

1. Fund a victim with test ETH and a test asset, then configure an unrelated gas wallet and safe wallet.
2. Run one successful transfer and confirm the undelegation, funding, rescue, and sweep order in the receipts.
3. Force an action revert and confirm submission remains blocked until the action is edited or removed.
4. Change the victim nonce between attempts and confirm the next bundle is rebuilt and simulated with the new nonce.
5. Confirm cancellation after a failed simulation broadcasts nothing.

## Local integration tests

The fast unit suite mocks only the relay and interactive boundaries. The integration suite
starts an isolated Anvil node using the Osaka execution fork, which is the current Ethereum
mainnet EVM fork after Fusaka:

```sh
make test-integration
```

To use an Anvil process you started yourself, set `ANVIL_RPC_URL`. Override
`ANVIL_HARDFORK` only when intentionally testing another execution fork.
