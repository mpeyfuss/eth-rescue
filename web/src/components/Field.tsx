import { type ReactNode, useId, useState } from "react";
import { looksLikeSecret } from "../plan/address";

interface TextFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  error?: string;
  hint?: ReactNode;
  placeholder?: string;
  mono?: boolean;
  // Off only where 32-byte hex is legitimate input (custom call args).
  blockSecrets?: boolean;
}

export function TextField({ label, value, onChange, error, hint, placeholder, mono, blockSecrets = true }: TextFieldProps) {
  const id = useId();
  const [blocked, setBlocked] = useState(false);

  function handleChange(next: string) {
    if (blockSecrets && looksLikeSecret(next)) {
      setBlocked(true);
      return;
    }
    setBlocked(false);
    onChange(next);
  }

  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        className={mono ? "mono" : undefined}
        value={value}
        placeholder={placeholder}
        spellCheck={false}
        autoComplete="off"
        aria-invalid={Boolean(error)}
        aria-describedby={blocked || error ? `${id}-error` : undefined}
        onChange={(event) => handleChange(event.target.value)}
      />
      {blocked && (
        <p id={`${id}-error`} className="field-error" role="alert">
          That looks like a private key or seed phrase, so it was not entered. Never paste secrets here.
        </p>
      )}
      {!blocked && error && (
        <p id={`${id}-error`} className="field-error">
          {error}
        </p>
      )}
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
}
