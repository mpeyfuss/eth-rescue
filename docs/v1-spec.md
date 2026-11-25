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

Pydantic models in `src/eth_rescue/plan.py` are the source of truth. `eth-rescue schema` prints JSON Schema, which is committed at `schema/plan.v1.schema.json` and consumed by the web app. That gives typegen via `json-schema-to-typescript` and runtime validation via `ajv`. A Python test fails if the committed schema is stale, the same pattern as the existing bytecode-staleness test.

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

Flags: `--yes` (skip the single send confirmation), `--rpc URL` (override the public RPC), `--json` (NDJSON events on stdout for scripting and tests; human-readable output goes to stderr).

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

`0` included and every checkable action verified · `1` invalid plan, keys, or network mismatch · `2` simulation failed (nothing sent) · `3` gas wallet not funded in time · `4` not included within `max_blocks` · `5` included, but one or more actions failed verification after the fallback.

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

1. **Plan + validate.** `plan.py`, schema export, `validate` and `schema` commands, migrate `examples/`.
2. **Dumb CLI.** `bundle.py`/`engine.py` split, reporters, `simulate`/`run`/`gas-wallet new`, delete the wizard, port integration tests.
3. **Web builder (manual).** Setup, manual actions, review/export, re-import. Usable end to end with no APIs.
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
- **`ActionResult(index, success)` event** in `EthRescue.rescue()`. This makes per-action call results readable from receipt logs, including for `custom` actions. State checks remain the source of truth for typed actions. Requires regenerating the embedded bytecode.
