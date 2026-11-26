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
import { ListOrdered } from "lucide-react";
import { type ReactNode, useEffect, useState } from "react";
import { BUILT_IN_KEY, loadUserKey } from "../discovery/key";
import type { Erc20Info, Erc721Info, Erc1155Info, OnchainReader, OwnershipInfo } from "../discovery/onchain";
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
import { formatTokenAmount, parseHumanAmount, shortAddress } from "../plan/format";
import type { Issue } from "../plan/validate";
import type { TokenMeta } from "../discovery/useAssetScan";
import { TextField } from "./Field";
import { ActionIcon, AddressBadge, TypeBadge, WhyTip } from "./Visual";

type ErrorFor = (path: string, value: string) => string | undefined;

interface Props {
  stepNumber: number;
  draft: Draft;
  dispatch: (event: DraftEvent) => void;
  errorFor: ErrorFor;
  issues: Issue[];
  tokenMeta: Record<string, TokenMeta>;
  onTokenMeta: (entries: Record<string, TokenMeta>) => void;
  onchain?: OnchainReader;
  scanPanel?: ReactNode;
  // Manual entry waits for the scan, so people pick from what was found first.
  scanPending?: boolean;
  onBack?: () => void;
  onContinue?: () => void;
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
  amount: "Smallest integer units (e.g. 18 decimals: 1 token = 1000000000000000000).",
  signature: "e.g. setApprovalForAll(address,bool)",
};

const ACTION_SUBTITLES: Record<ActionType, string> = {
  erc20: "Fungible tokens (USDC, WETH…)",
  erc721: "Single NFT by token ID",
  erc1155: "Multi-edition NFTs & items",
  ownership: "Transfer Ownable contract",
  transient_delist_erc721: "Cancel listing + rescue NFT",
  cryptopunk: "A punk by its number",
  superrare_v1: "An early SuperRare token",
  custom: "Any contract call (staking…)",
};

const TYPE_NOTES: Partial<Record<ActionType, string>> = {
  ownership: "Calls transferOwnership(destination). The contract must be Ownable.",
  transient_delist_erc721: "Delists the token from the Transient auction house, then transfers it to your destination wallet.",
  cryptopunk: "Calls transferPunk on the CryptoPunks contract, which also cancels any open sale offer.",
  superrare_v1: "Calls transfer on the original SuperRare contract, which has no transferFrom.",
  custom:
    "Called as the compromised wallet. Its arguments decide where assets go, not the destination wallet setting. Write the signature in canonical form (uint256, not uint; no names or spaces).",
};

const ADDRESS_FIELDS = new Set(["contract", "auction_house", "target"]);
const GAS_HINT = "Minimum gas; the CLI raises it from a live estimate.";

async function getReader(injected?: OnchainReader): Promise<OnchainReader> {
  if (injected) return injected;
  return (await import("../discovery/onchain")).defaultOnchainReader;
}

function actionName(action: DraftAction): string {
  const label = action.label.trim();
  return label ? `${ACTION_SPECS[action.type].label} · ${label}` : ACTION_SPECS[action.type].label;
}

