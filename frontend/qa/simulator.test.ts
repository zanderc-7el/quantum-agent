import test from "node:test";
import assert from "node:assert/strict";
import type {
  AppState,
  CreateWorkflowInput,
  Policy,
  Scenario,
  Workflow,
} from "../src/types.ts";
import {
  applyAction,
  createInitialState,
  createWorkflow,
  loadState,
  MAX_WORKFLOWS,
  NODE_DURATION_SECONDS,
  saveState,
  setPolicy,
  STORAGE_KEY,
  tickWorkflow,
} from "../src/lib/simulator.ts";
import { createGateway, createMockGateway } from "../src/lib/gateway.ts";

function fresh(
  policy: Policy = "threshold",
  scenario: Scenario = "normal",
  experiment: Workflow["experiment"] = "single",
): Workflow {
  return createWorkflow({
    name: "QA calibration",
    machine: "M7",
    experiment,
    qubits: experiment === "two" ? 2 : 1,
    target: experiment === "two" ? "Q12–Q13" : "Q12",
    type: "adaptive",
    policy,
    scenario,
  } as CreateWorkflowInput);
}

function assertInvariants(workflow: Workflow): void {
  assert.ok(workflow.progress >= 0 && workflow.progress <= 100);
  assert.ok(Number.isFinite(workflow.elapsed) && workflow.elapsed >= 0);
  assert.ok(
    workflow.nodes.filter(
      (node) => node.status === "running" || node.status === "attention",
    ).length <= 1,
  );
  assert.ok(
    workflow.nodes.every((node) => node.progress >= 0 && node.progress <= 100),
  );
  if (workflow.status === "running")
    assert.equal(
      workflow.nodes.filter((node) => node.status === "running").length,
      1,
    );
  if (workflow.status === "completed") {
    assert.equal(workflow.progress, 100);
    assert.ok(workflow.nodes.every((node) => node.status === "success"));
  }
}

test("seed fixture covers all eight UI states and the selected workflow starts at readout", () => {
  const { workflows } = createInitialState();
  assert.equal(workflows.length, 8);
  assert.equal(new Set(workflows.map((workflow) => workflow.status)).size, 8);
  const selected = workflows.find(
    (workflow) => workflow.id === "wf-m7-bringup",
  )!;
  assert.equal(selected.status, "running");
  assert.equal(selected.progress, 44);
  assert.equal(
    selected.nodes.find((node) => node.status === "running")?.id,
    "ReadoutParaSweepByIQProb",
  );
  assert.ok(workflows.every((workflow) => [1, 2].includes(workflow.qubits)));
  assert.ok(
    workflows.filter((workflow) => workflow.experiment === "two").length >= 2,
  );
  const investigation = workflows.find(
    (workflow) => workflow.id === "wf-q12-investigation",
  )!;
  assert.equal(investigation.status, "attention");
  workflows.forEach(assertInvariants);
});

test("single- and two-qubit routes match the PDF sequence including distinct retest instances", () => {
  const single = fresh();
  assert.deepEqual(
    single.nodes.map((node) => node.name),
    [
      "ReadoutFreqDip",
      "F01Spectroscopy",
      "XYGateAmpRabiImpl",
      "IqToProbability",
      "ReadoutParaSweepByIQProb",
      "IqToProbability",
      "T1Idle",
      "T2CPMG",
      "RB1QCal",
    ],
  );
  const two = fresh("threshold", "normal", "two");
  assert.deepEqual(
    two.nodes.map((node) => node.name),
    [
      ...single.nodes.map((node) => node.name),
      "F01ZPulseDependency",
      "ZeroPointFinderByQQSwap",
      "CZSwapSweepZPulse",
      "TTGAczDelicateG",
      "TTGAczDelicateDetune",
      "CZXEBCal",
      "CZParaOptimizer",
      "CZXEBCal",
    ],
  );
  assert.equal(new Set(two.nodes.map((node) => node.id)).size, 17);
  assert.match(two.nodes[12].detail, /CZ_width=60ns/);
  assert.match(two.nodes[5].detail, /复测/);
  assert.match(two.nodes[16].detail, /复测/);
  assert.ok(
    single.nodes.slice(6).every((node) => node.detail.includes("共享测控资源")),
  );
  const complete = tickWorkflow(applyAction(two, "start"), 1000);
  assert.match(complete.nodes[16].detail, /复测/);
});

