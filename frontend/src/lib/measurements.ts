import type { ExperimentNode, Workflow } from "../types";
import {
  getWorkflowResults,
  latestServerMeasurement,
  serverQubitParameters,
} from "./remoteMeasurements.ts";

export const DEMO_THRESHOLDS = {
  coherenceUs: 30,
  fidelityPercent: 98,
} as const;
export const DEMO_CZ_WIDTH_NS = 60;
export type MeasurementStage = "preview" | "partial" | "ready" | "interrupted";
/**
 * `server` means at least one value below comes from a recorded node result;
 * `simulated` means the deterministic front-end demo selector produced it.
 */
export type MeasurementSource = "server" | "simulated";

export function measurementStage(node?: ExperimentNode): MeasurementStage {
  if (!node || node.status === "pending" || node.attempt === 0)
    return "preview";
  if (node.status === "success" || node.status === "attention") return "ready";
  if (node.status === "abandoned") return "interrupted";
  return "partial";
}

export function measurementStageLabel(
  node?: ExperimentNode,
  source: MeasurementSource = "simulated",
): string {
  const stage = measurementStage(node);
  if (source === "server") {
    return {
      preview: "示例预览·尚未执行",
      partial: "阶段数据·服务端尚未完成",
      ready: "服务端测量结果",
      interrupted: "已终止·非最终结果",
    }[stage];
  }
  return {
    preview: "示例预览·尚未执行",
    partial: "阶段数据·尚未完成",
    ready: "已执行模拟结果",
    interrupted: "已终止·非最终结果",
  }[stage];
}

