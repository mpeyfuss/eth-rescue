import { useState } from "react";
import type { Mode } from "../App";
import type { WalletSnapshot } from "../discovery/onchain";
import { COMMANDS, detectOs, type Os, PLAN_FILE } from "../guide";
import type { ActionType, Network } from "../plan/draft";
import { formatTokenAmount } from "../plan/format";
import type { Issue } from "../plan/validate";
import { CommandLine, CopyButton } from "./Command";
import type { TokenMeta } from "../discovery/useAssetScan";
import { RunTimeline } from "./Education";
import { AiAssistantCard, CliInstallSteps } from "./InstallSection";
import { AddressBadge, TypeBadge } from "./Visual";

interface Props {
  stepNumber: number;
  plan: Record<string, unknown>;
  issues: Issue[];
  revealed?: boolean;
  mode?: Mode;
  tokenMeta?: Record<string, TokenMeta>;
  compromisedSnapshot?: WalletSnapshot | "loading";
  onReveal: () => void;
  onJumpToIssue?: (path: string) => void;
  onBack?: () => void;
}

const PATH_LABELS: Record<string, string> = {
  "": "Plan",
  network: "Network",
  compromised: "Compromised wallet",
  destination: "Destination wallet",
  gas_wallet: "Gas wallet",
  "options.priority_fee_gwei": "Priority fee",
  "options.max_blocks": "Max blocks",
  actions: "Rescue plan",
};

export function describePath(path: string): string {
  const match = /^actions\.(\d+)(?:\.(.+))?$/.exec(path);
  if (!match) return PATH_LABELS[path] ?? path;
  const action = `Action ${Number(match[1]) + 1}`;
  return match[2] ? `${action} · ${match[2]}` : action;
}

function download(json: string) {
  const url = URL.createObjectURL(new Blob([json], { type: "application/json" }));
  const link = Object.assign(document.createElement("a"), { href: url, download: PLAN_FILE });
  link.click();
  URL.revokeObjectURL(url);
}