test("unsupported qubit counts and mismatched routes are rejected", () => {
  const input = {
    name: "Invalid",
    machine: "M7",
    qubits: 3,
    target: "Q0–Q2",
    type: "adaptive",
    policy: "threshold",
    scenario: "normal",
  } as CreateWorkflowInput;
  assert.throws(() => createWorkflow(input), /仅支持单比特或双比特/);
  assert.throws(
    () => createWorkflow({ ...input, qubits: 1, experiment: "two" }),
    /须与所选量子比特数量一致/,
  );
});

test("target validation accepts supported separators, canonicalizes labels, and rejects duplicate qubits", () => {
  const input: CreateWorkflowInput = {
    name: "Target QA",
    machine: "M7",
    experiment: "single",
    qubits: 1,
    target: "q012",
    type: "adaptive",
    policy: "threshold",
    scenario: "normal",
  };
  assert.equal(createWorkflow(input).target, "Q12");
  assert.equal(createWorkflow({ ...input, target: "  " }).target, "Q12");
  for (const target of [
    "Q12–Q13",
    "Q12-Q13",
    "q12, q13",
    "Q12，Q13",
    " Q12 / Q13 ",
  ]) {
    const workflow = createWorkflow({
      ...input,
      experiment: "two",
      qubits: 2,
      target,
    });
    assert.equal(workflow.target, "Q12–Q13");
    assert.ok(workflow.nodes.every((node) => node.qubits === workflow.target));
  }
  assert.equal(
    createWorkflow({ ...input, experiment: "two", qubits: 2, target: "" })
      .target,
    "Q12–Q13",
  );
  for (const target of ["Q12,Q12", "q12/Q012", "Q0–Q00"])
    assert.throws(
      () => createWorkflow({ ...input, experiment: "two", qubits: 2, target }),
      /两个不同的量子比特/,
    );
  for (const target of ["Q12–Q13", "12", "Q-1", "Q1 Q2", "Q1<script>"])
    assert.throws(() => createWorkflow({ ...input, target }), /单比特目标/);
  for (const target of ["Q12", "Q1 Q2", "Q1,Q2,Q3", "Q1+Q2", "Q1:Q2"])
    assert.throws(
      () => createWorkflow({ ...input, experiment: "two", qubits: 2, target }),
      /双比特目标/,
    );
});

test("fresh draft is entirely pending and start is immutable and idempotent", () => {
  const draft = fresh();
  assert.equal(draft.status, "draft");
  assert.ok(
    draft.nodes.every(
      (node) =>
        node.status === "pending" && node.attempt === 0 && node.progress === 0,
    ),
  );
  const before = JSON.stringify(draft);
  const started = applyAction(draft, "start");
  assert.equal(JSON.stringify(draft), before);
  assert.equal(started.status, "running");
  assert.equal(started.nodes[0].attempt, 1);
  assert.equal(applyAction(started, "start"), started);
});

test("normal threshold run completes with exact active demo time and no manual approval", () => {
  const run = applyAction(fresh(), "start");
  const completed = tickWorkflow(run, 1000);
  assert.equal(completed.status, "completed");
  assert.equal(completed.elapsed, run.nodes.length * NODE_DURATION_SECONDS);
  assert.equal(completed.iterations, 0);
  assert.equal(tickWorkflow(completed, 100), completed);
  assertInvariants(completed);
});

