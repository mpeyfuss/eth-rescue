import { COMMANDS, PLAN_FILE, RUN_STEPS } from "../guide";
import type { Issue } from "../plan/validate";
import { CommandLine, CopyButton } from "./Command";

interface Props {
  plan: Record<string, unknown>;
  issues: Issue[];
  onReveal: () => void;
}


const PATH_LABELS: Record<string, string> = {
  "": "Plan",
  network: "Network",
  victim: "Compromised wallet",
  safe: "Safe wallet",
  gas_wallet: "Gas wallet",
  "options.priority_fee_gwei": "Priority fee",
  "options.max_blocks": "Max blocks",
  actions: "Assets to rescue",
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

export function ReviewSection({ plan, issues, onReveal }: Props) {
  const json = `${JSON.stringify(plan, null, 2)}\n`;
  const ready = issues.length === 0;

  return (
    <section className="card">
      <h2>4. Review and export</h2>
      {!ready && (
        <div className="issues" role="status">
          <p>
            Fix {issues.length} {issues.length === 1 ? "problem" : "problems"} before exporting.{" "}
            <button type="button" className="link" onClick={onReveal}>
              Show them on the form
            </button>
          </p>
          <ul>
            {issues.map((issue) => (
              <li key={`${issue.path}:${issue.message}`}>
                <strong>{describePath(issue.path)}</strong> {issue.message}
              </li>
            ))}
          </ul>
        </div>
      )}
      <PlanSummary plan={plan} />
      <pre className="preview" aria-label="Plan JSON">
        {json}
      </pre>
      <div className="buttons">
        <button type="button" className="primary" disabled={!ready} onClick={() => download(json)}>
          Download {PLAN_FILE}
        </button>
        <CopyButton text={json} label="Copy JSON" disabled={!ready} />
      </div>
      <h3>Then run</h3>
      <p className="muted">
        In a terminal, go to the folder where you saved {PLAN_FILE}. The CLI {RUN_STEPS}
      </p>
      <CommandLine command={COMMANDS.validate} />
      <CommandLine command={COMMANDS.run} />
    </section>
  );
}

const ACTION_VERBS: Record<string, (action: Record<string, unknown>) => string> = {
  erc20: (a) => `Send ${a.amount} base units of token ${a.contract} to the safe`,
  erc721: (a) => `Send NFT ${a.contract} #${a.token_id} to the safe`,
  erc1155: (a) => `Send ${a.amount} of token ${a.contract} #${a.token_id} to the safe`,
  ownership: (a) => `Transfer ownership of ${a.contract} to the safe`,
  transient_delist_erc721: (a) => `Delist NFT ${a.contract} #${a.token_id} from ${a.auction_house}, then send it to the safe`,
  custom: (a) => `Call ${a.signature} on ${a.target} with ${JSON.stringify(a.args)}; its arguments decide where anything goes`,
};

function PlanSummary({ plan }: { plan: Record<string, unknown> }) {
  const actions = Array.isArray(plan.actions) ? (plan.actions as Record<string, unknown>[]) : [];
  const options = (plan.options ?? {}) as Record<string, unknown>;
  return (
    <div className="summary">
      <h3>What this plan does</h3>
      <ul>
        <li>
          On <strong>{String(plan.network)}</strong>, moves assets out of <span className="mono">{String(plan.victim)}</span>{" "}
          to the safe <span className="mono">{String(plan.safe)}</span>, which also receives all ETH left in the
          compromised wallet.
        </li>
        <li>
          {plan.gas_wallet ? (
            <>
              The gas wallet <span className="mono">{String(plan.gas_wallet)}</span> pays every fee.
            </>
          ) : (
            "A gas wallet you choose when running pays every fee."
          )}
        </li>
        <li>
          {options.legacy_fallback === false
            ? "Actions that have no effect are reported, not retried."
            : "Actions that have no effect are retried once as direct transactions, without asking again."}
        </li>
      </ul>
      <ol>
        {actions.map((action, index) => (
          <li key={index} className={action.type === "custom" ? "custom-call" : undefined}>
            {(ACTION_VERBS[String(action.type)] ?? (() => String(action.type)))(action)}
          </li>
        ))}
      </ol>
    </div>
  );
}
