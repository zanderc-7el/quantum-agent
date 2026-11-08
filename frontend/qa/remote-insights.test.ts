import test from "node:test";
import assert from "node:assert/strict";
import {
  REMOTE_SESSION_KEY,
  type RemoteSession,
} from "../src/lib/apiClient.ts";
import {
  describeAuditEntry,
  describeReplayItem,
  fetchDevices,
  fetchWorkflowAudit,
  fetchWorkflowReplay,
  fetchWorkflowSkill,
} from "../src/lib/remoteInsights.ts";

const SESSION: RemoteSession = {
  accessToken: "token-insight",
  user: {
    userId: "user-leader",
    username: "leader",
    displayName: "团队负责人",
    roleId: "calibration_leader",
  },
  savedAt: 1,
};

const storageValues = new Map<string, string>();
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => storageValues.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storageValues.set(key, value);
    },
    removeItem: (key: string) => {
      storageValues.delete(key);
    },
  },
});
storageValues.set(REMOTE_SESSION_KEY, JSON.stringify(SESSION));

function ok(data: unknown): Response {
  return new Response(
    JSON.stringify({ success: true, data, error: null }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

interface RecordedCall {
  url: string;
  init: RequestInit;
}

function installFetch(data: unknown): RecordedCall[] {
  const calls: RecordedCall[] = [];
  globalThis.fetch = (async (input: unknown, init: RequestInit = {}) => {
    calls.push({ url: String(input), init });
    return ok(data);
  }) as typeof fetch;
  return calls;
}

const SKILL = {
  workflowId: "wf-1",
  revision: 3,
  binding: {
    skillId: "single-qubit-calibration",
    version: "1.0.0",
    source: "builtin",
  },
  autonomy: {
    level: "L2",
    name: "异常升级",
    policy: "threshold",
    description: "指标达标自动继续。",
  },
  skill: {
    skillId: "single-qubit-calibration",
    version: "1.0.0",
    name: "单比特校准",
    experiment: "single",
    description: "",
    steps: [
      {
        nodeId: "T1Idle",
        name: "T1Idle",
        label: "T1弛豫测量",
        acceptance: {
          metric: "parameters.*.t1Us",
          comparator: ">=",
          threshold: 20,
          unit: "μs",
          description: "T1 不低于 20 μs。",
        },
        onFail: "escalate",
        maxAttempts: 2,
      },
    ],
    autonomyLevels: [],
  },
};

test("device list unwraps items and sends the bearer token", async () => {
  const calls = installFetch({
    items: [
      {
        deviceId: "M3",
        version: "1.0.0",
        name: "M3 早期机",
        vendor: "simulated",
        qubits: 5,
        experiments: ["single"],
        adapter: "simulator",
        supportsDryRun: true,
        known: true,
        notes: "",
        acceptanceOverrides: {},
        fidelityCriterion: null,
      },
    ],
    total: 1,
  });
  const devices = await fetchDevices();
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:18789/api/v1/calibration/devices",
  );
  assert.equal(
    (calls[0].init.headers as Record<string, string>).Authorization,
    "Bearer token-insight",
  );
  assert.deepEqual(devices.map((device) => device.deviceId), ["M3"]);
});

test("skill binding uses the encoded workflow URL and bearer token", async () => {
  const calls = installFetch(SKILL);
  const binding = await fetchWorkflowSkill("wf/1 2");
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:18789/api/v1/calibration/workflows/wf%2F1%202/skill",
  );
  assert.equal(
    (calls[0].init.headers as Record<string, string>).Authorization,
    "Bearer token-insight",
  );
  assert.equal(binding.binding.skillId, "single-qubit-calibration");
  assert.equal(binding.skill.steps[0].acceptance.threshold, 20);
});

test("audit request clamps its limit and returns the timeline", async () => {
  const audit = {
    workflowId: "wf-1",
    revision: 4,
    status: "attention",
    skill: { skillId: "single-qubit-calibration", version: "1.0.0", name: "单比特校准" },
    autonomy: SKILL.autonomy,
    counts: { results: 1, actions: 1, events: 1 },
    timeline: [],
  };
  const calls = installFetch(audit);
  await fetchWorkflowAudit("wf-1", 999999);
  assert.match(calls[0].url, /\/audit\?limit=5000$/);
  await fetchWorkflowAudit("wf-1", 0);
  assert.match(calls[1].url, /\/audit\?limit=1$/);
});

test("replay request hits the read-only endpoint and marks drift", async () => {
  const replay = {
    workflowId: "wf/1 2",
    skill: {
      skillId: "single-qubit-calibration",
      version: "1.0.0",
      name: "单比特校准",
    },
    summary: { total: 2, changed: 1 },
    items: [
      {
        nodeId: "T1Idle",
        attempt: 1,
        stage: "completed",
        recorded: { passed: true, skillVersion: "0.9.0" },
        replayed: {
          metric: "parameters.*.t1Us",
          comparator: ">=",
          threshold: 20,
          unit: "μs",
          description: "",
          actual: 12,
          passed: false,
          samples: 1,
          reason: "threshold_not_met",
        },
        changed: true,
      },
      {
        nodeId: "T2CPMG",
        attempt: 1,
        stage: "completed",
        recorded: null,
        replayed: {
          metric: "parameters.*.t2Us",
          comparator: ">=",
          threshold: 10,
          unit: "μs",
          description: "",
          actual: 15,
          passed: true,
          samples: 1,
          reason: null,
        },
        changed: false,
      },
    ],
  };
  const calls = installFetch(replay);
  const result = await fetchWorkflowReplay("wf/1 2");
  assert.equal(
    calls[0].url,
    "http://127.0.0.1:18789/api/v1/calibration/workflows/wf%2F1%202/audit/replay",
  );
  assert.equal(
    (calls[0].init.headers as Record<string, string>).Authorization,
    "Bearer token-insight",
  );
  assert.equal(result.summary.changed, 1);

  const drift = describeReplayItem(result.items[0]);
  assert.equal(drift.label, "标准漂移");
  assert.equal(drift.warning, true);
  assert.match(drift.detail, /记录 通过 → 重放 未通过/);
  assert.match(drift.detail, /实测 12\.0 μs/);

  const stable = describeReplayItem(result.items[1]);
  assert.equal(stable.label, "一致");
  assert.equal(stable.warning, false);
  assert.match(stable.detail, /记录 无记录 → 重放 通过/);
});

test("a moved design target is drift even when the verdict is unchanged", () => {
  const target = (ideal: number) => ({
    metric: "parameters.*.t1Us",
    ideal,
    unit: "μs",
    direction: "higher",
    tolerance: null,
    description: "",
  });
  const outcome = {
    metric: "parameters.*.t1Us",
    comparator: ">=",
    threshold: 20,
    unit: "μs",
    description: "",
    actual: 30,
    passed: true,
    samples: 1,
    reason: null,
  };
  const moved = describeReplayItem({
    nodeId: "T1Idle",
    attempt: 1,
    stage: "completed",
    recorded: { passed: true },
    replayed: outcome,
    recordedTarget: target(45),
    replayedTarget: target(60),
    replayedTargetSource: "skill",
    changed: false,
    targetChanged: true,
  });
  assert.equal(moved.label, "目标漂移");
  assert.equal(moved.warning, true);
  assert.match(moved.detail, /记录 通过 → 重放 通过/);
  assert.match(
    moved.detail,
    /目标 ≥ 45\.0 μs（越大越好） → ≥ 60\.0 μs（越大越好）/,
  );

  // No frozen target means no baseline: an old result is not drift.
  const legacy = describeReplayItem({
    nodeId: "T1Idle",
    attempt: 1,
    stage: "completed",
    recorded: { passed: true },
    replayed: outcome,
    recordedTarget: null,
    replayedTarget: target(60),
    changed: false,
    targetChanged: null,
  });
  assert.equal(legacy.label, "一致");
  assert.equal(legacy.warning, false);
});

test("audit lines surface the measured value, escalation and command actor", () => {
  const result = describeAuditEntry({
    type: "result",
    time: "2026-01-01T00:00:01Z",
    nodeId: "T1Idle",
    attempt: 1,
    stage: "attention",
    evaluation: {
      criterion: {
        metric: "parameters.*.t1Us",
        comparator: ">=",
        threshold: 20,
        unit: "μs",
        description: "",
      },
      target: {
        metric: "parameters.*.t1Us",
        ideal: 60,
        unit: "μs",
        direction: "higher",
        tolerance: null,
        description: "",
      },
      targetSource: "skill",
      actual: 18.5,
      passed: false,
      decision: "attention",
    },
    evaluationSource: "recorded",
    escalation: { code: "metric_below_threshold", message: "指标未达标。" },
    metrics: {},
    rawRef: null,
  });
  assert.equal(result.label, "测量");
  assert.match(result.detail, /实测 18\.5 μs/);
  assert.match(result.detail, /阈值 ≥ 20\.0 μs/);
  assert.match(result.detail, /Skill 设计目标 ≥ 60\.0 μs/);
  assert.match(result.detail, /指标未达标/);
  assert.equal(result.warning, true);

  const action = describeAuditEntry({
    type: "action",
    time: "2026-01-01T00:00:00Z",
    requestId: "req-1",
    actorId: "user-leader",
    action: "approve",
    nodeId: "T1Idle",
    attempt: 1,
    expectedRevision: 3,
    resultRevision: 4,
    resultStatus: "success",
    response: null,
  });
  assert.match(action.detail, /approve · user-leader · revision 3→4/);
  assert.equal(action.warning, false);

  const event = describeAuditEntry({
    type: "event",
    time: "2026-01-01T00:00:00Z",
    seq: 1,
    kind: "warning",
    message: "等待决定。",
  });
  assert.equal(event.warning, true);
});