export function ActionsSection({
  stepNumber,
  draft,
  dispatch,
  errorFor,
  issues,
  tokenMeta,
  onTokenMeta,
  onchain,
  scanPanel,
  scanPending = false,
  onBack,
  onContinue,
}: Props) {
  const { actions } = draft;
  // Manually created actions open immediately; discovered valid actions start collapsed.
  const [expandedIds, setExpandedIds] = useState<Set<string>>(() => new Set());

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

  function addManual(actionType: ActionType) {
    dispatch({ kind: "add", actionType });
    // Expand the newly added action on next render via effect below or track by count:
    setPendingExpand(true);
  }

  const [pendingExpand, setPendingExpand] = useState(false);
  useEffect(() => {
    if (!pendingExpand || actions.length === 0) return;
    const newest = actions[actions.length - 1];
    setExpandedIds((prev) => new Set(prev).add(newest.id));
    setPendingExpand(false);
  }, [pendingExpand, actions]);

  function toggleExpand(id: string) {
    setExpandedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <section className="card">
      <h2>{stepNumber}. Rescue plan</h2>

      {scanPanel}

      <div className="part-head plan-head">
        <div className="part-title">
          <ListOrdered size={18} aria-hidden="true" />
          <h3>Your plan ({actions.length})</h3>
        </div>
        {actions.length > 1 && <span className="muted">Runs top to bottom. Drag to reorder.</span>}
      </div>

      {actions.length === 0 ? (
        <p className="empty-state muted">Nothing in the plan yet.</p>
      ) : (
        <DndContext
          sensors={sensors}
          collisionDetection={closestCenter}
          onDragEnd={onDragEnd}
          accessibility={{ announcements }}
        >
          <SortableContext items={actions.map((action) => action.id)} strategy={verticalListSortingStrategy}>
            <ol className="actions">
              {actions.map((action, index) => {
                const actionPrefix = `actions.${index}`;
                const actionIssues = issues.filter(
                  (issue) => issue.path === actionPrefix || issue.path.startsWith(`${actionPrefix}.`),
                );
                const isExpanded = expandedIds.has(action.id) || (!action.discoveredFor && actionIssues.length > 0);
                return (
                  <ActionCard
                    key={action.id}
                    action={action}
                    index={index}
                    count={actions.length}
                    expanded={isExpanded}
                    onToggleExpand={() => toggleExpand(action.id)}
                    dispatch={dispatch}
                    errorFor={errorFor}
                    issueCount={actionIssues.length}
                    network={draft.network}
                    compromised={draft.compromised}
                    destination={draft.destination}
                    tokenMeta={tokenMeta}
                    onTokenMeta={onTokenMeta}
                    onchain={onchain}
                  />
                );
              })}
            </ol>
          </SortableContext>
        </DndContext>
      )}

      <div className="picker-block">
        <div className="picker-head">
          <strong>Add by hand</strong>
          {scanPending && <span className="muted">Available once the scan finishes.</span>}
        </div>
        {!scanPending && (
        <div className="action-picker-grid">
          {ACTION_TYPES.map((type) => (
            <button key={type} type="button" className="action-tile" onClick={() => addManual(type)}>
              <span className="action-tile-icon">
                <ActionIcon type={type} />
              </span>
              <span className="action-tile-text">
                <strong>{ACTION_SPECS[type].label}</strong>
                <span>{ACTION_SUBTITLES[type]}</span>
              </span>
            </button>
          ))}
        </div>
        )}
      </div>

      {(onBack || onContinue) && (
        <div className="step-footer">
          <div>
            {onBack && (
              <button type="button" onClick={onBack}>
                ← Back to wallets
              </button>
            )}
          </div>
          {onContinue && (
            <button type="button" className={actions.length > 0 ? "primary" : undefined} onClick={onContinue}>
              Next: Review →
            </button>
          )}
        </div>
      )}
    </section>
  );
}

interface CardProps {
  network: Network;
  compromised: string;
  destination: string;
  action: DraftAction;
  index: number;
  count: number;
  expanded: boolean;
  onToggleExpand: () => void;
  dispatch: (event: DraftEvent) => void;
  errorFor: ErrorFor;
  issueCount: number;
  tokenMeta: Record<string, TokenMeta>;
  onTokenMeta: (entries: Record<string, TokenMeta>) => void;
  onchain?: OnchainReader;
}

