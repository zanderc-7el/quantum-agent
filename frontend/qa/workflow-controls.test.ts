import test from "node:test";
import assert from "node:assert/strict";
import type { Workflow } from "../src/types.ts";
import { precedingDependencies, workflowControls } from "../src/lib/workflowControls.ts";
import { numericParameterBounds, validateParameterChanges } from "../src/lib/parameters.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";

const fixture = (status: Workflow["status"], engineState = status): Workflow => ({ id: "run", revision: 4, mode: "real", name: "Q11", experiment: "single", machine: "八号机", qubits: 1, target: "Q11", type: "adaptive", status, engineState, progress: 0, owner: "操作员", createdAt: "2026-11-18T00:00:00Z", policy: "autopilot", scenario: "normal", elapsed: 0, iterations: 0, events: [], approved: false, maxAttemptsPerNode: 3, currentNodeId: "n1", nodes: [{ id: "n1", name: "S21", label: "找腔", status: "attention", engineState, progress: 0, attempt: 1, qubits: "Q11", detail: "", dependsOn: [] }] });

test("queued and terminal controls cannot issue invalid start or retry commands", () => {
  const queued = workflowControls(fixture("queued"));
  assert.equal(queued.start, false); assert.equal(queued.pause, true); assert.equal(queued.policy, false); assert.equal(queued.edit, false);
  for (const state of ["completed", "failed", "aborted"] as const) {
    const controls = workflowControls(fixture(state));
    assert.equal(controls.retry, false); assert.equal(controls.backtrack, false); assert.equal(controls.edit, false); assert.equal(controls.terminal, true);
  }
  assert.equal(workflowControls(fixture("running")).policy, false);
  assert.equal(workflowControls(fixture("draft")).edit, true);
});

test("attention actions follow quality, model waits, attempt budgets and quarantined receipts", () => {
  const workflow = fixture("attention", "quality_attention");
  assert.equal(workflowControls(workflow).approve, false);
  assert.equal(workflowControls(workflow).retry, true);
  assert.equal(workflowControls(workflow).decide, false);
  workflow.nodes[0].result = { dataKind: "real", metrics: {}, artifacts: [], quality: { status: "passed", reasons: [] } };
  assert.equal(workflowControls(workflow).decide, true);
  assert.equal(workflowControls(workflow).approve, true);
  workflow.nodes[0].attempt = 3;
  assert.equal(workflowControls(workflow).retry, false);
  for (const state of ["needs_reconciliation", "unknown"]) {
    workflow.engineState = state;
    const controls = workflowControls(workflow);
    assert.equal(controls.reconciliation, true); assert.equal(controls.approve, false); assert.equal(controls.edit, false); assert.equal(controls.retry, false);
  }
  workflow.engineState = "waiting_model";
  assert.equal(workflowControls(workflow).decide, true); assert.equal(workflowControls(workflow).retry, false);
});

test("backtrack targets are true DAG ancestors, excluding parallel siblings", () => {
  const workflow = fixture("paused");
  workflow.nodes.push({ ...workflow.nodes[0], id: "sibling", dependsOn: ["n1"] }, { ...workflow.nodes[0], id: "tail", dependsOn: ["n1"] });
  assert.deepEqual(precedingDependencies(workflow, workflow.nodes[2]).map(node => node.id), ["n1"]);
  workflow.currentNodeId = undefined; workflow.nodes.forEach(node => { node.status = "pending"; }); workflow.status = "attention"; workflow.engineState = "preflight_failed";
  assert.equal(workflowControls(workflow).recheck, true); assert.equal(workflowControls(workflow).retry, false);
});

test("legacy evidence rejection can ask for a new decision without treating hardware errors as model failures", () => {
  const workflow = fixture("attention", "execution_failed");
  workflow.nodes[0].result = { dataKind: "real", metrics: {}, artifacts: [], quality: { status: "failed", reasons: [] } };
  workflow.nodes[0].detail = "决策引用了当前节点之外的证据";
  assert.equal(workflowControls(workflow).decide, true);
  workflow.nodes[0].detail = "设备采集超时";
  assert.equal(workflowControls(workflow).decide, false);
  workflow.nodes[0].detail = "决策引用了当前节点之外的证据";
  workflow.nodes[0].result.stale = true;
  assert.equal(workflowControls(workflow).decide, false);
});

test("checkpoint budget updates expose continue without duplicate retry or acceptance actions", () => {
  const workflow = fixture("attention", "budget_updated");
  workflow.nodes[0].result = { dataKind: "real", metrics: {}, artifacts: [], quality: { status: "passed", reasons: [] } };
  const controls = workflowControls(workflow);
  assert.equal(controls.resume, true);
  assert.equal(controls.retry, false);
  assert.equal(controls.approve, false);
  workflow.nodes[0].result.quality.status = "failed";
  assert.equal(workflowControls(workflow).resume, false);
  assert.equal(workflowControls(workflow).decide, true);
  workflow.nodes[0].engineState = "needs_reconciliation";
  assert.equal(workflowControls(workflow).resume, false);
  workflow.nodes[0].engineState = "waiting_hardware";
  assert.equal(workflowControls(workflow).resume, false);
});

test("parameter changes reject unknown, blank, nonfinite, fractional integer and out-of-scope values", () => {
  const bounds = { hz: { min: 4e9, max: 6e9 }, points: { min: 3, max: 101, integer: true }, state: { min: 0, max: 2, enum: [0, 2] } };
  assert.deepEqual(validateParameterChanges({ hz: "4.8e9", points: "81" }, bounds), { hz: 4.8e9, points: 81 });
  for (const value of ["NaN", "Infinity", "", " ", "8e9"]) assert.throws(() => validateParameterChanges({ hz: value }, bounds));
  assert.throws(() => validateParameterChanges({ typo: "1" }, bounds));
  assert.throws(() => validateParameterChanges({ points: "3.5" }, bounds));
  assert.throws(() => validateParameterChanges({ state: "1" }, bounds));
  assert.throws(() => validateParameterChanges({ hz: "4.8e9" }, bounds, ["points"]));
  assert.throws(() => validateParameterChanges({}, bounds));
  assert.deepEqual(Object.keys(numericParameterBounds({ ...bounds, broken: { min: NaN, max: 5 } })), ["hz", "points", "state"]);
});

test("parameter save binds selected node and edit revision, refreshes token with same operation, never starts acquisition", async () => {
  const writes: { url: string; init: RequestInit }[] = []; let sessions = 0;
  const reply = (data: unknown, status = 200) => new Response(JSON.stringify(data), { status, headers: { "Content-Type": "application/json" } });
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (String(url).endsWith("/session")) return reply({ token: `session-${++sessions}` });
    writes.push({ url: String(url), init: init! });
    return writes.length === 1 ? reply({ error: { message: "expired" } }, 401) : reply(fixture("paused"));
  }) as typeof fetch });
  await gateway.parameters("run", "selected:Q11", 3, { hz: 4.8e9 });
  assert.equal(writes.length, 2); assert.equal(writes[0].url, "/api/workflows/run/nodes/selected%3AQ11/parameters");
  assert.equal(writes[0].init.method, "PATCH"); assert.equal(writes[0].init.body, writes[1].init.body);
  const body = JSON.parse(writes[0].init.body as string);
  assert.equal(body.expectedRevision, 3); assert.deepEqual(body.parameters, { hz: 4.8e9 }); assert.ok(body.operationId);
  assert.equal(writes.some(write => write.url.endsWith("/actions")), false);
});
