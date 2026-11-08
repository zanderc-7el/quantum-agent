/**
 * Read-only views over the backend's Skill registry and append-only audit.
 *
 * These endpoints are separate from the `WorkflowGateway` contract: the
 * workflow payload stays exactly what the strict frontend validator expects,
 * while provenance and acceptance data live beside it.
 */
import { apiRequest } from "./apiClient.ts";
import {
  formatWireCriterion,
  formatWireTarget,
  formatWireValue,
} from "./metricFormat.ts";

const BASE = "/api/v1/calibration";

export interface AcceptanceCriterion {
  metric: string;
  comparator: string;
  threshold: number;
  unit: string;
  description: string;
}

/** The design goal for a metric; distinct from its acceptance floor. */
export interface MetricTarget {
  metric: string;
  ideal: number;
  unit: string;
  direction: "higher" | "lower" | "band" | string;
  tolerance: number | null;
  description: string;
}

export interface SkillStep {
  nodeId: string;
  name: string;
  label: string;
  acceptance: AcceptanceCriterion;
  /** Absent on pre-v1.1.0 Skills and on steps without a quality metric. */
  target?: MetricTarget | null;
  onFail: string;
  maxAttempts: number;
}

export interface AutonomyLevel {
  level: string;
  name: string;
  policy: string;
  description: string;
}

export interface CalibrationSkill {
  skillId: string;
  version: string;
  name: string;
  experiment: string;
  description: string;
  steps: SkillStep[];
  autonomyLevels: AutonomyLevel[];
}

/** A machine's policy: adapter, capabilities and per-step threshold overrides. */
export interface DeviceProfile {
  deviceId: string;
  version: string;
  name: string;
  vendor: string;
  qubits: number;
  experiments: string[];
  adapter: string;
  supportsDryRun: boolean;
  /** False for a machine that has no registered profile; Skill defaults apply. */
  known: boolean;
  notes: string;
  acceptanceOverrides: Record<string, AcceptanceCriterion>;
  fidelityCriterion: AcceptanceCriterion | null;
  /** Absent on pre-v1.1.0 device profiles. */
  targetOverrides?: Record<string, MetricTarget>;
  fidelityTarget?: MetricTarget | null;
}

export interface EffectiveSkillStep extends SkillStep {
  /** The criterion the workflow's device actually applies. */
  effectiveAcceptance: AcceptanceCriterion;
  deviceOverride: boolean;
  /** The design target the workflow's device actually applies. */
  effectiveTarget?: MetricTarget | null;
  targetSource?: "device" | "skill" | null;
}

export interface SkillBinding {
  workflowId: string;
  revision: number;
  binding: { skillId: string; version: string; source: string };
  device?: DeviceProfile;
  autonomy: AutonomyLevel;
  skill: CalibrationSkill;
  effectiveSteps?: EffectiveSkillStep[];
  /** Flat index of effective targets, added in Skill v1.1.0. */
  targets?: {
    nodeId: string;
    target: MetricTarget | null;
    source: "device" | "skill" | null;
  }[];
}

export interface AuditEvaluation {
  skillId?: string;
  skillVersion?: string;
  deviceId?: string;
  deviceProfileVersion?: string;
  deviceKnown?: boolean;
  nodeId?: string;
  criterion?: AcceptanceCriterion;
  /** The design target frozen when the measurement was recorded. */
  target?: MetricTarget | null;
  targetSource?: string | null;
  actual?: number | null;
  passed?: boolean;
  samples?: number;
  reason?: string | null;
  decision?: string;
  autonomy?: AutonomyLevel;
}

export interface AuditResultEntry {
  type: "result";
  time: string;
  nodeId: string;
  attempt: number;
  stage: string;
  evaluation: AuditEvaluation;
  evaluationSource: "recorded" | "derived";
  escalation: { code: string; message: string } | null;
  metrics: Record<string, unknown>;
  rawRef: string | null;
}

export interface AuditActionEntry {
  type: "action";
  time: string;
  requestId: string;
  actorId: string;
  action: string;
  nodeId: string | null;
  attempt: number | null;
  expectedRevision: number | null;
  resultRevision: number | null;
  resultStatus: string;
  response: Record<string, unknown> | null;
}

export interface AuditEventEntry {
  type: "event";
  time: string;
  seq: number;
  kind: string;
  message: string;
}

export type AuditEntry =
  | AuditResultEntry
  | AuditActionEntry
  | AuditEventEntry;

export interface WorkflowAudit {
  workflowId: string;
  revision: number;
  status: string;
  skill: { skillId: string; version: string; name: string };
  device?: DeviceProfile;
  autonomy: AutonomyLevel;
  counts: {
    results: number;
    actions: number;
    events: number;
    escalations?: number;
  };
  timeline: AuditEntry[];
}

export interface ReplayOutcome extends AcceptanceCriterion {
  actual: number | null;
  passed: boolean;
  samples: number;
  reason: string | null;
  target?: MetricTarget | null;
  targetSource?: string | null;
}

export interface ReplayItem {
  nodeId: string;
  attempt: number;
  stage: string;
  /** The verdict frozen when the measurement was recorded, if it has one. */
  recorded: AuditEvaluation | null;
  /** The verdict today's Skill produces from the same metrics. */
  replayed: ReplayOutcome;
  /** The design target frozen with the result, if it had one. */
  recordedTarget?: MetricTarget | null;
  /** The design target today's Skill/device resolves for this step. */
  replayedTarget?: MetricTarget | null;
  replayedTargetSource?: "device" | "skill" | null;
  /** True when the two verdicts disagree — i.e. the Skill changed. */
  changed: boolean;
  /**
   * True when the recorded design goal differs from today's target. `null`
   * means the result predates targets or the step never had one, so it is not
   * evidence of drift.
   */
  targetChanged?: boolean | null;
}

