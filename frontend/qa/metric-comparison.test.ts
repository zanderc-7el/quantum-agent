import test from "node:test";
import assert from "node:assert/strict";
import type { Workflow } from "../src/types.ts";
import { applyAction, createWorkflow, tickWorkflow } from "../src/lib/simulator.ts";
import {
  attainmentBar,
  buildNodeComparisons,
  compareMetric,
  describeComparisonGap,
  formatMetricValue,
  targetForNode,
} from "../src/lib/metricComparison.ts";
import type { MetricTarget } from "../src/lib/metricComparison.ts";
import {
  publishWorkflowResults,
  clearWorkflowResults,
} from "../src/lib/remoteMeasurements.ts";
import type { NodeResultPayload } from "../src/lib/remoteMeasurements.ts";

const HIGHER: MetricTarget = {
  key: "readout_fidelity",
  label: "读取判别保真度",
  metric: "fidelity",
  ideal: 99.5,
  unit: "%",
  direction: "higher",
  decimals: 2,
  actualKey: "fidelity",
  demoThreshold: 98,
};

const LOWER: MetricTarget = {
  key: "readout_error",
  label: "读取误差",
  metric: "readoutError",
  ideal: 2.5,
  unit: "%",
  direction: "lower",
  decimals: 2,
  actualKey: "fidelity",
  demoThreshold: 3,
};

const BAND: MetricTarget = {
  key: "drive_frequency",
  label: "驱动频率",
  metric: "parameters.*.f01Ghz",
  ideal: 4,
  unit: "GHz",
  direction: "band",
  tolerance: 0.05,
  decimals: 3,
  actualKey: "fidelity",
  demoThreshold: 0.1,
};

function workflow(options: {
  scenario?: "normal" | "anomaly";
  policy?: Workflow["policy"];
} = {}): Workflow {
  return createWorkflow({
    name: "理想值与实测值对比验收",
    machine: "M7",
    experiment: "single",
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: options.policy ?? "threshold",
    scenario: options.scenario ?? "normal",
  });
}

function completed(): Workflow {
  return tickWorkflow(applyAction(workflow(), "start"), 1000);
}

function attention(): Workflow {
  return tickWorkflow(
    applyAction(workflow({ scenario: "anomaly" }), "start"),
    1000,
  );
}

function approx(
  actual: number | null,
  expected: number,
  epsilon = 1e-9,
): void {
  assert.ok(
    actual !== null && Math.abs(actual - expected) < epsilon,
    `expected ${expected}, got ${actual}`,
  );
}

function result(
  wf: Workflow,
  nodeId: string,
  overrides: Partial<NodeResultPayload> = {},
): NodeResultPayload {
  return {
    id: `result-${wf.id}-${nodeId}`,
    workflowId: wf.id,
    nodeId,
    attempt: 1,
    stage: "completed",
    metrics: {
      fidelity: 0.9712,
      simulated: true,
      adapter: "simulator",
      parameters: { Q12: { f01Ghz: 4.81, t1Us: 42.5, t2Us: 51.25 } },
    },
    rawRef: null,
    createdAt: "2026-11-05T00:00:00.000Z",
    ...overrides,
  };
}

test("higher-is-better: achieved, passing and failing are distinguished", () => {
  const achieved = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: 99.8,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(achieved.status, "achieved");
  assert.equal(achieved.actual, 99.8);
  assert.ok(achieved.shortfall !== null && achieved.shortfall < 0);
  approx(achieved.thresholdGap, 1.8);
  assert.match(describeComparisonGap(achieved), /优于理想 0\.30 pp/);

  const passing = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: 98.9,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(passing.status, "passing");
  approx(passing.shortfall, 0.6);
  approx(passing.thresholdGap, 0.9);
  assert.match(describeComparisonGap(passing), /距理想尚差 0\.60 pp/);

  const failing = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: 97.2,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(failing.status, "failing");
  approx(failing.shortfall, 2.3);
  approx(failing.thresholdGap, -0.8);
  assert.match(describeComparisonGap(failing), /距理想尚差 2\.30 pp/);
});

