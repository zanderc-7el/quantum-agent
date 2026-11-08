import test from "node:test";
import assert from "node:assert/strict";
import type { Workflow } from "../src/types.ts";
import { applyAction, createWorkflow, tickWorkflow } from "../src/lib/simulator.ts";
import { buildNodeComparisons } from "../src/lib/metricComparison.ts";
import {
  clearWorkflowTargets,
  getWorkflowTargets,
  publishWorkflowBinding,
} from "../src/lib/remoteTargets.ts";
import {
  clearWorkflowResults,
  publishWorkflowResults,
} from "../src/lib/remoteMeasurements.ts";
import type { NodeResultPayload } from "../src/lib/remoteMeasurements.ts";
import type {
  AcceptanceCriterion,
  MetricTarget,
  SkillBinding,
  SkillStep,
} from "../src/lib/remoteInsights.ts";

function completed(): Workflow {
  return tickWorkflow(
    applyAction(
      createWorkflow({
        name: "远端设计目标测试",
        machine: "M7",
        experiment: "single",
        qubits: 1,
        target: "Q12",
        type: "adaptive",
        policy: "threshold",
        scenario: "normal",
      }),
      "start",
    ),
    1000,
  );
}

function criterion(threshold: number): AcceptanceCriterion {
  return {
    metric: "fidelity",
    comparator: ">=",
    threshold,
    unit: "ratio",
    description: "test",
  };
}

function target(ideal: number): MetricTarget {
  return {
    metric: "fidelity",
    ideal,
    unit: "ratio",
    direction: "higher",
    tolerance: null,
    description: "test",
  };
}

function binding(
  nodeId: string,
  options: {
    target: MetricTarget | null;
    targetSource: "device" | "skill" | null;
    acceptance: AcceptanceCriterion;
    deviceOverride?: boolean;
  },
): SkillBinding {
  const step: SkillStep & {
    effectiveAcceptance: AcceptanceCriterion;
    deviceOverride: boolean;
    effectiveTarget: MetricTarget | null;
    targetSource: "device" | "skill" | null;
  } = {
    nodeId,
    name: nodeId,
    label: nodeId,
    acceptance: criterion(0.98),
    target: options.target,
    onFail: "retry",
    maxAttempts: 2,
    effectiveAcceptance: options.acceptance,
    deviceOverride: options.deviceOverride ?? false,
    effectiveTarget: options.target,
    targetSource: options.targetSource,
  };
  return {
    workflowId: "",
    revision: 1,
    binding: {
      skillId: "single-qubit-calibration",
      version: "1.1.0",
      source: "builtin",
    },
    autonomy: {
      level: "L2",
      name: "阈值内自主",
      policy: "threshold",
      description: "",
    },
    skill: {
      skillId: "single-qubit-calibration",
      version: "1.1.0",
      name: "单比特",
      experiment: "single",
      description: "",
      steps: [step],
      autonomyLevels: [],
    },
    effectiveSteps: [step],
  };
}

function result(
  wf: Workflow,
  nodeId: string,
  metrics: Record<string, unknown>,
  attempt = 1,
): NodeResultPayload {
  return {
    id: `result-${wf.id}-${nodeId}`,
    workflowId: wf.id,
    nodeId,
    attempt,
    stage: "completed",
    metrics,
    rawRef: null,
    createdAt: "2026-11-05T00:00:00.000Z",
  };
}

function approx(actual: number | null, expected: number, epsilon = 1e-9) {
  assert.ok(
    actual !== null && Math.abs(actual - expected) < epsilon,
    `expected ${expected}, got ${actual}`,
  );
}

test("a device binding replaces the demo ideal and floor, converting ratios", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  const published = publishWorkflowBinding(
    wf.id,
    binding("IqToProbability", {
      target: target(0.99),
      targetSource: "device",
      acceptance: criterion(0.97),
      deviceOverride: true,
    }),
  );
  assert.equal(published.targetsByNode.IqToProbability.source, "device");
  assert.equal(published.acceptanceByNode.IqToProbability.source, "device");

  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.targetSource, "device");
  assert.equal(comparison.thresholdSource, "device");
  approx(comparison.ideal, 99);
  approx(comparison.threshold, 97);
  // The simulated 98.9% readout is above the device floor yet short of the
  // device's 99% design goal.
  assert.equal(comparison.status, "passing");

  clearWorkflowTargets(wf.id);
});

