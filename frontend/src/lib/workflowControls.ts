import type { ExperimentNode, Workflow } from "../types.ts";

export function workflowControls(workflow: Workflow) {
  const terminal = ["completed", "failed", "aborted"].includes(workflow.status);
  const current = terminal ? undefined : workflow.nodes.find(node => node.id === workflow.currentNodeId) ?? workflow.nodes.find(node => ["running", "attention"].includes(node.status));
  const states = [workflow.engineState, current?.engineState];
  const reconciliation = states.some(state => ["needs_reconciliation", "unknown"].includes(state ?? ""));
  const hardware = states.includes("waiting_hardware");
  const attention = workflow.status === "attention";
  const budgetUpdated = attention && workflow.engineState === "budget_updated";
  const budgetDecision = budgetUpdated && workflow.policy === "autopilot" && !!current?.result && !current.result.stale && current.result.quality?.status !== "passed";
  const legacyDecisionError = workflow.engineState === "execution_failed" && ["决策引用了当前节点之外的证据", "证据引用必须为编号列表"].includes(current?.detail ?? "") && !!current?.result && !current.result.stale && current.result.quality?.status !== "passed";
  const blocked = states.some(state => ["deciding", "waiting_model", "budget_exhausted", "unsupported_method", "preflight_failed", "resource_busy", "controller_error"].includes(state ?? ""));
  const attemptsRemain = !current || workflow.maxAttemptsPerNode === undefined || current.attempt < workflow.maxAttemptsPerNode;
  return {
    terminal, current, reconciliation, hardware,
    start: workflow.status === "draft",
    pause: ["running", "queued"].includes(workflow.status) && !["pausing_at_checkpoint", "stopping_at_checkpoint"].includes(workflow.engineState ?? ""),
    resume: (workflow.status === "paused" || (budgetUpdated && !budgetDecision)) && !reconciliation && !hardware,
    recheck: attention && !current && !hardware && !reconciliation && workflow.engineState !== "deciding",
    decide: workflow.mode === "real" && attention && workflow.policy === "autopilot" && !!current?.result && !current.result.stale && (["quality_attention", "waiting_model"].includes(workflow.engineState ?? "") || budgetDecision || legacyDecisionError) && !hardware && !reconciliation,
    approve: attention && !budgetUpdated && !hardware && !reconciliation && !blocked && current?.result?.quality?.status === "passed",
    retry: attention && !budgetUpdated && !!current && !hardware && !reconciliation && !blocked && attemptsRemain,
    policy: ["draft", "paused", "attention"].includes(workflow.status) && !reconciliation && workflow.engineState !== "deciding",
    backtrack: ["attention", "paused"].includes(workflow.status) && !hardware && !reconciliation && workflow.engineState !== "deciding",
    edit: workflow.mode === "real" && ["draft", "paused", "attention"].includes(workflow.status) && !reconciliation && workflow.engineState !== "deciding",
  };
}

export function precedingDependencies(workflow: Workflow, current?: ExperimentNode) {
  const ids = new Set(current?.dependsOn ?? []);
  let size = -1;
  while (size !== ids.size) {
    size = ids.size;
    workflow.nodes.filter(node => ids.has(node.id)).forEach(node => node.dependsOn?.forEach(id => ids.add(id)));
  }
  return workflow.nodes.filter(node => ids.has(node.id));
}
