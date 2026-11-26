import type { JSONSchema4 } from "json-schema";
import { compile } from "json-schema-to-typescript";
import schema from "../../schema/plan.v1.schema.json";

export const TYPES_PATH = new URL("../src/plan/types.gen.ts", import.meta.url).pathname;

// Pydantic titles every property, which json2ts turns into one alias per field.
// Keep titles only on the root and the named $defs.
function stripTitles(node: unknown): unknown {
  if (Array.isArray(node)) return node.map(stripTitles);
  if (node === null || typeof node !== "object") return node;
  const entries = Object.entries(node).filter(([key]) => key !== "title");
  return Object.fromEntries(entries.map(([key, value]) => [key, stripTitles(value)]));
}

function namedSchema(): JSONSchema4 {
  const { $defs, ...root } = schema as Record<string, unknown> & { $defs: Record<string, object> };
  const defs: Record<string, object> = Object.fromEntries(
    Object.entries($defs).map(([name, def]) => [name, { ...(stripTitles(def) as object), title: name }]),
  );
  // Pydantic's JsonValue is `{}` (any JSON), which json2ts would read as an object.
  defs.JsonValue = { title: "JsonValue", tsType: "unknown" };
  return { ...(stripTitles(root) as object), title: "Plan", $defs: defs } as JSONSchema4;
}

export function renderPlanTypes(): Promise<string> {
  return compile(namedSchema(), "Plan", {
    bannerComment: "// Generated from schema/plan.v1.schema.json by `bun run gen:types`. Do not edit.",
    additionalProperties: false,
  });
}
