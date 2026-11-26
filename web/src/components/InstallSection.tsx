import { useState } from "react";
import type { Mode } from "../App";
import { assistantPrompt, COMMANDS, detectOs, type Os, OS_GUIDES, UV_DOCS } from "../guide";
import { CommandLine, CopyButton } from "./Command";

export function InstallSection({ mode = "emergency" }: { mode?: Mode }) {
  const [os, setOs] = useState<Os>(() => detectOs(navigator.userAgent));
  const guide = OS_GUIDES[os];
  const prompt = assistantPrompt(os);
  const preparing = mode === "prepare";

  return (
    <section className="card">
      <h2>{preparing ? "Before an emergency" : "Before you start"}</h2>
      <p className="muted">
        {preparing
          ? "Most time lost during a wallet compromise goes to finding a clean computer, installing tools, waiting on an exchange withdrawal for gas, or hunting down contract addresses under stress. Prepare those pieces now so you can run a rescue in seconds if a key ever leaks."
          : "This page only builds the plan. The rescue itself runs on your computer with the eth-rescue command-line tool. Use a computer you trust: if malware stole the key, don't run the rescue on that machine."}
      </p>
      {preparing && (
        <div className="prep-guide">
          <h3>Assume your main computer could be what gets compromised</h3>
          <p className="muted">
            If malware, a trojan download, or a bad browser extension steals your wallet key, you cannot safely run the
            rescue on that same machine—typing your gas wallet key there would hand the attacker your gas ETH too.
          </p>
          <ul className="security-rules">
            <li>
              <strong>Pick a fallback computer now:</strong> Decide which second machine you would use (a spare laptop,
              or a partner’s or trusted friend’s computer). If you have a secondary laptop, run the terminal install
              steps below on <em>that</em> machine now and keep it clean of day-to-day browsing.
            </li>
            <li>
              <strong>Create your Safe wallet off any computer:</strong> Use a hardware wallet or a fresh mobile wallet
              on your phone for the safe wallet. Because the rescue only needs its public <code>0x…</code> address, its
              seed phrase never has to touch a computer.
            </li>
            <li>
              <strong>Keep keys and plan.json reachable without booting the infected machine:</strong> Keep your wallet
              backups and standby gas wallet key readable offline (on paper) or on your phone—so you don’t have to turn
              on a compromised computer to copy them, or sign into your entire password manager on a borrowed machine.
            </li>
          </ul>
          <h3>Pre-install the CLI and set up a standby gas wallet</h3>
        </div>
      )}
      <div className="tabs" role="group" aria-label="Operating system">
        {(Object.keys(OS_GUIDES) as Os[]).map((value) => (
          <button key={value} type="button" aria-pressed={os === value} onClick={() => setOs(value)}>
            {OS_GUIDES[value].label}
          </button>
        ))}
      </div>
      <ol className="steps">
        <li>
          Open {guide.terminal}
          {preparing ? " (ideally on your clean fallback computer, as well as your main one)" : ""}.
        </li>
        <li>
          Install <a href={UV_DOCS}>uv</a> (runs the rescue tool), then close the terminal and open a new one:
          <CommandLine command={guide.installUv} />
        </li>
        <li>
          {preparing
            ? "Pre-download eth-rescue and its Python runtime now so it starts immediately on rescue day:"
            : "Check that eth-rescue runs. The first run downloads it and a compatible Python:"}
          <CommandLine command={COMMANDS.check} />
        </li>
        <li>
          {preparing
            ? "Create a dedicated standby gas wallet and fund it with a small ETH buffer (e.g. 0.02–0.05 ETH). Save its private key offline or on your phone, and paste its public 0x… address into Gas wallet below—having gas ready avoids waiting on an exchange withdrawal while a sweeper bot watches your wallet:"
            : "Need a separate wallet to pay gas fees? If you don't already have another uncompromised wallet with ETH, create a fresh one and put its address in the form below. It prints the private key once; save it somewhere safe."}
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
