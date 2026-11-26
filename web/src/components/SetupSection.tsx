import { type Draft, type DraftEvent, type DraftField, type Network, NETWORKS } from "../plan/draft";
import { TextField } from "./Field";

interface Props {
  draft: Draft;
  dispatch: (event: DraftEvent) => void;
  errorFor: (path: string, value: string) => string | undefined;
}

export function SetupSection({ draft, dispatch, errorFor }: Props) {
  const set = (field: DraftField) => (value: string | boolean) => dispatch({ kind: "set", field, value });
  const address = (field: "victim" | "safe" | "gas_wallet", label: string, hint: string) => (
    <TextField
      label={label}
      value={draft[field]}
      onChange={set(field)}
      error={errorFor(field, draft[field])}
      hint={hint}
      placeholder="0x…"
      mono
    />
  );

  return (
    <section className="card">
      <h2>1. Wallets</h2>
      <WalletDiagram />
      {NETWORKS.length > 1 && (
        <div className="field">
          <label htmlFor="network">Network</label>
          <select id="network" value={draft.network} onChange={(event) => set("network")(event.target.value as Network)}>
            {NETWORKS.map((network) => (
              <option key={network.value} value={network.value}>
                {network.label}
              </option>
            ))}
          </select>
        </div>
      )}
      {address("victim", "Compromised wallet", "The public 0x… address of the wallet whose key or seed phrase leaked.")}
      {address("safe", "Safe wallet", "Your new, secure wallet address. Receives every rescued asset and leftover ETH.")}
      {address(
        "gas_wallet",
        "Gas wallet (optional)",
        "A separate wallet with ETH is required to pay gas fees when you run the rescue. You can leave this blank now, or paste its address here so the CLI checks you enter the matching key later.",
      )}
      <details>
        <summary>Submission options</summary>
        <TextField
          label="Priority fee (gwei)"
          value={draft.priority_fee_gwei}
          onChange={set("priority_fee_gwei")}
          error={errorFor("options.priority_fee_gwei", draft.priority_fee_gwei)}
          hint="Extra tip on top of the CLI's fee estimate."
        />
        <TextField
          label="Max blocks"
          value={draft.max_blocks}
          onChange={set("max_blocks")}
          error={errorFor("options.max_blocks", draft.max_blocks)}
          hint="How many blocks the CLI keeps resubmitting before giving up."
        />
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.legacy_fallback}
            onChange={(event) => set("legacy_fallback")(event.target.checked)}
          />
          If any action fails, retry it as a direct transaction from the compromised wallet
        </label>
      </details>
    </section>
  );
}

function WalletDiagram() {
  return (
    <div className="wallet-guide" aria-label="How the three wallets work and how to stay secure">
      <div className="wallet-flow">
        <div className="wallet-node">
          <div className="wallet-node-header">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              <path d="M11 2L4 11h5l-1 7 8-9h-5l1-7z" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <strong>Gas wallet</strong>
          </div>
          <p>Pays transaction fees for the rescue bundle so ETH never sits in the compromised wallet.</p>
          <span className="wallet-badge">Key: typed in terminal</span>
        </div>
        <div className="wallet-arrow" aria-hidden="true">
          <span>Pays fees</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M4 12h16m0 0l-5-5m5 5l-5 5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div className="wallet-node compromised">
          <div className="wallet-node-header">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              <path d="M10 3L2 17h16L10 3z" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M10 8v4m0 2.5h.01" strokeLinecap="round" />
            </svg>
            <strong>Compromised wallet</strong>
          </div>
          <p>Signs permission to move your assets out and sweeps any leftover ETH at the end.</p>
          <span className="wallet-badge">Key: typed in terminal</span>
        </div>
        <div className="wallet-arrow" aria-hidden="true">
          <span>Sends assets</span>
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M4 12h16m0 0l-5-5m5 5l-5 5" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </div>
        <div className="wallet-node safe">
          <div className="wallet-node-header">
            <svg viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
              <path d="M10 2l6 2.5v4.8c0 4.1-2.6 7.4-6 8.7-3.4-1.3-6-4.6-6-8.7V4.5L10 2z" strokeLinejoin="round" />
              <path d="M7.5 10l1.8 1.8 3.5-3.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            <strong>Safe wallet</strong>
          </div>
          <p>A fresh wallet you control. Receives all rescued tokens, NFTs, ownership, and ETH.</p>
          <span className="wallet-badge ok">Key: never entered anywhere</span>
        </div>
      </div>
      <ul className="security-rules">
        <li>
          <strong>Public addresses only on this page:</strong> Only paste <code>0x…</code> addresses here. Never enter a
          private key or seed phrase on any website or chat.
        </li>
        <li>
          <strong>Use a clean computer:</strong> If malware or a bad download stole your key, create your safe wallet
          and run the rescue on a different device.
        </li>
        <li>
          <strong>Keep your safe wallet key offline:</strong> The rescue only needs your safe wallet’s public{" "}
          <code>0x…</code> address—never type its private key even in the terminal.
        </li>
      </ul>
    </div>
  );
}
