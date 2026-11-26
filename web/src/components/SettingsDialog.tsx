import { useState } from "react";
import { BUILT_IN_KEY } from "../discovery/key";
import type { Draft, DraftEvent, DraftField } from "../plan/draft";
import { Dialog } from "./Dialog";
import { TextField } from "./Field";

interface Props {
  open: boolean;
  onClose: () => void;
  draft: Draft;
  dispatch: (event: DraftEvent) => void;
  errorFor: (path: string, value: string) => string | undefined;
  userKey: string | null;
  onUserKey: (key: string | null) => void;
  showAllSteps: boolean;
  onShowAllSteps: (value: boolean) => void;
}

export function SettingsDialog({
  open,
  onClose,
  draft,
  dispatch,
  errorFor,
  userKey,
  onUserKey,
  showAllSteps,
  onShowAllSteps,
}: Props) {
  const set = (field: DraftField) => (value: string | boolean) => dispatch({ kind: "set", field, value });

  return (
    <Dialog open={open} title="Settings" closeLabel="Close settings" narrow onClose={onClose}>
      <section className="settings-group">
        <h3>Rescue</h3>
        <p className="muted">Saved in plan.json.</p>
        <label className="checkbox">
          <input
            type="checkbox"
            checked={draft.legacy_fallback}
            onChange={(event) => set("legacy_fallback")(event.target.checked)}
          />
          <span>Retry failed actions as direct transactions</span>
        </label>
        <p className="field-hint">
          Some older contracts reject the first, bundled attempt. With this on, the tool retries only the actions that
          failed, sent straight from the compromised wallet.
        </p>
        <div className="action-row">
          <TextField
            label="Priority fee (gwei)"
            value={draft.priority_fee_gwei}
            onChange={set("priority_fee_gwei")}
            error={errorFor("options.priority_fee_gwei", draft.priority_fee_gwei)}
            hint="Extra tip on top of the live fee estimate."
          />
          <TextField
            label="Max blocks"
            value={draft.max_blocks}
            onChange={set("max_blocks")}
            error={errorFor("options.max_blocks", draft.max_blocks)}
            hint="How many blocks to keep trying before giving up."
          />
        </div>
      </section>

      <section className="settings-group">
        <h3>Asset scan</h3>
        <AlchemyKeyField userKey={userKey} onChange={onUserKey} />
      </section>

      <section className="settings-group">
        <h3>Layout</h3>
        <label className="checkbox">
          <input type="checkbox" checked={showAllSteps} onChange={(event) => onShowAllSteps(event.target.checked)} />
          <span>Show all steps on one page</span>
        </label>
      </section>
    </Dialog>
  );
}

function AlchemyKeyField({ userKey, onChange }: { userKey: string | null; onChange: (key: string | null) => void }) {
  const [text, setText] = useState("");
  const status = userKey
    ? "Using your own Alchemy key."
    : BUILT_IN_KEY
      ? "Using the shared key built into this site."
      : "No key set, so the scan only finds Transient listings and contracts.";

  return (
    <div>
      <p className="muted">
        The scan finds tokens and NFTs through Alchemy. The shared key can hit rate limits when the site is busy. If
        scans fail, add your own free key from alchemy.com. It stays in this browser, and Alchemy sees which address you
        look up.
      </p>
      <p className="settings-status">{status}</p>
      <TextField label="Your Alchemy API key" value={text} onChange={setText} mono />
      <div className="buttons">
        <button
          type="button"
          disabled={!text.trim()}
          onClick={() => {
            onChange(text.trim());
            setText("");
          }}
        >
          Use this key
        </button>
        {userKey && (
          <button type="button" onClick={() => onChange(null)}>
            Remove my key
          </button>
        )}
      </div>
    </div>
  );
}
