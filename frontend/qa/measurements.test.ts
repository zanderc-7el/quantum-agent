import test from "node:test";
import assert from "node:assert/strict";
import type { ExperimentNode, Workflow } from "../src/types.ts";
import {
  applyAction,
  createWorkflow,
  NODE_DURATION_SECONDS,
  tickWorkflow,
} from "../src/lib/simulator.ts";
import {
  decaySamples,
  DEMO_CZ_WIDTH_NS,
  DEMO_THRESHOLDS,
  measurementStage,
  measurementStageLabel,
  nodeMeasurements,
  qubitMeasurements,
  reportMeasurements,
} from "../src/lib/measurements.ts";

function fresh(
  experiment: Workflow["experiment"],
  scenario: Workflow["scenario"] = "anomaly",
): Workflow {
  return createWorkflow({
    name: `Measurement QA ${experiment}`,
    machine: "M7",
    experiment,
    qubits: experiment === "two" ? 2 : 1,
    target: experiment === "two" ? "Q12–Q13" : "Q12",
    type: "adaptive",
    policy: "threshold",
    scenario,
  });
}

function node(workflow: Workflow, id: string): ExperimentNode {
  const result = workflow.nodes.find((item) => item.id === id);
  assert.ok(result, `Expected node ${id}`);
  return result;
}

function attention(experiment: Workflow["experiment"]): Workflow {
  const workflow = tickWorkflow(applyAction(fresh(experiment), "start"), 1000);
  assert.equal(workflow.status, "attention");
  return workflow;
}

