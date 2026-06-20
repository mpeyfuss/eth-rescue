# Whitehat Rescue Scripts
A compilation of whitehat rescue scripts for Ethereum.

## Background
When a private key or seedphrase is compromised, bad actors typically attach a sweeper bot to the account. So whenever any ETH is sent to the compromised wallet, the sweeper bot takes those funds before you can use the ETH for gas on any transactions to save NFTs, contract ownership, or any other onchain action.

These scripts use private bundled transactions through Flashbots to ensure that multiple transactions are all included together in an atomic way and gets around sweeper bots.

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

The tool writes saved plans relative to the directory you run it from — so run `rescue` from
your working folder.

## How the wizard works
`eth-rescue` walks you through the whole rescue, step by step:

**Pick a network.** Choose Ethereum mainnet, or **Sepolia** to rehearse the whole flow on a
testnet first (it just swaps the RPC and Flashbots relay URLs — everything else is identical).

**Step 1 — Set up accounts.** Enter the **compromised wallet's private key** (hidden input),
then set up the **gas wallet** that pays for the rescue: either enter an existing private key
or have the tool **create a new wallet** for you (it shows the address + key to save). The
Flashbots signing key is generated automatically.

**Step 2 — Build the rescue plan.** Either load a previously saved plan, or use the guided
wizard: enter the safe wallet to move everything to, then add one or more actions:

- **ERC721 NFT** — move an NFT (contract + token id)
- **ERC1155 NFT** — move semi-fungible tokens (contract + token id + amount)
- **ERC20 token** — move tokens (contract + amount in base units / wei)
- **Contract ownership** — `transferOwnership` of a contract you control
- **Custom (advanced)** — any function signature + JSON args

You can add multiple actions to one bundle, and at the end you're offered to **save the plan**
to `configs/` so you can reuse it next time (saved plans are git-ignored).

**Step 3 — Plan & cost preview.** The tool connects to the network, auto-estimates gas for
each action (with sensible fallbacks), and shows the estimated total cost.

**Step 4 — Fund the gas wallet.** The **gas wallet pays for the entire rescue**. The tool
tells you exactly how much ETH to send (including a safety buffer) and to which address, then
waits — send the funds and press Enter to re-check the balance until it's funded.

**Step 5 — Send.** After a final confirmation, the bundle (fund victim → run all rescue
actions, atomically) is submitted to Flashbots and **re-sent every block** until it lands,
refreshing the gas price each attempt. If it isn't included after a stretch of blocks, you're
asked whether to keep trying.

## Advanced: JSON config
Power users can drive everything from a JSON file. Choose **Load a saved JSON config file** in
step 1 and point it at a config shaped like the files in `examples/` (`address`,
`function_signature`, `args`, and an optional `gas_estimate`).