export interface WorkflowReplay {
  workflowId: string;
  skill: { skillId: string; version: string; name: string };
  device?: DeviceProfile;
  summary: { total: number; changed: number; targetChanged?: number };
  items: ReplayItem[];
}

export function fetchDevices(): Promise<DeviceProfile[]> {
  return apiRequest<{ items: DeviceProfile[]; total: number }>(
    `${BASE}/devices`,
  ).then((payload) => payload.items);
}

export function fetchWorkflowSkill(workflowId: string): Promise<SkillBinding> {
  return apiRequest<SkillBinding>(
    `${BASE}/workflows/${encodeURIComponent(workflowId)}/skill`,
  );
}

export function fetchWorkflowAudit(
  workflowId: string,
  limit = 200,
): Promise<WorkflowAudit> {
  const safeLimit = Math.max(1, Math.min(Math.trunc(limit), 5000));
  return apiRequest<WorkflowAudit>(
    `${BASE}/workflows/${encodeURIComponent(workflowId)}/audit?limit=${safeLimit}`,
  );
}

/**
 * Re-evaluate a workflow's stored measurements against the current Skill.
 * Read-only; the backend never rewrites recorded verdicts.
 */
export function fetchWorkflowReplay(workflowId: string): Promise<WorkflowReplay> {
  return apiRequest<WorkflowReplay>(
    `${BASE}/workflows/${encodeURIComponent(workflowId)}/audit/replay`,
  );
}

export interface AuditLine {
  label: string;
  detail: string;
  warning: boolean;
}

const STAGE_LABELS: Record<string, string> = {
  completed: "已完成",
  attention: "等待决定",
  auto_retry: "自动重试",
};

function describeReplayTarget(
  target: MetricTarget | null | undefined,
): string {
  if (!target) return "未设目标";
  const direction =
    target.direction === "lower"
      ? "越小越好"
      : target.direction === "band"
        ? "带容差"
        : "越大越好";
  return `${formatWireTarget(target)}（${direction}）`;
}

/**
 * One display line per replayed measurement.
 *
 * Two independent drifts are reported: a changed verdict (today's acceptance
 * floor judges the result differently) and a changed design target (the ideal
 * moved even when the verdict did not).
 */
export function describeReplayItem(item: ReplayItem): AuditLine {
  const actual =
    item.replayed.actual === null
      ? "无效值"
      : formatWireValue(
          item.replayed.actual,
          item.replayed.metric,
          item.replayed.unit,
        );
  const verdict = item.replayed.passed ? "通过" : "未通过";
  const recorded = item.recorded
    ? item.recorded.passed
      ? "通过"
      : "未通过"
    : "无记录";
  const targetDrift = item.targetChanged === true;
  const parts = [
    `${item.nodeId} · 第 ${item.attempt} 次 · 记录 ${recorded} → 重放 ${verdict}`,
  ];
  if (targetDrift) {
    parts.push(
      `目标 ${describeReplayTarget(item.recordedTarget)} → ${describeReplayTarget(item.replayedTarget)}`,
    );
  }
  parts.push(`实测 ${actual}`);
  return {
    label: item.changed ? "标准漂移" : targetDrift ? "目标漂移" : "一致",
    detail: parts.join(" · "),
    warning: item.changed || targetDrift,
  };
}

/** One display line per audit entry; exported so QA can assert the rendering rule. */
export function describeAuditEntry(entry: AuditEntry): AuditLine {
  if (entry.type === "result") {
    const evaluation = entry.evaluation ?? {};
    const stage = STAGE_LABELS[entry.stage] || entry.stage;
    const parts = [`${entry.nodeId} · 第 ${entry.attempt} 次 · ${stage}`];
    if (evaluation.criterion) {
      const actual =
        evaluation.actual === null || evaluation.actual === undefined
          ? "无效值"
          : formatWireValue(
              evaluation.actual,
              evaluation.criterion.metric,
              evaluation.criterion.unit,
            );
      parts.push(
        `实测 ${actual}，阈值 ${formatWireCriterion(evaluation.criterion)}`,
      );
    }
    // The design target was frozen next to the criterion; a reviewer needs
    // both to tell "missed the floor" from "the ideal has moved".
    if (evaluation.target) {
      const source =
        evaluation.targetSource === "device"
          ? "设备设计目标"
          : evaluation.targetSource === "skill"
            ? "Skill 设计目标"
            : "设计目标";
      parts.push(`${source} ${formatWireTarget(evaluation.target)}`);
    }
    if (entry.escalation) parts.push(entry.escalation.message);
    return {
      label: "测量",
      detail: parts.join("；"),
      warning: Boolean(entry.escalation),
    };
  }
  if (entry.type === "action") {
    const revision =
      entry.expectedRevision === null
        ? ""
        : ` · revision ${entry.expectedRevision}→${entry.resultRevision ?? "-"}`;
    return {
      label: "命令",
      detail: `${entry.action} · ${entry.actorId}${revision} · ${entry.resultStatus}`,
      warning: entry.resultStatus !== "success",
    };
  }
  return {
    label: "事件",
    detail: entry.message,
    warning: entry.kind === "warning",
  };
}
