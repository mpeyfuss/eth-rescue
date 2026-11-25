import Ajv2020, { type ErrorObject, type ValidateFunction } from "ajv/dist/2020";
import schema from "../../../schema/plan.v1.schema.json";
import { addressProblem, sameAddress } from "./address";
import { customArgsProblem } from "./custom";
import { isAvailableNetwork } from "./draft";
import type { Plan } from "./types.gen";

export interface Issue {
  path: string;
  message: string;
}

const MAX_UINT256 = 2n ** 256n - 1n;
const ZERO_ADDRESS = "0x0000000000000000000000000000000000000000";
const PLAN_ADDRESS_FIELDS = ["victim", "safe", "gas_wallet"];
const ACTION_ADDRESS_FIELDS = ["contract", "auction_house", "target"];
const ACTION_UINT_FIELDS = ["amount", "token_id"];

// Pydantic's `discriminator` keyword is not ajv's dialect, so strict mode is off and
// actions are validated one at a time against the $def their `type` names.
const ajv = new Ajv2020({ allErrors: true, strict: false });
ajv.addSchema(schema, "plan");

const actionDefs: Record<string, string> = schema.properties.actions.items.discriminator.mapping;
const actionValidators = Object.fromEntries(
  Object.entries(actionDefs).map(([type, ref]) => [type, ajv.getSchema(`plan${ref}`) as ValidateFunction]),
);
const planValidator = ajv.compile({
  ...schema,
  $id: "plan-shell",
  properties: { ...schema.properties, actions: { type: "array", minItems: 1 } },
});

function pointerToPath(pointer: string): string {
  return pointer.split("/").filter(Boolean).join(".");
}

function joinPath(...parts: (string | number)[]): string {
  return parts.filter((part) => part !== "").join(".");
}

function describe(error: ErrorObject): { field: string; message: string } {
  const field = pointerToPath(error.instancePath);
  switch (error.keyword) {
    case "required":
      return { field: joinPath(field, error.params.missingProperty), message: "is required" };
    case "additionalProperties":
      return { field: joinPath(field, error.params.additionalProperty), message: "is not a known field" };
    case "pattern":
      return { field, message: patternMessage(error.params.pattern) };
    case "exclusiveMinimum":
      return { field, message: `must be greater than ${error.params.limit}` };
    case "maximum":
      return { field, message: `must be at most ${error.params.limit}` };
    case "minItems":
      return { field, message: "needs at least one action" };
    default:
      return { field, message: error.message ?? "is invalid" };
  }
}

function patternMessage(pattern: string): string {
  if (pattern.includes("{40}")) return "must be a 0x address with 40 hex characters";
  if (pattern.includes("{1,78}")) return "must be a whole number";
  if (pattern.includes("{1,9}")) return "must be a gwei amount with at most 9 decimals";
  if (pattern.includes("\\(")) return "must look like name(type,...)";
  return `must match ${pattern}`;
}

function schemaIssues(validate: ValidateFunction, value: unknown, prefix = ""): Issue[] {
  if (validate(value)) return [];
  const issues = (validate.errors ?? []).map((error) => {
    const { field, message } = describe(error);
    return { path: joinPath(prefix, field), message };
  });
  // anyOf branches (e.g. `string | null`) repeat the same failure; keep the first.
  return issues.filter((issue, index) => issues.findIndex((other) => other.path === issue.path) === index);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function addressIssue(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const problem = addressProblem(value);
  if (problem) return problem;
  return sameAddress(value, ZERO_ADDRESS) ? "must not be the zero address" : null;
}

// Field checks run even when the schema already failed elsewhere in the action,
// so one error never hides another; a path the schema flagged is not repeated.
function fieldIssues(action: Record<string, unknown>, prefix: string, victim: unknown, flagged: Issue[]): Issue[] {
  const issues: Issue[] = [];
  const add = (field: string, message: string) => {
    const path = joinPath(prefix, field);
    if (!flagged.some((issue) => issue.path === path)) issues.push({ path, message });
  };
  for (const field of ACTION_ADDRESS_FIELDS) {
    const problem = addressIssue(action[field]);
    if (problem) add(field, problem);
    else if (typeof action[field] === "string" && typeof victim === "string" && sameAddress(action[field], victim)) {
      add(field, "must not be the compromised wallet");
    }
  }
  for (const field of ACTION_UINT_FIELDS) {
    const value = action[field];
    if (typeof value === "string" && /^\d+$/.test(value) && BigInt(value) > MAX_UINT256) add(field, "exceeds uint256");
  }
  if (action.type === "custom" && typeof action.signature === "string" && Array.isArray(action.args)) {
    const problem = customArgsProblem(action.signature, action.args);
    if (problem) add("args", problem);
  }
  return issues;
}

function actionIssues(action: unknown, index: number, victim: unknown): Issue[] {
  const prefix = `actions.${index}`;
  const type = isRecord(action) ? action.type : undefined;
  const validate = typeof type === "string" && Object.hasOwn(actionValidators, type) ? actionValidators[type] : undefined;
  if (!validate || !isRecord(action)) return [{ path: joinPath(prefix, "type"), message: "is not a known action type" }];
  const issues = schemaIssues(validate, action, prefix);
  return [...issues, ...fieldIssues(action, prefix, victim, issues)];
}

function walletIssues(plan: Record<string, unknown>, flagged: Issue[]): Issue[] {
  const issues: Issue[] = [];
  for (const field of PLAN_ADDRESS_FIELDS) {
    if (flagged.some((issue) => issue.path === field)) continue;
    const problem = addressIssue(plan[field]);
    if (problem) issues.push({ path: field, message: problem });
  }
  if (issues.length > 0 || flagged.some((issue) => PLAN_ADDRESS_FIELDS.includes(issue.path))) return issues;
  const { victim, safe, gas_wallet } = plan as Pick<Plan, "victim" | "safe" | "gas_wallet">;
  if (sameAddress(safe, victim)) issues.push({ path: "safe", message: "must differ from victim" });
  if (gas_wallet && (sameAddress(gas_wallet, victim) || sameAddress(gas_wallet, safe))) {
    issues.push({ path: "gas_wallet", message: "must differ from victim and safe" });
  }
  return issues;
}

export function validatePlan(value: unknown): Issue[] {
  if (!isRecord(value)) return [{ path: "", message: "plan must be a JSON object" }];
  const issues = schemaIssues(planValidator, value);
  if (!issues.some((issue) => issue.path === "network") && !isAvailableNetwork(value.network)) {
    issues.push({ path: "network", message: "is not supported here" });
  }
  issues.push(...walletIssues(value, issues));
  if (Array.isArray(value.actions)) {
    issues.push(...value.actions.flatMap((action, index) => actionIssues(action, index, value.victim)));
  }
  return issues;
}

export function isValidPlan(value: unknown): value is Plan {
  return validatePlan(value).length === 0;
}
