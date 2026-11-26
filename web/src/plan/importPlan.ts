import { looksLikeMnemonic, looksLikeSecret, sameAddress } from "./address";
import { type Draft, fromPlan, PlanImportError, toPlan } from "./draft";
import { type Issue, validatePlan } from "./validate";

export interface PlanImport {
  draft: Draft;
  // Problems in the file as written, before anything was converted.
  issues: Issue[];
  // Every way the draft differs from the file; the user confirms these.
  repairs: string[];
}

const OPTION_DEFAULTS: Record<string, unknown> = {
  "options.priority_fee_gwei": "0",
  "options.max_blocks": 25,
  "options.legacy_fallback": true,
};
const ADDRESS_RE = /^0x[0-9a-fA-F]{40}$/;

function walk(value: unknown, path: string, visit: (value: unknown, path: string) => void): void {
  visit(value, path);
  if (Array.isArray(value)) value.forEach((item, index) => walk(item, path ? `${path}.${index}` : `${index}`, visit));
  else if (typeof value === "object" && value !== null) {
    for (const [key, item] of Object.entries(value)) walk(item, path ? `${path}.${key}` : key, visit);
  }
}

function rejectUnsafe(raw: unknown): void {
  walk(raw, "", (value, path) => {
    if (typeof value === "number" && !Number.isSafeInteger(value) && Math.abs(value) > Number.MAX_SAFE_INTEGER) {
      throw new PlanImportError(`${path} cannot be read exactly by a browser; write it as a quoted string`);
    }
    if (typeof value !== "string") return;
    // bytes32 custom arguments look exactly like private keys.
    const isSecret = /^actions\.\d+\.args/.test(path) ? looksLikeMnemonic : looksLikeSecret;
    if (isSecret(value)) {
      throw new PlanImportError(`${path} looks like a private key or seed phrase; plans never contain secrets`);
    }
  });
}

function leaves(value: unknown): Map<string, unknown> {
  const out = new Map<string, unknown>();
  walk(value, "", (item, path) => {
    const container = typeof item === "object" && item !== null;
    const empty = container && Object.keys(item).length === 0;
    if (path && (!container || empty)) out.set(path, item);
  });
  return out;
}

function sameLeaf(a: unknown, b: unknown): boolean {
  if (typeof a === "string" && typeof b === "string" && ADDRESS_RE.test(a) && ADDRESS_RE.test(b)) {
    return sameAddress(a, b);
  }
  return JSON.stringify(a) === JSON.stringify(b);
}

function describeRepairs(raw: unknown, result: unknown): string[] {
  const before = leaves(raw);
  const after = leaves(result);
  const repairs: string[] = [];
  for (const [path, value] of before) {
    if (!after.has(path)) repairs.push(`drops ${path} (${JSON.stringify(value)})`);
    else if (!sameLeaf(value, after.get(path))) {
      repairs.push(`changes ${path} from ${JSON.stringify(value)} to ${JSON.stringify(after.get(path))}`);
    }
  }
  for (const [path, value] of after) {
    if (before.has(path)) continue;
    if (path in OPTION_DEFAULTS && sameLeaf(OPTION_DEFAULTS[path], value)) continue;
    repairs.push(`adds ${path} = ${JSON.stringify(value)}`);
  }
  return repairs;
}

export function importPlan(text: string): PlanImport {
  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch (error) {
    throw new PlanImportError(`not valid JSON (${error instanceof Error ? error.message : String(error)})`);
  }
  rejectUnsafe(raw);
  const draft = fromPlan(raw);
  return { draft, issues: validatePlan(raw), repairs: describeRepairs(raw, toPlan(draft)) };
}

/** What may be kept across reloads: never custom arguments, never anything
 * that looks like a secret. */
export function persistablePlan(plan: Record<string, unknown>): Record<string, unknown> {
  const copy = JSON.parse(JSON.stringify(plan));
  if (Array.isArray(copy.actions)) {
    for (const action of copy.actions) if (action?.type === "custom") action.args = [];
  }
  walk(copy, "", (value) => {
    if (typeof value !== "object" || value === null) return;
    for (const [key, item] of Object.entries(value)) {
      if (typeof item === "string" && looksLikeSecret(item)) (value as Record<string, unknown>)[key] = "";
    }
  });
  return copy;
}