test("a Skill binding marks the target and floor as Skill defaults", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  publishWorkflowBinding(
    wf.id,
    binding("IqToProbability", {
      target: target(0.999),
      targetSource: "skill",
      acceptance: criterion(0.98),
    }),
  );
  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.targetSource, "skill");
  assert.equal(comparison.thresholdSource, "skill");
  approx(comparison.ideal, 99.9);
  approx(comparison.threshold, 98);
  clearWorkflowTargets(wf.id);
});

test("the frozen evaluation target and criterion win over the live binding", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  publishWorkflowBinding(
    wf.id,
    binding("IqToProbability", {
      target: target(0.99),
      targetSource: "device",
      acceptance: criterion(0.97),
      deviceOverride: true,
    }),
  );
  publishWorkflowResults(wf.id, [
    result(wf, "IqToProbability", {
      fidelity: 0.9712,
      evaluation: {
        passed: false,
        target: { ...target(0.999) },
        targetSource: "skill",
        criterion: { ...criterion(0.985) },
      },
    }),
  ]);

  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.source, "server");
  assert.equal(comparison.targetSource, "skill");
  assert.equal(comparison.thresholdSource, "server");
  approx(comparison.ideal, 99.9);
  approx(comparison.threshold, 98.5);
  approx(comparison.actual, 97.12);
  assert.equal(comparison.status, "failing");

  clearWorkflowResults(wf.id);
  clearWorkflowTargets(wf.id);
});

test("a stale attempt cannot supply either reference from its frozen record", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  publishWorkflowBinding(
    wf.id,
    binding("IqToProbability", {
      target: target(0.99),
      targetSource: "device",
      acceptance: criterion(0.97),
      deviceOverride: true,
    }),
  );
  // attempt 2 belongs to a retry, not to this finished attempt 1 node.
  publishWorkflowResults(wf.id, [
    result(
      wf,
      "IqToProbability",
      {
        fidelity: 0.5,
        evaluation: {
          passed: false,
          target: { ...target(0.5) },
          targetSource: "skill",
          criterion: { ...criterion(0.5) },
        },
      },
      node.attempt + 1,
    ),
  ]);

  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.source, "simulated");
  assert.equal(comparison.targetSource, "device");
  approx(comparison.ideal, 99);
  assert.equal(comparison.thresholdSource, "device");
  approx(comparison.threshold, 97);
  approx(comparison.actual, 98.9);

  clearWorkflowResults(wf.id);
  clearWorkflowTargets(wf.id);
});

test("a server result without a frozen criterion never borrows the demo floor", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  publishWorkflowResults(wf.id, [
    result(wf, "IqToProbability", { fidelity: 0.985 }),
  ]);

  const snapshot = getWorkflowTargets(wf.id);
  assert.equal(snapshot, undefined);

  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.source, "server");
  // No binding and no recorded criterion: the target falls back to the demo
  // constant (labelled), but the acceptance floor stays unset.
  assert.equal(comparison.targetSource, "demo");
  assert.equal(comparison.thresholdSource, "none");
  assert.equal(comparison.threshold, null);
  approx(comparison.actual, 98.5);
  assert.equal(comparison.status, "passing");

  clearWorkflowResults(wf.id);
});

test("a binding without effective steps still resolves its flat target index", () => {
  const wf = completed();
  const node = wf.nodes.find((item) => item.id === "IqToProbability")!;
  const flat = binding("IqToProbability", {
    target: target(0.992),
    targetSource: "device",
    acceptance: criterion(0.975),
    deviceOverride: true,
  });
  delete flat.effectiveSteps;
  flat.targets = [
    {
      nodeId: "IqToProbability",
      target: target(0.992),
      source: "device",
    },
  ];
  flat.device = {
    deviceId: "M3",
    version: "1.1.0",
    name: "M3",
    vendor: "simulated",
    qubits: 5,
    experiments: ["single"],
    adapter: "simulator",
    supportsDryRun: true,
    known: true,
    notes: "",
    acceptanceOverrides: { IqToProbability: criterion(0.975) },
    fidelityCriterion: null,
    targetOverrides: { IqToProbability: target(0.992) },
    fidelityTarget: null,
  };
  publishWorkflowBinding(wf.id, flat);

  const [comparison] = buildNodeComparisons(wf, node);
  assert.equal(comparison.targetSource, "device");
  assert.equal(comparison.thresholdSource, "device");
  approx(comparison.ideal, 99.2);
  approx(comparison.threshold, 97.5);

  clearWorkflowTargets(wf.id);
});