test("lower-is-better metrics invert the comparison direction", () => {
  const achieved = compareMetric({
    target: LOWER,
    threshold: 3,
    actual: 2,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(achieved.status, "achieved");
  approx(achieved.shortfall, -0.5);
  approx(achieved.thresholdGap, 1);
  approx(achieved.attainment, 1.25);

  const passing = compareMetric({
    target: LOWER,
    threshold: 3,
    actual: 2.8,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(passing.status, "passing");
  approx(passing.shortfall, 0.3);

  const failing = compareMetric({
    target: LOWER,
    threshold: 3,
    actual: 3.4,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(failing.status, "failing");
  approx(failing.thresholdGap, -0.4);
});

test("band metrics compare distance from the design value against tolerance", () => {
  const inside = compareMetric({
    target: BAND,
    threshold: 0.1,
    actual: 4.03,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(inside.status, "achieved");
  assert.ok(inside.shortfall !== null && inside.shortfall < 0);
  assert.equal(inside.attainment, null);

  const passing = compareMetric({
    target: BAND,
    threshold: 0.1,
    actual: 4.08,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(passing.status, "passing");
  assert.ok(Math.abs((passing.shortfall ?? 0) - 0.03) < 1e-9);

  const failing = compareMetric({
    target: BAND,
    threshold: 0.1,
    actual: 4.2,
    stage: "ready",
    source: "simulated",
  });
  assert.equal(failing.status, "failing");
  assert.ok(Math.abs((failing.shortfall ?? 0) - 0.15) < 1e-9);
});

test("the attainment bar is drawn for higher, lower and band metrics", () => {
  const higher = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: 99.1,
    stage: "ready",
    source: "simulated",
  });
  const higherBar = attainmentBar(higher);
  assert.ok(higherBar && higherBar.kind === "linear");
  assert.equal(higherBar.direction, "higher");
  assert.ok(higherBar.actual < higherBar.ideal, "99.1% sits below 99.5%");
  assert.ok(higherBar.floor !== null && higherBar.floor < higherBar.ideal);

  const lower = compareMetric({
    target: LOWER,
    threshold: 3,
    actual: 2.2,
    stage: "ready",
    source: "simulated",
  });
  const lowerBar = attainmentBar(lower);
  assert.ok(lowerBar && lowerBar.kind === "linear");
  assert.equal(lowerBar.direction, "lower");
  assert.ok(lowerBar.actual < lowerBar.ideal, "2.2% is better than 2.5%");
  assert.ok(lowerBar.floor !== null && lowerBar.floor > lowerBar.ideal);

  const band = compareMetric({
    target: BAND,
    threshold: 0.1,
    actual: 4.08,
    stage: "ready",
    source: "simulated",
  });
  const bandBar = attainmentBar(band);
  assert.ok(bandBar && bandBar.kind === "band");
  assert.ok(bandBar.bandWidth > 0, "the tolerance window stays visible");
  assert.ok(
    bandBar.actual > bandBar.bandLeft + bandBar.bandWidth,
    "4.08 is outside the ±0.05 tolerance band",
  );
  assert.ok(bandBar.floorLeft !== null && bandBar.floorRight !== null);
  assert.ok(bandBar.floorLeft < bandBar.bandLeft);
  assert.ok(bandBar.floorRight > bandBar.bandLeft + bandBar.bandWidth);

  const preview = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: 99.1,
    stage: "preview",
    source: "simulated",
  });
  assert.equal(attainmentBar(preview), null);
});

test("no actual value is fabricated before an attempt completes", () => {
  const preview = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: 99.1,
    stage: "preview",
    source: "simulated",
  });
  assert.equal(preview.status, "preview");
  assert.equal(preview.actual, null);
  assert.equal(preview.shortfall, null);
  assert.equal(preview.attainment, null);
  assert.equal(preview.thresholdGap, null);
  assert.match(describeComparisonGap(preview), /尚未采集/);

  for (const stage of ["partial", "interrupted"] as const) {
    const comparison = compareMetric({
      target: HIGHER,
      threshold: 98,
      actual: 99.1,
      stage,
      source: "simulated",
    });
    assert.equal(comparison.actual, null);
    assert.equal(
      comparison.status,
      stage === "partial" ? "running" : "interrupted",
    );
  }

  const missing = compareMetric({
    target: HIGHER,
    threshold: 98,
    actual: null,
    stage: "ready",
    source: "server",
  });
  assert.equal(missing.status, "unavailable");
  assert.equal(missing.actual, null);
});

test("a recorded criterion in ratio units becomes a percent threshold", () => {
  const wf = attention();
  const node = wf.nodes.find((item) => item.status === "attention")!;
  publishWorkflowResults(wf.id, [
    result(wf, node.id, {
      metrics: {
        fidelity: 0.9712,
        evaluation: {
          passed: false,
          reason: "fidelity below device threshold",
          criterion: {
            metric: "fidelity",
            comparator: ">=",
            threshold: 0.98,
            unit: "ratio",
            description: "M7 readout fidelity",
          },
        },
      },
    }),
  ]);
  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.source, "server");
  assert.equal(comparison.thresholdSource, "server");
  approx(comparison.threshold, 98);
  assert.ok(Math.abs((comparison.actual ?? 0) - 97.12) < 1e-9);
  assert.equal(comparison.status, "failing");
  clearWorkflowResults(wf.id);
});

test("a stale attempt never supplies the actual value or the threshold", () => {
  const wf = completed();
  assert.equal(wf.status, "completed");
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  assert.equal(node.status, "success");
  publishWorkflowResults(wf.id, [
    result(wf, node.id, {
      attempt: node.attempt + 1,
      metrics: {
        fidelity: 0.5,
        evaluation: {
          passed: false,
          criterion: {
            metric: "fidelity",
            comparator: ">=",
            threshold: 0.9,
            unit: "ratio",
            description: "stale",
          },
        },
      },
    }),
  ]);
  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.source, "simulated");
  assert.equal(comparison.thresholdSource, "demo");
  assert.equal(comparison.threshold, 98);
  assert.equal(comparison.actual, 98.9);
  clearWorkflowResults(wf.id);
});

test("server-backed nodes do not borrow demo actual values when a field is missing", () => {
  const wf = attention();
  const node = wf.nodes.find((item) => item.status === "attention")!;
  publishWorkflowResults(wf.id, [
    result(wf, node.id, {
      metrics: { parameters: { Q12: { t1Us: 42.5 } } },
    }),
  ]);
  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.source, "server");
  assert.equal(comparison.actual, null);
  assert.equal(comparison.status, "unavailable");
  clearWorkflowResults(wf.id);
});

test("demo comparisons expose the design target and acceptance floor", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.ideal, 99.5);
  assert.equal(comparison.threshold, 98);
  assert.equal(comparison.targetSource, "demo");
  assert.equal(comparison.thresholdSource, "demo");
  assert.equal(comparison.actual, 98.9);
  assert.equal(comparison.status, "passing");
});

