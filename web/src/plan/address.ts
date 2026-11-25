import { getAddress } from "viem";

const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;
const PRIVATE_KEY_RE = /^(0x)?[0-9a-fA-F]{64}$/;
const MNEMONIC_RE = /^([a-z]+\s+){11,23}[a-z]+$/;

export function looksLikeSecret(value: string): boolean {
  const trimmed = value.trim();
  return PRIVATE_KEY_RE.test(trimmed) || MNEMONIC_RE.test(trimmed.toLowerCase());
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
