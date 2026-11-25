# eth-rescue v1 spec (draft)

## Goal

Replace the guided terminal wizard with:

1. **A non-interactive CLI** that takes a JSON rescue plan, asks only for secrets, simulates, and submits.
2. **A static web UI** (Vite + React + TypeScript, Bun) that helps people *build* that plan by discovering what the compromised wallet holds. The UI never handles private keys.

The split makes each half easy to test on its own. The CLI is a pure function of plan + keys + chain state. The UI is a pure function of addresses + API data → plan JSON.

```
web UI (addresses only) ──► plan.json ──► eth-rescue run plan.json ──► Flashbots relay
                                             ▲
                                   private keys (hidden prompt / env)
```

## Non-goals for v1

- No private keys in the browser, ever. No wallet connection for the victim.
- No hosted backend. The UI is a static site.
- No in-CLI plan editing. If simulation fails, the user fixes the plan and runs again.

---

## 1. Plan format (`plan.json`, version 1)

The plan is the contract between the UI and the CLI. It contains **no secrets**.

```json
{
  "version": 1,
  "network": "mainnet",
  "victim": "0xVictim...",
  "safe": "0xSafe...",
  "gas_wallet": "0xGas...",
  "options": {
    "priority_fee_gwei": "2",
    "max_blocks": 25,
    "legacy_fallback": true
  },
  "actions": [
    { "type": "erc20",   "contract": "0x...", "amount": "1000000000000000000" },
    { "type": "erc721",  "contract": "0x...", "token_id": "78" },
    { "type": "erc1155", "contract": "0x...", "token_id": "3", "amount": "5" },
    { "type": "ownership", "contract": "0x..." },
    { "type": "transient_delist_erc721", "auction_house": "0x...", "contract": "0x...", "token_id": "12" },
    { "type": "custom", "target": "0x...", "signature": "buyPunk(uint256)", "args": ["1234"], "gas": 150000 }
  ]
}
```

Rules:

