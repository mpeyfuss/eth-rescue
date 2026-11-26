import { type AbiFunction, type AbiParameter, encodeFunctionData, type Hex, parseAbiItem, toFunctionSignature } from "viem";
import { addressProblem, toChecksum } from "./address";

// Mirrors eth_rescue/calldata.py. Both sides are tested against
// tests/fixtures/abi_vectors.json, so a plan the builder accepts encodes to the
// same bytes in the CLI.

const SIGNATURE_RE = /^[A-Za-z_$][A-Za-z0-9_$]*\(.*\)$/s;
const INTEGER_RE = /^(-?[0-9]+|0x[0-9a-fA-F]+)$/;
const HEX_BYTES_RE = /^0x([0-9a-fA-F]{2})*$/;
const ARRAY_SUFFIX_RE = /\[(\d*)\]$/;
const INT_RE = /^(u?)int(\d*)$/;
const BYTES_RE = /^bytes(\d*)$/;

export class CustomCallError extends Error {}

function fail(message: string): never {
  throw new CustomCallError(message);
}

function components(param: AbiParameter): readonly AbiParameter[] {
  return "components" in param ? param.components : [];
}

function checkSupported(param: AbiParameter): void {
  const type = param.type.replace(/(\[\d*\])+$/, "");
  if (type === "tuple") return components(param).forEach(checkSupported);
  if (type === "address" || type === "bool" || type === "string") return;
  const int = INT_RE.exec(type);
  if (int) {
    const bits = Number(int[2] || 256);
    if (bits >= 8 && bits <= 256 && bits % 8 === 0) return;
  }
  const bytes = BYTES_RE.exec(type);
  if (bytes && (bytes[1] === "" || (Number(bytes[1]) >= 1 && Number(bytes[1]) <= 32))) return;
  fail(`unsupported type ${param.type}`);
}

/** The ABI of a canonical signature like `transfer(address,uint256)`; anything
 * else (aliases, spaces, parameter names, `returns`) is rejected, because the
 * selector is the hash of this exact text. */
export function parseSignature(signature: string): AbiFunction {
  if (!SIGNATURE_RE.test(signature)) fail(`${JSON.stringify(signature)} is not a function signature like name(type,...)`);
  let item;
  try {
    item = parseAbiItem(`function ${signature}`);
  } catch {
    fail(`could not parse function signature ${signature}`);
  }
  if (item.type !== "function") fail("not a function signature");
  const canonical = toFunctionSignature(item);
  if (canonical !== signature) fail(`signature must be canonical: use ${canonical} instead of ${signature}`);
  item.inputs.forEach(checkSupported);
  return item;
}

export function tryParseSignature(signature: string): AbiFunction | null {
  try {
    return parseSignature(signature);
  } catch {
    return null;
  }
}

function toInteger(type: string, value: unknown, path: string): bigint {
  let result: bigint;
  if (typeof value === "number") {
    if (!Number.isSafeInteger(value)) fail(`${path}: ${value} is not an exact integer; write it as a string`);
    result = BigInt(value);
  } else if (typeof value === "string" && INTEGER_RE.test(value)) {
    result = BigInt(value);
  } else {
    fail(`${path}: expected an integer or a decimal/0x string`);
  }
  const [, unsigned, size] = INT_RE.exec(type) as RegExpExecArray;
  const bits = BigInt(size || 256);
  const [min, max] = unsigned ? [0n, 2n ** bits - 1n] : [-(2n ** (bits - 1n)), 2n ** (bits - 1n) - 1n];
  if (result < min || result > max) fail(`${path}: out of range for ${type}`);
  return result;
}

function toBytes(type: string, value: unknown, path: string): Hex {
  if (typeof value !== "string" || !HEX_BYTES_RE.test(value)) fail(`${path}: expected a 0x hex string of whole bytes`);
  const size = (BYTES_RE.exec(type) as RegExpExecArray)[1];
  const length = (value.length - 2) / 2;
  if (size && length !== Number(size)) fail(`${path}: expected exactly ${size} bytes, got ${length}`);
  return value as Hex;
}

function coerce(param: AbiParameter, value: unknown, path: string): unknown {
  const array = ARRAY_SUFFIX_RE.exec(param.type);
  if (array) {
    if (!Array.isArray(value)) fail(`${path}: expected an array for ${param.type}`);
    if (array[1] && value.length !== Number(array[1])) fail(`${path}: expected ${array[1]} items, got ${value.length}`);
    const item = { ...param, type: param.type.replace(ARRAY_SUFFIX_RE, "") };
    return value.map((entry, index) => coerce(item, entry, `${path}[${index}]`));
  }
  if (param.type === "tuple") {
    const parts = components(param);
    if (!Array.isArray(value) || value.length !== parts.length) fail(`${path}: expected ${parts.length} values for a tuple`);
    return parts.map((part, index) => coerce(part, value[index], `${path}[${index}]`));
  }
  if (INT_RE.test(param.type)) return toInteger(param.type, value, path);
  if (BYTES_RE.test(param.type)) return toBytes(param.type, value, path);
  switch (param.type) {
    case "address": {
      if (typeof value !== "string") fail(`${path}: expected a 0x address with 40 hex characters`);
      const problem = addressProblem(value);
      if (problem) fail(`${path}: ${problem === "has an invalid checksum" ? "invalid address checksum" : problem}`);
      return toChecksum(value);
    }
    case "bool":
      if (typeof value !== "boolean") fail(`${path}: expected true or false`);
      return value;
    case "string":
      if (typeof value !== "string") fail(`${path}: expected a string`);
      return value;
  }
  fail(`${path}: unsupported type ${param.type}`);
}

export function encodeCustomCall(signature: string, args: unknown[]): Hex {
  const fn = parseSignature(signature);
  if (fn.inputs.length !== args.length) fail(`${signature} expects ${fn.inputs.length} args, got ${args.length}`);
  const coerced = fn.inputs.map((param, index) => coerce(param, args[index], `args[${index}]`));
  try {
    return encodeFunctionData({ abi: [fn], functionName: fn.name, args: coerced });
  } catch (error) {
    fail(`args do not encode: ${error instanceof Error ? error.message.split("\n")[0] : String(error)}`);
  }
}

export function customArgsProblem(signature: string, args: unknown[]): string | null {
  try {
    encodeCustomCall(signature, args);
    return null;
  } catch (error) {
    if (error instanceof CustomCallError) return error.message;
    throw error;
  }
}

/** Form text to a JSON plan value. Arrays and tuples are JSON; integers inside
 * them must be quoted when they exceed 2^53 (an unquoted one is reported). */
export function argFromText(text: string, param?: AbiParameter): unknown {
  if (param?.type === "string") return text;
  const trimmed = text.trim();
  if ((!param || param.type === "bool") && (trimmed === "true" || trimmed === "false")) return trimmed === "true";
  if (trimmed.startsWith("[")) {
    try {
      return JSON.parse(trimmed);
    } catch {
      return trimmed;
    }
  }
  return trimmed;
}

export function argToText(value: unknown): string {
  return typeof value === "string" ? value : JSON.stringify(value);
}
