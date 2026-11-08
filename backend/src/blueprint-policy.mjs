import { SIM_RECIPES } from "./recipes.mjs";

/** Server-side capability scope. A client label is never an authorization grant. */
export const BLUEPRINT_POLICIES = Object.freeze({
  single: Object.freeze({ id: "single", version: 1, recipe: SIM_RECIPES.single.id,
    targets: ["Q12"], skills: ["readout_resonator_scan", "f01_spectroscopy"] }),
  two: Object.freeze({ id: "two", version: 1, recipe: SIM_RECIPES.two.id,
    targets: ["Q12", "Q13", "Q12-Q13"], skills: ["readout_resonator_scan", "f01_spectroscopy", "cz_calibration"] }),
});

export function resolveBlueprint(id, version) {
  const policy = BLUEPRINT_POLICIES[id];
  if (!policy || version !== policy.version) {
    const error = new Error("未知或过期的蓝图版本");
    error.code = "BLUEPRINT_VERSION_UNSUPPORTED";
    throw error;
  }
  return policy;
}

export function assertBlueprintCapability({ blueprintId, blueprintVersion, recipeId, target }) {
  const policy = resolveBlueprint(blueprintId, blueprintVersion);
  if (!policy.skills.includes(recipeId) || !policy.targets.includes(target)) {
    const error = new Error("当前蓝图不允许该实验技能或目标");
    error.code = "BLUEPRINT_SCOPE_DENIED";
    throw error;
  }
  return policy;
}
