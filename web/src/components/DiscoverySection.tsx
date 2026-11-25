import { useEffect, useMemo, useRef, useState } from "react";
import { BUILT_IN_KEY, loadUserKey, saveUserKey } from "../discovery/key";
import type { Candidate, CandidateGroup, ScanRequest, ScanResult } from "../discovery/types";
import { addressProblem } from "../plan/address";
import { actionFromPlan, actionKey, actionToPlan, type Draft, type DraftEvent, discoveryScope } from "../plan/draft";
import { TextField } from "./Field";

export type Scan = (request: ScanRequest) => Promise<ScanResult>;

// Loaded on first scan: the RPC client is most of the bundle.
const scanWallet: Scan = async (request) => (await import("../discovery/scan")).scanWallet(request);

interface Props {
  draft: Draft;
  dispatch: (event: DraftEvent) => void;
  scan?: Scan;
}

const GROUPS: { group: CandidateGroup; label: string }[] = [
  { group: "listings", label: "Listed on Transient" },
  { group: "contracts", label: "Contracts you created on Transient" },
  { group: "nfts", label: "NFTs" },
  { group: "tokens", label: "ERC-20 tokens" },
];

type Status =
  | { kind: "idle" }
  | { kind: "scanning" }
  | { kind: "done"; result: ScanResult; scope: string }
  | { kind: "failed"; message: string };

export function DiscoverySection({ draft, dispatch, scan: runScan = scanWallet }: Props) {
  const [userKey, setUserKey] = useState(loadUserKey);
  const [status, setStatus] = useState<Status>({ kind: "idle" });
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const alchemyKey = userKey || BUILT_IN_KEY;
  const canScan = !addressProblem(draft.victim.trim()) && status.kind !== "scanning";
  const scope = discoveryScope(draft.network, draft.victim);
  // Only the latest scan for the current wallet may publish its results.
  const latest = useRef(0);

  useEffect(() => {
    latest.current += 1;
    setStatus({ kind: "idle" });
    setSelected(new Set());
  }, [draft.network, draft.victim]);

  const inPlan = useMemo(() => new Set(draft.actions.map((action) => actionKey(actionToPlan(action)))), [draft.actions]);
  const selectable = (candidate: Candidate) => !candidate.blockedReason && !inPlan.has(candidate.key);
  const chosen = status.kind === "done" ? status.result.candidates.filter((c) => selected.has(c.key) && selectable(c)) : [];

  async function scan() {
    const id = ++latest.current;
    const scanScope = scope;
    setStatus({ kind: "scanning" });
    try {
      const result = await runScan({ network: draft.network, victim: draft.victim.trim(), alchemyKey });
      if (id !== latest.current) return;
      setSelected(new Set(result.candidates.filter((c) => c.preselect).map((c) => c.key)));
      setStatus({ kind: "done", result, scope: scanScope });
    } catch (error) {
      if (id !== latest.current) return;
      setStatus({ kind: "failed", message: error instanceof Error ? error.message : String(error) });
    }
  }

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
    if (status.kind !== "done" || status.scope !== scope) return;
    const actions = chosen.map((candidate) => ({ ...actionFromPlan(candidate.action), discoveredFor: status.scope }));
    dispatch({ kind: "append", actions });
    toggle(
      chosen.map((candidate) => candidate.key),
      false,
    );
  }

  return (
    <section className="card">
      <h2>2. Find assets</h2>
      <p className="muted">
        Look up what the compromised wallet holds and tick what to rescue. You can still add anything by hand below.
      </p>
      <div className="buttons">
        <button type="button" className="primary" disabled={!canScan} onClick={scan}>
          {status.kind === "scanning" ? "Scanning…" : "Scan wallet"}
        </button>
      </div>
      {!alchemyKey && (
        <p className="field-hint">Token and NFT lookup needs an Alchemy API key (below). Transient lookup works without one.</p>
      )}
      <AlchemyKeyField userKey={userKey} onChange={(key) => { saveUserKey(key); setUserKey(key); }} />
      {status.kind === "failed" && (
        <p className="banner error" role="alert">
          The scan failed: {status.message}. You can scan again.
        </p>
      )}
      {status.kind === "done" && (
        <ScanResults
          result={status.result}
          selected={selected}
          inPlan={inPlan}
          selectable={selectable}
          toggle={toggle}
        />
      )}
      {chosen.length > 0 && (
        <div className="buttons sticky-add">
          <button type="button" className="primary" onClick={addSelected}>
            Add {chosen.length} to the plan
          </button>
        </div>
      )}
    </section>
  );
}

