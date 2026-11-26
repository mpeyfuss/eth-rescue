import { expect, test } from "bun:test";
import { validatePlan } from "../src/plan/validate";

const COMPROMISED = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const DESTINATION = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";

function plan(overrides: Record<string, unknown> = {}, action: Record<string, unknown> = {}) {
  return {
    version: 1,
    network: "mainnet",
    compromised: COMPROMISED,
    destination: DESTINATION,
    actions: [{ type: "erc20", contract: TOKEN, amount: "1", ...action }],
    ...overrides,
  };
}

const paths = (value: unknown) => validatePlan(value).map((issue) => `${issue.path}: ${issue.message}`);

test("minimal plan is valid", () => {
  expect(paths(plan())).toEqual([]);
});

test("lowercase addresses are accepted", () => {
  expect(paths(plan({ compromised: COMPROMISED.toLowerCase() }))).toEqual([]);
});

test("bad checksum is rejected", () => {
  const badChecksum = COMPROMISED.replace("d068c6A6", "D068c6A6");
  expect(paths(plan({ compromised: badChecksum }))).toEqual(["compromised: has an invalid checksum"]);
});

test("destination must differ from the compromised wallet", () => {
  expect(paths(plan({ destination: COMPROMISED.toLowerCase() }))).toEqual(["destination: must differ from the compromised wallet"]);
});

test("destination must not be the EthRescue contract", () => {
  expect(paths(plan({ destination: "0xdc7ec38149c572a29ac89813eda47140ac8cd969" }))).toEqual([
    "destination: must not be the EthRescue contract: ETH sent there is lost",
  ]);
});

test("a non-canonical signature is reported on the signature, not its args", () => {
  const custom = { type: "custom", target: TOKEN, signature: "f(uint)", args: ["1"], gas: 50000 };
  const issues = validatePlan({ ...plan(), actions: [custom] });
  expect(issues.map((issue) => issue.path)).toEqual(["actions.0.signature"]);
  expect(issues[0]?.message).toContain("f(uint256)");
});

test("gas wallet must differ from the compromised and destination wallets", () => {
  expect(paths(plan({ gas_wallet: DESTINATION }))).toEqual([
    "gas_wallet: must differ from the compromised wallet and destination wallet",
  ]);
});

test("missing fields and empty actions", () => {
  expect(paths({ version: 1, network: "mainnet", compromised: COMPROMISED, actions: [] })).toEqual([
    "destination: is required",
    "actions: needs at least one action",
  ]);
});

test("action errors carry their index and field", () => {
  expect(paths(plan({}, { amount: "1.5", extra: true }))).toEqual([
    "actions.0.extra: is not a known field",
    "actions.0.amount: must be a whole number greater than zero",
  ]);
});

test("zero amounts are rejected", () => {
  expect(paths(plan({}, { amount: "0" }))).toEqual(["actions.0.amount: must be a whole number greater than zero"]);
});

test("unknown action type", () => {
  expect(paths(plan({ actions: [{ type: "erc404" }] }))).toEqual(["actions.0.type: is not a known action type"]);
});

test("amount above uint256 is rejected", () => {
  expect(paths(plan({}, { amount: (2n ** 256n).toString() }))).toEqual(["actions.0.amount: exceeds uint256"]);
});

test("gas over the per-action cap is rejected", () => {
  expect(paths(plan({}, { gas: 8_000_001 }))).toEqual(["actions.0.gas: must be at most 8000000"]);
});

test("priority fee allows at most nine decimals", () => {
  expect(paths(plan({ options: { priority_fee_gwei: "1.0000000001" } }))).toEqual([
    "options.priority_fee_gwei: must be a gwei amount with at most 9 decimals",
  ]);
});

const custom = (signature: string, args: unknown[]) =>
  plan({ actions: [{ type: "custom", target: TOKEN, signature, args, gas: 100000 }] });

test("custom args are coerced like the CLI", () => {
  expect(paths(custom("transferPunk(address,uint256)", [DESTINATION, "0x4e"]))).toEqual([]);
  expect(paths(custom("f(uint256[],(address,bool),bytes)", [["1", 2], [DESTINATION, true], "0x1234"]))).toEqual([]);
});

test("custom arg count must match the signature", () => {
  expect(paths(custom("transferPunk(address,uint256)", [DESTINATION]))).toEqual([
    "actions.0.args: transferPunk(address,uint256) expects 2 args, got 1",
  ]);
});

test("custom args that do not encode are rejected", () => {
  const [issue] = validatePlan(custom("transferPunk(address,uint256)", [DESTINATION, "abc"]));
  expect(issue.path).toBe("actions.0.args");
  expect(issue.message).toBe("args[1]: expected an integer or a decimal/0x string");
});

test("custom gas is required", () => {
  const value = plan({ actions: [{ type: "custom", target: TOKEN, signature: "f()", args: [] }] });
  expect(paths(value)).toEqual(["actions.0.gas: is required"]);
});

test("a testnet plan is not supported", () => {
  expect(paths(plan({ network: "sepolia" }))).toEqual(["network: is not supported here"]);
});

test("an inherited object key is not an action type", () => {
  expect(paths(plan({}, { type: "toString" }))).toEqual(["actions.0.type: is not a known action type"]);
});

test("field checks still run when the schema rejects something else", () => {
  const issues = paths(plan({}, { contract: TOKEN.toLowerCase().replace("a0b8", "A0b8"), extra: 1 }));
  expect(issues).toContain("actions.0.contract: has an invalid checksum");
  expect(issues).toContain("actions.0.extra: is not a known field");
});

test("the zero address and the compromised wallet are refused where EthRescue refuses them", () => {
  const zero = "0x0000000000000000000000000000000000000000";
  expect(paths(plan({ destination: zero }))).toEqual(["destination: must not be the zero address"]);
  expect(paths(plan({}, { contract: COMPROMISED }))).toEqual(["actions.0.contract: must not be the compromised wallet"]);
});