export function ReviewSection({
  stepNumber,
  plan,
  issues,
  revealed = false,
  mode = "emergency",
  tokenMeta = {},
  compromisedSnapshot,
  onReveal,
  onJumpToIssue,
  onBack,
}: Props) {
  const [os, setOs] = useState<Os>(() => detectOs(navigator.userAgent));
  const [cliInstalled, setCliInstalled] = useState(mode === "prepare");

  const json = `${JSON.stringify(plan, null, 2)}\n`;
  const ready = issues.length === 0;
  const preparing = mode === "prepare";

  return (
    <section className="card">
      <h2>
        {stepNumber}. {preparing ? "Review & save plan" : "Review & run"}
      </h2>

      {!ready && (
        <div className={revealed ? "issues" : "issues checklist"} role="status">
          <p>
            {revealed
              ? `Fix ${issues.length} ${issues.length === 1 ? "problem" : "problems"} before exporting.`
              : `Still needed before exporting (${issues.length}):`}{" "}
            <button type="button" className="link" onClick={onReveal}>
              Highlight on form
            </button>
          </p>
          <ul className="issue-list">
            {issues.map((issue) => (
              <li key={`${issue.path}:${issue.message}`}>
                {onJumpToIssue ? (
                  <button type="button" className="issue-jump" onClick={() => onJumpToIssue(issue.path)}>
                    <strong>{describePath(issue.path)}</strong> {issue.message} →
                  </button>
                ) : (
                  <>
                    <strong>{describePath(issue.path)}</strong> {issue.message}
                  </>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      <PlanBlueprint plan={plan} tokenMeta={tokenMeta} compromisedSnapshot={compromisedSnapshot} />

      <div className="export-bar">
        <div className="buttons">
          <button type="button" className="primary" disabled={!ready} onClick={() => download(json)}>
            Download {PLAN_FILE}
          </button>
          <CopyButton text={json} label="Copy JSON" disabled={!ready} />
        </div>
      </div>

      <details className="raw-json-details">
        <summary>Show {PLAN_FILE}</summary>
        <pre className="preview" aria-label="Plan JSON">
          {json}
        </pre>
      </details>

      <div className="run-section">
        {preparing ? (
          <>
            <h3>Save your plan</h3>
            <p className="muted">
              {PLAN_FILE} holds only public <code>0x…</code> addresses, so it&apos;s safe to keep anywhere. Save a copy
              you can reach from another computer: a USB drive, a cloud drive, or an email to yourself. Then check it
              (this never asks for keys):
            </p>
            <CommandLine command={COMMANDS.validate} />
            <p className="muted">
              Whenever you acquire new NFTs or deploy new contracts, click <strong>Import plan.json</strong> at the top
              to re-scan and refresh your backup. On rescue day, open a terminal in the folder containing {PLAN_FILE}{" "}
              (e.g. <code>cd ~/Downloads</code>) and run:
            </p>
            <CommandLine command={COMMANDS.run} />
            <RunTimeline />
            <AiAssistantCard os={os} />
          </>
        ) : (
          <>
            <div className="run-head">
              <h3>Run your rescue on a clean computer</h3>
              <label className="installed-toggle">
                <input
                  type="checkbox"
                  checked={cliInstalled}
                  onChange={(e) => setCliInstalled(e.target.checked)}
                />
                <span>I already have eth-rescue installed</span>
              </label>
            </div>

            {!cliInstalled && (
              <div className="cli-setup-box">
                <strong>1. Install eth-rescue</strong>
                <CliInstallSteps os={os} setOs={setOs} />
              </div>
            )}

            <div className="cli-run-box">
              <strong>{cliInstalled ? "Run from your terminal" : "2. Check and run your plan"}</strong>
              <p className="muted">
                Open your terminal in the folder where you downloaded {PLAN_FILE} (for example,{" "}
                <code>cd ~/Downloads</code>):
              </p>
              <CommandLine command={COMMANDS.validate} />
              <CommandLine command={COMMANDS.run} />
            </div>

            <RunTimeline />
            <AiAssistantCard os={os} />
          </>
        )}
      </div>

      {onBack && (
        <div className="step-footer">
          <button type="button" onClick={onBack}>
            ← Back to rescue plan
          </button>
        </div>
      )}
    </section>
  );
}

function PlanBlueprint({
  plan,
  tokenMeta,
  compromisedSnapshot,
}: {
  plan: Record<string, unknown>;
  tokenMeta: Record<string, TokenMeta>;
  compromisedSnapshot?: WalletSnapshot | "loading";
}) {
  const network = (plan.network as Network) || "mainnet";
  const actions = Array.isArray(plan.actions) ? (plan.actions as Record<string, unknown>[]) : [];
  const options = (plan.options ?? {}) as Record<string, unknown>;
  const compromised = String(plan.compromised || "");
  const destination = String(plan.destination || "");
  const gasWallet = plan.gas_wallet ? String(plan.gas_wallet) : "";
  const snap = compromisedSnapshot && compromisedSnapshot !== "loading" ? compromisedSnapshot : null;

  return (
    <div className="blueprint">
      <div className="blueprint-header">
        <div className="blueprint-title">
          <strong>Summary</strong>
          <div className="blueprint-chips">
            <span className="meta-chip">{network === "mainnet" ? "Ethereum mainnet" : network}</span>
            <span className="meta-chip ok">
              ETH sweep: {snap ? snap.ethFormatted : "auto-included"}
            </span>
            <span className="meta-chip">
              Fallback retry: {options.legacy_fallback === false ? "Off" : "On"}
            </span>
          </div>
        </div>

        <div className="blueprint-route">
          <div className="blueprint-wallet">
            <span className="bp-label">Compromised</span>
            {compromised ? <AddressBadge address={compromised} network={network} link /> : <span className="muted">Not set</span>}
          </div>
          <span className="bp-arrow" aria-hidden="true">→</span>
          <div className="blueprint-wallet">
            <span className="bp-label">Destination</span>
            {destination ? <AddressBadge address={destination} network={network} link /> : <span className="muted">Not set</span>}
          </div>
          <div className="blueprint-wallet gas">
            <span className="bp-label">Gas payer</span>
            {gasWallet ? (
              <AddressBadge address={gasWallet} network={network} link />
            ) : (
              <span className="muted">Chosen in CLI</span>
            )}
          </div>
        </div>
      </div>

      {actions.length === 0 ? (
        <p className="empty bp-empty">No actions added yet.</p>
      ) : (
        <ol className="blueprint-actions">
          {actions.map((action, index) => {
            const type = String(action.type) as ActionType;
            const contract = String(action.contract ?? action.target ?? "");
            const meta = contract ? tokenMeta[contract.toLowerCase()] : undefined;
            const label = action.label ? String(action.label) : "";
            const isCustom = type === "custom";

            let detailText = "";
            if (type === "erc20") {
              const raw = String(action.amount ?? "");
              const formatted = meta?.decimals != null ? formatTokenAmount(raw, meta.decimals) : null;
              detailText = formatted
                ? `${formatted}${meta?.symbol ? ` ${meta.symbol}` : ""} (${raw} base units)`
                : `${raw} base units`;
            } else if (type === "erc721") {
              detailText = `Token #${action.token_id}`;
            } else if (type === "erc1155") {
              detailText = `Token #${action.token_id} × ${action.amount}`;
            } else if (type === "ownership") {
              detailText = "transferOwnership → Destination";
            } else if (type === "cryptopunk" || type === "superrare_v1") {
              detailText = `#${action.token_id}`;
            } else if (type === "transient_delist_erc721") {
              detailText = `Delist #${action.token_id} & transfer → Destination`;
            } else if (type === "custom") {
              detailText = `${action.signature} (${JSON.stringify(action.args)})`;
            }

            return (
              <li key={index} className={isCustom ? "bp-action custom-call" : "bp-action"}>
                <span className="bp-idx">{index + 1}</span>
                <TypeBadge type={type} />
                <div className="bp-action-main">
                  <div className="bp-action-top">
                    <strong>{label || (meta?.symbol ?? type)}</strong>
                    <span className="bp-action-detail mono">{detailText}</span>
                  </div>
                  <div className="bp-action-sub">
                    {contract && (
                      <AddressBadge
                        address={contract}
                        network={network}
                        tokenId={action.token_id ? String(action.token_id) : undefined}
                        link
                      />
                    )}
                    {isCustom ? (
                      <span className="summary-chip warn">Destination set by custom arguments</span>
                    ) : (
                      <span className="bp-dest">→ Destination</span>
                    )}
                  </div>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </div>
  );
}
