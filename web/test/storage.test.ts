import { expect, test } from "bun:test";
import { discoveryScope, emptyDraft, newAction, staleDiscoveries, toPlan } from "../src/plan/draft";
import { deserializeDraft, serializeDraft } from "../src/plan/storage";

const COMPROMISED = "0xd068c6A6db349A8Ce9C7aD3706391e53417abF61";
const OTHER_COMPROMISED = "0x74A7b842FDeb244C152aa5BC8B7fbae362091EE1";
const TOKEN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48";

function discoveredDraft() {
  const draft = emptyDraft();
  draft.compromised = COMPROMISED;
  draft.actions = [
    { ...newAction("erc20"), contract: TOKEN, amount: "1", discoveredFor: discoveryScope("mainnet", COMPROMISED) },
    { ...newAction("erc20"), contract: TOKEN, amount: "2" },
  ];
  return draft;
}

test("discovery tags survive a reload", () => {
  const restored = deserializeDraft(serializeDraft(discoveredDraft()));

  expect(restored.actions.map((action) => action.discoveredFor)).toEqual([discoveryScope("mainnet", COMPROMISED), ""]);
  expect(staleDiscoveries(restored)).toEqual([]);
});

test("a reload still flags discoveries once the wallet changes", () => {
  const restored = deserializeDraft(serializeDraft(discoveredDraft()));
  restored.compromised = OTHER_COMPROMISED;

  expect(staleDiscoveries(restored).map((issue) => issue.path)).toEqual(["actions.0"]);
});

test("a draft saved as a bare plan still loads", () => {
  const restored = deserializeDraft(JSON.stringify(toPlan(discoveredDraft())));

  expect(restored.compromised).toBe(COMPROMISED);
  expect(restored.actions.map((action) => action.discoveredFor)).toEqual(["", ""]);
});

test("unreadable storage gives an empty draft", () => {
  expect(deserializeDraft("{not json")).toEqual(emptyDraft());
  expect(deserializeDraft("3")).toEqual(emptyDraft());
  expect(deserializeDraft(null)).toEqual(emptyDraft());
});
