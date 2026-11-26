import { expect, test } from "bun:test";
import { TYPES_PATH } from "../scripts/plan-types";

// Rendered in a separate process: json-schema-to-typescript resolves file URLs,
// which happy-dom's URL rejects, and tearing down the shared DOM here would break
// every later test file.
async function renderInSubprocess(): Promise<string> {
  const proc = Bun.spawn(
    ["bun", "-e", 'import { renderPlanTypes } from "./scripts/plan-types"; process.stdout.write(await renderPlanTypes());'],
    { cwd: `${import.meta.dir}/..`, stdout: "pipe", stderr: "pipe" },
  );
  const [out, err, code] = await Promise.all([new Response(proc.stdout).text(), new Response(proc.stderr).text(), proc.exited]);
  if (code !== 0) throw new Error(err);
  return out;
}

test("committed plan types match the schema", async () => {
  expect(await Bun.file(TYPES_PATH).text()).toBe(await renderInSubprocess());
});
