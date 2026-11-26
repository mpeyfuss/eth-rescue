import { useState } from "react";
import type { Mode } from "../App";
import { assistantPrompt, COMMANDS, detectOs, type Os, OS_GUIDES, UV_DOCS } from "../guide";
import { CommandLine, CopyButton } from "./Command";
import { BreakGlassCards } from "./Education";

export function InstallSection({ mode = "prepare", onContinue }: { mode?: Mode; onContinue?: () => void }) {
  const [os, setOs] = useState<Os>(() => detectOs(navigator.userAgent));
  const preparing = mode === "prepare";

  return (
    <section className="card">
      <h2>{preparing ? "Before an emergency" : "Before you start"}</h2>
      <p className="muted">
        {preparing
          ? "Most time lost during a compromise goes to finding a clean computer, installing tools, waiting on an exchange withdrawal for gas, or hunting down contract addresses under stress. Do them now so a rescue takes minutes instead of hours."
          : "This page only builds the plan. The rescue itself runs on your computer with the eth-rescue command-line tool. Use a computer you trust: if malware stole the key, don't run the rescue on that machine."}
      </p>
      {preparing && <BreakGlassCards />}
      <h3>{preparing ? "Install the rescue tool and fund a gas wallet" : "Install eth-rescue on a clean computer"}</h3>
      <CliInstallSteps os={os} setOs={setOs} preparing={preparing} />
      <AiAssistantCard os={os} />
      {onContinue && (
        <div className="step-footer">
          <span className="muted">You can finish this later and enter addresses now.</span>
          <button type="button" className="primary" onClick={onContinue}>
            Next: Set up wallets →
          </button>
        </div>
      )}
    </section>
  );
}

export function CliInstallSteps({
  os,
  setOs,
  preparing = false,
}: {
  os: Os;
  setOs: (os: Os) => void;
  preparing?: boolean;
}) {
  const guide = OS_GUIDES[os];
  return (
    <div>
      <div className="tabs" role="group" aria-label="Operating system">
        {(Object.keys(OS_GUIDES) as Os[]).map((value) => (
          <button key={value} type="button" aria-pressed={os === value} onClick={() => setOs(value)}>
            {OS_GUIDES[value].label}
          </button>
        ))}
      </div>
      <ol className="steps compact-steps">
        <li>
          Open {guide.terminal}
          {preparing ? " (on your backup computer too, if you have one)" : ""}.
        </li>
        <li>
          Install <a href={UV_DOCS} target="_blank" rel="noreferrer">uv</a>, which runs the rescue tool, then close and
          reopen the terminal:
          <CommandLine command={guide.installUv} />
        </li>
        <li>
          {preparing
            ? "Download eth-rescue now so it starts right away when you need it:"
            : "Check that eth-rescue runs. The first run downloads it:"}
          <CommandLine command={COMMANDS.check} />
        </li>
        <li>
          {preparing
            ? "Create a gas wallet and send it 0.02–0.05 ETH. Write down its private key, and enter its address in the Wallets step:"
            : "No gas wallet yet? Create one. It prints the private key once, so save it:"}
          <CommandLine command={COMMANDS.gasWallet} />
        </li>
      </ol>
    </div>
  );
}

export function AiAssistantCard({ os }: { os: Os }) {
  const prompt = assistantPrompt(os);
  return (
    <div className="ai-helper-card">
      <div className="ai-helper-top">
        <div>
          <strong>New to the terminal?</strong>
          <p className="muted">
            Paste this prompt into Claude or ChatGPT. It walks you through one command at a time and tells the assistant
            never to ask for your keys or seed phrase.
          </p>
        </div>
        <CopyButton text={prompt} label="Copy prompt" />
      </div>
      <details>
        <summary>Show prompt ({OS_GUIDES[os].label})</summary>
        <pre className="preview prompt" aria-label="Assistant prompt">
          {prompt}
        </pre>
      </details>
    </div>
  );
}
