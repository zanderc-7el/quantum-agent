/**
 * Ideal-vs-actual comparison for one calibration metric.
 *
 * Calibration has three different numbers that must not be conflated:
 *
 *   ideal      the device's design target, i.e. what a good result looks like
 *   threshold  the acceptance floor below which a human must decide
 *   actual     the value this attempt recorded
 *
 * A threshold is not an ideal: "98%" means "we must investigate below this",
 * not "this is as good as the machine gets". The comparison therefore keeps
 * both references and reports the shortfall to each.
 *
 * The module is pure: it accepts a target plus the already-selected
 * measurement and returns display data. It never fabricates an actual value
 * for a preview, running or interrupted node.
 *
 * Reference resolution, strongest first:
 *
 *   target     frozen in the result's evaluation -> live Skill/device binding
 *              -> front-end demo constant (labelled 演示参考目标)
 *   threshold  frozen criterion of this attempt -> live binding acceptance
 *              -> demo floor (only for simulated measurements) -> none
 *
 * A server-backed result therefore never borrows a demo acceptance floor: if
 * the backend did not record one and no binding is cached, the comparison
 * says "未设验收下限" instead of inventing a threshold.
 */
import type { ExperimentNode, Workflow } from "../types.ts";
import { nodeMeasurements } from "./measurements.ts";
import type {
  MeasurementSource,
  MeasurementStage,
} from "./measurements.ts";
import { latestServerMeasurement } from "./remoteMeasurements.ts";
import type { ServerNodeMeasurement } from "./remoteMeasurements.ts";
import type { MetricTarget as WireMetricTarget } from "./remoteInsights.ts";
import {
  acceptanceForWorkflowNode,
  targetForWorkflowNode,
} from "./remoteTargets.ts";
import { displayValue, formatMetricValue } from "./metricFormat.ts";

// Existing callers import the formatter from this module; keep that path
// working while the implementation lives with the other wire formatters.
export { formatMetricValue } from "./metricFormat.ts";

export type MetricDirection = "higher" | "lower" | "band";
export type ComparisonStatus =
  | "preview"
  | "running"
  | "interrupted"
  | "unavailable"
  | "achieved"
  | "passing"
  | "failing";
export type TargetSource = "demo" | "device" | "skill";
export type ThresholdSource = "server" | "device" | "skill" | "demo" | "none";

export interface MetricTarget {
  key: string;
  label: string;
  /** Backend metric path, e.g. "fidelity" or "parameters.*.t1Us". */
  metric: string;
  ideal: number;
  unit: string;
  direction: MetricDirection;
  /** Band metrics only: allowed deviation from the design value. */
  tolerance?: number;
  decimals?: number;
  /** Which selected measurement supplies the actual value. */
  actualKey: "fidelity" | "t1Us" | "t2Us";
  /**
   * Front-end demo floor. A target supplied by the backend has none: the
   * binding's acceptance criterion is used instead, and a missing one means
   * "未设验收下限" rather than a fabricated demo value.
   */
  demoThreshold?: number;
}

export interface MetricComparison extends MetricTarget {
  threshold: number | null;
  actual: number | null;
  /**
   * Positive means "worse than ideal" for both directions:
   * higher-is-better -> ideal - actual, lower-is-better -> actual - ideal.
   */
  shortfall: number | null;
  gapPercent: number | null;
  /** actual / ideal for higher-is-better, ideal / actual for lower. */
  attainment: number | null;
  /** Signed distance from the acceptance floor (positive = safely inside). */
  thresholdGap: number | null;
  status: ComparisonStatus;
  source: MeasurementSource;
  thresholdSource: ThresholdSource;
  targetSource: TargetSource;
}

/**
 * Design targets for the demo devices. These are illustrative reference
 * values, labelled as such in the UI; they are not hardware measurements.
 * The backend Skill/device profile is authoritative in remote mode; this
 * table is the fallback when no binding is cached (mock mode or an
 * unreachable backend).
 */
