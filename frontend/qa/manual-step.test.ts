import test from "node:test";
import assert from "node:assert/strict";
import type { Workflow, ExperimentNode, MeasurementResult } from "../src/types.ts";
import * as controls from "../src/lib/workflowControls.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import { createElement, type ReactElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

const result = (): MeasurementResult => ({ dataKind: "real", metrics: {}, artifacts: [], quality: { status: "passed", reasons: [] } });
const node = (id: string, dependsOn: string[] = []): ExperimentNode => ({ id, name: id, label: id === "n1" ? "找腔" : "找比特", status: "pending", progress: 0, attempt: 0, qubits: "Q11", detail: "", dependsOn, capability: "available" });
const fixture = (): Workflow => ({ id: "manual", revision: 4, mode: "real", policy: "strict", name: "人工任务", experiment: "single", machine: "八号机", qubits: 1, target: "Q11", type: "adaptive", status: "draft", engineState: "draft", progress: 0, owner: "操作员", createdAt: "2026-11-20", scenario: "normal", elapsed: 0, iterations: 0, events: [], approved: false, maxAttemptsPerNode: 3, nodes: [node("n1"), node("n2", ["n1"]), node("sibling")], currentNodeId: "n1" });

test('parameter invalidation distinguishes unmeasured nodes from actual stale measurements', () => {
  const workflow = fixture();
  workflow.nodes.forEach(item => { item.engineState = 'stale'; });
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[0]).label, '待执行');
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[1]).label, '等待前置');
  workflow.nodes[0].attempt = 1;
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[0]).label, '待复测');
  workflow.nodes[1].result = { ...result(), stale: true };
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[1]).label, '待复测');
});

test("manual workflow removes automatic start/resume/retry but preserves replay and intelligent controls", () => {
  const workflow = fixture();
  assert.equal(controls.workflowControls(workflow).start, false);
  workflow.status = "paused";
  assert.equal(controls.workflowControls(workflow).resume, false);
  workflow.status = "attention"; workflow.engineState = "quality_attention";
  assert.equal(controls.workflowControls(workflow).retry, false);
  workflow.mode = "replay"; workflow.status = "draft";
  assert.equal(controls.workflowControls(workflow).start, true);
  workflow.mode = "real"; workflow.policy = "autopilot";
  assert.equal(controls.workflowControls(workflow).start, true);
});

test("selected step readiness names missing prerequisites and accepts only fresh verified predecessors", () => {
  const workflow = fixture();
  assert.equal(typeof controls.manualStepControls, "function");
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[0]).run, true);
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[1]).run, false);
  assert.match(controls.manualStepControls(workflow, workflow.nodes[1]).reason, /找腔/);
  workflow.nodes[0] = { ...workflow.nodes[0], status: "success", engineState: "verified", result: result() };
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[1]).run, true);
  workflow.nodes[0].result!.stale = true;
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[1]).run, false);
  workflow.nodes[0].result = { ...result(), dataKind: "historical" };
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[1]).run, false);
});

test("manual acceptance cannot certify failed, partial, pending verification, stale or unselected results", () => {
  const workflow = fixture(); workflow.status = "attention"; workflow.engineState = "awaiting_acceptance";
  const selected = workflow.nodes[0]; Object.assign(selected, { status: "attention", engineState: "awaiting_acceptance", attempt: 1, latestAttemptId: "a1", result: result() });
  assert.equal(typeof controls.manualStepControls, "function");
  assert.equal(controls.manualStepControls(workflow, selected).approve, true);
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[2]).approve, false);
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[2]).run, false);
  assert.equal(controls.workflowControls(workflow).policy, false);
  assert.equal(controls.workflowControls(workflow).edit, false);
  for (const patch of [{ stale: true }, { partial: true }, { verificationPending: true }, { dataKind: "historical" }, { quality: { status: "failed", reasons: [] } }, { quality: { status: "unverified", reasons: [] } }]) {
    selected.result = { ...result(), ...patch } as MeasurementResult;
    assert.equal(controls.manualStepControls(workflow, selected).approve, false);
    assert.equal(controls.workflowControls(workflow).approve, false);
  }
});

