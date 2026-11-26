# Ethereum Rescue

`eth-rescue` moves assets and contract control out of an Ethereum account whose private key or seed phrase has been compromised, using private Flashbots bundles so a sweeper bot never sees ETH arrive in the wallet.

You build a rescue plan (`plan.json`) in the web plan builder, or by hand, and the CLI runs it. The plan holds addresses only. The CLI asks for private keys in a hidden prompt on your own machine.

> [!WARNING]
> A clean simulation does not guarantee inclusion or recovery. An attacker holding the same key can spend the wallet's nonce or move assets first, builders may ignore the bundle, and private submission cannot make a contested account trustworthy.

> [!CAUTION]
> This is experimental software provided "as is," without warranty of any kind. Use it entirely at your own risk. To the fullest extent permitted by law, the authors, maintainers, and contributors are not liable for lost funds or assets, failed or partial rescues, transaction fees, compromised credentials, or any other damages arising from its use. See the [MIT License](LICENSE) for the governing terms.

## User guide

### Requirements

- [uv](https://docs.astral.sh/uv/getting-started/installation/). It fetches a compatible Python automatically.
- Network access to the public RPC and the Flashbots relay.

### Commands

Run a pinned release so a cached older eth-rescue, with a different plan format, is never used:

```sh
uvx eth-rescue@1.0.0 --help
uvx eth-rescue@1.0.0 gas-wallet new          # optional: a fresh wallet to pay fees (--out FILE saves the key to a private file)
uvx eth-rescue@1.0.0 validate plan.json      # schema + calldata check, offline, no keys
uvx eth-rescue@1.0.0 simulate plan.json      # build and simulate phase 1, never sends
uvx eth-rescue@1.0.0 run plan.json           # the rescue
uvx eth-rescue@1.0.0 schema                  # print the plan JSON Schema
```

Each command's `--help` lists its flags and exit codes. `--json` prints NDJSON events on stdout; warnings and errors go to stderr. To use your own RPC, set `ETH_RESCUE_RPC_URL` rather than passing `--rpc`, so an API key in the URL stays out of your shell history (printed errors never show URL paths).

### Accounts

- **Compromised wallet (`victim`):** holds what is being rescued. You type its private key.
- **Gas wallet:** pays every fee. It must be a plain account with no code. `gas-wallet new` prints a fresh one; save its key until you have recovered any leftover balance.
- **Safe (`safe`):** receives every typed asset and all ETH left in the compromised wallet. It must differ from both other wallets. It can be an ordinary account or a smart wallet; a smart wallet must be able to make arbitrary calls, because ERC-721s arrive by `transferFrom`, which does not check whether the receiver can handle them.

### What `run` does

1. Reads the plan and asks for the compromised-wallet and gas-wallet keys. If the plan names a `gas_wallet`, the key must match it.
2. Checks that the network's EthRescue deployment is the pinned build (runtime codehash), and shows what the plan will do: network, the three wallets, every custom call, and whether the fallback is on.
3. Waits until the gas wallet holds enough ETH, printing the amount. The amount covers base fees rising every block of a five-block window.
4. **Phase 1, EIP-7702.** One or more transactions from the gas wallet delegate the compromised wallet to the EthRescue singleton and run every action as that wallet. A victim-signed EIP-712 request binds the gas wallet, the safe, a deadline and the exact action list. Each action reports its own result, so one failing action never blocks the others. The remaining ETH is swept to the safe in the same transaction.
5. Simulates the exact signed bundle on the relay, then asks once before sending.
6. Submits for five blocks at a time, rebuilding with fresh nonces and fees between windows, until included or `options.max_blocks` blocks have been submitted. Before each rebuild it checks whether an earlier bundle landed. If the gas wallet's nonce moved and no bundle is visible, it stops instead of resubmitting.
7. Checks each action at the inclusion block: the safe's ERC-20/ERC-1155 balance went up by the amount, or the safe now owns the NFT or the contract.
8. **Phase 2, legacy fallback** (`options.legacy_fallback`, default on). Actions that failed or never ran are retried as direct transactions from the compromised wallet: clear the delegation, fund the wallet, send each action, sweep the ETH. It runs without asking again. An action that reverts in simulation, in any window, is left out so it cannot block the rest. Unverified and partial actions are never retried. The sweep is signed before the gas used is known, so unused gas money stays in the compromised wallet; the CLI prints the most that can be left behind.
9. Prints the outcome table, the ETH left in the compromised wallet, and its delegation.

If the compromised wallet's nonce changes before the phase-1 transaction in the same block, its EIP-7702 authorization is skipped: the transaction succeeds but rescues nothing. Step 7 reports those actions failed, and phase 2 (or a rerun) retries them.

### Outcomes and exit codes

Each action ends `rescued`, `partial` (some of the amount arrived, or an Ownable2Step contract now lists the safe as its pending owner: call `acceptOwnership()` from the safe), `unverified` (it ran, but nothing can check it: custom calls and delists), `failed`, or `not executed`. Confirm unverified actions yourself. A delist that has no effect while its NFT still reaches the safe (it was not listed) does not count against the rescue.

| Code | Meaning |
| --- | --- |
| 0 | Included; every checkable action verified |
| 1 | Invalid plan, arguments, keys, network or deployment, or an RPC error before anything was sent |
| 2 | Simulation failed; nothing was sent |
| 3 | The gas wallet was not funded in time |
| 4 | Not included within `max_blocks`, **or it cannot tell whether a bundle landed**. Check the gas wallet's latest transactions on a block explorer before running again |
| 5 | Included, but an action was not rescued (after the fallback, when enabled) |
| 6 | Declined at the confirmation |
| 130 | Interrupted. The message says whether a bundle was already submitted (it may still be included) |

When phase 1 lands and phase 2 cannot finish, the exit code is 4 if phase 2's inclusion is unknown, 3 if its funding ran out, and 5 otherwise.

### Plan format

```json
{
  "version": 1,
  "network": "mainnet",
  "victim": "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61",
  "safe": "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1",
  "gas_wallet": "0x2546BcD3c84621e976D8185a91A922aE77ECEc30",
  "options": { "priority_fee_gwei": "2.5", "max_blocks": 25, "legacy_fallback": true },
  "actions": [
    { "type": "erc20", "contract": "0x…", "amount": "1000000000000000000" },
    { "type": "erc721", "contract": "0x…", "token_id": "78" },
    { "type": "erc1155", "contract": "0x…", "token_id": "3", "amount": "5" },
    { "type": "ownership", "contract": "0x…" },
    { "type": "transient_delist_erc721", "auction_house": "0x…", "contract": "0x…", "token_id": "12" },
    { "type": "custom", "target": "0x…", "signature": "transferPunk(address,uint256)", "args": ["0x…", "78"], "gas": 75000 }
  ]
}
```

- Amounts, token IDs and the priority fee are decimal **strings**, so browsers never round them. Amounts must be above zero. `priority_fee_gwei` is added to every gas unit; the CLI warns above 50 gwei.
- Addresses must be valid checksums (or all lowercase or all uppercase), non-zero, and no action may target the compromised wallet.
- Every action takes an optional `label` and `gas` (a minimum; the CLI raises it from a live estimate).
- `custom` calls the target as the compromised wallet; its arguments, not `safe`, decide where anything goes. The signature must be **canonical** (`uint256`, not `uint`; no parameter names, spaces or `returns`), because the selector is the hash of that exact text. Arguments are strict JSON: integers as numbers or decimal/`0x` strings, `bool` as `true`/`false`, `bytes` as `0x` hex of whole bytes, addresses as checksummed or lowercase hex, arrays and tuples as JSON arrays.

[`schema/plan.v1.schema.json`](schema/plan.v1.schema.json) is the JSON Schema. The CLI also checks what a schema cannot express: address checksums, distinct wallets, the uint256 range and canonical signatures, so run `validate` on a plan. [`examples/`](examples) has sample plans with placeholder addresses.

### Networks

The CLI runs on Ethereum mainnet only. Testnet Flashbots relays rarely land bundles, so a testnet run can't show whether a rescue would work. The integration suite instead runs every rescue flow against the real EthRescue deployment on a fork of a live network (see [Integration tests](#integration-tests)).

## Web plan builder

`web/` is a static Vite + React app that builds `plan.json` from addresses. It never asks for keys, and it refuses input that looks like a private key or seed phrase. It can look up what the compromised wallet holds (Alchemy for tokens and NFTs, the Transient API for listings and creator contracts); names come from those providers, so check every address before adding it. The draft is saved in the browser, except custom-call arguments.

## Developer guide

```sh
git clone https://github.com/mpeyfuss/eth-rescue.git
cd eth-rescue
uv sync
uv run eth-rescue --help
```

| Command | What it does |
| --- | --- |
| `make fmt` / `make lint` | Ruff format and lint |
| `make test` | Python unit tests |
| `make test-integration` | Compile fixtures, run the Anvil suite on Osaka and Amsterdam |
| `make test-fork` | Run the Anvil suite on a fork of Sepolia, against its EthRescue deployment (needs network access) |
| `make schema` | Regenerate the plan schema and the web's TypeScript types |
| `make web-install` / `web-dev` / `web-test` / `web-build` | The web app (Bun) |
| `make test-all` | Unit, integration and web tests |

Contract tests: `cd contracts && forge test`.

`eth_rescue/`: `plan.py` (models, expansion), `calldata.py` (ABI rules), `bundle.py` (building and signing), `engine.py` (fund → simulate → send → verify → fallback), `checks.py` (postconditions), `relay.py` (Flashbots), `cli.py`, `report.py`/`ui.py` (output).

The ABI rules are shared with the web builder through `tests/fixtures/abi_vectors.json`: `tests/test_calldata.py` and `web/test/custom.test.ts` must agree on every vector, so a plan the builder accepts encodes to the same bytes in the CLI.

### Gas and forks

EthRescue never checks gas itself, so the CLI's budget is all that stands between a rescue and starved actions. The constants and their reasoning live in `eth_rescue/bundle.py`. The rules:

- **Measure, don't hardcode.** Each action's gas limit is a live `eth_estimateGas` × `--gas-multiplier`, with the template's gas as a floor that an estimate can only raise. The cost of sweeping to an empty safe comes from a live transfer estimate.
- **Size once, before any delegation changes.** Phase-1 estimates also describe the undelegated wallet that the legacy fallback runs as.
- **Budget per rescue transaction:** `RESCUE_TX_FIXED_GAS` + `SWEEP_CALL_GAS` + the safe's receive cost + Σ(stipend + stipend/63 + `RESCUE_ACTION_OVERHEAD_GAS` + calldata at 40 gas/byte). The stipend/63 term is what makes EIP-150 forward the full stipend.
- **Estimates only raise the budget.** Each transaction's limit is max(budget, whole-transaction estimate × 1.5). The node's estimator can't see an action starving inside `rescue()`, because the transaction still succeeds, so an estimate is never used alone. If the RPC can't estimate (no type-4 or state-override support), the budget stands and the CLI warns.
- **Never exceed 2^24 gas per transaction.** Under EIP-8037, gas above the EIP-7825 cap becomes a state-gas reservoir that `gasleft()` can't see and that bypasses call stipends.
- **Test every fork.** `make test-integration` runs Osaka and Amsterdam with EIP-7825 enforced, and the gas-burner test asserts worst-case overhead uses at most 75% of its budget.

Measured on Anvil:

| Operation | Osaka | Amsterdam |
| --- | --- | --- |
| Rescue tx: 1 ERC-721 action, fresh safe | 95.4k | 300.8k |
| ETH transfer to a fresh account | 21k | 204.6k |
| ERC-20 / ERC-1155 transfer to a fresh holder | 50k / 52k | 139.5k / 141k |
| Set 7702 delegation / clear it | 36.8k / 36.8k | 64k / 28.8k |
| Rescue overhead per action (calldata included) | ~5.3k | ~5.3k |

### Integration tests

The integration suite needs `forge` and `anvil` from [Foundry](https://book.getfoundry.sh/getting-started/installation). Its relay double (`tests/integration/conftest.py`) gives bundles Flashbots semantics on Anvil: a bundle lands atomically, in order, in its target block, or not at all, and blocks advance only when the engine waits. It does not model builder competition, privacy or real base-fee movement, and it never contacts a relay.

`make test-fork` runs the same suite on an Anvil fork of a live network (`FORK_NETWORK`, default `sepolia`) at one block pinned for the run. It uses that network's EthRescue deployment instead of a fresh one: every run checks the pinned codehash, and a dedicated test checks that the deployed code is this checkout's build apart from its EIP-712 immutables. Sepolia's deployment is pinned in `tests/integration/conftest.py`, since Sepolia is not a CLI network; any network pinned in `networks.py` (mainnet) can be forked too, e.g. `make test-fork FORK_NETWORK=mainnet`. `FORK_RPC_URL` overrides the public RPC, and `FORK_HARDFORK` (default `osaka`) must match the live chain's fork, because Anvil otherwise assumes its newest one.

## License

This project is available under the [MIT License](LICENSE).