interface ResultsProps {
  result: ScanResult;
  selected: Set<string>;
  inPlan: Set<string>;
  selectable: (candidate: Candidate) => boolean;
  toggle: (keys: string[], on: boolean) => void;
}

function ScanResults({ result, selected, inPlan, selectable, toggle }: ResultsProps) {
  return (
    <div className="scan-results">
      {result.errors.map((error) => (
        <p key={error.source} className="banner error" role="alert">
          {error.source} lookup failed: {error.message}
        </p>
      ))}
      {result.warnings.map((warning, index) => (
        <p key={`${warning.source}-${index}`} className="banner" role="status">
          {warning.source} results are incomplete: {warning.message}.
        </p>
      ))}
      {result.candidates.length > 0 && (
        <p className="field-hint">
          Names come from {Array.from(new Set(result.candidates.map((c) => c.source))).join(" and ")} and can be
          spoofed. Check each contract address and token ID before adding it.
        </p>
      )}
      {result.candidates.length === 0 && <p className="empty">Nothing found.</p>}
      {GROUPS.map(({ group, label }) => {
        const items = result.candidates.filter((candidate) => candidate.group === group);
        if (items.length === 0) return null;
        const keys = items.filter(selectable).map((candidate) => candidate.key);
        return (
          <fieldset key={group} className="group">
            <legend>
              {label} ({items.length})
            </legend>
            {keys.length > 1 && (
              <div className="buttons">
                <button type="button" className="link" onClick={() => toggle(keys, true)}>
                  Select all
                </button>
                <button type="button" className="link" onClick={() => toggle(keys, false)}>
                  Select none
                </button>
              </div>
            )}
            <ul className="candidates">
              {items.map((candidate) => {
                const note = inPlan.has(candidate.key) ? "Already in the plan." : candidate.blockedReason;
                return (
                  <li key={candidate.key}>
                    <label className={note ? "blocked" : undefined}>
                      <input
                        type="checkbox"
                        disabled={!selectable(candidate)}
                        checked={selectable(candidate) && selected.has(candidate.key)}
                        onChange={(event) => toggle([candidate.key], event.target.checked)}
                      />
                      <span>
                        <strong>{candidate.title}</strong>
                        <span className="candidate-detail mono">{candidate.detail}</span>
                        <span className="candidate-note">via {candidate.source}</span>
                        {note && <span className="candidate-note">{note}</span>}
                      </span>
                    </label>
                  </li>
                );
              })}
            </ul>
          </fieldset>
        );
      })}
    </div>
  );
}

function AlchemyKeyField({ userKey, onChange }: { userKey: string | null; onChange: (key: string | null) => void }) {
  const [text, setText] = useState("");
  const source = userKey ? "your own key" : BUILT_IN_KEY ? "this site's key" : "no key";
  return (
    <details>
      <summary>Alchemy API key ({source})</summary>
      <p className="muted">
        A free key from alchemy.com. It is kept in this browser only. Alchemy sees which address you look up.
      </p>
      <TextField label="Your Alchemy API key" value={text} onChange={setText} mono />
      <div className="buttons">
        <button type="button" disabled={!text.trim()} onClick={() => { onChange(text.trim()); setText(""); }}>
          Use this key
        </button>
        {userKey && (
          <button type="button" onClick={() => onChange(null)}>
            Forget my key
          </button>
        )}
      </div>
    </details>
  );
}
