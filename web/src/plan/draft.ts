import { toChecksum } from "./address";
import { argFromText, argToText, tryParseSignature } from "./custom";
import type { Plan } from "./types.gen";

export type Network = Plan["network"];
export type PlanAction = Plan["actions"][number];
export type ActionType = PlanAction["type"];

export const NETWORKS: { value: Network; label: string }[] = [
  { value: "mainnet", label: "Ethereum mainnet" },
  { value: "sepolia", label: "Sepolia (testnet)" },
];

type ActionField = "contract" | "amount" | "token_id" | "auction_house" | "target" | "signature";

export interface ActionSpec {
  label: string;
  fields: ActionField[];
  gas: "optional" | "required" | "none";
}

export const ACTION_SPECS: Record<ActionType, ActionSpec> = {
  erc20: { label: "ERC-20 tokens", fields: ["contract", "amount"], gas: "optional" },
  erc721: { label: "ERC-721 NFT", fields: ["contract", "token_id"], gas: "optional" },
  erc1155: { label: "ERC-1155 tokens", fields: ["contract", "token_id", "amount"], gas: "optional" },
  ownership: { label: "Contract ownership", fields: ["contract"], gas: "optional" },
  transient_delist_erc721: {
    label: "Transient: delist ERC-721",
    fields: ["auction_house", "contract", "token_id"],
    gas: "none",
  },
  custom: { label: "Custom call", fields: ["target", "signature"], gas: "required" },
};

export const ACTION_TYPES = Object.keys(ACTION_SPECS) as ActionType[];

const ADDRESS_FIELDS: ActionField[] = ["contract", "auction_house", "target"];

export interface DraftAction extends Record<ActionField, string> {
  id: string;
  type: ActionType;
  label: string;
  gas: string;
  args: string[];
}

export interface Draft {
  network: Network;
  victim: string;
  safe: string;
  gas_wallet: string;
  priority_fee_gwei: string;
  max_blocks: string;
  legacy_fallback: boolean;
  actions: DraftAction[];
}

export type DraftField = Exclude<keyof Draft, "actions">;

export class PlanImportError extends Error {}

let nextId = 0;
const actionId = () => `action-${++nextId}`;

export function emptyDraft(): Draft {
  return {
    network: "mainnet",
    victim: "",
    safe: "",
    gas_wallet: "",
    priority_fee_gwei: "0",
    max_blocks: "25",
    legacy_fallback: true,
    actions: [],
  };
}

export function newAction(type: ActionType): DraftAction {
  return {
    id: actionId(),
    type,
    label: "",
    gas: "",
    contract: "",
    amount: "",
    token_id: "",
    auction_house: "",
    target: "",
    signature: "",
    args: [],
  };
}

// Numbers stay strings when they are not plain integers so the schema reports them.
function integerOrText(text: string): number | string {
  return /^\d+$/.test(text) ? Number(text) : text;
}

function actionToPlan(action: DraftAction): Record<string, unknown> {
  const spec = ACTION_SPECS[action.type];
  const out: Record<string, unknown> = { type: action.type };
  const label = action.label.trim();
  if (label) out.label = label;
  for (const field of spec.fields) {
    const value = action[field].trim();
    out[field] = ADDRESS_FIELDS.includes(field) ? toChecksum(value) : value;
  }
  if (action.type === "custom") {
    const inputs = tryParseSignature(action.signature.trim())?.inputs;
    out.args = action.args.map((text, index) => argFromText(text, inputs?.[index]));
  }
  const gas = action.gas.trim();
  if (spec.gas === "required" || (spec.gas === "optional" && gas)) out.gas = integerOrText(gas);
  return out;
}

export function toPlan(draft: Draft): Record<string, unknown> {
  const plan: Record<string, unknown> = {
    version: 1,
    network: draft.network,
    victim: toChecksum(draft.victim.trim()),
    safe: toChecksum(draft.safe.trim()),
  };
  const gasWallet = draft.gas_wallet.trim();
  if (gasWallet) plan.gas_wallet = toChecksum(gasWallet);
  plan.options = {
    priority_fee_gwei: draft.priority_fee_gwei.trim(),
    max_blocks: integerOrText(draft.max_blocks.trim()),
    legacy_fallback: draft.legacy_fallback,
  };
  plan.actions = draft.actions.map(actionToPlan);
  return plan;
}

function text(value: unknown): string {
  if (value === undefined || value === null) return "";
  return String(value);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function actionFromPlan(value: unknown, index: number): DraftAction {
  if (!isRecord(value) || !(ACTION_TYPES as unknown[]).includes(value.type)) {
    throw new PlanImportError(`actions.${index}: unknown action type ${JSON.stringify(isRecord(value) ? value.type : value)}`);
  }
  const action = newAction(value.type as ActionType);
  for (const field of ACTION_SPECS[action.type].fields) action[field] = text(value[field]);
  action.label = text(value.label);
  action.gas = text(value.gas);
  if (Array.isArray(value.args)) action.args = value.args.map(argToText);
  return action;
}

export function fromPlan(value: unknown): Draft {
  if (!isRecord(value)) throw new PlanImportError("plan must be a JSON object");
  if (value.version !== 1) throw new PlanImportError("only version 1 plans can be imported");
  const draft = emptyDraft();
  if (NETWORKS.some((network) => network.value === value.network)) draft.network = value.network as Network;
  draft.victim = text(value.victim);
  draft.safe = text(value.safe);
  draft.gas_wallet = text(value.gas_wallet);
  if (isRecord(value.options)) {
    if (value.options.priority_fee_gwei !== undefined) draft.priority_fee_gwei = text(value.options.priority_fee_gwei);
    if (value.options.max_blocks !== undefined) draft.max_blocks = text(value.options.max_blocks);
    if (typeof value.options.legacy_fallback === "boolean") draft.legacy_fallback = value.options.legacy_fallback;
  }
  draft.actions = Array.isArray(value.actions) ? value.actions.map(actionFromPlan) : [];
  return draft;
}

export type DraftEvent =
  | { kind: "set"; field: DraftField; value: string | boolean }
  | { kind: "add"; actionType: ActionType }
  | { kind: "update"; id: string; patch: Partial<Omit<DraftAction, "id" | "type">> }
  | { kind: "remove"; id: string }
  | { kind: "move"; id: string; offset: -1 | 1 }
  | { kind: "replace"; draft: Draft };

function moveAction(actions: DraftAction[], id: string, offset: number): DraftAction[] {
  const from = actions.findIndex((action) => action.id === id);
  const to = from + offset;
  if (from < 0 || to < 0 || to >= actions.length) return actions;
  const next = [...actions];
  [next[from], next[to]] = [next[to], next[from]];
  return next;
}

export function draftReducer(draft: Draft, event: DraftEvent): Draft {
  switch (event.kind) {
    case "set":
      return { ...draft, [event.field]: event.value };
    case "add":
      return { ...draft, actions: [...draft.actions, newAction(event.actionType)] };
    case "update":
      return {
        ...draft,
        actions: draft.actions.map((action) => (action.id === event.id ? { ...action, ...event.patch } : action)),
      };
    case "remove":
      return { ...draft, actions: draft.actions.filter((action) => action.id !== event.id) };
    case "move":
      return { ...draft, actions: moveAction(draft.actions, event.id, event.offset) };
    case "replace":
      return event.draft;
  }
}
