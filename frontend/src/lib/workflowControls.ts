import type { ExperimentNode, Workflow } from "../types.ts";
import { rbDiagnosticMarker } from "./rbDiagnostic.ts";
import { engineLabel } from "./runtimeLabels.ts";

export const policyLabels = { strict: "人工逐步", threshold: "异常时决策", autopilot: "智能运行" };
export const isManualWorkflow = (workflow: Pick<Workflow, "mode" | "policy">) => workflow.mode === "real" && workflow.policy === "strict";
export const workflowPolicyLabel = (workflow: Pick<Workflow, "mode" | "policy">) => workflow.mode === "replay" ? "历史回放" : policyLabels[workflow.policy];
const freshPassed = (node?: ExperimentNode) => !!node?.result && node.result.dataKind === "real" && node.result.quality?.status === "passed" && !node.result.stale && !node.result.partial && !node.result.verificationPending;
const awaitingAcceptance = (workflow: Workflow) => workflow.engineState === "awaiting_acceptance" || workflow.nodes.some(node => node.engineState === "awaiting_acceptance");

export function manualStepControls(workflow: Workflow, selected: ExperimentNode) {
  const manual = isManualWorkflow(workflow);
  const diagnostic = rbDiagnosticMarker(workflow);
  const diagnosticBlocked = !!diagnostic && (["queued", "running"].includes(diagnostic.state) || workflow.currentNodeId === diagnostic.targetNodeId);
  const states = [workflow.engineState, selected.engineState, ...workflow.nodes.filter(node => node.id === workflow.currentNodeId || node.status === "running" || ["needs_reconciliation", "unknown"].includes(node.engineState ?? "")).map(node => node.engineState)];
  const blockedDependencies = (selected.dependsOn ?? []).filter(id => {
    const dependency = workflow.nodes.find(node => node.id === id);
    return dependency?.engineState !== "verified" || dependency.status !== "success" || !freshPassed(dependency);
  }).map(id => workflow.nodes.find(node => node.id === id)?.label ?? id);
  const unsafe = states.find(state => ["needs_reconciliation", "unknown", "waiting_hardware", "budget_exhausted", "deciding", "waiting_model", "unsupported_method", "preflight_failed", "resource_busy", "controller_error", "acquiring", "verifying", "pausing_at_checkpoint", "stopping_at_checkpoint"].includes(state ?? ""));
  const attention = workflow.nodes.find(node => node.engineState === "awaiting_acceptance");
  let reason = "";
  if (!manual) reason = "当前为非人工执行模式";
  else if (!["draft", "paused", "attention"].includes(workflow.status)) reason = ["completed", "failed", "aborted"].includes(workflow.status) ? "任务已结束" : "等待当前步骤停止";
  else if (diagnosticBlocked) reason = "RB 诊断期间不可执行主链步骤";
  else if (unsafe) reason = engineLabel(unsafe);
  else if (awaitingAcceptance(workflow)) reason = `先确认结果：${attention?.label ?? "当前步骤"}`;
  else if (selected.capability !== "available") reason = "此步骤暂不可测量";
  else if (selected.engineState === "verified" || selected.status === "success") reason = "已确认；重新测量需先回退";
  else if (blockedDependencies.length) reason = `先完成：${blockedDependencies.join("、")}`;
  else if (workflow.maxAttemptsPerNode !== undefined && selected.attempt >= workflow.maxAttemptsPerNode) reason = "本步执行次数已达上限";
  const selectedAwaiting = workflow.currentNodeId === selected.id && workflow.engineState === "awaiting_acceptance" && ["awaiting_acceptance", "verified"].includes(selected.engineState ?? "");
  const adjust = manual && ["attention", "paused"].includes(workflow.status) && selectedAwaiting && !unsafe && !diagnosticBlocked;
  const approve = adjust && freshPassed(selected);
  const staleLabel = selected.attempt === 0 && !selected.result ? (blockedDependencies.length ? "等待前置" : "待执行") : "待复测";
  const label = selectedAwaiting ? "待确认结果" : selected.engineState === "verified" && freshPassed(selected) ? "已确认" : selected.engineState === "awaiting_acceptance" ? "待确认结果" : selected.status === "running" ? engineLabel(selected.engineState) : selected.result?.stale || selected.engineState === "stale" ? staleLabel : selected.result?.quality?.status === "failed" ? "未通过" : blockedDependencies.length ? "等待前置" : selected.status === "abandoned" ? "已终止" : "待执行";
  return { manual, run: !reason, approve, adjust, reason, blockedDependencies, label, runLabel: selected.attempt > 0 ? "重测本步" : "开始本步" };
}

