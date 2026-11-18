import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { ExperimentNode, Workflow } from "../src/types.ts";
import { abortedNodePresentation } from "../src/lib/abortedNodePresentation.ts";
import { workflowControls } from "../src/lib/workflowControls.ts";

// Synthetic legacy receipt: selection succeeded, independent verification failed,
// and abort left the stored node in deciding. This is not a measurement result.
const nodeFixture = (): ExperimentNode => ({
  id: "power:Q11", name: "S21_Power", label: "读出功率扫描", qubits: "Q11",
  status: "attention", engineState: "deciding", attempt: 4, progress: 100,
  detail: "智能体正在根据实测证据选择下一步",
  telemetry: { completedRows: 1220, totalRows: 1220 },
  result: { dataKind: "real", metrics: { frequency_spread_hz: 200000 }, artifacts: [],
    quality: { status: "failed", reasons: ["独立复测的频率变化超过阈值"] },
    selectionResult: { dataKind: "real", metrics: { snr: 12.77 }, artifacts: [],
      quality: { status: "passed", reasons: [] } } },
});
const workflowFixture = (node = nodeFixture()): Workflow => ({
  id: "archived", revision: 19, mode: "real", name: "Q11", experiment: "single",
  machine: "八号机", qubits: 1, target: "Q11", type: "adaptive", status: "aborted",
  engineState: "aborted", progress: 20, owner: "操作员", createdAt: "2026-11-18T00:00:00Z",
  policy: "autopilot", scenario: "normal", elapsed: 120, iterations: 4, events: [],
  approved: false, maxAttemptsPerNode: 8, currentNodeId: node.id, nodes: [node],
});

function freezeDeep(value: unknown): void {
  if (value && typeof value === "object") {
    for (const child of Object.values(value)) freezeDeep(child);
    Object.freeze(value);
  }
}

test("legacy aborted deciding presentation ends the decision without changing either acquisition's quality", () => {
  const workflow = workflowFixture();
  const before = JSON.stringify(workflow);
  freezeDeep(workflow);
  const display = abortedNodePresentation(workflow, workflow.nodes[0]);
  assert.equal(display?.label, "已终止");
  assert.equal(display?.status, "abandoned");
  assert.equal(display?.engineState, "aborted");
  assert.match(display!.detail, /本轮决策结果不再执行/);
  assert.doesNotMatch(display!.detail, /正在|请求已取消|模型已停止/);
  assert.equal(JSON.stringify(workflow), before);
  assert.equal(workflow.nodes[0].result?.quality.status, "failed");
  assert.equal(workflow.nodes[0].result?.selectionResult?.quality.status, "passed");
});

test("aborted unstarted nodes say unexecuted; active acquisition snapshots say last record", () => {
  const node = nodeFixture();
  for (const state of ["pending", "stale"]) {
    const pending = { ...node, status: "pending" as const, engineState: state, attempt: 0, result: undefined };
    const display = abortedNodePresentation({ status: "aborted" }, pending);
    assert.equal(display?.label, "未执行");
    assert.match(display!.detail, /此节点未执行/);
  }
  for (const engineState of ["acquiring", "verifying", "queued", "retry_queued", "running", "pausing_at_checkpoint", "stopping_at_checkpoint"]) {
    const display = abortedNodePresentation({ status: "aborted" }, { ...node, status: "running", engineState });
    assert.match(display!.detail, /最后采集记录/);
    assert.doesNotMatch(display!.detail, /设备已释放|硬件已停止/);
  }
});

test("terminal overlay retains quality failure and reconciliation explanations", () => {
  for (const engineState of ["quality_attention", "needs_reconciliation", "unknown"]) {
    const node = { ...nodeFixture(), engineState, detail: "原始诊断与待核查证据" };
    assert.match(abortedNodePresentation({ status: "aborted" }, node)!.detail, /原始诊断与待核查证据/);
  }
});

test("verified and historical nodes retain their accepted history in aborted tasks", () => {
  for (const node of [
    { ...nodeFixture(), status: "success" as const, engineState: "verified" },
    { ...nodeFixture(), engineState: "historical" },
  ]) assert.equal(abortedNodePresentation({ status: "aborted" }, node), undefined);
});

test("no presentation overlay applies to any non-aborted workflow", () => {
  for (const status of ["draft", "queued", "running", "attention", "paused", "completed", "failed"] as const) {
    for (const engineState of ["deciding", "waiting_model", "acquiring", "quality_attention"]) {
      assert.equal(abortedNodePresentation({ status }, { ...nodeFixture(), engineState }), undefined);
    }
  }
});

test("terminal controls remain disabled with a stale deciding node", () => {
  const controls = workflowControls(workflowFixture());
  assert.equal(controls.terminal, true);
  for (const key of ["start", "pause", "resume", "recheck", "decide", "approve", "retry", "policy", "backtrack", "edit"] as const) {
    assert.equal(controls[key], false, key);
  }
});

// Render the actual TSX components without a browser, API calls, timers, or effects.
// External React is shared with the renderer; CSS contributes no server markup.
const compiled = buildSync({
  entryPoints: [fileURLToPath(new URL("../src/components/RemotePanels.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external", loader: { ".css": "empty" },
}).outputFiles[0].text;
const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
new Function("require", "module", "exports", compiled)(createRequire(import.meta.url), module, module.exports);
const { RealNodeInspector, RealFlowCanvas, MeasurementProgress, RealResult } = module.exports;

test("actual inspector and canvas render aborted state while keeping failed verification and selection visible", () => {
  const workflow = workflowFixture();
  const before = JSON.stringify(workflow);
  const node = workflow.nodes[0];
  const inspector = renderToStaticMarkup(createElement(RealNodeInspector, {
    workflow, node, gateway: {}, onAction: async () => {}, onPolicy: async () => {}, busy: false, onBusy: () => {},
  }));
  assert.match(inspector, /本轮决策结果不再执行/);
  assert.match(inspector, /已终止/);
  assert.match(inspector, /最后采集记录/);
  assert.doesNotMatch(inspector, /智能体正在根据实测证据|>deciding</);
  const canvas = renderToStaticMarkup(createElement(RealFlowCanvas, { workflow, selectedNodeId: node.id, onSelectNode: () => {} }));
  assert.match(canvas, /rp-flow-node abandoned selected/);
  assert.doesNotMatch(canvas, /rp-flow-progress|fc-edge--active/);
  const result = renderToStaticMarkup(createElement(RealResult, { result: node.result }));
  assert.match(result, /首次采集/);
  assert.match(result, /独立复测/);
  assert.match(result, /质量检查通过/);
  assert.match(result, /质量检查未通过/);
  assert.match(result, /独立复测的频率变化超过阈值/);
  assert.equal(JSON.stringify(workflow), before);
});

test("actual active inspector and progress retain original deciding and acquisition displays", () => {
  const workflow = { ...workflowFixture(), status: "running" as const, engineState: "deciding" };
  const node = workflow.nodes[0];
  const inspector = renderToStaticMarkup(createElement(RealNodeInspector, {
    workflow, node, gateway: {}, onAction: async () => {}, onPolicy: async () => {}, busy: false, onBusy: () => {},
  }));
  assert.match(inspector, /智能体正在根据实测证据选择下一步/);
  assert.match(inspector, />deciding</);
  assert.doesNotMatch(inspector, /本轮决策结果不再执行|最后采集记录/);
  const progress = renderToStaticMarkup(createElement(MeasurementProgress, { workflow, node }));
  assert.match(progress, /当前实际采集进度/);
  assert.match(progress, /1220/);
});
