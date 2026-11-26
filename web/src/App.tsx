import { BookOpen, Check, Settings, TriangleAlert } from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ActionsSection } from "./components/ActionsSection";
import { EducationModal, HowItWorksVisual } from "./components/Education";
import { InstallSection } from "./components/InstallSection";
import { ReviewSection } from "./components/ReviewSection";
import { ScanPanel } from "./components/ScanPanel";
import { SettingsDialog } from "./components/SettingsDialog";
import { SetupSection, type WalletSnapshots } from "./components/SetupSection";
import { ThemeSwitch } from "./components/ThemeSwitch";
import { BUILT_IN_KEY, loadUserKey, saveUserKey } from "./discovery/key";
import type { OnchainReader } from "./discovery/onchain";
import { type Scan, type TokenMeta, useAssetScan } from "./discovery/useAssetScan";
import { addressProblem } from "./plan/address";
import { type Draft, draftReducer, emptyDraft, PlanImportError, staleDiscoveries, toPlan } from "./plan/draft";
import { importPlan } from "./plan/importPlan";
import { deserializeDraft, serializeDraft } from "./plan/storage";
import { validatePlan } from "./plan/validate";

const STORAGE_KEY = "eth-rescue:draft";

function loadStoredDraft(): Draft {
  try {
    return deserializeDraft(localStorage.getItem(STORAGE_KEY));
  } catch {
    return emptyDraft();
  }
}

function storeDraft(draft: Draft) {
  try {
    localStorage.setItem(STORAGE_KEY, serializeDraft(draft));
  } catch {
    // Storage is a convenience only.
  }
}

function isBlank(draft: Draft): boolean {
  return !draft.compromised && !draft.destination && !draft.gas_wallet && draft.actions.length === 0;
}

export type Mode = "emergency" | "prepare";
export type StepId = "prepare" | "wallets" | "actions" | "review";

function stepsForMode(mode: Mode): { id: StepId; label: string }[] {
  if (mode === "prepare") {
    return [
      { id: "prepare", label: "Prepare" },
      { id: "wallets", label: "Wallets" },
      { id: "actions", label: "Rescue plan" },
      { id: "review", label: "Review & save" },
    ];
  }
  return [
    { id: "wallets", label: "Wallets" },
    { id: "actions", label: "Rescue plan" },
    { id: "review", label: "Review & run" },
  ];
}

