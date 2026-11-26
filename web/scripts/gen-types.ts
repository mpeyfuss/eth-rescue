import { renderPlanTypes, TYPES_PATH } from "./plan-types";

await Bun.write(TYPES_PATH, await renderPlanTypes());
