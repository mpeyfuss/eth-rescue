import { useState } from "react";
import { assistantPrompt, COMMANDS, detectOs, type Os, OS_GUIDES, UV_DOCS } from "../guide";
import { CommandLine, CopyButton } from "./Command";

export function InstallSection() {
  const [os, setOs] = useState<Os>(() => detectOs(navigator.userAgent));
  const guide = OS_GUIDES[os];
  const prompt = assistantPrompt(os);

  return (
    <section className="card">
      <h2>Before you start: install the CLI</h2>
      <p className="muted">
        This page only builds the plan. The rescue itself runs on your computer with the eth-rescue command-line tool.
        Use a computer you trust: if malware stole the key, don't run the rescue on that machine.
      </p>
      <div className="tabs" role="group" aria-label="Operating system">
        {(Object.keys(OS_GUIDES) as Os[]).map((value) => (
          <button key={value} type="button" aria-pressed={os === value} onClick={() => setOs(value)}>
            {OS_GUIDES[value].label}
          </button>
        ))}
      </div>
      <ol className="steps">
        <li>
          Open {guide.terminal}.
        </li>
        <li>
          Install <a href={UV_DOCS}>uv</a>, then close the terminal and open a new one:
          <CommandLine command={guide.installUv} />
        </li>
        <li>
          Check that eth-rescue runs. The first run downloads it and a compatible Python:
          <CommandLine command={COMMANDS.check} />
        </li>
        <li>
          Optional: create a fresh gas wallet to pay for the rescue, and put its address in the form below. It prints
          the private key once; save it somewhere safe.
          <CommandLine command={COMMANDS.gasWallet} />
        </li>
      </ol>
      <details>
        <summary>Prefer step-by-step help from an AI assistant?</summary>
        <p className="muted">
          Paste this into ChatGPT, Claude or another assistant. It tells the assistant which commands to use and never to
          ask for your keys. Never share a private key or seed phrase with an assistant.
        </p>
        <pre className="preview prompt" aria-label="Assistant prompt">
          {prompt}
        </pre>
        <CopyButton text={prompt} label="Copy prompt" />
      </details>
    </section>
  );
}
