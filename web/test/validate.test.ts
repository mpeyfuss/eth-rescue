import { expect, test } from "bun:test";
import { validatePlan } from "../src/plan/validate";

const VICTIM = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const TOKEN = "0x41A322b28D0fF354040e2CbC676F0320d8c8850d";

function plan(overrides: Record<string, unknown> = {}, action: Record<string, unknown> = {}) {
  return {
    version: 1,
    network: "mainnet",
    victim: VICTIM,
    safe: SAFE,
    actions: [{ type: "erc20", contract: TOKEN, amount: "1", ...action }],
    ...overrides,
  };
}

const paths = (value: unknown) => validatePlan(value).map((issue) => `${issue.path}: ${issue.message}`);

test("minimal plan is valid", () => {
  expect(paths(plan())).toEqual([]);
});

test("lowercase addresses are accepted", () => {
  expect(paths(plan({ victim: VICTIM.toLowerCase() }))).toEqual([]);
});

test("bad checksum is rejected", () => {
  const badChecksum = VICTIM.replace("d068c6A6", "D068c6A6");
  expect(paths(plan({ victim: badChecksum }))).toEqual(["victim: has an invalid checksum"]);
});

test("safe must differ from victim", () => {
  expect(paths(plan({ safe: VICTIM.toLowerCase() }))).toEqual(["safe: must differ from victim"]);
});

test("gas wallet must differ from victim and safe", () => {
  expect(paths(plan({ gas_wallet: SAFE }))).toEqual(["gas_wallet: must differ from victim and safe"]);
});

test("missing fields and empty actions", () => {
  expect(paths({ version: 1, network: "mainnet", victim: VICTIM, actions: [] })).toEqual([
    "safe: is required",
    "actions: needs at least one action",
  ]);
});

test("action errors carry their index and field", () => {
  expect(paths(plan({}, { amount: "1.5", extra: true }))).toEqual([
    "actions.0.extra: is not a known field",
    "actions.0.amount: must be a whole number",
  ]);
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
  expect(paths(custom("transferPunk(address,uint256)", [SAFE, "0x4e"]))).toEqual([]);
  expect(paths(custom("f(uint256[],(address,bool),bytes)", [["1", 2], [SAFE, true], "0x1234"]))).toEqual([]);
});

test("custom arg count must match the signature", () => {
  expect(paths(custom("transferPunk(address,uint256)", [SAFE]))).toEqual([
    "actions.0.args: transferPunk(address,uint256) expects 2 args, got 1",
  ]);
});

test("custom args that do not encode are rejected", () => {
  const [issue] = validatePlan(custom("transferPunk(address,uint256)", [SAFE, "abc"]));
  expect(issue.path).toBe("actions.0.args");
  expect(issue.message).toBe("args[1]: expected an integer or a decimal/0x string");
});

test("custom gas is required", () => {
  const value = plan({ actions: [{ type: "custom", target: TOKEN, signature: "f()", args: [] }] });
  expect(paths(value)).toEqual(["actions.0.gas: is required"]);
});

test("a testnet plan is not supported unless enabled", () => {
  expect(paths(plan({ network: "sepolia" }))).toEqual(["network: is not supported here"]);
});

test("an inherited object key is not an action type", () => {
  expect(paths(plan({}, { type: "toString" }))).toEqual(["actions.0.type: is not a known action type"]);
});

test("field checks still run when the schema rejects something else", () => {
  const issues = paths(plan({}, { contract: TOKEN.toLowerCase().replace("41a3", "41A3"), extra: 1 }));
  expect(issues).toContain("actions.0.contract: has an invalid checksum");
  expect(issues).toContain("actions.0.extra: is not a known field");
});

test("the zero address and the compromised wallet are refused where EthRescue refuses them", () => {
  const zero = "0x0000000000000000000000000000000000000000";
  expect(paths(plan({ safe: zero }))).toEqual(["safe: must not be the zero address"]);
  expect(paths(plan({}, { contract: VICTIM }))).toEqual(["actions.0.contract: must not be the compromised wallet"]);
});
