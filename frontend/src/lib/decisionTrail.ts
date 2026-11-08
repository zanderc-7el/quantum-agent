/**
 * Read-only decision views derived from the server audit and replay payloads.
 *
 * Pure functions only, so QA can assert the marking rules without a DOM. The
 * backend remains the source of truth: these helpers never rewrite a recorded
 * verdict, they only choose what the blueprint surface should show.
 */
import { describeReplayItem } from "./remoteInsights.ts";
import type {
  AuditActionEntry,
  AuditResultEntry,
  WorkflowAudit,
  WorkflowReplay,
} from "./remoteInsights.ts";
import {
  formatWireCriterion,
  formatWireTarget,
  formatWireValue,
} from "./metricFormat.ts";

export type DecisionTone = "pass" | "fail" | "drift";

/** The verdict badge drawn on one blueprint node. */
export interface NodeDecision {
  tone: DecisionTone;
  label: string;
  detail: string;
}

export interface MeasurementVerdict {
  tone: "pass" | "fail" | "neutral";
  label: string;
  detail: string;
}

export interface DecisionPathItem {
  key: string;
  nodeId: string;
  attempt: number;
  kind: "result" | "action";
  tone: "pass" | "fail" | "neutral";
  label: string;
  detail: string;
  time: string;
}

const ACTION_LABELS: Record<string, string> = {
  approve: "批准继续",
  retry: "拒绝重试",
  pause: "暂停",
  resume: "继续",
  abort: "终止",
  start: "启动",
  policy: "调整策略",
};

/** The most recent recorded measurement per node, in timeline order. */
export function latestMeasurements(
  audit: WorkflowAudit | null,
): AuditResultEntry[] {
  if (!audit) return [];
  const latest = new Map<string, AuditResultEntry>();
  for (const entry of audit.timeline) {
    if (entry.type === "result") latest.set(entry.nodeId, entry);
  }
  return [...latest.values()];
}

/**
 * Turn one frozen measurement into a verdict. Values written before the
 * acceptance pipeline existed have no boolean and stay neutral rather than
 * being presented as a pass or a failure.
 */
export function describeMeasurementDecision(
  entry: AuditResultEntry,
): MeasurementVerdict {
  const evaluation = entry.evaluation ?? {};
  const criterion = evaluation.criterion;
  const actual =
    evaluation.actual === null || evaluation.actual === undefined
      ? "无效值"
      : criterion
        ? formatWireValue(evaluation.actual, criterion.metric, criterion.unit)
        : `${evaluation.actual}`;
  const measured = criterion
    ? `实测 ${actual}，阈值 ${formatWireCriterion(criterion)}`
    : `实测 ${actual}`;
  // The frozen design target is the other half of the decision: the floor says
  // whether it passed, the target says what "good" meant at the time.
  const withTarget = evaluation.target
    ? `${measured}，设计目标 ${formatWireTarget(evaluation.target)}`
    : measured;
  // A derived verdict was computed now from the current Skill, not frozen when
  // the run happened. It must never be presented as that run's pass/fail badge.
  if (entry.evaluationSource === "derived") {
    return {
      tone: "neutral",
      label: "推导判定",
      detail: `${withTarget}；该结果早于验收管线，当前判定由现行 Skill 推导，不是冻结记录。`,
    };
  }
  if (evaluation.passed === true) {
    return { tone: "pass", label: "通过", detail: withTarget };
  }
  if (evaluation.passed === false) {
    return {
      tone: "fail",
      label: entry.escalation ? "升级" : "未通过",
      detail: entry.escalation
        ? `${withTarget}；${entry.escalation.message}`
        : withTarget,
    };
  }
  return {
    tone: "neutral",
    label: "无判定",
    detail: "该结果没有存储验收判定。",
  };
}

/**
 * Badges keyed by experiment node id. A replay drift overrides the recorded
 * verdict because it is the newer signal a reviewer needs to see.
 */
export function buildNodeDecisions(
  audit: WorkflowAudit | null,
  replay: WorkflowReplay | null,
): Record<string, NodeDecision> {
  const decisions: Record<string, NodeDecision> = {};
  for (const entry of latestMeasurements(audit)) {
    const verdict = describeMeasurementDecision(entry);
    if (verdict.tone === "neutral") continue;
    decisions[entry.nodeId] = {
      tone: verdict.tone,
      label:
        verdict.tone === "pass"
          ? "验收通过"
          : entry.escalation
            ? "已升级"
            : "未达标",
      detail: `${verdict.detail}（第 ${entry.attempt} 次）`,
    };
  }
  for (const item of replay?.items ?? []) {
    if (!item.changed && item.targetChanged !== true) continue;
    decisions[item.nodeId] = {
      tone: "drift",
      label: item.changed ? "标准漂移" : "目标漂移",
      detail: describeReplayItem(item).detail,
    };
  }
  return decisions;
}

function pathItem(
  entry: AuditResultEntry | AuditActionEntry,
): DecisionPathItem {
  if (entry.type === "result") {
    const verdict = describeMeasurementDecision(entry);
    return {
      key: `result-${entry.nodeId}-${entry.attempt}-${entry.time}`,
      nodeId: entry.nodeId,
      attempt: entry.attempt,
      kind: "result",
      tone: verdict.tone,
      label: verdict.label,
      detail: `第 ${entry.attempt} 次 · ${verdict.detail}`,
      time: entry.time,
    };
  }
  return {
    key: `action-${entry.requestId}-${entry.time}`,
    nodeId: entry.nodeId ?? "",
    attempt: entry.attempt ?? 0,
    kind: "action",
    tone: entry.resultStatus === "success" ? "neutral" : "fail",
    label: ACTION_LABELS[entry.action] ?? entry.action,
    detail: `${entry.actorId} · ${entry.resultStatus}`,
    time: entry.time,
  };
}

/**
 * The latest measurements and commands in the order the server recorded them.
 * Events are omitted here: the blueprint already has a live event panel.
 */
export function buildDecisionPath(
  audit: WorkflowAudit | null,
  limit = 14,
): DecisionPathItem[] {
  if (!audit) return [];
  const relevant = audit.timeline.filter(
    (entry): entry is AuditResultEntry | AuditActionEntry =>
      entry.type === "result" || entry.type === "action",
  );
  return relevant.slice(-Math.max(1, Math.trunc(limit))).map(pathItem);
}
