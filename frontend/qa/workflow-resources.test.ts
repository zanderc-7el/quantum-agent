import test from "node:test";
import assert from "node:assert/strict";
import type { Catalog, ExperimentNode, NodeDetail, Workflow } from "../src/types.ts";
import { adapterIdentity, displayedNodeParameters, pulseStageLabel, selectableResources, workflowBudget } from "../src/lib/workflowResources.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";

const catalog: Catalog = { blueprint: { version: "test", nodes: [], edges: [] }, resources: [{ id: "Q11", label: "Q11" }, { id: "Q18", label: "Q18" }], rules: [], capabilities: { id: "arclight-native-cy8", resources: ["Q11"] } };

test("real resources use exact adapter-supported IDs while replay retains historical resources", () => {
  assert.deepEqual(selectableResources(catalog, "real").map(resource => resource.id), ["Q11"]);
  assert.deepEqual(selectableResources(catalog, "replay").map(resource => resource.id), ["Q11", "Q18"]);
  assert.deepEqual(selectableResources({ ...catalog, capabilities: {} }, "real"), []);
  assert.deepEqual(selectableResources({ ...catalog, capabilities: { resources: ["Q011", "Q19"] } }, "real"), []);
});

test("budget reflects backend values, preserves zero and does not invent missing totals", () => {
  const workflow = { maxTotalShots: 30000000, reservedShots: 20696000, iterations: 13, maxWorkflowAttempts: 40, maxAttemptsPerNode: 3 } as Workflow;
  assert.deepEqual(workflowBudget(workflow), { limit: 30000000, reserved: 20696000, remaining: 9304000, attempts: 13, attemptLimit: 40, nodeLimit: 3 });
  assert.equal(workflowBudget({ ...workflow, maxTotalShots: 8000000 }).limit, 8000000);
  assert.equal(workflowBudget({ ...workflow, reservedShots: 0 }).reserved, 0);
  assert.equal(workflowBudget({} as Workflow).limit, undefined);
});

test("executor identity comes from backend metadata and preserves bound historical adapter IDs", () => {
  assert.deepEqual(adapterIdentity(catalog), { id: "arclight-native-cy8" });
  assert.deepEqual(adapterIdentity(catalog, { mode: "real", adapterId: "f8-native-cy8" } as Workflow), { id: "f8-native-cy8" });
  assert.deepEqual(adapterIdentity({ ...catalog, adapter: { id: "arclight-native-cy8", name: "ArcLight" } }), { id: "arclight-native-cy8", name: "ArcLight" });
});

test("replay stage never attributes old F8 evidence to the current ArcLight adapter", () => {
  const workflow = { mode: "replay", adapterId: "arclight-current" } as Workflow;
  assert.equal(adapterIdentity(catalog, workflow), undefined);
  assert.equal(pulseStageLabel(catalog, workflow), "历史实测回放");
  const real = { mode: "real", adapterId: "bound-adapter", adapter: { id: "other-adapter", name: "Wrong" } } as Workflow;
  assert.deepEqual(adapterIdentity(catalog, real), { id: "bound-adapter" });
  assert.equal(pulseStageLabel(catalog, real), "bound-adapter 脉冲测量");
});

test("historical nested measurement parameters survive empty live detail parameters", () => {
  const historical = { ParaSpace: { setting: { Power: -15, drivQubit: "Q11" } } };
  const node = { parameters: historical } as unknown as ExperimentNode;
  const detail = { parameters: {} } as NodeDetail;
  assert.deepEqual(displayedNodeParameters({ mode: "replay" } as Workflow, node, detail), historical);
  node.result = { parameters: { native: "F8", points: 81 } } as ExperimentNode["result"];
  assert.deepEqual(displayedNodeParameters({ mode: "replay" } as Workflow, node, detail), { native: "F8", points: 81 });
  assert.deepEqual(displayedNodeParameters({ mode: "real" } as Workflow, node, detail), {});
});

test("new workflow requests omit budget fields and retain the controller default", async () => {
  let payload: Record<string, unknown> = {};
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    const session = String(url).endsWith("/session");
    if (!session) payload = JSON.parse(init?.body as string);
    return new Response(JSON.stringify(session ? { token: "fake" } : { id: "fake", nodes: [], revision: 1 }), { headers: { "Content-Type": "application/json" } });
  }) as typeof fetch });
  await gateway.create({ mode: "real", name: "Q11", resourceIds: ["Q11"], target: "Q11", machine: "八号机", qubits: 1, type: "adaptive", policy: "autopilot", scenario: "normal", experiment: "single" });
  assert.equal(Object.hasOwn(payload, "maxTotalShots"), false);
  assert.equal(Object.hasOwn(payload, "budget"), false);
  assert.equal(Object.hasOwn(payload, "reservedShots"), false);
});
