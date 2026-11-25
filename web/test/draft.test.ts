import { expect, test } from "bun:test";
import { looksLikeSecret } from "../src/plan/address";
import { draftReducer, emptyDraft, fromPlan, networkOptions, newAction, PlanImportError, toPlan } from "../src/plan/draft";

const SAFE = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";

function withActions(count: number) {
  const draft = emptyDraft();
  draft.actions = Array.from({ length: count }, () => newAction("erc20"));
  return draft;
}

test("reorder moves an action to the position of another", () => {
  const draft = withActions(4);
  const [a, b, c, d] = draft.actions;
  expect(draftReducer(draft, { kind: "reorder", id: a.id, overId: c.id }).actions).toEqual([b, c, a, d]);
  expect(draftReducer(draft, { kind: "reorder", id: d.id, overId: b.id }).actions).toEqual([a, d, b, c]);
  expect(draftReducer(draft, { kind: "reorder", id: a.id, overId: "missing" }).actions).toEqual([a, b, c, d]);
});

test("move swaps neighbours and ignores moves past either end", () => {
  const draft = withActions(3);
  const [a, b, c] = draft.actions;
  expect(draftReducer(draft, { kind: "move", id: b.id, offset: -1 }).actions).toEqual([b, a, c]);
  expect(draftReducer(draft, { kind: "move", id: c.id, offset: 1 }).actions).toEqual([a, b, c]);
});

test("toPlan checksums lowercase addresses and omits empty optional fields", () => {
  const draft = emptyDraft();
  draft.safe = SAFE.toLowerCase();
  draft.actions = [{ ...newAction("erc721"), contract: SAFE.toLowerCase(), token_id: "7", label: "  " }];
  const plan = toPlan(draft);
  expect(plan.safe).toBe(SAFE);
  expect(plan).not.toHaveProperty("gas_wallet");
  expect(plan.actions).toEqual([{ type: "erc721", contract: SAFE, token_id: "7" }]);
});

test("custom args become JSON values by parameter type", () => {
  const draft = emptyDraft();
  draft.actions = [
    {
      ...newAction("custom"),
      target: SAFE,
      signature: "f(string,bool,uint256[],uint256)",
      args: ["[not json", "true", "[1, 2]", "5"],
      gas: "90000",
    },
  ];
  const [action] = toPlan(draft).actions as Record<string, unknown>[];
  expect(action.args).toEqual(["[not json", true, [1, 2], "5"]);
  expect(action.gas).toBe(90000);
});

test("non-integer numbers stay text so validation reports them", () => {
  const draft = { ...emptyDraft(), max_blocks: "2.5" };
  expect((toPlan(draft).options as Record<string, unknown>).max_blocks).toBe("2.5");
});

test("fromPlan rejects non-plans", () => {
  expect(() => fromPlan([])).toThrow(PlanImportError);
  expect(() => fromPlan({ version: 2 })).toThrow("only version 1");
  expect(() => fromPlan({ version: 1, actions: [] })).toThrow("network is required");
  expect(() => fromPlan({ version: 1, network: "mainnet", actions: [{ type: "nope" }] })).toThrow("actions.0");
});

test("secrets are recognised", () => {
  expect(looksLikeSecret("ab".repeat(32))).toBe(true);
  expect(looksLikeSecret(`0x${"ab".repeat(32)}`)).toBe(true);
  expect(looksLikeSecret(Array(12).fill("abandon").join(" "))).toBe(true);
  expect(looksLikeSecret(SAFE)).toBe(false);
});

test("the testnet is offered only when enabled", () => {
  expect(networkOptions(false).map((network) => network.value)).toEqual(["mainnet"]);
  expect(networkOptions(true).map((network) => network.value)).toEqual(["mainnet", "sepolia"]);
});

test("looksLikeSecret finds keys and seed phrases inside other text", () => {
  const key = `0x${"ab".repeat(32)}`;
  expect(looksLikeSecret(`Private key: ${key}`)).toBe(true);
  expect(looksLikeSecret(`Address: 0x${"12".repeat(20)}\nPrivate key: ${key}`)).toBe(true);
  expect(looksLikeSecret("my words: abandon ability able about above absent absorb abstract absurd abuse access accident")).toBe(true);
  expect(looksLikeSecret(`0x${"12".repeat(20)}`)).toBe(false);
  expect(looksLikeSecret("My favourite art drop from the summer")).toBe(false);
});
