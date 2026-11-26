import { getAddress } from "viem";
import { english } from "viem/accounts";

const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
// 64 hex digits anywhere, e.g. "Private key: 0x…" pasted from `gas-wallet new`.
const PRIVATE_KEY_RE = /(^|[^0-9a-fA-F])(0x)?[0-9a-fA-F]{64}($|[^0-9a-fA-F])/;
const MNEMONIC_WORDS = new Set(english);
const MIN_MNEMONIC_WORDS = 12;

export function looksLikeMnemonic(value: string): boolean {
  let run = 0;
  for (const word of value.toLowerCase().split(/[^a-z]+/)) {
    if (!word) continue;
    run = MNEMONIC_WORDS.has(word) ? run + 1 : 0;
    if (run >= MIN_MNEMONIC_WORDS) return true;
  }
  return false;
}

export function looksLikeSecret(value: string): boolean {
  return PRIVATE_KEY_RE.test(value) || looksLikeMnemonic(value);
}

export function addressProblem(value: string): string | null {
  if (!ADDRESS_RE.test(value)) return "must be a 0x address with 40 hex characters";
  const digits = value.slice(2);
  const mixedCase = digits !== digits.toLowerCase() && digits !== digits.toUpperCase();
  if (mixedCase && getAddress(value.toLowerCase()) !== value) return "has an invalid checksum";
  return null;
}

export function toChecksum(value: string): string {
  return addressProblem(value) ? value : getAddress(value.toLowerCase());
}

export function sameAddress(a: string, b: string): boolean {
  return a.toLowerCase() === b.toLowerCase();
}