test("pause freezes runtime and progress, resume continues from the same point", () => {
  const run = tickWorkflow(applyAction(fresh(), "start"), 1.5);
  const paused = applyAction(run, "pause");
  assert.equal(tickWorkflow(paused, 999), paused);
  const resumed = tickWorkflow(applyAction(paused, "resume"), 0.5);
  assert.equal(resumed.elapsed, 2);
  assert.equal(resumed.nodes[0].progress, 50);
  assert.equal(run.nodes[0].progress, 37.5);
});

test("strict pauses every node; duplicate approve cannot skip a node", () => {
  let run = applyAction(fresh("strict"), "start");
  for (let index = 0; index < run.nodes.length; index += 1) {
    run = tickWorkflow(run, 100);
    assert.equal(run.status, "attention");
    assert.equal(run.nodes[index].status, "attention");
    assert.equal(
      run.nodes.filter((node) => node.status === "success").length,
      index,
    );
    const elapsed = run.elapsed;
    assert.equal(tickWorkflow(run, 100).elapsed, elapsed);
    run = applyAction(run, "approve");
    assert.equal(applyAction(run, "approve"), run);
    assert.equal(
      run.nodes.filter((node) => node.status === "success").length,
      index + 1,
    );
    assertInvariants(run);
  }
  assert.equal(run.status, "completed");
  assert.equal(run.elapsed, run.nodes.length * NODE_DURATION_SECONDS);
});

test("threshold anomaly pauses at readout; approve accepts while retry reruns the same node", () => {
  const attention = tickWorkflow(
    applyAction(fresh("threshold", "anomaly"), "start"),
    100,
  );
  const index = attention.nodes.findIndex(
    (node) => node.status === "attention",
  );
  assert.equal(attention.nodes[index].id, "ReadoutParaSweepByIQProb");
  assert.equal(attention.status, "attention");
  const approved = applyAction(attention, "approve");
  assert.equal(approved.nodes[index].status, "success");
  assert.equal(approved.nodes[index].attempt, 1);
  assert.equal(approved.iterations, 0);
  const retried = applyAction(attention, "retry");
  assert.equal(retried.nodes[index].status, "running");
  assert.equal(retried.nodes[index].progress, 0);
  assert.equal(retried.nodes[index].attempt, 2);
  assert.equal(retried.iterations, 1);
  assert.equal(retried.approved, false);
  assert.equal(tickWorkflow(retried, 100).status, "completed");
});

test("accepting a low-metric result can complete the workflow without claiming every experiment passed", () => {
  const attention = tickWorkflow(
    applyAction(fresh("threshold", "anomaly"), "start"),
    100,
  );
  const completed = tickWorkflow(applyAction(attention, "approve"), 100);
  assert.equal(completed.status, "completed");
  assert.match(
    completed.nodes.find((node) => node.id === "ReadoutParaSweepByIQProb")!
      .detail,
    /接受不代表指标达标/,
  );
  assert.ok(
    completed.events.some(
      (event) =>
        event.kind === "warning" && event.message.includes("不代表指标达标"),
    ),
  );
  assert.match(completed.events.at(-1)!.message, /流程执行完成/);
  assert.ok(
    completed.events.every(
      (event) => !/全部.*通过|全部.*达标|All .*passed/i.test(event.message),
    ),
  );
});

test("user-visible events and node outcomes use Chinese while preserving experiment identifiers", () => {
  const workflows = [
    ...createInitialState().workflows,
    tickWorkflow(
      applyAction(fresh("autopilot", "anomaly", "two"), "start"),
      1000,
    ),
  ];
  for (const workflow of workflows) {
    assert.ok(
      workflow.events.every((event) => /[\u4e00-\u9fff]/.test(event.message)),
    );
    assert.ok(
      workflow.nodes.every((node) => /[\u4e00-\u9fff]/.test(node.detail)),
    );
  }
});