function useWalletSnapshot(
  network: Draft["network"],
  address: string,
  onchain?: OnchainReader,
): WalletSnapshots["compromised"] {
  const trimmed = address.trim();
  const valid = Boolean(trimmed) && !addressProblem(trimmed);
  const [snapshot, setSnapshot] = useState<WalletSnapshots["compromised"]>(undefined);

  useEffect(() => {
    if (!valid) {
      setSnapshot(undefined);
      return;
    }
    let cancelled = false;
    const timer = setTimeout(async () => {
      setSnapshot("loading");
      try {
        const reader = onchain ?? (await import("./discovery/onchain")).defaultOnchainReader;
        const result = await reader.readWalletSnapshot(network, loadUserKey() || BUILT_IN_KEY, trimmed);
        if (!cancelled) setSnapshot(result);
      } catch {
        if (!cancelled) setSnapshot(undefined);
      }
    }, onchain ? 0 : 250);

    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [network, trimmed, valid, onchain]);

  return snapshot;
}

export function App({ scan, onchain }: { scan?: Scan; onchain?: OnchainReader } = {}) {
  const [draft, dispatch] = useReducer(draftReducer, undefined, loadStoredDraft);
  const [mode, setMode] = useState<Mode | null>(() => (isBlank(loadStoredDraft()) ? null : "emergency"));
  const [step, setStep] = useState<StepId>("wallets");
  const stepperRef = useRef<HTMLElement>(null);

  // A new step starts at its top, not wherever the last one was scrolled to.
  useEffect(() => {
    const stepper = stepperRef.current;
    if (stepper && stepper.getBoundingClientRect().top < 0) stepper.scrollIntoView({ block: "start" });
  }, [step]);
  const [showAllSteps, setShowAllSteps] = useState(false);
  const [eduOpen, setEduOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [userKey, setUserKey] = useState(loadUserKey);
  const [revealed, setRevealed] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importNotice, setImportNotice] = useState<string | null>(null);
  const [tokenMeta, setTokenMeta] = useState<Record<string, TokenMeta>>({});
  const fileInput = useRef<HTMLInputElement>(null);

  const plan = useMemo(() => toPlan(draft), [draft]);
  const issues = useMemo(() => [...validatePlan(plan), ...staleDiscoveries(draft)], [plan, draft]);
  const issueByPath = useMemo(() => new Map(issues.map((issue) => [issue.path, issue.message])), [issues]);

  const compromisedSnapshot = useWalletSnapshot(draft.network, draft.compromised, onchain);
  const destinationSnapshot = useWalletSnapshot(draft.network, draft.destination, onchain);
  const gasSnapshot = useWalletSnapshot(draft.network, draft.gas_wallet, onchain);
  const snapshots: WalletSnapshots = useMemo(
    () => ({ compromised: compromisedSnapshot, destination: destinationSnapshot, gas_wallet: gasSnapshot }),
    [compromisedSnapshot, destinationSnapshot, gasSnapshot],
  );

  useEffect(() => storeDraft(draft), [draft]);

  // Automatically hydrate ERC-20 symbol & decimals for imported or reloaded actions.
  const erc20Contracts = useMemo(() => {
    const unique = new Set<string>();
    for (const action of draft.actions) {
      if (action.type !== "erc20") continue;
      const addr = action.contract.trim();
      if (addr && !addressProblem(addr) && !tokenMeta[addr.toLowerCase()]) unique.add(addr);
    }
    return Array.from(unique);
  }, [draft.actions, tokenMeta]);

  useEffect(() => {
    if (erc20Contracts.length === 0) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      try {
        const reader = onchain ?? (await import("./discovery/onchain")).defaultOnchainReader;
        const key = loadUserKey() || BUILT_IN_KEY;
        const results = await Promise.allSettled(
          erc20Contracts.map((contract) => reader.readErc20Info(draft.network, key, contract)),
        );
        if (cancelled) return;
        const entries: Record<string, TokenMeta> = {};
        results.forEach((res, idx) => {
          if (res.status !== "fulfilled") return;
          const info = res.value;
          if (info.decimals !== null || info.symbol || info.name) {
            entries[erc20Contracts[idx].toLowerCase()] = {
              decimals: info.decimals ?? undefined,
              symbol: info.symbol ?? undefined,
              name: info.name ?? undefined,
            };
          }
        });
        if (Object.keys(entries).length > 0) setTokenMeta((prev) => ({ ...prev, ...entries }));
      } catch {
        // Ignore transient RPC failures; raw base units still render.
      }
    }, onchain ? 0 : 250);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [draft.network, erc20Contracts, onchain]);

  const mergeTokenMeta = useCallback((entries: Record<string, TokenMeta>) => {
    setTokenMeta((prev) => ({ ...prev, ...entries }));
  }, []);

  const assetScan = useAssetScan({
    network: draft.network,
    compromised: draft.compromised,
    alchemyKey: userKey || BUILT_IN_KEY,
    scan,
    onTokenMeta: mergeTokenMeta,
  });
  const planVisible = mode !== null && (showAllSteps || step === "actions");
  const { addressReady, run: runScan } = assetScan;
  const scanIdle = assetScan.status.kind === "idle";

  useEffect(() => {
    if (planVisible && addressReady && scanIdle) runScan();
  }, [planVisible, addressReady, scanIdle, runScan]);

  const errorFor = (path: string, value: string) => (revealed || value.trim() ? issueByPath.get(path) : undefined);

  function chooseMode(nextMode: Mode) {
    setMode(nextMode);
    setStep(nextMode === "prepare" ? "prepare" : "wallets");
  }

  function switchMode(nextMode: Mode) {
    setMode(nextMode);
    if (nextMode === "emergency" && step === "prepare") {
      setStep("wallets");
    }
  }

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
    setStep(result.issues.length > 0 ? "wallets" : "review");
    setRevealed(true);
    if (result.issues.length > 0) {
      setImportNotice(`${file.name} has ${result.issues.length} problem(s) as written; they are marked below.`);
    }
  }

  function startOver() {
    if (replace(emptyDraft())) {
      setRevealed(false);
      setMode(null);
      setStep("wallets");
      setShowAllSteps(false);
    }
  }

  function jumpToIssue(path: string) {
    setRevealed(true);
    if (path.startsWith("options.")) {
      setSettingsOpen(true);
    } else if (path === "actions" || path.startsWith("actions.")) {
      setStep("actions");
    } else {
      setStep("wallets");
    }
  }

  function changeUserKey(key: string | null) {
    saveUserKey(key);
    setUserKey(key);
  }

  const steps = mode ? stepsForMode(mode) : [];
  const walletIssueCount = issues.filter(
    (i) => i.path === "compromised" || i.path === "destination" || i.path === "gas_wallet",
  ).length;
  const settingsNeedAttention = revealed && issues.some((i) => i.path.startsWith("options."));
  const actionIssueCount = issues.filter((i) => i.path === "actions" || i.path.startsWith("actions.")).length;
  const walletsComplete = Boolean(draft.compromised.trim() && draft.destination.trim() && walletIssueCount === 0);

  const stepOffset = mode === "prepare" ? 1 : 0;
  const showStep = (id: StepId) => showAllSteps || step === id;

  return (
    <main>
      <div className="topbar">
        <button type="button" className="topbar-button" onClick={() => setEduOpen(true)}>
          <BookOpen size={15} aria-hidden="true" />
          How it works
        </button>
        <div className="topbar-right">
          <button
            type="button"
            className={settingsNeedAttention ? "topbar-button attention" : "topbar-button"}
            onClick={() => setSettingsOpen(true)}
          >
            <Settings size={15} aria-hidden="true" />
            Settings
          </button>
          <ThemeSwitch />
        </div>
      </div>

      <header className="page-header">
        <div>
          <h1 className="brand">
            <span className="brand-mark" aria-hidden="true">
              ⛨
            </span>
            ETH Rescue
          </h1>
          <p className="tagline">A tool I hope you never have to use.</p>
          {mode === null && (
            <p className="muted">
              Move tokens, NFTs, and contract ownership out of a compromised Ethereum wallet, without ever sending it
              ETH that a sweeper bot could steal.
            </p>
          )}
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

      <div className="key-warning" role="note">
        <TriangleAlert className="key-warning-icon" size={22} aria-hidden="true" />
        <p>
          <strong>Never enter a private key or seed phrase on this website.</strong>
          <span>
            It only needs public <code>0x…</code> addresses. The rescue tool asks for keys later, in your own terminal.
          </span>
        </p>
      </div>

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
        <>
          <div className="doors-intro">
            <h2 id="doors-heading">What&apos;s your situation?</h2>
          </div>
          <div className="doors" role="region" aria-labelledby="doors-heading">
            <button type="button" className="door emergency" onClick={() => chooseMode("emergency")}>
              <div>
                <span className="door-title">My wallet is compromised</span>
                <span className="door-desc">Someone else has your private key or seed phrase.</span>
                <ul className="door-points">
                  <li>Find what the wallet holds</li>
                  <li>Choose what to move and where it goes</li>
                  <li>Download plan.json and run it from your terminal</li>
                </ul>
              </div>
              <span className="door-action">Start a rescue</span>
            </button>
            <button type="button" className="door prepare" onClick={() => chooseMode("prepare")}>
              <div>
                <span className="door-title">Prepare a break-glass plan</span>
                <span className="door-desc">Your wallet is safe today. Be ready in case it isn&apos;t.</span>
                <ul className="door-points">
                  <li>Install the rescue tool now</li>
                  <li>Fund a separate gas wallet with 0.02–0.05 ETH</li>
                  <li>Save plan.json somewhere you can reach from another computer</li>
                </ul>
              </div>
              <span className="door-action">Make a plan</span>
            </button>
          </div>
          <HowItWorksVisual />
        </>
      ) : (
        <>
          <div className="workspace-controls">
            <div className="mode-bar" role="group" aria-label="Mode">
              <button type="button" aria-pressed={mode === "emergency"} onClick={() => switchMode("emergency")}>
                Active emergency
              </button>
              <button type="button" aria-pressed={mode === "prepare"} onClick={() => switchMode("prepare")}>
                Plan ahead
              </button>
            </div>
          </div>

          <nav ref={stepperRef} className="stepper" aria-label="Rescue steps">
            {steps.map((item, idx) => {
              const isCurrent = !showAllSteps && step === item.id;
              const isComplete =
                (item.id === "wallets" && walletsComplete) ||
                (item.id === "actions" && draft.actions.length > 0 && actionIssueCount === 0) ||
                (item.id === "review" && issues.length === 0);
              const badge =
                item.id === "actions" && draft.actions.length > 0
                  ? `${draft.actions.length}`
                  : item.id === "review" && issues.length === 0
                    ? "Ready"
                    : undefined;
              const hasWarn =
                revealed &&
                ((item.id === "wallets" && walletIssueCount > 0) || (item.id === "actions" && actionIssueCount > 0));

              return (
                <button
                  key={item.id}
                  type="button"
                  className={`step-pill ${isCurrent ? "current" : ""} ${isComplete ? "done" : ""} ${hasWarn ? "warn" : ""}`}
                  aria-current={isCurrent ? "step" : undefined}
                  onClick={() => {
                    setShowAllSteps(false);
                    setStep(item.id);
                  }}
                >
                  <span className="step-num">{isComplete ? <Check size={13} aria-label="Done" /> : idx + 1}</span>
                  <span className="step-label">{item.label}</span>
                  {badge && <span className="step-badge">{badge}</span>}
                </button>
              );
            })}
          </nav>

          <div className="stack">
            {mode === "prepare" && showStep("prepare") && (
              <InstallSection mode={mode} onContinue={showAllSteps ? undefined : () => setStep("wallets")} />
            )}

            {showStep("wallets") && (
              <SetupSection
                mode={mode}
                draft={draft}
                dispatch={dispatch}
                errorFor={errorFor}
                snapshots={snapshots}
                onBack={!showAllSteps && mode === "prepare" ? () => setStep("prepare") : undefined}
                onContinue={showAllSteps ? undefined : () => setStep("actions")}
              />
            )}

            {showStep("actions") && (
              <ActionsSection
                stepNumber={2 + stepOffset}
                draft={draft}
                dispatch={dispatch}
                errorFor={errorFor}
                issues={issues}
                tokenMeta={tokenMeta}
                onTokenMeta={mergeTokenMeta}
                onchain={onchain}
                scanPanel={
                  <ScanPanel
                    draft={draft}
                    dispatch={dispatch}
                    status={assetScan.status}
                    scope={assetScan.scope}
                    addressReady={addressReady}
                    alchemyKey={userKey || BUILT_IN_KEY}
                    compromisedSnapshot={compromisedSnapshot}
                    onScan={runScan}
                    onOpenSettings={() => setSettingsOpen(true)}
                  />
                }
                scanPending={addressReady && (scanIdle || assetScan.status.kind === "scanning")}
                onBack={showAllSteps ? undefined : () => setStep("wallets")}
                onContinue={showAllSteps ? undefined : () => setStep("review")}
              />
            )}

            {showStep("review") && (
              <ReviewSection
                stepNumber={3 + stepOffset}
                plan={plan}
                issues={issues}
                revealed={revealed}
                mode={mode}
                tokenMeta={tokenMeta}
                compromisedSnapshot={compromisedSnapshot}
                onReveal={() => setRevealed(true)}
                onJumpToIssue={jumpToIssue}
                onBack={showAllSteps ? undefined : () => setStep("actions")}
              />
            )}
          </div>
        </>
      )}

      <footer className="page-footer">
        <p>
          <strong>Disclaimer:</strong> ETH Rescue is free,{" "}
          <a href="https://github.com/mpeyfuss/eth-rescue">open-source</a> tooling built with the best intentions and
          provided &ldquo;as is,&rdquo; without warranty of any kind. A rescue is a race against whoever else holds the
          compromised key and is never guaranteed to succeed. You use this site and the CLI entirely at your own
          risk. The authors and contributors are not liable for any lost funds, assets, transaction fees, or other
          damages.
        </p>
      </footer>

      <EducationModal open={eduOpen} onClose={() => setEduOpen(false)} />
      <SettingsDialog
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        draft={draft}
        dispatch={dispatch}
        errorFor={errorFor}
        userKey={userKey}
        onUserKey={changeUserKey}
        showAllSteps={showAllSteps}
        onShowAllSteps={setShowAllSteps}
      />
    </main>
  );
}
