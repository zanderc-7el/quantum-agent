import type { Catalog, CreateWorkflowInput, Workflow, WorkflowCoverage } from "../types.ts";

type CoverageContext = Pick<CreateWorkflowInput, "mode" | "experiment" | "resourceIds" | "coverage">;

/** Display eligibility follows the backend catalog; the create endpoint revalidates the exact profile. */
export function workingPointUnavailableReason(catalog: Catalog | undefined, input: CoverageContext): string | undefined {
  if (input.mode !== "real") return "已有工作点复刻仅用于真实测量。";
  if (input.experiment !== "single") return "已有工作点复刻仅用于单比特流程。";
  const capability = catalog?.workingPointCoverage;
  if (!capability?.available) return capability?.reason || "当前执行器尚未提供已审计工作点。";
  const ids = input.resourceIds ?? [];
  if (ids.length !== 1 || !capability.resourceIds.includes(ids[0])) {
    return `已有工作点复刻仅支持 ${capability.resourceIds.join("、") || "后端登记的目标资源"}，请选择对应资源。`;
  }
  return undefined;
}

export function workflowCoverageError(catalog: Catalog | undefined, input: CoverageContext): string | undefined {
  return input.coverage === "working_point" ? workingPointUnavailableReason(catalog, input) : undefined;
}

export function defaultCoverage(mode: CreateWorkflowInput["mode"]): WorkflowCoverage {
  return mode === "real" ? "full" : "main";
}

export function coverageDescription(coverage: WorkflowCoverage): string {
  if (coverage === "working_point") return "复用已审计 Q11 参考点作初值，不重做读出找腔、功率扫描和比特定频；从 Rabi 开始，10 项脉冲测量重新采集。历史结果不计本次通过。";
  if (coverage === "full") return "八个主步骤，以及功率扫描、时间 Rabi、AllXY、Ramsey、Echo 的独立采集。";
  return "执行原流程八个主步骤。";
}

/** Reporting scope is descriptive only: never synthesize accepted nodes from the seed. */
export function workingPointScope(workflow: Workflow) {
  if (workflow.coverage !== "working_point") return undefined;
  const count = workflow.completionScope?.selectedNodeCount ?? workflow.nodes.length;
  return {
    title: `已有工作点复刻 · 本次 ${count} 项脉冲测量`,
    scope: "从 Rabi 开始新采集；读出找腔、功率扫描和比特定频沿用参考初值。本任务仅验收所选脉冲范围。",
    seed: "参考初值 · seed_only · 历史结果不计本次通过",
    procedures: workflow.nodes.map(node => node.label),
    source: workflow.seedReference,
    count,
  };
}