test("autopilot retries the anomaly once and reaches completion without an infinite loop", () => {
  const run = tickWorkflow(
    applyAction(fresh("autopilot", "anomaly"), "start"),
    1000,
  );
  assert.equal(run.status, "completed");
  assert.equal(run.iterations, 1);
  assert.equal(
    run.nodes.find((node) => node.id === "ReadoutParaSweepByIQProb")?.attempt,
    2,
  );
  assert.equal(run.elapsed, (run.nodes.length + 1) * NODE_DURATION_SECONDS);
  assertInvariants(run);
});

test("two-qubit anomaly triggers at the first CZ benchmark and still executes its later retest", () => {
  const attention = tickWorkflow(
    applyAction(fresh("threshold", "anomaly", "two"), "start"),
    1000,
  );
  assert.equal(attention.status, "attention");
  assert.equal(
    attention.nodes.find((node) => node.status === "attention")?.id,
    "CZXEBCal",
  );
  assert.equal(
    attention.nodes.find((node) => node.id === "ReadoutParaSweepByIQProb")
      ?.attempt,
    1,
  );
  const completed = tickWorkflow(applyAction(attention, "retry"), 1000);
  assert.equal(completed.status, "completed");
  assert.equal(
    completed.nodes.find((node) => node.id === "CZXEBCal")?.attempt,
    2,
  );
  assert.equal(
    completed.nodes.find((node) => node.id === "CZXEBCalRetest")?.attempt,
    1,
  );
  const auto = tickWorkflow(
    applyAction(fresh("autopilot", "anomaly", "two"), "start"),
    1000,
  );
  assert.equal(auto.status, "completed");
  assert.equal(auto.iterations, 1);
  assert.equal(auto.elapsed, 72);
});

test("abort is terminal and cannot start, resume, approve, or retry", () => {
  const run = applyAction(
    tickWorkflow(applyAction(fresh(), "start"), 6),
    "abort",
  );
  assert.equal(run.status, "aborted");
  assert.ok(
    run.nodes.every(
      (node) => node.status === "success" || node.status === "abandoned",
    ),
  );
  for (const action of [
    "start",
    "resume",
    "approve",
    "retry",
    "pause",
    "abort",
  ] as const)
    assert.equal(applyAction(run, action), run);
  assert.equal(tickWorkflow(run, 999), run);
});

test("policy changes are logged and do not silently accept an existing pending decision", () => {
  const pending = tickWorkflow(applyAction(fresh("strict"), "start"), 4);
  const changed = setPolicy(pending, "autopilot");
  assert.equal(changed.policy, "autopilot");
  assert.equal(changed.status, "attention");
  assert.equal(changed.events.length, pending.events.length + 1);
  assert.equal(
    tickWorkflow(applyAction(changed, "approve"), 100).status,
    "completed",
  );
});

test("nonpositive and nonfinite ticks cannot corrupt the simulation", () => {
  const run = applyAction(fresh(), "start");
  for (const seconds of [0, -1, Number.NaN, Number.POSITIVE_INFINITY])
    assert.equal(tickWorkflow(run, seconds), run);
});

test("all policy/scenario combinations preserve state invariants through small ticks", () => {
  for (const policy of ["strict", "threshold", "autopilot"] as const) {
    for (const scenario of ["normal", "anomaly"] as const) {
      for (const experiment of ["single", "two"] as const) {
        let run = applyAction(fresh(policy, scenario, experiment), "start");
        for (
          let tick = 0;
          tick < 400 && run.status !== "completed";
          tick += 1
        ) {
          assertInvariants(run);
          run =
            run.status === "attention"
              ? applyAction(run, "approve")
              : tickWorkflow(run, 0.25);
        }
        assert.equal(
          run.status,
          "completed",
          `${experiment}/${policy}/${scenario} must terminate`,
        );
        assertInvariants(run);
      }
    }
  }
});