export const DEMO_METRIC_TARGETS: Record<string, MetricTarget> = {
  IqToProbability: {
    key: "readout_fidelity",
    label: "读取判别保真度",
    metric: "fidelity",
    ideal: 99.5,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  ReadoutParaSweepByIQProb: {
    key: "readout_fidelity",
    label: "读取参数优化保真度",
    metric: "fidelity",
    ideal: 99.5,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  IqToProbabilityRetest: {
    key: "readout_fidelity",
    label: "读取判别保真度（复测）",
    metric: "fidelity",
    ideal: 99.5,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  RB1QCal: {
    key: "rb_fidelity",
    label: "单比特 RB 保真度",
    metric: "fidelity",
    ideal: 99.9,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  CZXEBCal: {
    key: "cz_fidelity",
    label: "CZ 交叉熵保真度",
    metric: "fidelity",
    ideal: 99.5,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  CZXEBCalRetest: {
    key: "cz_fidelity",
    label: "CZ 交叉熵保真度（复测）",
    metric: "fidelity",
    ideal: 99.5,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  CZParaOptimizer: {
    key: "cz_fidelity",
    label: "CZ 优化后保真度",
    metric: "fidelity",
    ideal: 99.5,
    unit: "%",
    direction: "higher",
    decimals: 2,
    actualKey: "fidelity",
    demoThreshold: 98,
  },
  T1Idle: {
    key: "t1",
    label: "T1 弛豫时间",
    metric: "parameters.*.t1Us",
    ideal: 60,
    unit: "μs",
    direction: "higher",
    decimals: 1,
    actualKey: "t1Us",
    demoThreshold: 30,
  },
  T2CPMG: {
    key: "t2",
    label: "T2 相干时间",
    metric: "parameters.*.t2Us",
    ideal: 80,
    unit: "μs",
    direction: "higher",
    decimals: 2,
    actualKey: "t2Us",
    demoThreshold: 30,
  },
};

export function targetForNode(
  node: ExperimentNode | undefined,
): MetricTarget | undefined {
  if (!node) return undefined;
  return DEMO_METRIC_TARGETS[node.id] ?? DEMO_METRIC_TARGETS[node.name];
}

function worseThanIdeal(
  target: MetricTarget,
  actual: number,
): number {
  if (target.direction === "lower") return actual - target.ideal;
  if (target.direction === "band") {
    return Math.abs(actual - target.ideal) - (target.tolerance ?? 0);
  }
  return target.ideal - actual;
}

function meetsIdeal(target: MetricTarget, actual: number): boolean {
  if (target.direction === "lower") return actual <= target.ideal;
  if (target.direction === "band") {
    return Math.abs(actual - target.ideal) <= (target.tolerance ?? 0);
  }
  return actual >= target.ideal;
}

function failsThreshold(
  target: MetricTarget,
  actual: number,
  threshold: number | null,
): boolean {
  if (threshold === null) return false;
  if (target.direction === "lower") return actual > threshold;
  if (target.direction === "band") {
    return Math.abs(actual - target.ideal) > threshold;
  }
  return actual < threshold;
}

function thresholdDistance(
  target: MetricTarget,
  actual: number,
  threshold: number | null,
): number | null {
  if (threshold === null) return null;
  if (target.direction === "lower") return threshold - actual;
  if (target.direction === "band") {
    return threshold - Math.abs(actual - target.ideal);
  }
  return actual - threshold;
}

function ratio(numerator: number, denominator: number): number | null {
  if (!Number.isFinite(numerator) || !Number.isFinite(denominator)) return null;
  if (denominator === 0) return null;
  return numerator / denominator;
}

export interface CompareMetricInput {
  target: MetricTarget;
  threshold: number | null;
  actual: number | null;
  stage: MeasurementStage;
  source: MeasurementSource;
  thresholdSource?: ThresholdSource;
  targetSource?: TargetSource;
}

export function compareMetric({
  target,
  threshold,
  actual,
  stage,
  source,
  thresholdSource = "demo",
  targetSource = "demo",
}: CompareMetricInput): MetricComparison {
  // Only a finished attempt has a value that may be compared. Partial,
  // preview and interrupted nodes keep the references visible but expose no
  // "actual" (and therefore no gap).
  const effectiveActual = stage === "ready" ? actual : null;
  const shortfall =
    effectiveActual === null ? null : worseThanIdeal(target, effectiveActual);
  const thresholdGap =
    effectiveActual === null
      ? null
      : thresholdDistance(target, effectiveActual, threshold);

  let status: ComparisonStatus;
  if (stage === "preview") status = "preview";
  else if (stage === "partial") status = "running";
  else if (stage === "interrupted") status = "interrupted";
  else if (effectiveActual === null) status = "unavailable";
  else if (failsThreshold(target, effectiveActual, threshold))
    status = "failing";
  else if (meetsIdeal(target, effectiveActual)) status = "achieved";
  else status = "passing";

  const attainment =
    effectiveActual === null || target.direction === "band"
      ? null
      : target.direction === "lower"
        ? ratio(target.ideal, effectiveActual)
        : ratio(effectiveActual, target.ideal);
  const relativeGap = shortfall === null ? null : ratio(shortfall, target.ideal);

  return {
    ...target,
    threshold,
    actual: effectiveActual,
    shortfall,
    gapPercent: relativeGap === null ? null : relativeGap * 100,
    attainment,
    thresholdGap,
    status,
    source,
    thresholdSource,
    targetSource,
  };
}

function actualKeyFor(metric: string): MetricTarget["actualKey"] | null {
  if (metric === "fidelity") return "fidelity";
  if (metric.endsWith("t1Us")) return "t1Us";
  if (metric.endsWith("t2Us")) return "t2Us";
  return null;
}

function directionOf(value: string): MetricDirection {
  return value === "lower" || value === "band" ? value : "higher";
}

/**
 * Merge a backend target into the display shape. The demo target supplies
 * layout-only metadata (label, decimals, which measurement field to read);
 * the ideal, direction and tolerance always come from the backend.
 */
function wireToDisplayTarget(
  wire: WireMetricTarget,
  base: MetricTarget | undefined,
  fallbackLabel: string,
): MetricTarget | null {
  const layout = base && base.metric === wire.metric ? base : undefined;
  const actualKey = layout?.actualKey ?? actualKeyFor(wire.metric);
  if (!actualKey) return null;
  const unit = layout?.unit ?? (wire.unit === "ratio" ? "%" : wire.unit);
  const ideal = displayValue(wire.ideal, wire.unit, unit);
  if (ideal === null) return null;
  const tolerance =
    wire.tolerance === null
      ? undefined
      : (displayValue(wire.tolerance, wire.unit, unit) ?? undefined);
  const direction = directionOf(wire.direction);
  if (direction === "band" && tolerance === undefined) return null;
  return {
    key: layout?.key ?? wire.metric,
    label: layout?.label ?? fallbackLabel,
    metric: wire.metric,
    ideal,
    unit,
    direction,
    tolerance,
    decimals: layout?.decimals,
    actualKey,
    demoThreshold: layout?.demoThreshold,
  };
}

interface ResolvedComparisonTarget {
  target: MetricTarget;
  targetSource: TargetSource;
}

/**
 * Design target for this node, strongest reference first:
 *
 *   1. the target frozen with this exact attempt's result — it is what the
 *      recorded verdict was judged against, even if the policy changed since;
 *   2. the workflow's live Skill/device binding;
 *   3. the front-end demo constant, explicitly labelled as such.
 */
function resolveTarget(
  workflow: Workflow,
  node: ExperimentNode,
  currentServer: ServerNodeMeasurement | undefined,
): ResolvedComparisonTarget | null {
  const demo = targetForNode(node);

  if (currentServer?.evaluationTarget) {
    const frozen = wireToDisplayTarget(
      currentServer.evaluationTarget,
      demo,
      node.label,
    );
    if (frozen) {
      return {
        target: frozen,
        targetSource:
          currentServer.evaluationTargetSource === "device" ? "device" : "skill",
      };
    }
  }

  const bound = targetForWorkflowNode(workflow.id, node.id);
  if (bound) {
    const display = wireToDisplayTarget(bound.target, demo, node.label);
    if (display) return { target: display, targetSource: bound.source };
  }

  return demo ? { target: demo, targetSource: "demo" } : null;
}

function acceptanceThreshold(
  criterion: { threshold: number; unit: string } | null | undefined,
  unit: string,
): number | null {
  return criterion ? displayValue(criterion.threshold, criterion.unit, unit) : null;
}

interface ResolvedThreshold {
  threshold: number | null;
  thresholdSource: ThresholdSource;
}

/**
 * Acceptance floor for this attempt, in the target's display unit.
 *
 * A server-backed result never falls back to the demo floor: when neither a
 * frozen criterion nor a binding acceptance criterion is available, the floor
 * is `null` and the UI says so.
 */
function resolveThreshold(
  workflow: Workflow,
  node: ExperimentNode,
  target: MetricTarget,
  currentServer: ServerNodeMeasurement | undefined,
  source: MeasurementSource,
): ResolvedThreshold {
  const frozen = acceptanceThreshold(currentServer?.criterion, target.unit);
  if (frozen !== null) {
    return { threshold: frozen, thresholdSource: "server" };
  }

  const bound = acceptanceForWorkflowNode(workflow.id, node.id);
  if (bound) {
    const threshold = acceptanceThreshold(bound.criterion, target.unit);
    return threshold === null
      ? { threshold: null, thresholdSource: "none" }
      : { threshold, thresholdSource: bound.source };
  }

  if (source === "simulated" && target.demoThreshold !== undefined) {
    return { threshold: target.demoThreshold, thresholdSource: "demo" };
  }

  return { threshold: null, thresholdSource: "none" };
}

/**
 * Comparison rows for a node. Control-parameter steps (frequency sweeps,
 * pulse amplitudes) intentionally return an empty list: their "ideal" is a
 * design setting with a tolerance, not a quality score.
 */
export function buildNodeComparisons(
  workflow: Workflow,
  node: ExperimentNode | undefined,
): MetricComparison[] {
  if (!node) return [];
  const measurement = nodeMeasurements(workflow, node);
  const server =
    measurement.source === "server"
      ? latestServerMeasurement(workflow.id, node.id)
      : undefined;
  const currentServer =
    server && server.attempt === node.attempt ? server : undefined;
  const resolved = resolveTarget(workflow, node, currentServer);
  if (!resolved) return [];
  const { target } = resolved;

  let actual: number | null = null;
  if (measurement.stage === "ready") {
    if (measurement.source === "server") {
      // Never fall back to a demo/base value for a server-backed node: a
      // missing field means "no valid measurement", not "use the default".
      actual = currentServer
        ? target.actualKey === "fidelity"
          ? currentServer.fidelityPercent
          : target.actualKey === "t1Us"
            ? currentServer.t1Us
            : currentServer.t2Us
        : null;
    } else {
      actual =
        target.actualKey === "fidelity"
          ? measurement.fidelity
          : measurement.tau;
    }
  }

  const { threshold, thresholdSource } = resolveThreshold(
    workflow,
    node,
    target,
    currentServer,
    measurement.source,
  );

  return [
    compareMetric({
      target,
      threshold,
      actual,
      stage: measurement.stage,
      source: measurement.source,
      thresholdSource,
      targetSource: resolved.targetSource,
    }),
  ];
}

export const COMPARISON_STATUS_LABELS: Record<ComparisonStatus, string> = {
  preview: "尚未采集",
  running: "采集中",
  interrupted: "已终止",
  unavailable: "无有效数据",
  achieved: "达到理想",
  passing: "达标 · 有差距",
  failing: "未达验收",
};

/** Where the displayed design target came from. */
export const TARGET_SOURCE_LABELS: Record<TargetSource, string> = {
  demo: "演示参考目标",
  device: "设备设计目标",
  skill: "Skill 设计目标",
};

/** Where the displayed acceptance floor came from. */
export const THRESHOLD_SOURCE_LABELS: Record<ThresholdSource, string> = {
  server: "服务端冻结标准",
  device: "设备验收下限",
  skill: "Skill 验收下限",
  demo: "演示验收下限",
  none: "未设验收下限",
};

/** Short, honest description of the distance, for cards and the inspector. */
export function describeComparisonGap(comparison: MetricComparison): string {
  switch (comparison.status) {
    case "preview":
      return "尚未采集，完成本节点后显示差距";
    case "running":
      return "采集中，完成本节点后显示差距";
    case "interrupted":
      return "已终止，本次未产生最终结果";
    case "unavailable":
      return "无有效实测值";
    default:
      break;
  }
  if (comparison.shortfall === null) return "无有效实测值";
  const gapUnit =
    comparison.unit === "%" ? "pp" : comparison.unit || "";
  const decimals = comparison.decimals ?? 2;
  if (comparison.direction === "band") {
    const deviation =
      comparison.actual === null
        ? null
        : comparison.actual - comparison.ideal;
    if (deviation === null) return "无有效实测值";
    const amount = formatMetricValue(Math.abs(deviation), gapUnit, decimals);
    const sign = deviation > 0 ? "+" : deviation < 0 ? "−" : "";
    return comparison.shortfall > 0
      ? `偏离设计值 ${sign}${amount}，超出容差`
      : `偏离设计值 ${sign}${amount}，在容差内`;
  }
  const amount = formatMetricValue(
    Math.abs(comparison.shortfall),
    gapUnit,
    decimals,
  );
  if (comparison.shortfall > 0) return `距理想尚差 ${amount}`;
  if (comparison.shortfall < 0) return `优于理想 ${amount}`;
  return "与理想值一致";
}

/**
 * Geometry for the dual-scale attainment bar, in percent of the bar width.
 *
 * `higher` and `lower` share one linear axis from zero: the fill is the
 * measurement, the floor and the ideal are markers. A zero-based axis is
 * useless for a `band` metric (a ±0.05 GHz tolerance around 4 GHz would be
 * invisible), so those get a window centred on the design value instead, with
 * the tolerance band shaded and both acceptance edges marked.
 *
 * Returns `null` when there is nothing to draw: no measured value, or a
 * reference set that would divide by zero.
 */
export type AttainmentBar =
  | {
      kind: "linear";
      direction: "higher" | "lower";
      /** Measured value, as a percentage of the axis. */
      actual: number;
      ideal: number;
      floor: number | null;
    }
  | {
      kind: "band";
      actual: number;
      ideal: number;
      /** Tolerance window around the design value. */
      bandLeft: number;
      bandWidth: number;
      /** Acceptance window edges (`ideal ± threshold`), when a floor exists. */
      floorLeft: number | null;
      floorRight: number | null;
    };

export function attainmentBar(
  comparison: MetricComparison,
): AttainmentBar | null {
  const { actual, ideal, threshold, direction, tolerance } = comparison;
  if (actual === null) return null;
  const percent = (value: number, min: number, span: number) =>
    Math.min(100, Math.max(0, ((value - min) / span) * 100));

  if (direction === "band") {
    const half = Math.max(
      tolerance ?? 0,
      Math.abs(actual - ideal),
      threshold ?? 0,
    );
    if (!Number.isFinite(half) || half <= 0) return null;
    // 1.3× the widest reference keeps the tolerance window visible instead of
    // collapsing it to a hairline next to a tolerance-free measurement.
    const span = half * 1.3;
    const min = ideal - span;
    const size = span * 2;
    const toleranceWidth = tolerance ?? 0;
    const left = percent(ideal - toleranceWidth, min, size);
    const right = percent(ideal + toleranceWidth, min, size);
    return {
      kind: "band",
      actual: percent(actual, min, size),
      ideal: percent(ideal, min, size),
      bandLeft: left,
      bandWidth: right - left,
      floorLeft:
        threshold === null ? null : percent(ideal - threshold, min, size),
      floorRight:
        threshold === null ? null : percent(ideal + threshold, min, size),
    };
  }

  const scale = Math.max(ideal, actual, threshold ?? 0) * 1.05;
  if (!Number.isFinite(scale) || scale <= 0) return null;
  const position = (value: number) =>
    Math.min(100, Math.max(0, (value / scale) * 100));
  return {
    kind: "linear",
    direction,
    actual: position(actual),
    ideal: position(ideal),
    floor: threshold === null ? null : position(threshold),
  };
}

export function comparisonStatusClass(status: ComparisonStatus): string {
  if (status === "failing") return "pp-compare-failing";
  if (status === "achieved") return "pp-compare-achieved";
  if (status === "passing") return "pp-compare-passing";
  return "pp-compare-pending";
}
