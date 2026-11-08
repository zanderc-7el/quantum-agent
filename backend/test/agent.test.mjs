import test from "node:test";
import assert from "node:assert/strict";
import { previewAgentGoal, reviewAgentProposal, assertAgentToolAllowed, AGENT_TOOLS } from "../src/agent.mjs";
import { PROFILE } from "../src/core.mjs";

test("agent preview selects a reviewed recipe without executing equipment", () => {
  const one = previewAgentGoal("检查 Q12 单比特读出校准", "single", 1);
  assert.equal(one.recipeId, "single-calibration");
  assert.equal(one.nodeCount, 9);
  assert.equal(one.executable, false);
  assert.equal(one.modelUsed, false);
  const two = previewAgentGoal("对 Q12 和 Q13 做 CZ 校准", "two", 1);
  assert.equal(two.recipeId, "two-cz-calibration");
  assert.equal(two.nodeCount, 26);
  assert.deepEqual(two.firstBatch, ["Q12:ReadoutFreqDip"]);
});

test("unknown targets and direct control tools are denied", () => {
  assert.equal(previewAgentGoal("校准 Q99 的读出", "single", 1).reason, "UNKNOWN_TARGET");
  assert.equal(previewAgentGoal("校准 Q13 的读出", "single", 1).reason, "SINGLE_Q13_NOT_IMPLEMENTED");
  assert.equal(previewAgentGoal("立即发布 Q12 参数", "single", 1).reason, "CONTROL_ACTION_REQUIRES_SEPARATE_AUTHORIZATION");
  assert.equal(previewAgentGoal("对 Q12 和 Q13 做 CZ 校准", "single", 1).reason, "BLUEPRINT_SCOPE_DENIED");
  assert.equal(assertAgentToolAllowed("get_run_evidence", "read"), true);
  assert.equal(AGENT_TOOLS.some(item => item.name === "publish_parameter"), false);
  assert.throws(() => assertAgentToolAllowed("device_execute", "write"), /AGENT_TOOL_DENIED/);
});

test("untrusted planner proposals are bounded by blueprint and resource budgets", () => {
  const request = { goal: "校准 Q12 的读出频率", blueprintId: "single", blueprintVersion: 1,
    plan: { blueprintId: "single", blueprintVersion: 1, targetQubitId: "Q12", deviceId: PROFILE.id,
      recipeId: "readout_resonator_scan", recipeVersion: 1, motivation: "验证 Q12 读出谐振频率",
      parameters: { centerHz: PROFILE.centerHz, spanHz: 40_000_000, points: 41 } },
    budget: { maxAttempts: 2, maxDurationSeconds: 120 } };
  const accepted = reviewAgentProposal(request);
  assert.equal(accepted.accepted, true);
  assert.equal(accepted.executable, false);
  assert.equal(accepted.modelUsed, false);
  assert.equal(reviewAgentProposal({ ...request, plan: { ...request.plan, blueprintId: "two" } }).reason, "BLUEPRINT_SCOPE_DENIED");
  assert.equal(reviewAgentProposal({ ...request, budget: { maxAttempts: 100, maxDurationSeconds: 120 } }).reason, "BUDGET_EXCEEDED");
  assert.equal(reviewAgentProposal({ ...request, plan: { ...request.plan, deviceCommand: "pulse" } }).reason, "INVALID_INPUT");
  assert.equal(reviewAgentProposal({ ...request, goal: "立即发布 Q12 参数" }).reason,
    "CONTROL_ACTION_REQUIRES_SEPARATE_AUTHORIZATION");
});