test("local storage roundtrip validates version and corruption and works with storage unavailable", () => {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  const data = new Map<string, string>();
  try {
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => data.get(key) ?? null,
        setItem: (key: string, value: string) => data.set(key, value),
      },
    });
    const state = createInitialState();
    saveState(state);
    assert.deepEqual(loadState(), state);
    for (const corrupt of [
      "{",
      JSON.stringify({ ...state, version: 2 }),
      JSON.stringify({ version: 1, workflows: [{}] }),
      JSON.stringify({
        version: 1,
        workflows: [state.workflows[0], state.workflows[0]],
      }),
    ]) {
      data.set(STORAGE_KEY, corrupt);
      const recovered = loadState();
      assert.equal(recovered.version, 1);
      assert.equal(recovered.workflows.length, 8);
      recovered.workflows.forEach(assertInvariants);
    }
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      get() {
        throw new Error("Storage denied");
      },
    });
    assert.doesNotThrow(() => saveState(state));
    assert.equal(loadState().workflows.length, 8);
    Reflect.deleteProperty(globalThis, "localStorage");
    assert.equal(loadState().workflows.length, 8);
  } finally {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
});

test("mock gateway supports injection, immutable snapshots, subscriptions, and explicit clock", async () => {
  let saved: AppState | undefined;
  let notificationCount = 0;
  const gateway = createMockGateway({
    load: createInitialState,
    save: (state) => {
      saved = state;
    },
  });
  assert.equal(gateway.mode, "mock");
  assert.equal(createGateway(gateway), gateway);
  const remove = gateway.subscribe(() => {
    notificationCount += 1;
  });
  const workflow = await gateway.create({
    name: "Gateway QA",
    machine: "M7",
    qubits: 1,
    target: "Q0",
    type: "fixed",
    policy: "autopilot",
    scenario: "normal",
  } as CreateWorkflowInput);
  await gateway.act(workflow.id, "start");
  gateway.advance(100);
  assert.equal((await gateway.get(workflow.id)).status, "completed");
  assert.equal(saved?.workflows[0].status, "completed");
  assert.equal(notificationCount, 3);
  const snapshot = gateway.snapshot();
  snapshot.workflows.length = 0;
  assert.equal((await gateway.list()).length, 9);
  remove();
  await gateway.policy("wf-m7-coherence", "strict");
  assert.equal(notificationCount, 3);
  await assert.rejects(gateway.get("missing"), /未找到工作流/);
});

test("the 200-workflow persistence boundary rejects another create without losing existing state", async () => {
  const initial: AppState = {
    version: 1,
    workflows: Array.from({ length: MAX_WORKFLOWS - 1 }, () => fresh()),
  };
  let writes = 0;
  const gateway = createMockGateway({
    load: () => initial,
    save: () => {
      writes += 1;
    },
  });
  const input: CreateWorkflowInput = {
    experiment: "single",
    name: "Boundary",
    machine: "M7",
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: "threshold",
    scenario: "normal",
  };
  await gateway.create(input);
  assert.equal((await gateway.list()).length, MAX_WORKFLOWS);
  const snapshot = gateway.snapshot();
  await assert.rejects(gateway.create(input), /最多保存 200 个任务/);
  assert.deepEqual(gateway.snapshot(), snapshot);
  assert.equal(writes, 1);
  const previous = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  try {
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: { getItem: () => JSON.stringify(snapshot) },
    });
    assert.deepEqual(loadState(), snapshot);
  } finally {
    if (previous) Object.defineProperty(globalThis, "localStorage", previous);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
});

test("mock reset restores and publishes seed state through the same gateway instance", async () => {
  let saved: AppState | undefined;
  let received: AppState | undefined;
  const gateway = createMockGateway({
    load: () => ({ version: 1, workflows: [fresh()] }),
    save: (state) => {
      saved = state;
    },
  });
  const unsubscribe = gateway.subscribe((state) => {
    received = state;
  });
  assert.equal((await gateway.list()).length, 1);
  gateway.resetDemo();
  const snapshot = gateway.snapshot();
  assert.equal(snapshot.workflows.length, 8);
  assert.equal(snapshot.workflows[0].id, "wf-m7-bringup");
  assert.deepEqual(saved, snapshot);
  assert.deepEqual(received, snapshot);
  unsubscribe();
});
