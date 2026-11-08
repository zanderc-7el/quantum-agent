import test from "node:test";
import assert from "node:assert/strict";
import type { Workflow } from "../src/types.ts";
import {
  applyAction,
  createWorkflow,
  tickWorkflow,
} from "../src/lib/simulator.ts";
import {
  clearWorkflowResults,
  publishWorkflowResults,
  buildSnapshot,
  serverQubitParameters,
} from "../src/lib/remoteMeasurements.ts";
import type { NodeResultPayload } from "../src/lib/remoteMeasurements.ts";
import {
  nodeMeasurements,
  qubitMeasurements,
  reportMeasurements,
} from "../src/lib/measurements.ts";

function attentionWorkflow(): Workflow {
  const workflow = createWorkflow({
    name: "Server measurement QA",
    machine: "M7",
    experiment: "single",
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: "threshold",
    scenario: "anomaly",
  });
  return tickWorkflow(applyAction(workflow, "start"), 1000);
}

function result(
  workflow: Workflow,
  nodeId: string,
  overrides: Partial<NodeResultPayload> = {},
): NodeResultPayload {
  return {
    id: `result-${nodeId}`,
    workflowId: workflow.id,
    nodeId,
    attempt: 1,
    stage: "completed",
    metrics: {
      fidelity: 0.9712,
      simulated: true,
      adapter: "simulator",
      parameters: {
        Q12: { f01Ghz: 4.81, readoutGhz: 6.91, t1Us: 42.5, t2Us: 51.25 },
      },
    },
    rawRef: null,
    createdAt: "2026-11-05T00:00:00.000Z",
    ...overrides,
  };
}

test("adapter metrics are normalized: fractions become percent, parameters are keyed by qubit", () => {
  const workflow = attentionWorkflow();
  const snapshot = buildSnapshot(workflow.id, [
    result(workflow, "ReadoutParaSweepByIQProb", {
      metrics: {
        fidelity: 0.9712,
        parameters: { Q12: { t1Us: 42.5, t2Us: 51.25 } },
      },
    }),
  ]);
  const measurement = snapshot.latestByNode.ReadoutParaSweepByIQProb;
  assert.equal(measurement.fidelityPercent, 97.12);
  assert.equal(measurement.t1Us, 42.5);
  assert.equal(measurement.t2Us, 51.25);
  assert.equal(measurement.passed, null);
});

test("the newest attempt wins and a stale attempt is never shown for a running retry", () => {
  const workflow = attentionWorkflow();
  const nodeId = "ReadoutParaSweepByIQProb";
  const retried = applyAction(workflow, "retry");
  assert.equal(retried.nodes.find((node) => node.id === nodeId)!.attempt, 2);
  publishWorkflowResults(workflow.id, [
    result(workflow, nodeId, { attempt: 1, metrics: { fidelity: 0.94 } }),
    result(workflow, nodeId, {
      id: "result-latest",
      attempt: 2,
      metrics: { fidelity: 0.995 },
    }),
  ]);
  const currentNode = retried.nodes.find((node) => node.id === nodeId)!;
  assert.equal(nodeMeasurements(retried, currentNode).fidelity, 99.5);
  assert.equal(nodeMeasurements(retried, currentNode).source, "server");

  // Another retry launched: the stored result belongs to attempt 2.
  const again = retried.nodes.map((node) =>
    node.id === nodeId
      ? { ...node, attempt: 3, status: "running" as const }
      : node,
  );
  const stale = { ...retried, nodes: again };
  assert.equal(
    nodeMeasurements(stale, again.find((node) => node.id === nodeId)!).source,
    "simulated",
  );
  clearWorkflowResults();
});

test("recorded values and the frozen verdict drive node, report and qubit views", () => {
  const workflow = attentionWorkflow();
  const nodeId = "ReadoutParaSweepByIQProb";
  publishWorkflowResults(workflow.id, [
    result(workflow, nodeId, {
      metrics: {
        fidelity: 0.9712,
        parameters: { Q12: { t1Us: 42.5, t2Us: 51.25 } },
        evaluation: { passed: true, reason: "device threshold 95%" },
      },
    }),
  ]);
  const node = workflow.nodes.find((item) => item.id === nodeId)!;
  const measured = nodeMeasurements(workflow, node);
  assert.equal(measured.fidelity, 97.12);
  assert.equal(measured.anomaly, false, "a frozen pass beats the demo 98% gate");
  assert.equal(measured.source, "server");

  const report = reportMeasurements(workflow);
  assert.equal(report.source, "server");
  assert.equal(report.readout, 97.12);
  assert.equal(report.qualityFailed, false);
  assert.equal(report.label, "阶段报告·服务端测量");

  const qubit = qubitMeasurements("Q12", workflow);
  assert.equal(qubit.t1, 42.5);
  assert.equal(qubit.t2, 51.25);
  assert.deepEqual(serverQubitParameters(workflow.id, "q12"), {
    t1Us: 42.5,
    t2Us: 51.25,
  });
  clearWorkflowResults();
});

test("a recorded failure marks the report as failed even above the demo threshold", () => {
  const workflow = attentionWorkflow();
  const nodeId = "ReadoutParaSweepByIQProb";
  publishWorkflowResults(workflow.id, [
    result(workflow, nodeId, {
      metrics: {
        fidelity: 0.99,
        evaluation: { passed: false, reason: "device threshold 99.5%" },
      },
    }),
  ]);
  const node = workflow.nodes.find((item) => item.id === nodeId)!;
  assert.equal(nodeMeasurements(workflow, node).anomaly, true);
  assert.equal(reportMeasurements(workflow).qualityFailed, true);
  clearWorkflowResults();
});

test("without a server snapshot every selector keeps the deterministic demo values", () => {
  const workflow = attentionWorkflow();
  const node = workflow.nodes.find(
    (item) => item.id === "ReadoutParaSweepByIQProb",
  )!;
  const measured = nodeMeasurements(workflow, node);
  assert.equal(measured.source, "simulated");
  assert.equal(measured.fidelity, 94.2);
  assert.equal(reportMeasurements(workflow).label, "阶段报告·流程未完成");
  assert.equal(qubitMeasurements("Q12").t1, 50.84);
});
