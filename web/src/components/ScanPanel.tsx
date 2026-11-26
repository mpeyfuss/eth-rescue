import { RefreshCw, ScanSearch } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { WalletSnapshot } from "../discovery/onchain";
import type { Candidate, CandidateGroup, ScanResult } from "../discovery/types";
import type { ScanStatus } from "../discovery/useAssetScan";
import { actionFromPlan, actionKey, actionToPlan, type Draft, type DraftEvent, type Network } from "../plan/draft";
import { AddressBadge, TypeBadge } from "./Visual";

interface Props {
  draft: Draft;
  dispatch: (event: DraftEvent) => void;
  status: ScanStatus;
  scope: string;
  addressReady: boolean;
  alchemyKey: string | null;
  compromisedSnapshot?: WalletSnapshot | "loading";
  onScan: () => void;
  onOpenSettings?: () => void;
}

const GROUPS: { group: CandidateGroup; label: string; short: string }[] = [
  { group: "listings", label: "Listed on Transient", short: "Listings" },
  { group: "contracts", label: "Contracts created on Transient", short: "Contracts" },
  { group: "nfts", label: "NFTs", short: "NFTs" },
  { group: "tokens", label: "ERC-20 tokens", short: "Tokens" },
];

export function ScanPanel({
  draft,
  dispatch,
  status,
  scope,
  addressReady,
  alchemyKey,
  compromisedSnapshot,
  onScan,
  onOpenSettings,
}: Props) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState<CandidateGroup | "all">("all");
  const [query, setQuery] = useState("");
  const [showSpam, setShowSpam] = useState(false);
  const [justAdded, setJustAdded] = useState<number>(0);
  const result = status.kind === "done" ? status.result : null;

  useEffect(() => {
    setSelected(new Set(result?.candidates.filter((c) => c.preselect).map((c) => c.key) ?? []));
    setActiveTab("all");
    setQuery("");
    setShowSpam(false);
    setJustAdded(0);
  }, [result]);

  const inPlan = useMemo(
    () => new Set(draft.actions.map((action) => actionKey(actionToPlan(action)))),
    [draft.actions],
  );
  const selectable = (candidate: Candidate) => !candidate.blockedReason && !inPlan.has(candidate.key);
  const chosen = result ? result.candidates.filter((c) => selected.has(c.key) && selectable(c)) : [];

  function toggle(keys: string[], on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const key of keys) {
        if (on) next.add(key);
        else next.delete(key);
      }
      return next;
    });
  }

  function addSelected() {
    const actions = chosen.map((candidate) => ({ ...actionFromPlan(candidate.action), discoveredFor: scope }));
    dispatch({ kind: "append", actions });
    setJustAdded(actions.length);
    toggle(
      chosen.map((candidate) => candidate.key),
      false,
    );
  }

  const chosenBreakdown = useMemo(() => {
    const counts = new Map<string, number>();
    for (const c of chosen) {
      const label = GROUPS.find((g) => g.group === c.group)?.short ?? c.group;
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([label, n]) => `${n} ${label.toLowerCase()}`)
      .join(", ");
  }, [chosen]);

  const snap = compromisedSnapshot && compromisedSnapshot !== "loading" ? compromisedSnapshot : null;
  const compromisedAddr = draft.compromised.trim();

  const title = (
    <div className="part-title">
      <ScanSearch size={18} aria-hidden="true" />
      <h3>Found in the compromised wallet</h3>
    </div>
  );

  if (!addressReady) {
    return (
      <section className="scan-panel" aria-label="Found in the compromised wallet">
        <div className="part-head">{title}</div>
        <p className="muted">Enter the compromised wallet on the Wallets step, and it will be scanned here.</p>
      </section>
    );
  }

  return (
    <section className="scan-panel" aria-label="Found in the compromised wallet">
      <div className="part-head">
        {title}
        {(status.kind === "done" || status.kind === "failed") && (
          <button type="button" className="scan-again" onClick={onScan}>
            <RefreshCw size={14} aria-hidden="true" /> Scan again
          </button>
        )}
      </div>
      <div className="scan-target-row">
        <AddressBadge address={compromisedAddr} network={draft.network} link />
        {snap && (
          <span className="eth-sweep-pill">
            {snap.ethFormatted} {snap.ethBalance > 0n ? "· swept at the end" : ""}
          </span>
        )}
      </div>

      {!alchemyKey && (
        <p className="field-hint">
          Token and NFT lookup needs an Alchemy API key. <SettingsLink onOpen={onOpenSettings} />
        </p>
      )}

      {(status.kind === "scanning" || status.kind === "idle") && (
        <div className="scan-progress" role="status">
          <span className="scan-spinner" aria-hidden="true" />
          <div>
            <strong>Scanning…</strong>
            <span className="muted">Tokens, NFTs, CryptoPunks, SuperRare V1, and Transient listings and contracts</span>
          </div>
        </div>
      )}

      {status.kind === "failed" && (
        <p className="banner error" role="alert">
          The scan failed: {status.message}. Try again
          {onOpenSettings ? (
            <>
              , or <SettingsLink onOpen={onOpenSettings} lower />
            </>
          ) : (
            "."
          )}
        </p>
      )}

      {justAdded > 0 && (
        <p className="banner ok-banner" role="status">
          Added <strong>{justAdded}</strong> {justAdded === 1 ? "asset" : "assets"} to the plan below.
        </p>
      )}

      {result && (
        <ScanResults
          network={draft.network}
          result={result}
          onOpenSettings={onOpenSettings}
          selected={selected}
          inPlan={inPlan}
          selectable={selectable}
          toggle={toggle}
          activeTab={activeTab}
          setActiveTab={setActiveTab}
          query={query}
          setQuery={setQuery}
          showSpam={showSpam}
          setShowSpam={setShowSpam}
        />
      )}

      {chosen.length > 0 && (
        <div className="sticky-add-bar">
          <div className="sticky-add-meta">
            <strong>{chosen.length} selected</strong>
            {chosenBreakdown && <span className="muted">{chosenBreakdown}</span>}
          </div>
          <button type="button" className="primary" onClick={addSelected}>
            Add {chosen.length} to the plan
          </button>
        </div>
      )}
    </section>
  );
}

