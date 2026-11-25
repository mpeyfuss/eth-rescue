import { expect, test } from "bun:test";
import { PlanImportError } from "../src/plan/draft";
import { importPlan, persistablePlan } from "../src/plan/importPlan";

const VICTIM = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const TOKEN = "0x41A322b28D0fF354040e2CbC676F0320d8c8850d";

const plan = (actions: unknown[], extra: Record<string, unknown> = {}) =>
  JSON.stringify({ version: 1, network: "mainnet", victim: VICTIM, safe: SAFE, actions, ...extra });

test("a valid plan imports with nothing to confirm", () => {
  const result = importPlan(plan([{ type: "erc20", contract: TOKEN, amount: "5" }]));
  expect(result.issues).toEqual([]);
  expect(result.repairs).toEqual([]);
});

test("numbers a browser would round are refused, not rounded", () => {
  const text = plan([{ type: "erc20", contract: TOKEN, amount: "1" }]).replace('"1"', "1000000000000000001");
  expect(() => importPlan(text)).toThrow(/actions\.0\.amount cannot be read exactly/);
});

test("conversions are listed for confirmation and raw problems are reported", () => {
  const result = importPlan(plan([{ type: "erc721", contract: TOKEN, token_id: 78, colour: "red" }]));
  expect(result.repairs).toEqual([
    'changes actions.0.token_id from 78 to "78"',
    'drops actions.0.colour ("red")',
  ]);
  expect(result.issues.map((issue) => issue.path)).toContain("actions.0.token_id");
});

test("a plan without a network is refused", () => {
  expect(() => importPlan(JSON.stringify({ version: 1, victim: VICTIM, safe: SAFE, actions: [] }))).toThrow(
    PlanImportError,
  );
});

test("files carrying secrets are refused", () => {
  const key = `0x${"ab".repeat(32)}`;
  expect(() => importPlan(plan([{ type: "erc20", contract: TOKEN, amount: "1", label: key }]))).toThrow(/private key/);
  const bytes32 = { type: "custom", target: TOKEN, signature: "f(bytes32)", args: [key], gas: 50000 };
  expect(importPlan(plan([bytes32])).issues).toEqual([]);
});

test("persisted plans drop custom arguments", () => {
  const stored = persistablePlan({ actions: [{ type: "custom", args: ["0x1234"] }, { type: "erc20", amount: "1" }] });
  expect(stored.actions).toEqual([{ type: "custom", args: [] }, { type: "erc20", amount: "1" }]);
});
