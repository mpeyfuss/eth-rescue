import { COMMANDS, PLAN_FILE } from "../guide";
import type { Issue } from "../plan/validate";
import { CommandLine, CopyButton } from "./Command";

interface Props {
  plan: Record<string, unknown>;
  issues: Issue[];
  onReveal: () => void;
}


const PATH_LABELS: Record<string, string> = {
  "": "Plan",
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
      <h2>3. Review and export</h2>
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
        In a terminal, go to the folder where you saved {PLAN_FILE}. The CLI asks for the private keys in a hidden
        prompt, simulates the rescue, tells you how much ETH the gas wallet needs, and asks once before sending.
      </p>
      <CommandLine command={COMMANDS.validate} />
      <CommandLine command={COMMANDS.run} />
    </section>
  );
}