interface ResultsProps {
  network: Network;
  result: ScanResult;
  onOpenSettings?: () => void;
  selected: Set<string>;
  inPlan: Set<string>;
  selectable: (candidate: Candidate) => boolean;
  toggle: (keys: string[], on: boolean) => void;
  activeTab: CandidateGroup | "all";
  setActiveTab: (tab: CandidateGroup | "all") => void;
  query: string;
  setQuery: (q: string) => void;
  showSpam: boolean;
  setShowSpam: (v: boolean) => void;
}

function ScanResults({
  network,
  result,
  onOpenSettings,
  selected,
  inPlan,
  selectable,
  toggle,
  activeTab,
  setActiveTab,
  query,
  setQuery,
  showSpam,
  setShowSpam,
}: ResultsProps) {
  const spamCount = result.candidates.filter((c) => c.meta?.spam).length;
  const normalizedQuery = query.trim().toLowerCase();

  const visibleCandidates = useMemo(() => {
    return result.candidates.filter((c) => {
      if (!showSpam && c.meta?.spam) return false;
      if (!normalizedQuery) return true;
      return (
        c.title.toLowerCase().includes(normalizedQuery) ||
        c.detail.toLowerCase().includes(normalizedQuery) ||
        (c.meta?.contractAddress?.toLowerCase().includes(normalizedQuery) ?? false) ||
        (c.meta?.tokenId?.toLowerCase().includes(normalizedQuery) ?? false)
      );
    });
  }, [result.candidates, showSpam, normalizedQuery]);

  const countsByGroup = useMemo(() => {
    const map = new Map<CandidateGroup, number>();
    for (const c of visibleCandidates) map.set(c.group, (map.get(c.group) ?? 0) + 1);
    return map;
  }, [visibleCandidates]);

  const groupsToRender = GROUPS.filter(
    ({ group }) => (activeTab === "all" || activeTab === group) && (countsByGroup.get(group) ?? 0) > 0,
  );

  return (
    <div className="scan-results">
      {result.errors.map((error) => (
        <p key={error.source} className="banner error" role="alert">
          {error.source} lookup failed: {error.message}.{" "}
          {error.source.includes("Alchemy") && <SettingsLink onOpen={onOpenSettings} />}
        </p>
      ))}
      {result.warnings.map((warning, index) => (
        <p key={`${warning.source}-${index}`} className="banner" role="status">
          {warning.source} results are incomplete: {warning.message}.
        </p>
      ))}

      {result.candidates.length === 0 ? (
        <p className="empty">Nothing found for this wallet. You can still add assets by hand below.</p>
      ) : (
        <>
          <p className="verify-strip">
            Names can be faked. If you don&apos;t recognize something, check its contract on Etherscan before adding it.
          </p>

          <div className="scan-toolbar">
            <div className="filter-pills" role="tablist" aria-label="Asset categories">
              <button
                type="button"
                role="tab"
                aria-selected={activeTab === "all"}
                className={activeTab === "all" ? "pill active" : "pill"}
                onClick={() => setActiveTab("all")}
              >
                All ({visibleCandidates.length})
              </button>
              {GROUPS.map(({ group, short }) => {
                const count = countsByGroup.get(group) ?? 0;
                if (count === 0) return null;
                return (
                  <button
                    key={group}
                    type="button"
                    role="tab"
                    aria-selected={activeTab === group}
                    className={activeTab === group ? "pill active" : "pill"}
                    onClick={() => setActiveTab(group)}
                  >
                    {short} ({count})
                  </button>
                );
              })}
            </div>

            <div className="scan-controls">
              {result.candidates.length > 4 && (
                <input
                  type="search"
                  className="scan-search"
                  placeholder="Filter by name, symbol, or 0x…"
                  aria-label="Filter scan results"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              )}
              {spamCount > 0 && (
                <button
                  type="button"
                  className={showSpam ? "pill active" : "pill"}
                  aria-pressed={showSpam}
                  onClick={() => setShowSpam(!showSpam)}
                >
                  {showSpam ? `Hiding 0 spam` : `Show ${spamCount} flagged spam`}
                </button>
              )}
            </div>
          </div>

          {groupsToRender.length === 0 && (
            <p className="empty">No matching assets for the current filter.</p>
          )}

          {groupsToRender.map(({ group, label }) => {
            const items = visibleCandidates.filter((candidate) => candidate.group === group);
            const keys = items.filter(selectable).map((candidate) => candidate.key);
            const isTokenGroup = group === "tokens";

            return (
              <fieldset key={group} className="group visual-group">
                <div className="group-header">
                  <legend>
                    {label} ({items.length})
                  </legend>
                  {keys.length > 1 && (
                    <div className="group-actions">
                      <button type="button" className="link" onClick={() => toggle(keys, true)}>
                        Select all
                      </button>
                      <button type="button" className="link" onClick={() => toggle(keys, false)}>
                        Select none
                      </button>
                    </div>
                  )}
                </div>

                {isTokenGroup && (
                  <p className="field-hint">
                    Unchecked by default, since most unknown tokens are spam.
                  </p>
                )}

                <ul className={isTokenGroup ? "candidate-rows" : "candidate-grid"}>
                  {items.map((candidate) => {
                    const isInPlan = inPlan.has(candidate.key);
                    const note = isInPlan ? "Already in the plan." : candidate.blockedReason;
                    const isSelectable = selectable(candidate);
                    const isChecked = isSelectable && selected.has(candidate.key);
                    const contractAddr =
                      candidate.meta?.contractAddress ??
                      ("contract" in candidate.action ? candidate.action.contract : undefined);
                    const tokenId =
                      candidate.meta?.tokenId ??
                      ("token_id" in candidate.action ? candidate.action.token_id : undefined);

                    return (
                      <li key={candidate.key}>
                        <label
                          className={`candidate-card ${isChecked ? "selected" : ""} ${note ? "blocked" : ""} ${isTokenGroup ? "token-row" : ""}`}
                        >
                          <input
                            type="checkbox"
                            disabled={!isSelectable}
                            checked={isChecked}
                            onChange={(event) => toggle([candidate.key], event.target.checked)}
                          />
                          <div className="candidate-body">
                            <div className="candidate-top">
                              <div className="candidate-title-wrap">
                                <strong>{candidate.title}</strong>
                                {candidate.meta?.collection && candidate.meta.collection !== candidate.title && (
                                  <span className="candidate-sub">{candidate.meta.collection}</span>
                                )}
                              </div>
                              {isTokenGroup && candidate.meta?.formattedAmount ? (
                                <span className="token-balance-chip mono">{candidate.meta.formattedAmount}</span>
                              ) : (
                                <TypeBadge type={candidate.action.type} />
                              )}
                            </div>

                            <div className="candidate-meta">
                              {contractAddr && (
                                <AddressBadge
                                  address={contractAddr}
                                  network={network}
                                  tokenId={tokenId}
                                  copyable
                                  link
                                />
                              )}
                              {tokenId && <span className="meta-chip mono">#{tokenId}</span>}
                              {candidate.action.type === "erc1155" && candidate.action.amount !== "1" && (
                                <span className="meta-chip mono">× {candidate.action.amount}</span>
                              )}
                              {candidate.meta?.spam && <span className="meta-chip warn">Flagged spam</span>}
                            </div>

                            {note && (
                              <span className={isInPlan ? "candidate-status ok" : "candidate-status warn"}>{note}</span>
                            )}
                          </div>
                        </label>
                      </li>
                    );
                  })}
                </ul>
              </fieldset>
            );
          })}
        </>
      )}
    </div>
  );
}

function SettingsLink({ onOpen, lower = false }: { onOpen?: () => void; lower?: boolean }) {
  if (!onOpen) return null;
  return (
    <button type="button" className="link" onClick={onOpen}>
      {lower ? "add" : "Add"} your own Alchemy key in Settings.
    </button>
  );
}