export function workflowControls(workflow: Workflow) {
  const manual = isManualWorkflow(workflow);
  const diagnostic = rbDiagnosticMarker(workflow);
  const diagnosticActive = !!diagnostic && ["queued", "running"].includes(diagnostic.state);
  const diagnosticLane = !!diagnostic && (diagnosticActive || workflow.currentNodeId === diagnostic.targetNodeId);
  const terminal = ["completed", "failed", "aborted"].includes(workflow.status);
  const current = terminal ? undefined : workflow.nodes.find(node => node.id === workflow.currentNodeId) ?? workflow.nodes.find(node => ["running", "attention"].includes(node.status));
  const states = [workflow.engineState, current?.engineState];
  const reconciliation = states.some(state => ["needs_reconciliation", "unknown"].includes(state ?? ""));
  // Hardware handover is a workflow action. A node may retain historical
  // attention, but cannot authorize a command the controller will reject.
  const hardware = !terminal && workflow.mode === "real" && workflow.engineState === "waiting_hardware";
  const attention = workflow.status === "attention";
  const budgetUpdated = attention && workflow.engineState === "budget_updated";
  const budgetDecision = budgetUpdated && workflow.policy === "autopilot" && !!current?.result && !current.result.stale && current.result.quality?.status !== "passed";
  const legacyDecisionError = workflow.engineState === "execution_failed" && ["决策引用了当前节点之外的证据", "证据引用必须为编号列表"].includes(current?.detail ?? "") && !!current?.result && !current.result.stale && current.result.quality?.status !== "passed";
  const blocked = states.some(state => ["deciding", "waiting_model", "budget_exhausted", "unsupported_method", "preflight_failed", "resource_busy", "controller_error"].includes(state ?? ""));
  const attemptsRemain = !current || workflow.maxAttemptsPerNode === undefined || current.attempt < workflow.maxAttemptsPerNode;
  return {
    terminal, current, reconciliation, hardware,
    start: !manual && !diagnosticLane && workflow.status === "draft",
    pause: ["running", "queued"].includes(workflow.status) && !["pausing_at_checkpoint", "stopping_at_checkpoint"].includes(workflow.engineState ?? ""),
    resume: !manual && !diagnosticLane && (workflow.status === "paused" || (budgetUpdated && !budgetDecision)) && !reconciliation && !hardware,
    recheck: !manual && !diagnosticLane && attention && !current && !hardware && !reconciliation && workflow.engineState !== "deciding",
    decide: !diagnosticLane && workflow.mode === "real" && attention && workflow.policy === "autopilot" && !!current?.result && !current.result.stale && (["quality_attention", "waiting_model"].includes(workflow.engineState ?? "") || budgetDecision || legacyDecisionError) && !hardware && !reconciliation,
    approve: !diagnosticLane && (attention || (manual && workflow.status === "paused")) && !budgetUpdated && !hardware && !reconciliation && !blocked && current?.result?.quality?.status === "passed" && !current.result.stale && !current.result.partial && !current.result.verificationPending && (!manual || manualStepControls(workflow, current).approve),
    retry: !manual && !diagnosticLane && attention && !budgetUpdated && !!current && !hardware && !reconciliation && !blocked && attemptsRemain,
    policy: !diagnosticActive && !awaitingAcceptance(workflow) && ["draft", "paused", "attention"].includes(workflow.status) && !reconciliation && workflow.engineState !== "deciding",
    backtrack: !diagnosticActive && ["attention", "paused"].includes(workflow.status) && !hardware && !reconciliation && workflow.engineState !== "deciding",
    edit: !diagnosticActive && !(manual && awaitingAcceptance(workflow)) && workflow.mode === "real" && ["draft", "paused", "attention"].includes(workflow.status) && !reconciliation && workflow.engineState !== "deciding",
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