for (const experiment of ["single", "two"] as const) {
  const anomalyId =
    experiment === "two" ? "CZXEBCal" : "ReadoutParaSweepByIQProb";
  const retestId =
    experiment === "two" ? "CZXEBCalRetest" : "IqToProbabilityRetest";
  const expectedRecoveredFidelity = experiment === "two" ? 99.12 : 98.9;
  const reportedFidelity = (workflow: Workflow) => {
    const report = reportMeasurements(workflow);
    return experiment === "two" ? report.cz : report.readout;
  };
  const reportedNode = (workflow: Workflow) => {
    const report = reportMeasurements(workflow);
    return experiment === "two" ? report.czNode : report.readoutNode;
  };

  test(`${experiment}: a fresh anomaly scenario remains an unexecuted preview, not an observed failure`, () => {
    const workflow = fresh(experiment);
    const report = reportMeasurements(workflow);
    assert.equal(workflow.status, "draft");
    assert.equal(report.preview, true);
    assert.equal(report.completed, false);
    assert.equal(report.qualityFailed, false);
    assert.equal(report.label, "示例预览·尚未执行");
    assert.match(report.conclusion, /尚未执行/);
    assert.equal(measurementStage(report.t1Node), "preview");
    assert.equal(measurementStage(reportedNode(workflow)), "preview");
    assert.equal(
      nodeMeasurements(workflow, node(workflow, anomalyId)).anomaly,
      false,
    );
    assert.equal(reportedFidelity(workflow), expectedRecoveredFidelity);
    assert.ok(workflow.nodes.every((item) => item.attempt === 0));
  });

  test(`${experiment}: the actual first-attempt anomaly is 94.2% at its own node and does not contaminate T1/T2`, () => {
    const workflow = attention(experiment);
    const anomalousNode = node(workflow, anomalyId);
    const report = reportMeasurements(workflow);
    assert.equal(anomalousNode.status, "attention");
    assert.equal(anomalousNode.attempt, 1);
    assert.equal(nodeMeasurements(workflow, anomalousNode).fidelity, 94.2);
    assert.equal(reportedFidelity(workflow), 94.2);
    assert.equal(report.qualityFailed, true);
    assert.equal(report.preview, false);
    assert.equal(report.completed, false);
    assert.equal(report.label, "阶段报告·流程未完成");
    assert.match(report.conclusion, /低于 98%/);
    assert.equal(reportedNode(workflow)?.id, anomalyId);
    assert.equal(
      nodeMeasurements(workflow, node(workflow, "T1Idle")).tau,
      50.84,
    );
    assert.equal(
      nodeMeasurements(workflow, node(workflow, "T2CPMG")).tau,
      56.94,
    );
    assert.equal(
      nodeMeasurements(workflow, node(workflow, "T1Idle")).anomaly,
      false,
    );
    assert.equal(
      nodeMeasurements(workflow, node(workflow, "T2CPMG")).anomaly,
      false,
    );
    if (experiment === "two") assert.equal(report.readout, 98.9);
    else assert.equal(measurementStage(report.t1Node), "preview");
  });

  test(`${experiment}: retry advances the same node to attempt 2 and the final report recovers`, () => {
    const original = attention(experiment);
    const retried = applyAction(original, "retry");
    const retryNode = node(retried, anomalyId);
    assert.equal(retryNode.attempt, 2);
    assert.equal(retryNode.status, "running");
    assert.equal(measurementStage(retryNode), "partial");
    assert.equal(reportMeasurements(retried).completed, false);
    assert.equal(nodeMeasurements(retried, retryNode).anomaly, false);
    assert.equal(
      nodeMeasurements(retried, retryNode).fidelity,
      expectedRecoveredFidelity,
    );
    assert.equal(
      node(original, anomalyId).attempt,
      1,
      "Retry must preserve the original immutable result",
    );
    assert.equal(reportedFidelity(original), 94.2);

    const completed = tickWorkflow(retried, 1000);
    const report = reportMeasurements(completed);
    assert.equal(completed.status, "completed");
    assert.equal(completed.progress, 100);
    assert.equal(report.completed, true);
    assert.equal(report.label, "模拟完成报告");
    assert.equal(report.qualityFailed, false);
    assert.equal(reportedFidelity(completed), expectedRecoveredFidelity);
    assert.equal(reportedNode(completed)?.id, retestId);
    assert.equal(measurementStage(report.t1Node), "ready");
    assert.equal(
      nodeMeasurements(completed, node(completed, "T1Idle")).tau,
      50.84,
    );
  });

  test(`${experiment}: accepting a low result does not fabricate a pass before a completed retest`, () => {
    const original = attention(experiment);
    let accepted = applyAction(original, "approve");
    assert.equal(node(accepted, anomalyId).status, "success");
    assert.equal(node(accepted, anomalyId).attempt, 1);
    assert.equal(
      nodeMeasurements(accepted, node(accepted, anomalyId)).fidelity,
      94.2,
    );
    assert.equal(reportedFidelity(accepted), 94.2);
    assert.equal(reportMeasurements(accepted).qualityFailed, true);
    assert.equal(reportMeasurements(accepted).completed, false);
    assert.equal(reportedNode(accepted)?.id, anomalyId);

    if (experiment === "two") {
      assert.equal(node(accepted, "CZParaOptimizer").status, "running");
      accepted = tickWorkflow(accepted, NODE_DURATION_SECONDS);
      assert.equal(node(accepted, "CZParaOptimizer").status, "success");
      assert.equal(node(accepted, "CZParaOptimizer").attempt, 1);
      assert.equal(
        reportedFidelity(accepted),
        94.2,
        "Optimization alone is not a measured retest",
      );
    }
    assert.equal(node(accepted, retestId).status, "running");
    const partialRetest = tickWorkflow(accepted, NODE_DURATION_SECONDS / 2);
    assert.equal(node(partialRetest, retestId).progress, 50);
    assert.equal(reportedNode(partialRetest)?.id, anomalyId);
    assert.equal(reportedFidelity(partialRetest), 94.2);
    assert.equal(reportMeasurements(partialRetest).qualityFailed, true);

    const measuredRetest = tickWorkflow(
      partialRetest,
      NODE_DURATION_SECONDS / 2,
    );
    assert.equal(node(measuredRetest, retestId).status, "success");
    assert.equal(node(measuredRetest, retestId).attempt, 1);
    assert.equal(reportedNode(measuredRetest)?.id, retestId);
    assert.equal(reportedFidelity(measuredRetest), expectedRecoveredFidelity);
    assert.equal(reportMeasurements(measuredRetest).qualityFailed, false);
    assert.equal(
      nodeMeasurements(measuredRetest, node(measuredRetest, anomalyId))
        .fidelity,
      94.2,
      "Historical accepted result must retain its original low metric",
    );
    assert.equal(
      nodeMeasurements(measuredRetest, node(measuredRetest, retestId)).fidelity,
      expectedRecoveredFidelity,
    );
  });

  test(`${experiment}: node and report measurements share deterministic parameters and decay samples`, () => {
    const workflow = tickWorkflow(
      applyAction(fresh(experiment, "normal"), "start"),
      1000,
    );
    const report = reportMeasurements(workflow);
    const expected = qubitMeasurements("Q12");
    assert.equal(expected.t1, 50.84);
    assert.equal(expected.t2, 56.94);
    assert.equal(expected.error, 0.62);
    assert.equal(expected.r2, 0.9985);
    assert.equal(expected.rbFidelity, 99.37);
    assert.equal(nodeMeasurements(workflow, report.t1Node!).tau, expected.t1);
    assert.equal(nodeMeasurements(workflow, report.t2Node!).tau, expected.t2);
    assert.equal(
      nodeMeasurements(workflow, report.rbNode!).fidelity,
      expected.rbFidelity,
    );
    assert.equal(
      nodeMeasurements(workflow, report.readoutNode!).fidelity,
      report.readout,
    );
    if (experiment === "two")
      assert.equal(
        nodeMeasurements(workflow, report.czNode!).fidelity,
        report.cz,
      );
    const reportSamples = decaySamples("Q12");
    const nodeSamples = decaySamples(
      nodeMeasurements(workflow, report.t1Node!).qubit,
      nodeMeasurements(workflow, report.t1Node!).tau,
    );
    assert.deepEqual(nodeSamples, reportSamples);
    assert.equal(reportSamples.length, 21);
    assert.equal(reportSamples[0].time, 0);
    assert.equal(reportSamples.at(-1)!.time, 160);
    assert.equal(reportSamples[0].fitted, 0.98);
    assert.ok(
      reportSamples.every(
        (sample) => sample.measured >= 0 && sample.measured <= 1,
      ),
    );
    assert.deepEqual(decaySamples("Q12"), reportSamples);
  });
}

test("measurement stage and threshold labels are explicit for missing, running and interrupted data", () => {
  assert.equal(measurementStage(undefined), "preview");
  assert.equal(measurementStageLabel(undefined), "示例预览·尚未执行");
  const running = applyAction(fresh("single", "normal"), "start");
  assert.equal(measurementStage(running.nodes[0]), "partial");
  assert.equal(measurementStageLabel(running.nodes[0]), "阶段数据·尚未完成");
  const abandoned = applyAction(tickWorkflow(running, 1), "abort");
  assert.equal(measurementStage(abandoned.nodes[0]), "interrupted");
  assert.equal(reportMeasurements(abandoned).completed, false);
  assert.equal(DEMO_THRESHOLDS.coherenceUs, 30);
  assert.equal(DEMO_THRESHOLDS.fidelityPercent, 98);
  assert.equal(DEMO_CZ_WIDTH_NS, 60);
});