test("control-parameter steps return no quality comparison", () => {
  const wf = completed();
  const sweep = wf.nodes.find((item) => item.id === "ReadoutFreqDip")!;
  assert.deepEqual(buildNodeComparisons(wf, sweep), []);
  assert.equal(targetForNode(sweep), undefined);
});

test("retest instances keep their own labels and targets", () => {
  const wf = completed();
  const retest = wf.nodes.find((item) => item.id === "IqToProbabilityRetest")!;
  assert.equal(targetForNode(retest)?.label, "读取判别保真度（复测）");
  const t2 = wf.nodes.find((item) => item.id === "T2CPMG")!;
  assert.equal(targetForNode(t2)?.unit, "μs");
  assert.equal(targetForNode(t2)?.ideal, 80);
});

test("values and gaps format consistently for percent and time units", () => {
  assert.equal(formatMetricValue(99.5, "%"), "99.50%");
  assert.equal(formatMetricValue(60, "μs", 1), "60.0 μs");
  assert.equal(formatMetricValue(null, "μs"), "—");
  assert.equal(
    describeComparisonGap(
      compareMetric({
        target: HIGHER,
        threshold: 98,
        actual: 99.5,
        stage: "ready",
        source: "simulated",
      }),
    ),
    "与理想值一致",
  );
});
