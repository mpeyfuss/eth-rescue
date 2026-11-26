import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ActionsSection } from "./components/ActionsSection";
import { DiscoverySection, type Scan } from "./components/DiscoverySection";
import { InstallSection } from "./components/InstallSection";
import { ReviewSection } from "./components/ReviewSection";
import { SetupSection } from "./components/SetupSection";
import { ThemeSwitch } from "./components/ThemeSwitch";
import { type Draft, draftReducer, emptyDraft, fromPlan, PlanImportError, staleDiscoveries, toPlan } from "./plan/draft";
import { importPlan, persistablePlan } from "./plan/importPlan";
import { validatePlan } from "./plan/validate";

// Kept across reloads: addresses and typed actions. Custom arguments and
// anything that looks like a secret are left out (see persistablePlan).
const STORAGE_KEY = "eth-rescue:draft";

function loadStoredDraft(): Draft {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    return stored ? fromPlan(JSON.parse(stored)) : emptyDraft();
  } catch {
    return emptyDraft();
  }
}

function storeDraft(plan: Record<string, unknown>) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(persistablePlan(plan)));
  } catch {
    // Storage is a convenience only.
  }
}

function isBlank(draft: Draft): boolean {
  return !draft.victim && !draft.safe && !draft.gas_wallet && draft.actions.length === 0;
}

export type Mode = "emergency" | "prepare";

export function App({ scan }: { scan?: Scan } = {}) {
  const [draft, dispatch] = useReducer(draftReducer, undefined, loadStoredDraft);
  const [mode, setMode] = useState<Mode | null>(() => (isBlank(loadStoredDraft()) ? null : "emergency"));
  const [revealed, setRevealed] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  const plan = useMemo(() => toPlan(draft), [draft]);
  const issues = useMemo(() => [...validatePlan(plan), ...staleDiscoveries(draft)], [plan, draft]);
  const issueByPath = useMemo(() => new Map(issues.map((issue) => [issue.path, issue.message])), [issues]);

  useEffect(() => storeDraft(plan), [plan]);

  const errorFor = (path: string, value: string) => (revealed || value.trim() ? issueByPath.get(path) : undefined);

  function replace(next: Draft) {
    if (!isBlank(draft) && !window.confirm("Replace the current plan?")) return false;
    dispatch({ kind: "replace", draft: next });
    return true;
  }

  async function importFile(file: File) {
    setImportError(null);
    setImportNotice(null);
    let result;
    try {
      result = importPlan(await file.text());
    } catch (error) {
      const reason = error instanceof PlanImportError ? error.message : String(error);
      setImportError(`Could not import ${file.name}: ${reason}`);
      return;
    }
    if (
      result.repairs.length > 0 &&
      !window.confirm(
        `Importing ${file.name} would change it:\n\n- ${result.repairs.join("\n- ")}\n\nImport the changed plan?`,
      )
    ) {
      return;
    }
    if (!replace(result.draft)) return;
    setMode((current) => current ?? "emergency");
    setRevealed(true);
    if (result.issues.length > 0) {
      setImportNotice(`${file.name} has ${result.issues.length} problem(s) as written; they are marked below.`);
    }
  }

  function startOver() {
    if (replace(emptyDraft())) {
      setRevealed(false);
      setMode(null);
    }
  }

  return (
    <main>
      <div className="topbar">
        <ThemeSwitch />
      </div>
      <header className="page-header">
        <div>
          <h1>
            <span aria-hidden="true">⛨ </span>ETH Rescue
          </h1>
          <p className="muted">
            Recover tokens, NFTs, and contract ownership from a compromised Ethereum wallet using a separate wallet to
            pay gas. Prepare your rescue plan here, then run it on your computer.
          </p>
        </div>
        <div className="buttons">
          <button type="button" onClick={() => fileInput.current?.click()}>
            Import plan.json
          </button>
          {(mode !== null || !isBlank(draft)) && (
            <button type="button" onClick={startOver}>
              Start over
            </button>
          )}
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(event) => {
              const file = event.target.files?.[0];
              event.target.value = "";
              if (file) importFile(file);
            }}
          />
        </div>
      </header>
      <p className="banner" role="note">
        <strong>Never paste a private key or seed phrase into this page.</strong> The plan only uses public{" "}
        <code>0x…</code> addresses. The command-line tool asks for keys on your own computer.
      </p>
      {importError && (
        <p className="banner error" role="alert">
          {importError}
        </p>
      )}
      {importNotice && (
        <p className="banner" role="status">
          {importNotice}
        </p>
      )}
      {mode === null ? (
        <div className="doors" role="region" aria-label="Choose your situation">
          <button type="button" className="door emergency" onClick={() => setMode("emergency")}>
            <div>
              <span className="door-kicker">Active emergency</span>
              <span className="door-title">My wallet is compromised</span>
              <span className="door-desc">
                Move tokens, NFTs, and contract ownership out right now before a sweeper bot can take the gas.
              </span>
              <ul className="door-points">
                <li>Run on a clean computer (not one that may have malware)</li>
                <li>Scan or list what the compromised wallet still holds</li>
                <li>Download plan.json and execute the rescue bundle</li>
              </ul>
            </div>
            <span className="door-action">Start emergency rescue →</span>
          </button>
          <button type="button" className="door prepare" onClick={() => setMode("prepare")}>
            <div>
              <span className="door-kicker">Plan ahead</span>
              <span className="door-title">Prepare a break-glass plan</span>
              <span className="door-desc">
                Install the CLI in advance, pre-fund a standby gas wallet, and save a ready-to-run plan.json while there
                is no time pressure.
              </span>
              <ul className="door-points">
                <li>Plan how to run the rescue from a second, clean computer</li>
                <li>Pre-install eth-rescue and fund a standby gas wallet (~0.02–0.05 ETH)</li>
                <li>Save a verified plan.json you can reach without your main machine</li>
              </ul>
            </div>
            <span className="door-action">Prepare ahead of time →</span>
          </button>
        </div>
      ) : (
        <>
          <div className="mode-bar" role="group" aria-label="Mode">
            <button type="button" aria-pressed={mode === "emergency"} onClick={() => setMode("emergency")}>
              Active emergency
            </button>
            <button type="button" aria-pressed={mode === "prepare"} onClick={() => setMode("prepare")}>
              Plan ahead
            </button>
          </div>
          <div className="stack">
            <InstallSection mode={mode} />
            <SetupSection draft={draft} dispatch={dispatch} errorFor={errorFor} />
            <DiscoverySection draft={draft} dispatch={dispatch} scan={scan} />
            <ActionsSection draft={draft} dispatch={dispatch} errorFor={errorFor} />
            <ReviewSection
              plan={plan}
              issues={issues}
              revealed={revealed}
              mode={mode}
              onReveal={() => setRevealed(true)}
            />
          </div>
        </>
      )}
      <footer className="page-footer">
        <p>
          <strong>Disclaimer:</strong> ETH Rescue is free,{" "}
          <a href="https://github.com/mpeyfuss/eth-rescue">open-source</a> tooling built with the best intentions and
          provided &ldquo;as is,&rdquo; without warranty of any kind. A rescue is a race against whoever else holds the
          compromised key and is never guaranteed to succeed. You use this site and the CLI entirely at your own
          risk—the authors and contributors are not liable for any lost funds, assets, transaction fees, or other
          damages.
        </p>
      </footer>
    </main>
  );
}
