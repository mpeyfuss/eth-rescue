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
      {address("victim", "Compromised wallet", "The address whose key leaked.")}
      {address("safe", "Safe wallet", "Receives every rescued asset and leftover ETH. Use a fresh wallet.")}
      {address(
        "gas_wallet",
        "Gas wallet (optional)",
        "Pays for the rescue. If set, the CLI checks the key you type matches it.",
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
