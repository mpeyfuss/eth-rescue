import { useState } from "react";
import { tryParseSignature } from "../plan/custom";
import {
  ACTION_SPECS,
  ACTION_TYPES,
  type ActionType,
  type DraftAction,
  type DraftEvent,
} from "../plan/draft";
import { TextField } from "./Field";

type ErrorFor = (path: string, value: string) => string | undefined;

interface Props {
  actions: DraftAction[];
  dispatch: (event: DraftEvent) => void;
  errorFor: ErrorFor;
}

const FIELD_LABELS = {
  contract: "Contract",
  amount: "Amount (base units)",
  token_id: "Token ID",
  auction_house: "Auction house",
  target: "Target contract",
  signature: "Function signature",
} as const;

const FIELD_HINTS: Partial<Record<keyof typeof FIELD_LABELS, string>> = {
  amount: "Smallest units, e.g. wei for an 18-decimal token: 1 token = 1000000000000000000.",
  signature: "e.g. transferPunk(address,uint256)",
};

const TYPE_NOTES: Partial<Record<ActionType, string>> = {
  ownership: "Calls transferOwnership(safe). The contract must be Ownable.",
  transient_delist_erc721: "Delists the token from the Transient auction house, then transfers it to the safe.",
  custom: "Called as the compromised wallet. Integers may be decimal or 0x; arrays, tuples and booleans are JSON.",
};

const ADDRESS_FIELDS = new Set(["contract", "auction_house", "target"]);
const GAS_HINT = "Minimum gas; the CLI raises it from a live estimate.";

export function ActionsSection({ actions, dispatch, errorFor }: Props) {
  const [nextType, setNextType] = useState<ActionType>("erc20");

  return (
    <section className="card">
      <h2>2. Assets to rescue</h2>
      <p className="muted">Actions run in this order. Destinations are always the safe wallet.</p>
      {actions.length === 0 && <p className="empty">No actions yet.</p>}
      <ol className="actions">
        {actions.map((action, index) => (
          <ActionCard
            key={action.id}
            action={action}
            index={index}
            count={actions.length}
            dispatch={dispatch}
            errorFor={errorFor}
          />
        ))}
      </ol>
      <div className="add-action">
        <select
          aria-label="Action type"
          value={nextType}
          onChange={(event) => setNextType(event.target.value as ActionType)}
        >
          {ACTION_TYPES.map((type) => (
            <option key={type} value={type}>
              {ACTION_SPECS[type].label}
            </option>
          ))}
        </select>
        <button type="button" onClick={() => dispatch({ kind: "add", actionType: nextType })}>
          Add action
        </button>
      </div>
    </section>
  );
}

interface CardProps {
  action: DraftAction;
  index: number;
  count: number;
  dispatch: (event: DraftEvent) => void;
  errorFor: ErrorFor;
}

function ActionCard({ action, index, count, dispatch, errorFor }: CardProps) {
  const spec = ACTION_SPECS[action.type];
  const prefix = `actions.${index}`;
  const update = (patch: Partial<Omit<DraftAction, "id" | "type">>) =>
    dispatch({ kind: "update", id: action.id, patch });

  return (
    <li className="action">
      <header>
        <strong>
          {index + 1}. {spec.label}
        </strong>
        <div className="action-controls">
          <button type="button" aria-label="Move up" disabled={index === 0} onClick={() => dispatch({ kind: "move", id: action.id, offset: -1 })}>
            ↑
          </button>
          <button type="button" aria-label="Move down" disabled={index === count - 1} onClick={() => dispatch({ kind: "move", id: action.id, offset: 1 })}>
            ↓
          </button>
          <button type="button" className="danger" onClick={() => dispatch({ kind: "remove", id: action.id })}>
            Remove
          </button>
        </div>
      </header>
      {TYPE_NOTES[action.type] && <p className="muted">{TYPE_NOTES[action.type]}</p>}
      {spec.fields.map((field) => (
        <TextField
          key={field}
          label={FIELD_LABELS[field]}
          value={action[field]}
          onChange={(value) => update(field === "signature" ? withSignature(action, value) : { [field]: value })}
          error={errorFor(`${prefix}.${field}`, action[field])}
          hint={FIELD_HINTS[field]}
          placeholder={ADDRESS_FIELDS.has(field) ? "0x…" : undefined}
          mono
        />
      ))}
      {action.type === "custom" && <CustomArgs action={action} update={update} error={errorFor(`${prefix}.args`, action.args.join(""))} />}
      <div className="action-row">
        {spec.gas !== "none" && (
          <TextField
            label={spec.gas === "required" ? "Gas limit" : "Gas limit (optional)"}
            value={action.gas}
            onChange={(gas) => update({ gas })}
            error={errorFor(`${prefix}.gas`, action.gas)}
            hint={GAS_HINT}
          />
        )}
        <TextField label="Label (optional)" value={action.label} onChange={(label) => update({ label })} />
      </div>
    </li>
  );
}

// Keep one arg slot per parameter while the signature is being typed.
function withSignature(action: DraftAction, signature: string): Partial<DraftAction> {
  const inputs = tryParseSignature(signature.trim())?.inputs;
  if (!inputs) return { signature };
  const args = inputs.map((_, index) => action.args[index] ?? "");
  return { signature, args };
}

interface CustomArgsProps {
  action: DraftAction;
  update: (patch: Partial<DraftAction>) => void;
  error?: string;
}

function CustomArgs({ action, update, error }: CustomArgsProps) {
  const inputs = tryParseSignature(action.signature.trim())?.inputs;
  if (!inputs) {
    return <p className="muted">Enter a valid function signature to fill in its arguments.</p>;
  }
  const setArg = (index: number, value: string) =>
    update({ args: action.args.map((arg, i) => (i === index ? value : arg)) });

  return (
    <fieldset className="args">
      <legend>Arguments</legend>
      {inputs.length === 0 && <p className="muted">This function takes no arguments.</p>}
      {inputs.map((param, index) => (
        <TextField
          key={index}
          label={`${param.name || `arg ${index + 1}`} (${param.type})`}
          value={action.args[index] ?? ""}
          onChange={(value) => setArg(index, value)}
          placeholder={placeholderFor(param.type)}
          mono
          blockSecrets={false}
        />
      ))}
      {error && <p className="field-error">{error}</p>}
    </fieldset>
  );
}

function placeholderFor(type: string): string | undefined {
  if (type.endsWith("]") || type.startsWith("tuple")) return '["…", "…"]';
  if (type === "address") return "0x…";
  if (type === "bool") return "true or false";
  if (type.startsWith("bytes")) return "0x…";
  if (/int/.test(type)) return "decimal or 0x";
  return undefined;
}
