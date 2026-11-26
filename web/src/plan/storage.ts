import { type Draft, emptyDraft, fromPlan, toPlan } from "./draft";
import { persistablePlan } from "./importPlan";

// Kept across reloads: addresses and typed actions. Custom arguments and
// anything that looks like a secret are left out (see persistablePlan). Each
// action's discovery tag is kept beside the plan, which has no field for it,
// so a reload cannot hide that an action was found for another wallet.
interface Stored {
  plan: unknown;
  discoveredFor: string[];
}

export function serializeDraft(draft: Draft): string {
  const stored: Stored = {
    plan: persistablePlan(toPlan(draft)),
    discoveredFor: draft.actions.map((action) => action.discoveredFor),
  };
  return JSON.stringify(stored);
}

export function deserializeDraft(text: string | null): Draft {
  if (!text) return emptyDraft();
  try {
    const value = JSON.parse(text);
    // Drafts saved before discovery tags were kept are the bare plan.
    const { plan, discoveredFor } = "plan" in value ? (value as Stored) : { plan: value, discoveredFor: [] };
    const draft = fromPlan(plan);
    draft.actions = draft.actions.map((action, index) => {
      const tag = Array.isArray(discoveredFor) ? discoveredFor[index] : undefined;
      return { ...action, discoveredFor: typeof tag === "string" ? tag : "" };
    });
    return draft;
  } catch {
    return emptyDraft();
  }
}
