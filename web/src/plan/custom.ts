import { type AbiFunction, type AbiParameter, encodeFunctionData, parseAbiItem } from "viem";

export function parseSignature(signature: string): AbiFunction {
  const item = parseAbiItem(`function ${signature}`);
  if (item.type !== "function") throw new Error("not a function signature");
  return item;
}

export function tryParseSignature(signature: string): AbiFunction | null {
  try {
    return parseSignature(signature);
  } catch {
    return null;
  }
}

const ARRAY_SUFFIX = /\[\d*\]$/;

function components(param: AbiParameter): readonly AbiParameter[] {
  return "components" in param ? param.components : [];
}

// Mirrors calldata._coerce in the CLI: integers may be decimal or 0x strings,
// bytes are 0x strings, arrays and tuples are JSON arrays.
function coerce(param: AbiParameter, value: unknown): unknown {
  if (ARRAY_SUFFIX.test(param.type)) {
    if (!Array.isArray(value)) throw new Error(`expected an array for ${param.type}`);
    const item = { ...param, type: param.type.replace(ARRAY_SUFFIX, "") };
    return value.map((entry) => coerce(item, entry));
  }
  if (param.type === "tuple") {
    const parts = components(param);
    if (!Array.isArray(value) || value.length !== parts.length) {
      throw new Error(`expected ${parts.length} values for a tuple`);
    }
    return parts.map((part, index) => coerce(part, value[index]));
  }
  if (/^u?int\d*$/.test(param.type)) {
    if (typeof value === "number" || typeof value === "string") return BigInt(value);
    throw new Error(`expected an integer for ${param.type}`);
  }
  return value;
}

export function customArgsProblem(signature: string, args: unknown[]): string | null {
  let fn: AbiFunction;
  try {
    fn = parseSignature(signature);
  } catch {
    return `could not parse function signature ${signature}`;
  }
  if (fn.inputs.length !== args.length) {
    return `${signature} expects ${fn.inputs.length} args, got ${args.length}`;
  }
  try {
    const coerced = fn.inputs.map((param, index) => coerce(param, args[index]));
    encodeFunctionData({ abi: [fn], functionName: fn.name, args: coerced });
  } catch (error) {
    return `args do not encode: ${error instanceof Error ? error.message.split("\n")[0] : error}`;
  }
  return null;
}

// Form text to a JSON plan value: strings stay strings, everything else may be JSON.
export function argFromText(text: string, param?: AbiParameter): unknown {
  if (param?.type === "string") return text;
  const trimmed = text.trim();
  if (trimmed === "true" || trimmed === "false") return trimmed === "true";
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
