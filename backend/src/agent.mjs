import { SIM_RECIPES, selectBatch } from "./recipes.mjs";
import { resolveBlueprint } from "./blueprint-policy.mjs";
import { validatePlan } from "./core.mjs";

export const AGENT_TOOLS = Object.freeze([
  { name: "get_topology", mode: "read" },
  { name: "get_recipe", mode: "read" },
  { name: "get_run_evidence", mode: "read" },
  { name: "get_parameter_history", mode: "read" },
  { name: "propose_plan", mode: "proposal" },
  { name: "propose_followup", mode: "proposal" },
  { name: "propose_parameter_candidate", mode: "proposal" },
]);

/** Deterministic placeholder for an LLM planner; it never issues control commands. */
export function previewAgentGoal(goal, blueprintId, blueprintVersion) {
  let blueprint;
  try { blueprint = resolveBlueprint(blueprintId, blueprintVersion); }
  catch { return { accepted: false, reason: "BLUEPRINT_VERSION_UNSUPPORTED", dataSource: "simulator" }; }
  if (typeof goal !== "string" || goal.trim().length < 4 || goal.length > 500)
    return { accepted: false, reason: "INVALID_GOAL", dataSource: "simulator" };
  if (/发布|下发|写回|删除|急停|execute|publish/i.test(goal))
    return { accepted: false, reason: "CONTROL_ACTION_REQUIRES_SEPARATE_AUTHORIZATION", dataSource: "simulator" };
  if (!/校准|测控|读出|扫频|CZ|Rabi|频谱/i.test(goal))
    return { accepted: false, reason: "UNSUPPORTED_GOAL", dataSource: "simulator" };
  const explicit = [...new Set((goal.match(/Q\d+/gi) ?? []).map(value => value.toUpperCase()))];
  const unknown = explicit.filter(value => !["Q12", "Q13"].includes(value));
  if (unknown.length) return { accepted: false, reason: "UNKNOWN_TARGET", targets: unknown, dataSource: "simulator" };
  const two = /CZ|双比特|两比特|耦合/i.test(goal);
  if (two && blueprint.id !== "two")
    return { accepted: false, reason: "BLUEPRINT_SCOPE_DENIED", dataSource: "simulator" };
  const recipeKey = two ? "two" : "single";
  if (two && explicit.length === 1 && !explicit.includes("Q12"))
    return { accepted: false, reason: "PAIR_MISMATCH", dataSource: "simulator" };
  if (!two && explicit.includes("Q13"))
    return { accepted: false, reason: "SINGLE_Q13_NOT_IMPLEMENTED", dataSource: "simulator" };
  const recipe = SIM_RECIPES[recipeKey];
  return { accepted: true, dataSource: "simulator", planner: "deterministic-preview-v1",
    blueprintId: blueprint.id, blueprintVersion: blueprint.version,
    recipeId: recipe.id, recipeVersion: recipe.version, target: two ? "Q12-Q13" : "Q12",
    nodeCount: recipe.nodes.length, firstBatch: selectBatch(recipe).map(item => item.id),
    requiredReview: ["实验定义与参数限值", "设备拓扑和资源", "物理质量门"],
    executable: false, modelUsed: false };
}

export function assertAgentToolAllowed(name, mode) {
  const tool = AGENT_TOOLS.find(item => item.name === name);
  if (!tool || tool.mode !== mode) throw new Error("AGENT_TOOL_DENIED");
  return true;
}

/** Review an untrusted planner output. A passing review is still not execution approval. */
export function reviewAgentProposal(input) {
  if (!input || typeof input !== "object" || Array.isArray(input))
    return { accepted: false, reason: "INVALID_PROPOSAL", executable: false };
  const { goal, blueprintId, blueprintVersion, plan, budget } = input;
  const intent = previewAgentGoal(goal, blueprintId, blueprintVersion);
  if (!intent.accepted) return { accepted: false, reason: intent.reason, executable: false };
  if (!plan || typeof plan !== "object" || Array.isArray(plan) ||
      plan.blueprintId !== blueprintId || plan.blueprintVersion !== blueprintVersion)
    return { accepted: false, reason: "BLUEPRINT_SCOPE_DENIED", executable: false };
  if (!budget || typeof budget !== "object" || Array.isArray(budget) ||
      Object.keys(budget).sort().join() !== "maxAttempts,maxDurationSeconds" ||
      !Number.isInteger(budget.maxAttempts) || budget.maxAttempts < 1 || budget.maxAttempts > 4 ||
      !Number.isInteger(budget.maxDurationSeconds) || budget.maxDurationSeconds < 1 || budget.maxDurationSeconds > 600)
    return { accepted: false, reason: "BUDGET_EXCEEDED", executable: false };
  try {
    const validated = validatePlan(plan);
    return { accepted: true, dataSource: "simulator", planner: "untrusted-proposal-review-v1",
      blueprintId, blueprintVersion, plan: validated, budget: structuredClone(budget),
      requiredReview: intent.requiredReview, executable: false, modelUsed: false };
  } catch (error) {
    return { accepted: false, reason: error.code ?? "INVALID_PROPOSAL", executable: false };
  }
}
