import { useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ActionsSection } from "./components/ActionsSection";
import { DiscoverySection, type Scan } from "./components/DiscoverySection";
import { InstallSection } from "./components/InstallSection";
import { ReviewSection } from "./components/ReviewSection";
import { SetupSection } from "./components/SetupSection";
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

export function App({ scan }: { scan?: Scan } = {}) {
  const [draft, dispatch] = useReducer(draftReducer, undefined, loadStoredDraft);
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
    setRevealed(true);
    if (result.issues.length > 0) {
      setImportNotice(`${file.name} has ${result.issues.length} problem(s) as written; they are marked below.`);
    }
  }

  function startOver() {
    if (replace(emptyDraft())) setRevealed(false);
  }

  return (
    <main>
      <header className="page-header">
        <div>
          <h1>eth-rescue plan builder</h1>
          <p className="muted">Build a rescue plan for a compromised Ethereum wallet, then run it with the eth-rescue CLI.</p>
        </div>
        <div className="buttons">
          <button type="button" onClick={() => fileInput.current?.click()}>
            Import plan.json
          </button>
          <button type="button" onClick={startOver}>
            Start over
          </button>
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
        <strong>Never paste a private key or seed phrase into this page.</strong> The plan only needs addresses. The
        CLI asks for keys on your own machine.
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
      <InstallSection />
      <SetupSection draft={draft} dispatch={dispatch} errorFor={errorFor} />
      <DiscoverySection draft={draft} dispatch={dispatch} scan={scan} />
      <ActionsSection draft={draft} dispatch={dispatch} errorFor={errorFor} />
      <ReviewSection plan={plan} issues={issues} onReveal={() => setRevealed(true)} />
    </main>
  );
}