function ActionCard({
  action,
  index,
  count,
  expanded,
  onToggleExpand,
  dispatch,
  errorFor,
  issueCount,
  network,
  compromised,
  destination,
  tokenMeta,
  onTokenMeta,
  onchain,
}: CardProps) {
  const spec = ACTION_SPECS[action.type];
  const prefix = `actions.${index}`;
  const update = (patch: Partial<Omit<DraftAction, "id" | "type">>) =>
    dispatch({ kind: "update", id: action.id, patch });
  const { attributes, listeners, setNodeRef, setActivatorNodeRef, transform, transition, isDragging } = useSortable({
    id: action.id,
  });
  const style = { transform: CSS.Translate.toString(transform), transition };

  const meta = action.contract ? tokenMeta[action.contract.trim().toLowerCase()] : undefined;
  const formattedErc20 =
    action.type === "erc20" && meta?.decimals != null ? formatTokenAmount(action.amount, meta.decimals) : null;

  const contractAddr = action.contract.trim() || action.target.trim();
  const titleText =
    action.label.trim() ||
    (action.type === "erc20" && meta?.symbol) ||
    (action.type === "custom" && action.signature.trim()) ||
    ACTION_SPECS[action.type].label;

  return (
    <li
      ref={setNodeRef}
      style={style}
      className={`action ${expanded ? "expanded" : "compact"} ${isDragging ? "dragging" : ""} ${issueCount > 0 ? "has-issue" : ""}`}
    >
      <header className="action-header">
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
          <span className="action-idx">{index + 1}</span>
          <TypeBadge type={action.type} />
          <div className="action-headline">
            <strong>{titleText}</strong>
            {!expanded && (
              <div className="action-summary-meta">
                {action.type === "erc20" && action.amount && (
                  <span className="summary-chip mono">
                    {formattedErc20
                      ? `${formattedErc20}${meta?.symbol ? ` ${meta.symbol}` : ""}`
                      : `${action.amount} base units`}
                  </span>
                )}
                {(action.type === "erc721" || action.type === "transient_delist_erc721") && action.token_id && (
                  <span className="summary-chip mono">#{action.token_id}</span>
                )}
                {action.type === "erc1155" && (
                  <span className="summary-chip mono">
                    {action.token_id ? `#${action.token_id}` : ""} {action.amount ? `× ${action.amount}` : ""}
                  </span>
                )}
                {action.type === "ownership" && <span className="summary-chip">transferOwnership → Destination</span>}
                {action.type === "custom" && <span className="summary-chip warn">Custom destination</span>}
                {contractAddr && (
                  <AddressBadge
                    address={contractAddr}
                    network={network}
                    tokenId={action.token_id.trim() || undefined}
                    link
                  />
                )}
                {issueCount > 0 && (
                  <span className="summary-chip err">
                    {issueCount} {issueCount === 1 ? "issue" : "issues"}
                  </span>
                )}
              </div>
            )}
          </div>
        </div>
        <div className="action-controls">
          <button type="button" onClick={onToggleExpand} aria-expanded={expanded}>
            {expanded ? "Done" : "Edit"}
          </button>
          <button
            type="button"
            aria-label="Move up"
            disabled={index === 0}
            onClick={() => dispatch({ kind: "move", id: action.id, offset: -1 })}
          >
            ↑
          </button>
          <button
            type="button"
            aria-label="Move down"
            disabled={index === count - 1}
            onClick={() => dispatch({ kind: "move", id: action.id, offset: 1 })}
          >
            ↓
          </button>
          <button type="button" className="danger" onClick={() => dispatch({ kind: "remove", id: action.id })}>
            Remove
          </button>
        </div>
      </header>

      {expanded && (
        <div className="action-editor">
          {TYPE_NOTES[action.type] && <p className="muted">{TYPE_NOTES[action.type]}</p>}
          {errorFor(prefix, "x") && <p className="field-error">This action {errorFor(prefix, "x")}.</p>}

          {spec.fields.map((field) => {
            if (action.type === "erc20" && field === "amount") {
              return (
                <Erc20AmountField
                  key="amount"
                  action={action}
                  network={network}
                  compromised={compromised}
                  meta={meta}
                  error={errorFor(`${prefix}.amount`, action.amount)}
                  onChangeAmount={(amount) => update({ amount })}
                  onAutoFill={(patch) => update(patch)}
                  onTokenMeta={onTokenMeta}
                  onchain={onchain}
                />
              );
            }
            return (
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
            );
          })}

          {action.type === "erc721" && (
            <Erc721Assistant
              network={network}
              compromised={compromised}
              contract={action.contract.trim()}
              tokenId={action.token_id.trim()}
              hasLabel={Boolean(action.label.trim())}
              onAutoLabel={(label) => update({ label })}
              onchain={onchain}
            />
          )}

          {action.type === "erc1155" && (
            <Erc1155Assistant
              network={network}
              compromised={compromised}
              contract={action.contract.trim()}
              tokenId={action.token_id.trim()}
              onUseBalance={(amount) => update({ amount })}
              onchain={onchain}
            />
          )}

          {action.type === "ownership" && (
            <OwnerCheck
              key={`${network}:${action.contract}`}
              network={network}
              compromised={compromised}
              contract={action.contract.trim()}
              hasLabel={Boolean(action.label.trim())}
              onAutoLabel={(label) => update({ label })}
              onchain={onchain}
            />
          )}

          {action.type === "custom" && (
            <CustomArgs
              action={action}
              destination={destination}
              update={update}
              error={errorFor(`${prefix}.args`, action.args.join(""))}
            />
          )}

          <div className="action-row">
            <TextField label="Label (optional)" value={action.label} onChange={(label) => update({ label })} />
            {spec.gas === "required" && (
              <TextField
                label="Gas limit"
                value={action.gas}
                onChange={(gas) => update({ gas })}
                error={errorFor(`${prefix}.gas`, action.gas)}
                hint={GAS_HINT}
              />
            )}
          </div>

          {spec.gas === "optional" && (
            <details className="inline-details" open={Boolean(action.gas.trim())}>
              <summary>Advanced gas limit (optional)</summary>
              <TextField
                label="Gas limit (optional)"
                value={action.gas}
                onChange={(gas) => update({ gas })}
                error={errorFor(`${prefix}.gas`, action.gas)}
                hint={GAS_HINT}
              />
            </details>
          )}
        </div>
      )}
    </li>
  );
}

