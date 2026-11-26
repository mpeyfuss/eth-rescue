import { describe, expect, test } from "bun:test";
import { Glob } from "bun";
import { fromPlan, toPlan } from "../src/plan/draft";
import { validatePlan } from "../src/plan/validate";

const EXAMPLES_DIR = new URL("../../examples/", import.meta.url).pathname;
const examples = [...new Glob("*.json").scanSync(EXAMPLES_DIR)].sort();

const OPTION_DEFAULTS = { priority_fee_gwei: "0", max_blocks: 25, legacy_fallback: true };

async function load(name: string): Promise<Record<string, unknown>> {
  return Bun.file(EXAMPLES_DIR + name).json();
}

test("examples exist", () => {
  expect(examples.length).toBeGreaterThan(0);
});

// examples/ is the cross-language contract: the CLI's tests load the same files.
describe.each(examples)("%s", (name) => {
  test("validates", async () => {
    expect(validatePlan(await load(name))).toEqual([]);
  });

  test("round-trips through the builder", async () => {
    const plan = await load(name);
    const expected = { ...plan, options: { ...OPTION_DEFAULTS, ...(plan.options as object) } };
    expect(toPlan(fromPlan(plan))).toEqual(expected);
  });
});