- **Big integers are decimal strings** (`amount`, `token_id`, `priority_fee_gwei`). JS numbers lose precision above 2^53, and the UI is JS.
- `victim` and `safe` are required. `gas_wallet` is optional. When present, the CLI checks that the entered keys match these addresses, which catches a wrong-key paste before anything is signed.
- **Typed actions** are the default. The CLI fills in `from`/`to` from `victim`/`safe`, adds the default gas limit from `templates.py`, and derives the postcondition check (today's `checks.infer_check`). The UI never has to ABI-encode anything.
- `custom` is the escape hatch. `args` are converted by ABI type: `int`/`uint` accept integers or decimal/`0x` strings, `bytes`/`bytesN` take `0x` hex strings, and arrays and tuples are JSON arrays. `gas` is required for custom actions because there's no sane default.
- Any typed action may set an optional `gas` override and a `label` for display.
- The legacy bare-array plan format is dropped.

### Schema source of truth

Pydantic models in `src/eth_rescue/plan.py` are the source of truth. `eth-rescue schema` prints JSON Schema, which is committed at `schema/plan.v1.schema.json` and consumed by the web app. That gives typegen via `json-schema-to-typescript` and runtime validation via `ajv`. A Python test fails if the committed schema is stale.

`pydantic` is a new runtime dependency (decided).

---

## 2. CLI

### Commands

| Command | Network | Keys | Purpose |
| --- | --- | --- | --- |
| `eth-rescue validate plan.json` | no | no | Schema check plus calldata encoding. Prints the expanded action list. |
| `eth-rescue simulate plan.json` | yes | yes | Build the exact bundle and simulate it via the relay. Never submits. |
| `eth-rescue run plan.json` | yes | yes | Simulate, confirm once, submit with retry, verify outcomes, run the optional legacy fallback. |
| `eth-rescue gas-wallet new` | no | no | Generate a fresh gas wallet and print its address and key once. |
| `eth-rescue schema` | no | no | Print the plan JSON Schema. |

Flags: `--yes` (skip the single send confirmation), `--rpc URL` (override the public RPC), `--json` (NDJSON events on stdout for scripting and tests; human-readable output goes to stderr), `--gas-multiplier X` (multiply each action's live gas estimate by X, 1–10, default 1.5; the plan's gas is always the minimum and failed estimates keep it).

Argument parsing uses stdlib `argparse`, so no new dependency.

### Secrets

- Hidden prompts (`getpass`) only. No env vars, key files, or flags. Without a TTY, the CLI exits 1.
- These are the **only** prompts besides the send confirmation. No menus, no questionary.
- The engine takes `LocalAccount`s as arguments, and only `cli.py` prompts. Integration tests pass keys directly.
- Keys are never written to disk or included in `--json` output.

### Behaviour changes from the current flow

| Today (interactive) | v1 |
| --- | --- |
| Wizard builds the plan | Plan file required |
| "Press a key to re-check" funding | Poll the gas balance each block until funded, or `--funding-timeout` expires, then exit 3 |
| Post-simulation correction menu | Print a per-action failure report and exit 2. Nothing is sent. |
| "Keep trying?" after 25 blocks | Stop after `options.max_blocks` and exit 4 |
| "Attempt legacy fallback?" prompt | `options.legacy_fallback` (default `true`) |
| Generate gas wallet mid-flow | `eth-rescue gas-wallet new` beforehand |

### Exit codes

`0` included and every checkable action verified · `1` invalid plan, keys, or network mismatch · `2` simulation failed (nothing sent) · `3` gas wallet not funded in time · `4` not included within `max_blocks` · `5` included, but one or more actions failed verification after the fallback. · `6` send declined at the confirmation prompt · `130` interrupted.

The single confirmation covers both phases; the legacy fallback is opted into by `options.legacy_fallback`, so it doesn't prompt again.

### Code changes

- **New** `plan.py`: models, `load_plan(path)`, `expand(plan) -> list[RescueData]` (reuses `templates.py`).
- **New** `cli.py`: argparse entry point. `[project.scripts]` points here.
- **Refactor** `rescue.py` (1,056 lines) into:
  - `bundle.py`: the pure-ish builders (`prepare_7702_bundle`, `prepare_bundle`, fee and funding math, signing). These are mostly unchanged.
  - `engine.py`: the orchestration loop (fund → simulate → send → verify → fallback). It emits events to a reporter instead of calling `ui.*` and `prompt_*` directly.
- `ui.py` becomes a `Reporter` with two implementations: rich (human) and NDJSON.
- **Delete** `wizard.py`, `prompts.py` (except the secret/confirm helpers), the `questionary` dependency, and `tests/test_wizard.py` / `tests/test_prompts.py`.
- Keep `calldata.py`, `relay.py`, `contract.py`, `checks.py`, `abi.py`, and `templates.py` as-is.

### Testing

- Unit tests: `plan.py` validation and expansion, all `examples/*.json` load, and the schema is fresh.
- Integration tests (Anvil plus the existing sequential relay double): drive `engine.run(plan, keys, reporter)` directly. No prompt monkeypatching needed. Add one test per exit code path.
- CLI smoke test: `subprocess` run of `validate` and `schema`.

---

## 3. Web UI (`web/`)

Stack: Vite + React + TypeScript, Bun as package manager and test runner (`bun test` or vitest). It's a static build deployable to GitHub Pages or Cloudflare Pages. Validation uses `ajv` against the committed schema.

### Flow

1. **Setup.** Pick the network, then enter the victim address, safe address, and optionally the gas wallet address. A persistent banner says: *never paste a private key here*.
2. **Discover.** Fetch what the victim holds:
   - ERC-20: Alchemy `alchemy_getTokenBalances` plus `alchemy_getTokenMetadata`, showing human units with full base-unit amounts.
   - ERC-721 / ERC-1155: Alchemy NFT API `getNFTsForOwner`.
   - Transient API: tokens escrowed in the auction house by the victim become `transient_delist_erc721` actions, and creator contracts owned by the victim become `ownership` actions.
   - Any other owned contract: manual entry, with an on-chain `owner()` check to confirm.
3. **Select.** Pick assets with checkboxes. Amounts default to the full balance and can be edited. Drag to reorder.
4. **Add manually.** Forms for each typed action plus `custom`.
5. **Review & export.** Show the validated plan, a download button for `plan.json`, and a copyable `uvx eth-rescue run plan.json`. You can also re-import a plan to edit it.

### Testing

- Pure functions (`alchemy response → actions`, `state → plan`) unit-tested with recorded API fixtures.
- Every plan the builder emits is validated against the schema in tests.
- `examples/*.json` must validate in both Python and TS. This shared fixture set is the cross-language contract test.

### As built (milestone 3)

- One page with three sections (wallets, assets, review) instead of a stepper. The draft autosaves to `localStorage`; it holds addresses only.
- `web/src/plan/validate.ts` mirrors `parse_plan`: ajv (draft 2020-12, strict off because pydantic's `discriminator` keyword is not ajv's) validates the plan shell and then each action against the `$def` its `type` names, so errors point at one field. On top of the schema it checks address checksums, the uint256 bound, distinct wallets, and that custom args encode (viem, with the CLI's coercion rules).
- `web/src/plan/types.gen.ts` is generated from the schema (`bun run gen:types`, also run by `make schema`), and a test fails when it is stale.
- Every text field except custom args refuses input that looks like a private key or seed phrase.
- A "Before you start" card gives per-OS install steps (uv, then `uvx eth-rescue@latest`) and a copyable prompt for an AI assistant that pins the exact commands and forbids asking for keys. Commands use `@latest` so a cached pre-v1 eth-rescue is never reused.
- Tests run with `bun test`; DOM tests use happy-dom and Testing Library.

---

## 4. Repo layout

```
src/eth_rescue/       CLI package
contracts/            EthRescue.sol (Foundry)
schema/               plan.v1.schema.json (generated, committed)
web/                  Vite + React app (bun)
examples/             v1 plans (shared fixtures)
docs/                 this spec
```

Makefile gains `web-dev`, `web-test`, `web-build`, and `schema`.

---

## 5. Milestones

1. ✅ **Plan + validate.** `plan.py`, schema export, `validate` and `schema` commands, migrate `examples/`.
2. ✅ **Dumb CLI.** `bundle.py`/`engine.py` split, reporters, `simulate`/`run`/`gas-wallet new`, delete the wizard, port integration tests.
3. ✅ **Web builder (manual).** Setup, manual actions, review/export, re-import. Usable end to end with no APIs.
4. **Discovery.** Alchemy tokens and NFTs, then Transient.
5. **Docs + release.** README rewrite, Sepolia rehearsal against the new flow, publish.

---

## 6. Decisions

- **Pydantic** is the plan model and schema source.
- **Alchemy:** ship a domain-restricted key (`VITE_ALCHEMY_KEY`), and let users paste their own (stored in `localStorage`).
- **Contract ownership:** the Transient API for creator contracts, plus manual entry with an on-chain `owner()` check.
- **Keys:** hidden prompts only.
- **Old plan format:** dropped. `examples/` is converted to v1.
- **Monorepo:** `web/` lives in this repo.
- **`ActionResult` event:** added in milestone 2. It's informational only; state checks stay authoritative for typed actions.

## 7. Rescue-path hardening (milestone 2)

- **Gas budget scales with action count.** `RESCUE_TX_OVERHEAD` is a flat 120k. It doesn't cover per-action calldata and the first call to each target when many actions burn their full gas limit (e.g. old `throw`/`assert` contracts losing a race). If the sweep runs out of gas, the transaction reverts and the bundle is dropped. Fix: a fixed base plus a per-action allowance, and an Anvil test with N gas-burning actions under the real budget.
- **Nonce race.** If the victim's nonce changes before our transaction in the same block, the 7702 authorization is skipped. The transaction succeeds but rescues nothing. The state checks report "failed", and the fallback or a rerun handles it. Add an integration test for this.
- **`ActionResult(index, success)` event** in `EthRescue.rescue()`. This makes per-action call results readable from receipt logs, including for `custom` actions. State checks remain the source of truth for typed actions.

## 8. Gas and fork resilience (EIP-8037 / Amsterdam)

Measured on Anvil (`osaka` vs `amsterdam`):

| Operation | Osaka | Amsterdam |
| --- | --- | --- |
| Deploy EthRescue v1 per rescue (2,756-byte runtime; replaced) | 648k | 4.47M |
| Deploy EthRescue once per network, by the maintainer (4,649-byte runtime, before OZ EIP712) | 1.05M | 7.39M |
| Singleton rescue tx: 1 ERC-721 action, fresh safe | 95.4k | 300.8k |
| ETH transfer to a fresh account | 21k | 204.6k |
| ERC-20 / ERC-1155 transfer to a fresh holder | 50k / 52k | 139.5k / 141k |
| Set 7702 delegation / clear it | 36.8k / 36.8k | 64k / 28.8k |
| 7702 rescue overhead per action (calldata included) | ~5.3k | ~5.3k |

Client rules (implemented):

- **Measure, don't hardcode.** Per-action gas limits are live `eth_estimateGas` × `--gas-multiplier` (default 1.5), with template gas as a floor that an estimate can only raise. The sweep's new-account cost comes from a live transfer estimate. The deploy has no fixed fallback.
- **Size once, before any delegation changes.** Phase-1 estimates also describe the undelegated victim the legacy fallback runs as.
- **Never exceed 2^24 gas per transaction.** Under EIP-8037, gas above the cap becomes a state-gas reservoir that `GAS`/`gasleft()` can't see and that bypasses call stipends. Staying under the cap keeps the reservoir at zero.
- **Test every fork.** `make test-integration` runs Osaka and Amsterdam with EIP-7825 enforced. The gas-burner test asserts worst-case overhead uses ≤ 75% of its budget.

Contract rules (implemented in section 9):

- **No gas introspection.** `EthRescue` never reads `gasleft()`, so EIP-8037's reservoir and state-gas refills can't change its logic. Per-action stipends stop a hostile target from starving later actions or the sweep.
- **The gas limit is the client's job.** Only the signed rescuer can submit, so starvation can only come from the CLI's own budget. An earlier up-front `gasleft()` check was removed: its reserves came from the same CLI constants, and a miscalibration would revert the whole rescue instead of failing only the tail actions, which state checks catch and the legacy fallback retries.
- **Client budget per rescue tx:** `RESCUE_TX_FIXED_GAS` (150k) + `SWEEP_CALL_GAS` (40k) + the measured new-account charge for an empty safe + Σ(stipend + stipend/63 + 6k overhead + calldata at 16 gas/byte). The stipend/63 term is what makes EIP-150 forward the full stipend.
- **Whole-transaction estimate, raise-only:** each batch's gas limit is max(budget, `eth_estimateGas` × 1.5), capped at 2^24. The first batch is estimated as a type-4 call with its authorization; later batches with a state override giving the victim the delegation code. An estimate is never used alone: a starved action inside `tryCall` doesn't revert the transaction, so the node's estimator can't see it. If an estimate fails (an RPC without type-4 or override support), the batch keeps its budget and the CLI warns.
- **Probe (ERC-20 + ERC-721, singleton deployed):** budget 668k vs estimate 146k on Osaka, 861k vs 435k on Amsterdam; both actions succeeded at the bare estimate. The budget normally dominates; the estimate covers a fork repricing the fixed overhead.
- `ActionResult` reports the call's success flag only.

## 9. EthRescue singleton (EIP-712 requests)

`EthRescue` has no constructor arguments or storage (OpenZeppelin's EIP712 adds immutables). One deployment per network serves every rescue: the victim's 7702 authorization points at it, and each `rescue()` call carries a request signed by the victim key.

```solidity
struct Action { address target; uint256 gasStipend; bytes data; }
struct RescueRequest { address rescuer; address safe; uint256 deadline; bytes32 actionsHash; }

function rescue(RescueRequest calldata request, Action[] calldata actions, bytes calldata signature) external;
```

- **EIP-712 domain:** inherits OpenZeppelin `EIP712("EthRescue", "1")`, so the domain is `block.chainid` plus `verifyingContract = address(this)`. Inside a delegated call that is the victim, so a signature can't be replayed on another account, chain or the implementation. OZ uses its cached separator only when `address(this)` is the singleton, so under delegation it rebuilds per call. The name and version are short strings, so OZ's storage fallbacks (which would read the victim's possibly dirty storage) are never used. ERC-5267 `eip712Domain()` reports the victim as the verifying contract.
- **Checks, in order:** `msg.sender == rescuer`, `block.timestamp <= deadline`, the safe is not zero, the rescuer or the victim, `keccak256(abi.encode(actions)) == actionsHash`, and OpenZeppelin `ECDSA.recoverCalldata` equals `address(this)`. Malformed, wrong-length and high-s signatures revert with OpenZeppelin's `ECDSAInvalidSignature*` errors; a valid signature from another key reverts with `InvalidSignature`. Then action-shape validation (non-zero target that isn't the victim, non-zero stipend) before any call, the action loop and the sweep.
- **No nonce.** A request can only be replayed by its own rescuer, to its own safe, before its deadline, so replay only repeats the signed rescue. Avoiding storage also avoids EIP-8037 state gas in the victim's account.
- **No gas parameters.** Gas repricing in a fork changes only the CLI's budget constants, never the contract.
- **`required` is gone.** The client never set it: every action is optional, so a race can't revert the bundle.
- **Deployment:** the maintainer deploys `EthRescue` on each supported network and pins the address as `eth_rescue` in `src/eth_rescue/networks.py`. The CLI doesn't embed bytecode and never deploys. It refuses to run on a network whose `eth_rescue` is unset, and refuses (exit 1) if the pinned address has no code. Rebuilding the contract doesn't move a deployed address; shipping a new version means deploying it and updating the pin.
- **Client:** one request per batch, signed with eth-account's EIP-712 implementation, rescuer = gas wallet, deadline = latest block timestamp + 10 minutes (each retry window rebuilds and re-signs).
- **Tests:** integration fixtures deploy `EthRescue` from the forge artifact (`contracts/out`) and pass that address as the context's `rescue_contract`.
