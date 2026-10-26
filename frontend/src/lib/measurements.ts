import type { ExperimentNode, Workflow } from "../types";

export const DEMO_THRESHOLDS = {
  coherenceUs: 30,
  fidelityPercent: 98,
} as const;
export const DEMO_CZ_WIDTH_NS = 60;
export type MeasurementStage = "preview" | "partial" | "ready" | "interrupted";

export function measurementStage(node?: ExperimentNode): MeasurementStage {
  if (!node || node.status === "pending" || node.attempt === 0)
    return "preview";
  if (node.status === "success" || node.status === "attention") return "ready";
  if (node.status === "abandoned") return "interrupted";
  return "partial";
}

export function measurementStageLabel(node?: ExperimentNode): string {
  return {
    preview: "示例预览·尚未执行",
    partial: "阶段数据·尚未完成",
    ready: "已执行模拟结果",
    interrupted: "已终止·非最终结果",
  }[measurementStage(node)];
}

export function qubitMeasurements(qubit: string) {
  const seed = Number(qubit.replace(/\D/g, "")) || 1;
  const t1 = Number((36.8 + (seed % 13) * 1.17).toFixed(2));
  return {
    t1,
    t2: Number((t1 * 1.12).toFixed(2)),
    error: Number((0.32 + (seed % 7) * 0.06).toFixed(2)),
    r2: 0.9985,
    rbFidelity: 99.37,
  };
}

export function decaySamples(qubit: string, tau = qubitMeasurements(qubit).t1) {
  const seed = Number(qubit.replace(/\D/g, "")) || 1;
  return Array.from({ length: 21 }, (_, i) => {
    const time = i * 8;
    const fitted = 0.96 * Math.exp(-time / tau) + 0.02;
    const noise =
      Math.sin((i + 1) * (seed + 3) * 1.73) * 0.009 + Math.cos(i * 2.1) * 0.003;
    return { time, fitted, measured: Math.min(1, Math.max(0, fitted + noise)) };
  });
}

/** A first-attempt exception belongs to its actual node; it never changes T1/T2. */
export function nodeMeasurements(workflow: Workflow, node: ExperimentNode) {
  const qubit = (node.qubits || workflow.target).match(/Q\d+/i)?.[0] || "Q12";
  const base = qubitMeasurements(qubit);
  const anomalyId =
    workflow.experiment === "two" ? "CZXEBCal" : "ReadoutParaSweepByIQProb";
  const stage = measurementStage(node);
  const anomaly =
    node.attempt === 1 &&
    stage !== "preview" &&
    (node.detail.includes("94.2%") ||
      (workflow.scenario === "anomaly" && node.id === anomalyId));
  const fidelity = anomaly
    ? 94.2
    : /RB1QCal/.test(node.name)
      ? base.rbFidelity
      : /XEB|CZParaOptimizer/.test(node.name)
        ? 99.12
        : 98.9;
  return {
    ...base,
    qubit,
    anomaly,
    fidelity,
    tau: /T2CPMG/.test(node.name) ? base.t2 : base.t1,
    stage,
  };
}

function latestObservedNode(workflow: Workflow, first: string, retest: string) {
  const later = workflow.nodes.find((node) => node.id === retest);
  return later && measurementStage(later) === "ready"
    ? later
    : workflow.nodes.find((node) => node.id === first);
}

export function reportMeasurements(workflow: Workflow) {
  const t1Node = workflow.nodes.find((node) => node.id === "T1Idle");
  const t2Node = workflow.nodes.find((node) => node.id === "T2CPMG");
  const rbNode = workflow.nodes.find((node) => node.id === "RB1QCal");
  const readoutNode = latestObservedNode(
    workflow,
    "ReadoutParaSweepByIQProb",
    "IqToProbabilityRetest",
  );
  const czNode = latestObservedNode(workflow, "CZXEBCal", "CZXEBCalRetest");
  const readout = readoutNode
    ? nodeMeasurements(workflow, readoutNode).fidelity
    : 98.9;
  const cz = czNode ? nodeMeasurements(workflow, czNode).fidelity : 99.12;
  const preview = workflow.status === "draft" || workflow.status === "queued";
  const completed = workflow.status === "completed";
  const label = preview
    ? "示例预览·尚未执行"
    : completed
      ? "模拟完成报告"
      : "阶段报告·流程未完成";
  const failingReadout =
    measurementStage(readoutNode) === "ready" &&
    readout < DEMO_THRESHOLDS.fidelityPercent;
  const failingCZ =
    workflow.experiment === "two" &&
    measurementStage(czNode) === "ready" &&
    cz < DEMO_THRESHOLDS.fidelityPercent;
  const qualityFailed = failingReadout || failingCZ;
  const conclusion = preview
    ? "当前流程尚未执行，图表及数值均为示例预览，不能作为已执行结果。"
    : qualityFailed
      ? `${failingCZ ? "CZ XEB" : "读取判别"}模拟保真度低于 98% 演示阈值；T1Idle 指标不受该异常影响。请查看对应节点的审批或重试状态。`
      : completed
        ? "流程已完成，当前有效模拟结果符合演示阈值。"
        : "当前流程尚未完成。各项指标以节点状态区分已执行结果、阶段数据与示例预览。";
  return {
    t1Node,
    t2Node,
    rbNode,
    readoutNode,
    czNode,
    readout,
    cz,
    preview,
    completed,
    label,
    qualityFailed,
    conclusion,
  };
}
