import test from "node:test";
import assert from "node:assert/strict";
import { allowsBlueprintIntent, BLUEPRINTS, BLUEPRINT_STAGES, requestedBlueprint } from "../src/lib/blueprints.ts";
import { createWorkflow } from "../src/lib/simulator.ts";

test("single-qubit blueprint contains only single-qubit calibration nodes", () => {
  const workflow = createWorkflow({ experiment: "single", name: "单比特校准蓝图", machine: "M7", qubits: 1,
    target: "Q12", type: "adaptive", policy: "threshold", scenario: "normal" });
  assert.equal(workflow.nodes.length, BLUEPRINTS.single.nodeCount);
  assert.equal(workflow.nodes.some(node => /CZ|XEB|Coupler/.test(node.id)), false);
  assert.equal(workflow.nodes[0].id, "ReadoutFreqDip");
  assert.equal(workflow.nodes.at(-1)?.id, "RB1QCal");
});

test("blueprint intent gate rejects cross-blueprint plan requests", () => {
  assert.equal(requestedBlueprint("生成双比特 CZ 校准计划"), "two");
  assert.equal(requestedBlueprint("生成单比特校准计划"), "single");
  assert.equal(allowsBlueprintIntent("single", "生成双比特 CZ 校准计划"), false);
  assert.equal(allowsBlueprintIntent("single", "运行 T1Idle"), true);
  assert.equal(allowsBlueprintIntent("two", "生成单比特校准计划"), false);
});

test("stage progress definitions cover each workflow node exactly once", () => {
  for (const experiment of ["single", "two"] as const) {
    const workflow = createWorkflow({ experiment, name: "阶段校验", machine: "M7", qubits: experiment === "single" ? 1 : 2,
      target: experiment === "single" ? "Q12" : "Q12–Q13", type: "fixed", policy: "threshold", scenario: "normal" });
    const stageIds = BLUEPRINT_STAGES[experiment].flatMap(stage => [...stage.nodes]);
    assert.equal(new Set(stageIds).size, stageIds.length);
    assert.deepEqual(new Set(stageIds), new Set(workflow.nodes.map(node => node.id)));
  }
});
