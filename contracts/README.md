# EthRescue contract

A stateless singleton that compromised accounts delegate to with EIP-7702. One deployment serves every rescue; nothing about a rescue is fixed at deployment.

## How a rescue is authorized

The gas wallet sends a type-4 transaction to the compromised wallet. The transaction's authorization delegates the compromised wallet to EthRescue, and its calldata calls `rescue(request, actions, signature)`, which runs as the compromised wallet's code.

`RescueRequest { rescuer, destination, deadline, actionsHash }` is EIP-712 data signed by the compromised wallet's key. The domain's verifying contract is the compromised wallet itself. `rescue` reverts unless:

- `msg.sender == request.rescuer`, so only the gas wallet the compromised wallet named can use the signature;
- the deadline has not passed;
- `destination` is non-zero and differs from the rescuer and from the compromised wallet;
- `keccak256(abi.encode(actions))` equals `actionsHash`, so the exact action list is signed;
- the signature recovers to the compromised wallet;
- every action has a non-zero target other than the compromised wallet, and a non-zero gas stipend.

## What it does

- Each action is a zero-value call with its own gas stipend. A failing action never reverts the rescue. Its result is returned in `bool[] results` and emitted as `ActionResult(index, success)`. A call to an address with no code reports `false`, because a codeless "success" proves nothing.
- Return data is never copied, which bounds hostile return data.
- After the actions, the compromised wallet's whole ETH balance goes to `destination` with a full-gas call, so a contract destination can run its `receive`. A failed transfer reverts the rescue.
- It accepts ERC-721 and ERC-1155 safe transfers to the compromised wallet while delegated.

## Trust model

- The compromised wallet's signature binds the rescuer, destination, deadline and action list. An attacker who also holds the key can sign their own request, but cannot change one already signed.
- Postconditions decide whether an asset was rescued: a call that succeeds can still be a protocol-level failure. The CLI checks the destination wallet's balances and ownership at the inclusion block.
- The client supplies enough outer gas for every stipend plus overhead; too little outer gas reverts the whole transaction.
- No storage, so no state survives between rescues, and a reentrant call from the destination wallet cannot pass the rescuer check.

## Design notes

- **No nonce.** A request can only be replayed by its own rescuer, to its own destination wallet, before its deadline, so a replay only repeats the signed rescue. Skipping storage also avoids EIP-8037 state gas in the compromised wallet's account.
- **EIP-712 domain under delegation.** OpenZeppelin `EIP712("EthRescue", "1")` uses `block.chainid` and `verifyingContract = address(this)`, which is the compromised wallet inside a delegated call, so a signature can't be replayed on another account, chain or the singleton itself. OZ uses its cached separator only when `address(this)` is the singleton, so under delegation it rebuilds it per call. The name and version are short strings, so OZ's storage fallbacks, which would read the compromised wallet's possibly dirty storage, are never used. ERC-5267 `eip712Domain()` reports the compromised wallet as the verifying contract.
- **No gas introspection or gas parameters.** The contract never reads `gasleft()`, so EIP-8037's reservoir and state-gas refills can't change its logic, and a fork repricing gas changes only the CLI's budget constants. Per-action stipends stop a hostile target from starving later actions or the sweep. Only the signed rescuer can submit, so starvation can only come from the CLI's own budget. An up-front `gasleft()` check would take its reserves from those same constants, and a miscalibration would revert the whole rescue instead of failing only the tail actions, which the CLI's checks catch and the legacy fallback retries.
- **Every action is optional.** There's no `required` flag, so an action lost to a race can't revert the bundle.

## Deployments

| Network | Address | Runtime codehash |
| --- | --- | --- |
| Sepolia | [`0xdC7eC38149c572a29Ac89813eda47140Ac8cD969`](https://sepolia.etherscan.io/address/0xdC7eC38149c572a29Ac89813eda47140Ac8cD969) | `0x2e61375f0a3726610b9b221cfc0fcf470e0ea7a582d8c0c46ff8f47e08d6ac66` |
| Mainnet | [`0xdC7eC38149c572a29Ac89813eda47140Ac8cD969`](https://etherscan.io/address/0xdC7eC38149c572a29Ac89813eda47140Ac8cD969) | `0x5635918452603f8714e9e5f7348cc9b7f4d397529d7a92ba2ed818db0e7048fa` |

`script/Deploy.s.sol` deploys through [CreateX](https://github.com/pcaversaccio/createx) with a salt that mixes in neither the sender nor the chain, so anyone can deploy the same code to the same address on any chain that has CreateX.

### Salt and versions

The salt is the `SALT` constant in `script/Deploy.s.sol`: 20 zero bytes (anyone may deploy), a `0x00` byte (no chain id), then the label `"EthRescue"`. It never changes. CreateX derives the address from the salt and the init-code hash, so a new version, with new init code, gets a new address on its own.

Versions are plain integers (1, 2, 3, ...), matching the EIP-712 domain version. Every new deployment bumps it, because under delegation the domain's verifying contract is the compromised wallet rather than the singleton, so the version is what keeps one deployment's signatures from validating under another. A version bump touches:

- `EIP712("EthRescue", "<n>")` and the version tag in `src/EthRescue.sol`;
- `EIP712_DOMAIN_VERSION` in `eth_rescue/contract.py`;
- the address and codehash pins in `eth_rescue/networks.py`, `web/src/plan/draft.ts`, `tests/integration/conftest.py` and the table above.

Copy `.env.example` to `.env` and fill it in: RPC URLs for Sepolia and mainnet, an Etherscan API key for verification, and the signer. The signer is a Trezor by default (`SENDER` picks its address); set `ACCOUNT` to use a Foundry keystore instead.

Run from `contracts/`:

```sh
make predict                  # deterministic CreateX address (offline)
make dry-run-sepolia          # simulate on Sepolia and print address + runtime codehash
make deploy-sepolia           # broadcast on Sepolia, then verify on Etherscan
make verify-sepolia           # verify on Etherscan (auto-computes address; or pass ADDR=0x...)
make verify-sepolia-sourcify  # verify on Sourcify

# Mainnet equivalents: dry-run-mainnet, deploy-mainnet, verify-mainnet, verify-mainnet-sourcify
# Override signer at invocation time:
make deploy-sepolia ACCOUNT=deployer
make deploy-sepolia SIGNER_ARGS="--ledger --sender 0x..."  # any other forge signer
```

The address depends only on the init code, so `foundry.toml` pins everything that shapes it (solc 0.8.38, EVM `prague`, optimizer runs, no metadata hash). Changing the contract or those settings moves the address. Running the script where EthRescue already exists sends nothing. It also refuses to run unless the factory's runtime codehash matches the canonical CreateX, so a different contract at CreateX's address can't put other code at EthRescue's address.

The runtime codehash differs per chain, because EIP-712 caches the chain id in an immutable. The script prints it. Pin each CLI network's address and codehash in `eth_rescue/networks.py` (`eth_rescue`, `eth_rescue_codehash`). The CLI refuses to sign a delegation unless the code at that address matches the hash, and refuses to run on a network without both pins. The CLI runs on mainnet only; the Sepolia deployment is pinned in `tests/integration/conftest.py`, where `make test-fork` checks it from the repository root.

## Development

```sh
make build       # forge build --sizes
make test-std    # forge test
make test-quick  # forge test --fuzz-runs 256
make test-gas    # forge test --gas-report
make test-cov    # forge coverage
make test-fuzz   # forge test --fuzz-runs 10000
```