test("paused and legacy awaiting results remain confirmable without being labeled already accepted", () => {
  const workflow = fixture(); workflow.status = "paused"; workflow.engineState = "awaiting_acceptance";
  const selected = workflow.nodes[0]; Object.assign(selected, { status: "attention", engineState: "awaiting_acceptance", result: result() });
  assert.equal(controls.manualStepControls(workflow, selected).approve, true);
  assert.equal(controls.workflowControls(workflow).approve, true);
  selected.engineState = "verified"; selected.status = "success";
  assert.equal(controls.manualStepControls(workflow, selected).approve, true);
  assert.equal(controls.manualStepControls(workflow, selected).label, "待确认结果");
  assert.equal(controls.workflowControls(workflow).edit, false);
});

test("unknown capability and another node's quarantined receipt cannot enable manual acquisition", () => {
  const workflow = fixture();
  for (const capability of [undefined, "unavailable", "unsupported", "historical_only"] as const) {
    workflow.nodes[2].capability = capability;
    assert.equal(controls.manualStepControls(workflow, workflow.nodes[2]).run, false);
  }
  workflow.nodes[2].capability = "available";
  workflow.nodes[0].engineState = "needs_reconciliation";
  assert.equal(controls.manualStepControls(workflow, workflow.nodes[2]).run, false);
});

const bundle = buildSync({ entryPoints: [fileURLToPath(new URL("../src/components/ManualStepPanel.tsx", import.meta.url))], bundle: true, platform: "node", format: "cjs", write: false, external: ["react", "react-dom/server"], loader: { ".css": "empty" } }).outputFiles[0].text;
const module = { exports: {} as Record<string, any> };
new Function("require", "module", "exports", bundle)(createRequire(import.meta.url), module, module.exports);
const ManualStepPanel = module.exports.default;
const ExecutionModeSelector = module.exports.ExecutionModeSelector;
function buttons(element: ReactElement): any[] {
  const children = (element.props as { children?: unknown }).children;
  return [ ...(element.type === "button" ? [element.props] : []), ...[children].flat(Infinity).filter((value: any) => value && typeof value === "object" && "props" in value).flatMap((child: any) => buttons(child)) ];
}
const buttonText = (button: any) => renderToStaticMarkup(createElement("span", null, button.children)).replace(/<[^>]+>/g, "");

test("actual manual panel targets selection, rejects dirty launch and offers discard without approval", async () => {
  const workflow = fixture(); const selected = workflow.nodes[2];
  const sent: unknown[][] = []; const selectedIds: string[] = [];
  const props = { workflow, node: selected, busy: false, parameterDirty: false, onAction: async (...args: unknown[]) => { sent.push(args); }, onSelect: (id: string) => selectedIds.push(id) };
  let tree = ManualStepPanel(props);
  const start = buttons(tree).find(item => buttonText(item) === "开始本步");
  assert.equal(start.disabled, false); await start.onClick();
  assert.deepEqual(sent, [["run_step", "sibling"]]);
  buttons(tree).find(item => buttonText(item).includes("1找腔")).onClick();
  assert.deepEqual(selectedIds, ["n1"]);
  tree = ManualStepPanel({ ...props, parameterDirty: true });
  assert.equal(buttons(tree).find(item => buttonText(item) === "开始本步").disabled, true);
  assert.match(renderToStaticMarkup(tree), /请先保存或取消参数修改/);
  workflow.status = "attention"; workflow.engineState = "awaiting_acceptance"; workflow.currentNodeId = selected.id;
  Object.assign(selected, { status: "attention", engineState: "awaiting_acceptance", result: result(), attempt: 1 });
  tree = ManualStepPanel(props);
  const adjust = buttons(tree).find(item => buttonText(item) === "重新调整");
  assert.ok(adjust, "passed result can be discarded instead of forced acceptance");
  await adjust.onClick(); assert.deepEqual(sent[1], ["backtrack", "sibling"]);
  await buttons(tree).find(item => buttonText(item) === "确认结果").onClick();
  assert.deepEqual(sent[2], ["approve", "sibling"]);
  assert.equal(buttons(tree).some(item => ["开始本步", "重测本步"].includes(buttonText(item))), false);
});