function demoQubit(qubit: string) {
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

/**
 * Qubit-level summary.
 *
 * Without a workflow (or before the backend has recorded a result) this is
 * the deterministic demo value the whole application used before remote mode.
 * With a workflow, recorded device parameters win.
 */
export function qubitMeasurements(qubit: string, workflow?: Workflow) {
  const base = demoQubit(qubit);
  const parameters = serverQubitParameters(workflow?.id, qubit);
  const t1 = parameters?.t1Us;
  const t2 = parameters?.t2Us;
  if (t1 === undefined && t2 === undefined) return base;
  return {
    ...base,
    t1: typeof t1 === "number" ? Number(t1.toFixed(3)) : base.t1,
    t2: typeof t2 === "number" ? Number(t2.toFixed(3)) : base.t2,
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
  const base = qubitMeasurements(qubit, workflow);
  const anomalyId =
    workflow.experiment === "two" ? "CZXEBCal" : "ReadoutParaSweepByIQProb";
  const stage = measurementStage(node);
  const server = latestServerMeasurement(workflow.id, node.id);
  // A stale result from the previous attempt must not label a running retry
  // as already measured.
  const measured =
    server && server.attempt === node.attempt && stage !== "preview"
      ? server
      : undefined;
  if (measured) {
    const isT2 = /T2CPMG/.test(node.name);
    const tau = isT2
      ? (measured.t2Us ?? base.t2)
      : (measured.t1Us ?? base.t1);
    const fidelity = measured.fidelityPercent ?? base.rbFidelity;
    // The frozen verdict wins. When an older result has no evaluation, fall
    // back to the same 98% gate the demo used, but only for fidelity-bearing
    // nodes so a T1/T2 relaxation node is never called anomalous.
    const fidelityNode = /IQ|Readout|XEB|RB1QCal|CZ/.test(node.name);
    const anomaly =
      measured.passed !== null
        ? measured.passed === false
        : fidelityNode && fidelity < DEMO_THRESHOLDS.fidelityPercent;
    return {
      ...base,
      qubit,
      anomaly,
      fidelity,
      tau,
      stage,
      source: "server" as MeasurementSource,
      capturedAt: measured.capturedAt,
      reason: measured.reason,
    };
  }
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
    source: "simulated" as MeasurementSource,
    capturedAt: null,
    reason: null,
  };
}

function latestObservedNode(workflow: Workflow, first: string, retest: string) {
  const later = workflow.nodes.find((node) => node.id === retest);
  return later && measurementStage(later) === "ready"
    ? later
    : workflow.nodes.find((node) => node.id === first);
}

/**
 * The frozen verdict for this exact attempt, if one exists. A recorded pass
 * under a device-specific threshold must not be re-judged by the demo's 98%
 * gate.
 */
function serverVerdict(
  workflow: Workflow,
  node?: ExperimentNode,
): boolean | null {
  if (!node) return null;
  const measurement = latestServerMeasurement(workflow.id, node.id);
  return measurement && measurement.attempt === node.attempt
    ? measurement.passed
    : null;
}

export function reportMeasurements(workflow: Workflow) {
  const snapshot = getWorkflowResults(workflow.id);
  const source: MeasurementSource =
    snapshot && snapshot.results.length > 0 ? "server" : "simulated";
  const t1Node = workflow.nodes.find((node) => node.id === "T1Idle");
  const t2Node = workflow.nodes.find((node) => node.id === "T2CPMG");
  const rbNode = workflow.nodes.find((node) => node.id === "RB1QCal");
  const readoutNode = latestObservedNode(
    workflow,
    "ReadoutParaSweepByIQProb",
    "IqToProbabilityRetest",
  );
  const czNode = latestObservedNode(workflow, "CZXEBCal", "CZXEBCalRetest");
  const readoutMeasurement = readoutNode
    ? nodeMeasurements(workflow, readoutNode)
    : null;
  const czMeasurement = czNode ? nodeMeasurements(workflow, czNode) : null;
  const rbMeasurement = rbNode ? nodeMeasurements(workflow, rbNode) : null;
  const readout = readoutMeasurement?.fidelity ?? 98.9;
  const cz = czMeasurement?.fidelity ?? 99.12;
  const rb = rbMeasurement?.fidelity ?? 99.37;
  const preview = workflow.status === "draft" || workflow.status === "queued";
  const completed = workflow.status === "completed";
  const label = preview
    ? "示例预览·尚未执行"
    : source === "server"
      ? completed
        ? "服务端测量报告"
        : "阶段报告·服务端测量"
      : completed
        ? "模拟完成报告"
        : "阶段报告·流程未完成";
  const readoutVerdict = serverVerdict(workflow, readoutNode);
  const czVerdict = serverVerdict(workflow, czNode);
  const failingReadout =
    measurementStage(readoutNode) === "ready" &&
    (readoutVerdict === null
      ? readout < DEMO_THRESHOLDS.fidelityPercent
      : readoutVerdict === false);
  const failingCZ =
    workflow.experiment === "two" &&
    measurementStage(czNode) === "ready" &&
    (czVerdict === null
      ? cz < DEMO_THRESHOLDS.fidelityPercent
      : czVerdict === false);
  const qualityFailed = failingReadout || failingCZ;
  const conclusion = preview
    ? "当前流程尚未执行，图表及数值均为示例预览，不能作为已执行结果。"
    : qualityFailed
      ? source === "server"
        ? `${failingCZ ? "CZ XEB" : "读取判别"}服务端保真度未达到设备验收标准；T1Idle 指标不受该异常影响。请查看对应节点的审批或重试状态。`
        : `${failingCZ ? "CZ XEB" : "读取判别"}模拟保真度低于 98% 演示阈值；T1Idle 指标不受该异常影响。请查看对应节点的审批或重试状态。`
      : completed
        ? source === "server"
          ? "流程已完成，报告使用后端记录的测量结果。"
          : "流程已完成，当前有效模拟结果符合演示阈值。"
        : "当前流程尚未完成。各项指标以节点状态区分已执行结果、阶段数据与示例预览。";
  return {
    t1Node,
    t2Node,
    rbNode,
    readoutNode,
    czNode,
    readout,
    cz,
    rb,
    source,
    preview,
    completed,
    label,
    qualityFailed,
    conclusion,
  };
}
