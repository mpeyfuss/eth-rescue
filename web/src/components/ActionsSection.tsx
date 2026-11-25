import {
  type Announcements,
  closestCenter,
  DndContext,
  type DragEndEvent,
  KeyboardSensor,
  PointerSensor,
  useSensor,
  useSensors,
} from "@dnd-kit/core";
import {
  SortableContext,
  sortableKeyboardCoordinates,
  useSortable,
  verticalListSortingStrategy,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import { useState } from "react";
import { BUILT_IN_KEY, loadUserKey } from "../discovery/key";
import { addressProblem, looksLikeMnemonic, looksLikeSecret, sameAddress } from "../plan/address";
import { parseSignature, tryParseSignature } from "../plan/custom";
import {
  ACTION_SPECS,
  ACTION_TYPES,
  type ActionType,
  type Draft,
  type DraftAction,
  type DraftEvent,
  type Network,
} from "../plan/draft";
import { TextField } from "./Field";

type ErrorFor = (path: string, value: string) => string | undefined;

interface Props {
  draft: Draft;
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
  custom:
    "Called as the compromised wallet. Its arguments, not the safe setting, decide where anything goes. The signature must be canonical (uint256, not uint; no names or spaces). Integers may be decimal or 0x; arrays, tuples and booleans are JSON, with large integers quoted.",
};

const ADDRESS_FIELDS = new Set(["contract", "auction_house", "target"]);
const GAS_HINT = "Minimum gas; the CLI raises it from a live estimate.";

function actionName(action: DraftAction): string {
  const label = action.label.trim();
  return label ? `${ACTION_SPECS[action.type].label} · ${label}` : ACTION_SPECS[action.type].label;
}

export function ActionsSection({ draft, dispatch, errorFor }: Props) {
  const [nextType, setNextType] = useState<ActionType>("erc20");
  const { actions } = draft;
  const sensors = useSensors(
    useSensor(PointerSensor),
    useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }),
  );

  const position = (id: string | number) => actions.findIndex((action) => action.id === id) + 1;
  const name = (id: string | number) => {
    const action = actions.find((candidate) => candidate.id === id);
    return action ? actionName(action) : "action";
  };
  const announcements: Announcements = {
    onDragStart: ({ active }) => `Picked up ${name(active.id)} at position ${position(active.id)}.`,
    onDragOver: ({ active, over }) => (over ? `${name(active.id)} is over position ${position(over.id)}.` : undefined),
    onDragEnd: ({ active, over }) =>
      over ? `${name(active.id)} dropped at position ${position(over.id)}.` : `${name(active.id)} dropped.`,
    onDragCancel: ({ active }) => `Moving ${name(active.id)} was cancelled.`,
  };

  function onDragEnd({ active, over }: DragEndEvent) {
    if (over && active.id !== over.id) dispatch({ kind: "reorder", id: String(active.id), overId: String(over.id) });
  }

  return (
    <section className="card">
      <h2>3. Assets to rescue</h2>
      <p className="muted">
        Actions run in this order. Typed actions send to the safe wallet; a custom call does exactly what its target
        and arguments say, so check where it sends anything.
      </p>
      {actions.length === 0 && <p className="empty">No actions yet.</p>}
      <DndContext
        sensors={sensors}
        collisionDetection={closestCenter}
        onDragEnd={onDragEnd}
        accessibility={{ announcements }}
      >
        <SortableContext items={actions.map((action) => action.id)} strategy={verticalListSortingStrategy}>
          <ol className="actions">
            {actions.map((action, index) => (
              <ActionCard
                key={action.id}
                action={action}
                index={index}
                count={actions.length}
                dispatch={dispatch}
                errorFor={errorFor}
                network={draft.network}
                victim={draft.victim}
              />
            ))}
          </ol>
        </SortableContext>
      </DndContext>
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
  network: Network;
  victim: string;
  action: DraftAction;
  index: number;
  count: number;
  dispatch: (event: DraftEvent) => void;
  errorFor: ErrorFor;
}