test("actual mode controls preserve policy semantics and replay never offers manual execution", () => {
  const policies: string[] = [];
  const render = (policy: Workflow["policy"], disabled = false) => ExecutionModeSelector({ policy, disabled, onChange: (next: string) => policies.push(next) });
  buttons(render("autopilot")).find(item => buttonText(item) === "人工逐步").onClick();
  buttons(render("strict")).find(item => buttonText(item) === "智能运行").onClick();
  assert.deepEqual(policies, ["strict", "autopilot"]);
  assert.ok(buttons(render("strict", true)).every(item => item.disabled));
  assert.match(renderToStaticMarkup(render("threshold")), /异常时决策/);
  const workflow = fixture(); workflow.mode = "replay";
  assert.equal(ManualStepPanel({ workflow, node: workflow.nodes[0] }), null);
  assert.equal(controls.workflowPolicyLabel(workflow), "历史回放");
  workflow.mode = "real";
  assert.equal(controls.workflowPolicyLabel(workflow), "人工逐步");
  workflow.policy = "threshold"; assert.equal(controls.workflowPolicyLabel(workflow), "异常时决策");
  workflow.policy = "autopilot"; assert.equal(controls.workflowPolicyLabel(workflow), "智能运行");
});

test("manual eligibility respects busy states, attempt budget, unavailable methods and completed nodes", () => {
  const workflow = fixture(); const selected = workflow.nodes[0];
  assert.equal(typeof controls.manualStepControls, "function");
  for (const state of ["needs_reconciliation", "unknown", "waiting_hardware", "budget_exhausted", "deciding", "verifying", "acquiring", "controller_error", "resource_busy"]) {
    workflow.engineState = state;
    assert.equal(controls.manualStepControls(workflow, selected).run, false, state);
  }
  workflow.engineState = "manual_ready"; workflow.status = "paused";
  assert.equal(controls.manualStepControls(workflow, selected).run, true);
  selected.attempt = 3;
  assert.equal(controls.manualStepControls(workflow, selected).run, false);
  selected.attempt = 0; selected.capability = "unavailable";
  assert.equal(controls.manualStepControls(workflow, selected).run, false);
  selected.capability = "available"; selected.status = "success"; selected.engineState = "verified"; selected.result = result();
  assert.equal(controls.manualStepControls(workflow, selected).run, false);
  workflow.status = "running";
  assert.equal(controls.workflowControls(workflow).policy, false);
});

test("run_step routes selected node and attempt while backtrack retains current source and explicit target", async () => {
  const workflow = fixture(); workflow.nodes[2].latestAttemptId = "selected-attempt";
  const payloads: Record<string, unknown>[] = [];
  const response = (value: unknown) => new Response(JSON.stringify(value), { headers: { "Content-Type": "application/json" } });
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (String(url).endsWith("/session")) return response({ token: "test-only" });
    if (String(url).endsWith("/actions")) payloads.push(JSON.parse(init!.body as string));
    return response(workflow);
  }) as typeof fetch });
  await gateway.command(workflow.id, "run_step", "sibling");
  assert.equal(payloads[0].nodeId, "sibling"); assert.equal(payloads[0].attemptId, "selected-attempt"); assert.equal(payloads[0].targetNodeId, undefined);
  assert.equal(payloads[0].expectedRevision, 4); assert.ok(payloads[0].operationId);
  await gateway.command(workflow.id, "backtrack", "sibling");
  assert.equal(payloads[1].nodeId, "n1"); assert.equal(payloads[1].targetNodeId, "sibling");
  await assert.rejects(gateway.command(workflow.id, "run_step", "missing"));
  await assert.rejects(gateway.command(workflow.id, "run_step"));
  assert.equal(payloads.length, 2);
});
