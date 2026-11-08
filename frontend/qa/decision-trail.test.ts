import test from "node:test";
import assert from "node:assert/strict";
import {
  buildDecisionPath,
  buildNodeDecisions,
  describeMeasurementDecision,
  latestMeasurements,
} from "../src/lib/decisionTrail.ts";
import type {
  AuditResultEntry,
  WorkflowAudit,
  WorkflowReplay,
} from "../src/lib/remoteInsights.ts";

const CRITERION = {
  metric: "parameters.*.t1Us",
  comparator: ">=",
  threshold: 20,
  unit: "μs",
  description: "",
};

function result(
  nodeId: string,
  attempt: number,
  passed: boolean | undefined,
  options: {
    time?: string;
    actual?: number | null;
    escalation?: boolean;
    derived?: boolean;
  } = {},
): AuditResultEntry {
  return {
    type: "result",
    time: options.time ?? `2026-11-05T01:00:0${attempt}.000Z`,
    nodeId,
    attempt,
    stage: passed === true ? "completed" : "attention",
    evaluation: {
      criterion: CRITERION,
      actual: options.actual === undefined ? 18.5 : options.actual,
      passed,
      decision: passed === true ? "continue" : "attention",
    },
    evaluationSource: options.derived ? "derived" : "recorded",
    escalation: options.escalation
      ? { code: "metric_below_threshold", message: "指标未达标，需要人工判断。" }
      : null,
    metrics: {},
    rawRef: null,
  };
}

function audit(timeline: WorkflowAudit["timeline"]): WorkflowAudit {
  return {
    workflowId: "wf-1",
    revision: 5,
    status: "attention",
    skill: { skillId: "single-qubit-calibration", version: "1.0.0", name: "单比特校准" },
    autonomy: {
      level: "L2",
      name: "异常升级",
      policy: "threshold",
      description: "",
    },
    counts: { results: 3, actions: 1, events: 0, escalations: 1 },
    timeline,
  };
}

const TIMELINE: WorkflowAudit["timeline"] = [
  result("T1Idle", 1, true, { time: "2026-11-05T01:00:01.000Z" }),
  result("T1Idle", 2, false, {
    time: "2026-11-05T01:00:02.000Z",
    escalation: true,
  }),
  result("T2CPMG", 1, true, { time: "2026-11-05T01:00:03.000Z" }),
  result("IqToProbability", 1, undefined, {
    time: "2026-11-05T01:00:04.000Z",
    derived: true,
  }),
  {
    type: "action",
    time: "2026-11-05T01:00:05.000Z",
    requestId: "req-1",
    actorId: "user-leader",
    action: "approve",
    nodeId: "T1Idle",
    attempt: 2,
    expectedRevision: 4,
    resultRevision: 5,
    resultStatus: "success",
    response: null,
  },
];

test("node decisions keep the latest verdict per node and skip neutral records", () => {
  const decisions = buildNodeDecisions(audit(TIMELINE), null);
  assert.deepEqual(Object.keys(decisions).sort(), ["T1Idle", "T2CPMG"]);
  assert.equal(decisions.T1Idle.tone, "fail");
  assert.equal(decisions.T1Idle.label, "已升级");
  assert.match(decisions.T1Idle.detail, /实测 18\.5 μs/);
  assert.match(decisions.T1Idle.detail, /指标未达标/);
  assert.match(decisions.T1Idle.detail, /第 2 次/);
  assert.equal(decisions.T2CPMG.tone, "pass");
  assert.equal(decisions.T2CPMG.label, "验收通过");
});

test("a replayed drift overrides the recorded badge without rewriting it", () => {
  const replayed: WorkflowReplay = {
    workflowId: "wf-1",
    skill: { skillId: "single-qubit-calibration", version: "1.1.0", name: "单比特校准" },
    summary: { total: 3, changed: 1 },
    items: [
      {
        nodeId: "T2CPMG",
        attempt: 1,
        stage: "completed",
        recorded: {
          criterion: CRITERION,
          actual: 24,
          passed: true,
          samples: 1,
        },
        replayed: {
          ...CRITERION,
          actual: 24,
          passed: false,
          samples: 1,
          reason: "threshold_not_met",
        },
        changed: true,
      },
    ],
  };
  const decisions = buildNodeDecisions(audit(TIMELINE), replayed);
  assert.equal(decisions.T2CPMG.tone, "drift");
  assert.equal(decisions.T2CPMG.label, "标准漂移");
  assert.match(decisions.T2CPMG.detail, /记录 通过 → 重放 未通过/);
  // The recorded failure is untouched.
  assert.equal(decisions.T1Idle.tone, "fail");
});

test("derived and unverdictable measurements never become a pass or fail badge", () => {
  const derived = result("IqToProbability", 1, true, {
    derived: true,
    actual: 0.94,
  });
  const verdict = describeMeasurementDecision(derived);
  assert.equal(verdict.tone, "neutral");
  assert.equal(verdict.label, "推导判定");
  assert.match(verdict.detail, /不是冻结记录/);
  assert.equal(
    buildNodeDecisions(audit([derived]), null).IqToProbability,
    undefined,
  );

  const missing = result("T1Idle", 1, undefined, { actual: 12 });
  assert.equal(describeMeasurementDecision(missing).label, "无判定");

  const invalid = describeMeasurementDecision(
    result("T1Idle", 1, false, { actual: null }),
  );
  assert.equal(invalid.tone, "fail");
  assert.match(invalid.detail, /无效值/);
});

test("the decision path keeps chronology, labels commands and clamps its limit", () => {
  const path = buildDecisionPath(audit(TIMELINE));
  assert.deepEqual(
    path.map((item) => item.label),
    ["通过", "升级", "通过", "推导判定", "批准继续"],
  );
  assert.equal(path[4].kind, "action");
  assert.match(path[4].detail, /user-leader · success/);
  assert.equal(path[4].nodeId, "T1Idle");

  const tail = buildDecisionPath(audit(TIMELINE), 2);
  assert.deepEqual(
    tail.map((item) => item.kind),
    ["result", "action"],
  );
  assert.equal(buildDecisionPath(audit(TIMELINE), 0).length, 1);
  assert.equal(buildDecisionPath(null).length, 0);

  const latest = latestMeasurements(audit(TIMELINE));
  assert.deepEqual(
    latest.map((entry) => `${entry.nodeId}:${entry.attempt}`),
    ["T1Idle:2", "T2CPMG:1", "IqToProbability:1"],
  );
});
