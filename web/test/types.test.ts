import { expect, test } from "bun:test";
import { GlobalRegistrator } from "@happy-dom/global-registrator";
import { renderPlanTypes, TYPES_PATH } from "../scripts/plan-types";

// json-schema-to-typescript resolves file URLs, which happy-dom's URL rejects.
async function withoutDom<T>(run: () => Promise<T>): Promise<T> {
  await GlobalRegistrator.unregister();
  try {
    return await run();
  } finally {
    GlobalRegistrator.register();
  }
}

test("committed plan types match the schema", async () => {
  expect(await Bun.file(TYPES_PATH).text()).toBe(await withoutDom(renderPlanTypes));
});