function ActionCard({ action, index, count, dispatch, errorFor, network, victim }: CardProps) {
  const spec = ACTION_SPECS[action.type];
  const prefix = `actions.${index}`;
  const update = (patch: Partial<Omit<DraftAction, "id" | "type">>) =>
    dispatch({ kind: "update", id: action.id, patch });
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: action.id,
  });
  const style = { transform: CSS.Translate.toString(transform), transition };

  return (
    <li ref={setNodeRef} style={style} className={isDragging ? "action dragging" : "action"}>
      <header>
        <div className="action-title">
          <button
            type="button"
            ref={setActivatorNodeRef}
            className="drag-handle"
            aria-label={`Drag to reorder ${actionName(action)}`}
            {...attributes}
            {...listeners}
          >
            ⠿
          </button>
          <strong>
            {index + 1}. {actionName(action)}
          </strong>
        </div>
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
      {errorFor(prefix, "x") && <p className="field-error">This action {errorFor(prefix, "x")}.</p>}
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
      {action.type === "ownership" && (
        <OwnerCheck key={`${network}:${action.contract}`} network={network} victim={victim} contract={action.contract.trim()} />
      )}
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
  const signature = action.signature.trim();
  let inputs;
  try {
    inputs = parseSignature(signature).inputs;
  } catch (error) {
    if (!signature) return <p className="muted">Enter a function signature to fill in its arguments.</p>;
    return <p className="field-error">{error instanceof Error ? error.message : String(error)}</p>;
  }
  const setArg = (index: number, value: string) =>
    update({ args: inputs.map((_, i) => (i === index ? value : (action.args[i] ?? ""))) });

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
          isSecret={secretCheckFor(param.type)}
        />
      ))}
      {error && <p className="field-error">{error}</p>}
    </fieldset>
  );
}

// bytes32 values look exactly like private keys, so bytes arguments only block
// seed phrases. Custom arguments are never saved in this browser either way.
function secretCheckFor(type: string) {
  return /^bytes|^tuple|\]$/.test(type) ? looksLikeMnemonic : looksLikeSecret;
}

function placeholderFor(type: string): string | undefined {
  if (type.endsWith("]") || type.startsWith("tuple")) return '["…", "…"]';
  if (type === "address") return "0x…";
  if (type === "bool") return "true or false";
  if (type.startsWith("bytes")) return "0x…";
  if (/int/.test(type)) return "decimal or 0x";
  return undefined;
}

type OwnerStatus = { kind: "idle" } | { kind: "checking" } | { kind: "done"; owner: string | null };

function OwnerCheck({ network, victim, contract }: { network: Network; victim: string; contract: string }) {
  const [status, setStatus] = useState<OwnerStatus>({ kind: "idle" });
  const ready = !addressProblem(contract) && !addressProblem(victim.trim());

  async function check() {
    setStatus({ kind: "checking" });
    try {
      const { readOwners } = await import("../discovery/onchain");
      const owners = await readOwners(network, loadUserKey() || BUILT_IN_KEY, [contract]);
      setStatus({ kind: "done", owner: owners.get(contract.toLowerCase()) ?? null });
    } catch {
      setStatus({ kind: "done", owner: null });
    }
  }

  return (
    <div className="owner-check">
      <button type="button" disabled={!ready || status.kind === "checking"} onClick={check}>
        {status.kind === "checking" ? "Checking…" : "Check owner on-chain"}
      </button>
      {status.kind === "done" && <OwnerResult owner={status.owner} victim={victim} />}
    </div>
  );
}

function OwnerResult({ owner, victim }: { owner: string | null; victim: string }) {
  if (owner === null) return <p className="field-error">Could not read owner(). The contract may not be Ownable.</p>;
  if (sameAddress(owner, victim.trim())) return <p className="field-ok">Owned by the compromised wallet.</p>;
  return <p className="field-error">Owned by {owner}, not the compromised wallet, so this action would fail.</p>;
}