function Erc20AmountField({
  action,
  network,
  compromised,
  meta,
  error,
  onChangeAmount,
  onAutoFill,
  onTokenMeta,
  onchain,
}: {
  action: DraftAction;
  network: Network;
  compromised: string;
  meta?: TokenMeta;
  error?: string;
  onChangeAmount: (amount: string) => void;
  onAutoFill: (patch: Partial<DraftAction>) => void;
  onTokenMeta: (entries: Record<string, TokenMeta>) => void;
  onchain?: OnchainReader;
}) {
  const contract = action.contract.trim();
  const compromisedAddr = compromised.trim();
  const contractValid = !addressProblem(contract);
  const compromisedValid = !addressProblem(compromisedAddr);

  const [info, setInfo] = useState<Erc20Info | null>(null);
  const [loading, setLoading] = useState(false);
  const [manualDecimals, setManualDecimals] = useState<number | null>(null);
  const [rawMode, setRawMode] = useState(false);
  const [humanDraft, setHumanDraft] = useState<string | null>(null);

  const decimals = info?.decimals ?? meta?.decimals ?? manualDecimals;
  const symbol = info?.symbol ?? meta?.symbol ?? null;
  const tokenName = info?.name ?? meta?.name ?? null;

  useEffect(() => {
    if (!contractValid) {
      setInfo(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const reader = await getReader(onchain);
        const result = await reader.readErc20Info(
          network,
          loadUserKey() || BUILT_IN_KEY,
          contract,
          compromisedValid ? compromisedAddr : undefined,
        );
        if (cancelled) return;
        setInfo(result);
        if (result.decimals !== null || result.symbol || result.name) {
          onTokenMeta({
            [contract.toLowerCase()]: {
              decimals: result.decimals ?? undefined,
              symbol: result.symbol ?? undefined,
              name: result.name ?? undefined,
            },
          });
        }
        if (!action.label.trim() && result.symbol) {
          onAutoFill({ label: result.symbol });
        }
      } catch {
        // Gracefully stay in manual mode on RPC error.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, onchain ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [network, contract, contractValid, compromisedAddr, compromisedValid, onchain]);

  const balanceFormatted =
    info?.balance != null
      ? decimals != null
        ? `${formatTokenAmount(info.balance.toString(), decimals)}${symbol ? ` ${symbol}` : ""}`
        : `${info.balance.toString()} base units`
      : null;

  const displayedHuman =
    humanDraft !== null
      ? humanDraft
      : decimals != null && action.amount
        ? (formatTokenAmount(action.amount, decimals)?.replace(/,/g, "") ?? "")
        : "";

  function handleHumanChange(next: string) {
    setHumanDraft(next);
    if (decimals == null) return;
    if (!next.trim()) {
      onChangeAmount("");
      return;
    }
    const parsed = parseHumanAmount(next, decimals);
    if (parsed !== null) onChangeAmount(parsed);
  }

  function useMaxBalance() {
    if (info?.balance == null || info.balance <= 0n) return;
    setHumanDraft(null);
    onChangeAmount(info.balance.toString());
  }

  return (
    <div className="erc20-assistant">
      {loading && <p className="onchain-pill loading">Reading token decimals &amp; wallet balance on-chain…</p>}
      {!loading && (tokenName || symbol || balanceFormatted) && (
        <div className="onchain-token-bar">
          <div className="onchain-token-info">
            <strong>{tokenName ? `${tokenName}${symbol ? ` (${symbol})` : ""}` : symbol || "ERC-20 token"}</strong>
            {decimals != null && <span className="meta-chip">{decimals} decimals</span>}
            {info?.balance != null && (
              <span className={info.balance > 0n ? "onchain-balance ok" : "onchain-balance warn"}>
                Wallet holds {balanceFormatted}
              </span>
            )}
          </div>
          {info?.balance != null && info.balance > 0n && (
            <button type="button" className="use-max-btn" onClick={useMaxBalance}>
              Use full balance
            </button>
          )}
        </div>
      )}

      {decimals != null && !rawMode ? (
        <div className="field">
          <div className="field-top-row">
            <label htmlFor={`human-${action.id}`}>Amount ({symbol ?? "tokens"})</label>
            <button
              type="button"
              className="link small-link"
              onClick={() => {
                setHumanDraft(null);
                setRawMode(true);
              }}
            >
              Edit raw base units
            </button>
          </div>
          <input
            id={`human-${action.id}`}
            className="mono"
            value={displayedHuman}
            placeholder="e.g. 125.5"
            spellCheck={false}
            autoComplete="off"
            aria-invalid={Boolean(error)}
            onChange={(e) => handleHumanChange(e.target.value)}
            onBlur={() => setHumanDraft(null)}
          />
          {humanDraft !== null && humanDraft.trim() && parseHumanAmount(humanDraft, decimals) === null && (
            <p className="field-error">Enter a positive number with at most {decimals} decimal places.</p>
          )}
          {error && <p className="field-error">{error}</p>}
          <p className="field-hint">
            Automatically converted using {decimals} decimals
            {action.amount ? (
              <>
                {" "}
                → <code className="mono">{action.amount}</code> base units in <code>plan.json</code>
              </>
            ) : (
              "."
            )}
          </p>
        </div>
      ) : (
        <div>
          <div className="field-label-aux">
            {decimals != null ? (
              <button
                type="button"
                className="link small-link"
                onClick={() => {
                  setHumanDraft(null);
                  setRawMode(false);
                }}
              >
                Switch to {symbol ?? "token"} units ({decimals} decimals)
              </button>
            ) : (
              <span className="decimals-quick">
                Convert from token units:{" "}
                {[18, 6, 8].map((d) => (
                  <button
                    key={d}
                    type="button"
                    className="link small-link"
                    onClick={() => {
                      setManualDecimals(d);
                      setRawMode(false);
                    }}
                  >
                    {d} dec
                  </button>
                ))}
              </span>
            )}
          </div>
          <TextField
            label="Amount (base units)"
            value={action.amount}
            onChange={(value) => {
              setHumanDraft(null);
              onChangeAmount(value);
            }}
            error={error}
            hint={
              decimals != null && action.amount && formatTokenAmount(action.amount, decimals)
                ? `= ${formatTokenAmount(action.amount, decimals)}${symbol ? ` ${symbol}` : ""}`
                : FIELD_HINTS.amount
            }
            mono
          />
        </div>
      )}
    </div>
  );
}

function Erc721Assistant({
  network,
  compromised,
  contract,
  tokenId,
  hasLabel,
  onAutoLabel,
  onchain,
}: {
  network: Network;
  compromised: string;
  contract: string;
  tokenId: string;
  hasLabel: boolean;
  onAutoLabel: (label: string) => void;
  onchain?: OnchainReader;
}) {
  const [info, setInfo] = useState<Erc721Info | null>(null);
  const [loading, setLoading] = useState(false);
  const validContract = !addressProblem(contract);

  useEffect(() => {
    if (!validContract) {
      setInfo(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const reader = await getReader(onchain);
        const result = await reader.readErc721Info(network, loadUserKey() || BUILT_IN_KEY, contract, tokenId);
        if (cancelled) return;
        setInfo(result);
        if (!hasLabel && result.name) {
          onAutoLabel(tokenId ? `${result.name} #${tokenId}` : result.name);
        }
      } catch {
        // Ignore transient RPC errors.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, onchain ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [network, contract, tokenId, validContract, onchain]);

  if (!validContract) return null;
  if (loading) return <p className="onchain-pill loading">Checking NFT collection &amp; owner on-chain…</p>;
  if (!info) return null;

  const ownerMatches = info.owner && !addressProblem(compromised.trim()) && sameAddress(info.owner, compromised.trim());
  const ownerMismatch = info.owner && !addressProblem(compromised.trim()) && !sameAddress(info.owner, compromised.trim());

  return (
    <div className="onchain-status-group">
      {info.name && (
        <p className="onchain-pill">
          <strong>Collection:</strong> {info.name}
          {info.symbol ? ` (${info.symbol})` : ""}
        </p>
      )}
      {ownerMatches && <p className="onchain-pill ok">Owned by the compromised wallet.</p>}
      {ownerMismatch && (
        <p className="onchain-pill danger" role="status">
          Owned by {shortAddress(info.owner!)} (not the compromised wallet), so transferring this token would fail.
        </p>
      )}
    </div>
  );
}

function Erc1155Assistant({
  network,
  compromised,
  contract,
  tokenId,
  onUseBalance,
  onchain,
}: {
  network: Network;
  compromised: string;
  contract: string;
  tokenId: string;
  onUseBalance: (amount: string) => void;
  onchain?: OnchainReader;
}) {
  const [info, setInfo] = useState<Erc1155Info | null>(null);
  const [loading, setLoading] = useState(false);
  const ready = !addressProblem(contract) && !addressProblem(compromised.trim()) && /^\d+$/.test(tokenId);

  useEffect(() => {
    if (!ready) {
      setInfo(null);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setLoading(true);
      try {
        const reader = await getReader(onchain);
        const result = await reader.readErc1155Info(network, loadUserKey() || BUILT_IN_KEY, contract, tokenId, compromised.trim());
        if (!cancelled) setInfo(result);
      } catch {
        // Ignore transient RPC errors.
      } finally {
        if (!cancelled) setLoading(false);
      }
    }, onchain ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [network, contract, tokenId, compromised, ready, onchain]);

  if (!ready) return null;
  if (loading) return <p className="onchain-pill loading">Checking ERC-1155 balance on-chain…</p>;
  if (!info || info.balance === null) return null;

  return (
    <div className="onchain-token-bar">
      <div className="onchain-token-info">
        <span className={info.balance > 0n ? "onchain-balance ok" : "onchain-balance warn"}>
          {info.balance > 0n
            ? `Compromised wallet holds ${info.balance.toString()} of token #${tokenId}`
            : `The compromised wallet holds none of token #${tokenId}, so the transfer would fail`}
        </span>
      </div>
      {info.balance > 0n && (
        <button type="button" className="use-max-btn" onClick={() => onUseBalance(info.balance!.toString())}>
          Use full balance ({info.balance.toString()})
        </button>
      )}
    </div>
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
  destination: string;
  update: (patch: Partial<DraftAction>) => void;
  error?: string;
}

function CustomArgs({ action, destination, update, error }: CustomArgsProps) {
  const signature = action.signature.trim();
  let inputs;
  try {
    inputs = parseSignature(signature).inputs;
  } catch (err) {
    if (!signature) return <p className="muted">Enter a function signature to fill in its arguments.</p>;
    return <p className="field-error">{err instanceof Error ? err.message : String(err)}</p>;
  }
  const setArg = (index: number, value: string) =>
    update({ args: inputs.map((_, i) => (i === index ? value : (action.args[i] ?? ""))) });
  const destinationReady = Boolean(destination.trim()) && !addressProblem(destination.trim());

  return (
    <fieldset className="args">
      <legend>
        Arguments{" "}
        <WhyTip label="Custom call safety">
          <p>
            The CLI calls this exact function as your compromised wallet. These arguments, not the destination wallet
            setting, decide where assets go, so check every address argument.
          </p>
        </WhyTip>
      </legend>
      {inputs.length === 0 && <p className="muted">This function takes no arguments.</p>}
      {inputs.map((param, index) => (
        <div key={index}>
          {param.type === "address" && destinationReady && (
            <div className="field-label-aux">
              <button type="button" className="link small-link" onClick={() => setArg(index, destination.trim())}>
                Use destination wallet ({shortAddress(destination)})
              </button>
            </div>
          )}
          <TextField
            label={`${param.name || `arg ${index + 1}`} (${param.type})`}
            value={action.args[index] ?? ""}
            onChange={(value) => setArg(index, value)}
            placeholder={placeholderFor(param.type)}
            mono
            isSecret={secretCheckFor(param.type)}
          />
        </div>
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

type OwnerStatus = { kind: "idle" } | { kind: "checking" } | { kind: "done"; info: OwnershipInfo };

function OwnerCheck({
  network,
  compromised,
  contract,
  hasLabel,
  onAutoLabel,
  onchain,
}: {
  network: Network;
  compromised: string;
  contract: string;
  hasLabel: boolean;
  onAutoLabel: (label: string) => void;
  onchain?: OnchainReader;
}) {
  const [status, setStatus] = useState<OwnerStatus>({ kind: "idle" });
  const ready = !addressProblem(contract) && !addressProblem(compromised.trim());

  async function runCheck() {
    setStatus({ kind: "checking" });
    try {
      const reader = await getReader(onchain);
      const info = await reader.readOwnershipInfo(network, loadUserKey() || BUILT_IN_KEY, contract);
      setStatus({ kind: "done", info });
      if (!hasLabel && info.name) onAutoLabel(info.name);
    } catch {
      setStatus({ kind: "done", info: { owner: null, pendingOwner: null, name: null } });
    }
  }

  useEffect(() => {
    if (!ready) {
      setStatus({ kind: "idle" });
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setStatus({ kind: "checking" });
      try {
        const reader = await getReader(onchain);
        const info = await reader.readOwnershipInfo(network, loadUserKey() || BUILT_IN_KEY, contract);
        if (cancelled) return;
        setStatus({ kind: "done", info });
        if (!hasLabel && info.name) onAutoLabel(info.name);
      } catch {
        if (!cancelled) setStatus({ kind: "done", info: { owner: null, pendingOwner: null, name: null } });
      }
    }, onchain ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [network, contract, compromised, ready, onchain]);

  return (
    <div className="owner-check">
      <button type="button" disabled={!ready || status.kind === "checking"} onClick={runCheck}>
        {status.kind === "checking" ? "Checking…" : status.kind === "done" ? "Re-check owner" : "Check owner on-chain"}
      </button>
      {status.kind === "done" && <OwnerResult info={status.info} compromised={compromised} />}
    </div>
  );
}

function OwnerResult({ info, compromised }: { info: OwnershipInfo; compromised: string }) {
  if (info.owner === null) {
    return <p className="field-error">Could not read owner(). The contract may not be Ownable.</p>;
  }
  if (sameAddress(info.owner, compromised.trim())) {
    return (
      <div className="onchain-status-group">
        <p className="field-ok">
          Owned by the compromised wallet{info.name ? ` (${info.name})` : ""}.
        </p>
        {info.pendingOwner !== null && (
          <p className="onchain-pill warn">
            <strong>Ownable2Step detected:</strong> After the rescue lands, call <code>acceptOwnership()</code> from your
            destination wallet to finish the transfer.
          </p>
        )}
      </div>
    );
  }
  return <p className="field-error">Owned by {info.owner}, not the compromised wallet, so this action would fail.</p>;
}
