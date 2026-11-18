import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import type { Catalog, CreateWorkflowInput, ExperimentNode, Workflow } from "../src/types.ts";
import { defaultCoverage, workflowCoverageError, workingPointScope, workingPointUnavailableReason } from "../src/lib/workflowCoverage.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";

// Offline UI contracts only: these fixtures contain no acquired results or scientific claims.
const catalog: Catalog = {
  blueprint: { version: "test", nodes: [], edges: [] }, rules: [],
  resources: [{ id: "Q11", label: "Q11" }, { id: "Q18", label: "Q18" }],
  capabilities: { id: "arclight-native-cy8", resources: ["Q11"] },
  coverage: { realDefault: "full", main: "原流程八个主步骤", full: "完整测量", working_point: "复刻已有工作点 · 10项脉冲测量" },
  workingPointCoverage: { available: true, reason: "", resourceIds: ["Q11"], adapterProfileIds: ["arclight-private-f8-q11-v2"], selectedNodeCount: 10, seedStatus: "seed_only", label: "复刻已有工作点" },
};
const input: CreateWorkflowInput = {
  name: "Q11 · 已有工作点复刻", machine: "八号机", target: "Q11", qubits: 1,
  mode: "real", experiment: "single", resourceIds: ["Q11"], coverage: "working_point",
  type: "adaptive", policy: "threshold", scenario: "normal",
};
const labels = ["幅度 Rabi", "时间 Rabi", "IQ 读出", "读出优化", "T1", "AllXY", "Ramsey", "SpinEcho", "T2/CPMG", "RB"];
const workflow = (): Workflow => ({
  ...input, id: "offline-working-point-ui", mode: "real", experiment: "single", coverage: "working_point",
  owner: "测试", createdAt: "2026-11-18T00:00:00Z", status: "draft", progress: 0,
  iterations: 0, elapsed: 0, approved: false, events: [],
  nodes: labels.map((label, i): ExperimentNode => ({ id: `node-${i}`, label, name: label, qubits: "Q11", detail: "", status: "pending", progress: 0, attempt: 0 })),
  seedReference: { schema: "working-point-seed/1", status: "seed_only", sourceId: "offline-reference-id", parameters: { drive_hz: 4600000000 }, sourceFiles: [{ kind: "parameters", name: "offline-seed.json", path: "offline/seed.json", sha256: "a".repeat(64) }], sourceReport: { path: "offline/report.json", sha256: "b".repeat(64) }, meaning: "只提供初值；历史通过数不迁入本次任务。" },
  completionScope: { selectedNodeCount: 10, omittedProcedures: ["ro_frequency", "ro_power_shift", "qubit_frequency"], full13Fresh: false, meaning: "验收本次10项脉冲范围。" },
});

test("working-point is opt-in and main/full defaults keep their existing meaning", () => {
  assert.equal(defaultCoverage("real"), "full");
  assert.equal(defaultCoverage("replay"), "main");
  assert.equal(defaultCoverage(undefined), "main");
  for (const coverage of [undefined, "main", "full"] as const) {
    assert.equal(workflowCoverageError(undefined, { ...input, coverage, resourceIds: ["Q18"] }), undefined);
  }
  assert.equal(workingPointUnavailableReason(catalog, input), undefined);
});

test("catalog capability and exact single target gate working-point creation", () => {
  for (const mode of ["replay", undefined] as const) assert.match(workflowCoverageError(catalog, { ...input, mode })!, /真实测量/);
  for (const experiment of ["two", undefined] as const) assert.match(workflowCoverageError(catalog, { ...input, experiment })!, /单比特/);
  for (const resourceIds of [[], ["Q18"], ["Q011"], ["Q11", "Q18"], ["Q11", "Q11"]]) {
    assert.match(workflowCoverageError(catalog, { ...input, resourceIds })!, /仅支持 Q11/);
  }
  assert.match(workflowCoverageError(undefined, input)!, /尚未提供/);
  const unavailable = { ...catalog, workingPointCoverage: { ...catalog.workingPointCoverage!, available: false, reason: "静态 profile 审计尚未匹配" } };
  assert.equal(workflowCoverageError(unavailable, input), "静态 profile 审计尚未匹配");
  assert.match(workflowCoverageError({ ...catalog, workingPointCoverage: undefined }, input)!, /尚未提供/);
});

test("scope projection keeps fresh counters and scientific records unchanged", () => {
  const value = workflow();
  const before = JSON.stringify(value);
  Object.freeze(value.nodes); Object.freeze(value);
  const display = workingPointScope(value)!;
  assert.equal(display.count, 10); assert.deepEqual(display.procedures, labels);
  assert.match(display.seed, /seed_only/); assert.match(display.seed, /历史结果不计本次通过/);
  assert.match(display.scope, /仅验收所选脉冲范围/);
  assert.equal(display.source, value.seedReference);
  assert.equal(JSON.stringify(value), before);
  assert.equal(value.nodes.filter(node => node.status === "success").length, 0);
  assert.equal(value.nodes.every(node => node.result === undefined), true);
  for (const coverage of [undefined, "main", "full"] as const) assert.equal(workingPointScope({ ...value, coverage }), undefined);
});

const compiled = buildSync({ entryPoints: [fileURLToPath(new URL("../src/components/WorkflowCoverage.tsx", import.meta.url))], bundle: true, write: false, platform: "node", format: "cjs", packages: "external", loader: { ".css": "empty" } }).outputFiles[0].text;
const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
new Function("require", "module", "exports", compiled)(createRequire(import.meta.url), module, module.exports);
const { WorkflowCoverageSelect, WorkingPointScope } = module.exports;

test("actual creation selector exposes scoped mode and explains skipped scans", () => {
  const markup = renderToStaticMarkup(createElement(WorkflowCoverageSelect, { catalog, form: input, onChange() {} }));
  assert.match(markup, /aria-label="测量范围"/);
  assert.match(markup, /value="full"/); assert.match(markup, /value="main"/);
  assert.match(markup, /value="working_point" selected=""/);
  assert.match(markup, /不重做读出找腔、功率扫描和比特定频/);
  assert.match(markup, /10 项脉冲测量重新采集/);
  const unavailable = renderToStaticMarkup(createElement(WorkflowCoverageSelect, { catalog, form: { ...input, resourceIds: ["Q18"] }, onChange() {} }));
  assert.match(unavailable, /value="working_point" disabled="" selected=""/);
  assert.match(unavailable, /role="alert"/); assert.match(unavailable, /仅支持 Q11/);
  assert.equal(renderToStaticMarkup(createElement(WorkflowCoverageSelect, { catalog, form: { ...input, mode: "replay" }, onChange() {} })), "");
});

test("actual task scope renders provenance, the ten fresh nodes, and no imported acceptance", () => {
  const value = workflow(), before = JSON.stringify(value);
  const markup = renderToStaticMarkup(createElement(WorkingPointScope, { workflow: value }));
  assert.match(markup, /本次 10 项脉冲测量/);
  assert.match(markup, /参考初值 · seed_only/); assert.match(markup, /历史结果不计本次通过/);
  assert.match(markup, /offline-reference-id/); assert.match(markup, /offline-seed.json/); assert.match(markup, /a{64}/);
  for (const label of labels) assert.ok(markup.includes(label));
  assert.doesNotMatch(markup, /13\s*项.*完成|13\s*\/\s*13|全流程已完成|节点已验证/);
  assert.equal(JSON.stringify(value), before);
  assert.equal(renderToStaticMarkup(createElement(WorkingPointScope, { workflow: { ...value, coverage: "full" } })), "");
  assert.match(renderToStaticMarkup(createElement(WorkingPointScope, { workflow: { ...value, seedReference: undefined } })), /来源记录尚未载入/);
});

test("scope lists stored methods as an unordered range instead of inventing an execution path", () => {
  const value = workflow();
  value.nodes = [0, 2, 3, 4, 8, 9, 1, 5, 6, 7].map(index => value.nodes[index]);
  const timeRabi = value.nodes.find(node => node.label === "时间 Rabi")!;
  const iq = value.nodes.find(node => node.label === "IQ 读出")!;
  iq.dependsOn = [timeRabi.id]; // Storage puts IQ first; the actual blueprint dependency puts TimeRabi first.
  const before = JSON.stringify(value);
  const markup = renderToStaticMarkup(createElement(WorkingPointScope, { workflow: value }));
  assert.match(markup, /本次测项（执行顺序见蓝图）/);
  assert.ok(markup.includes(value.nodes.map(node => node.label).join("、")));
  assert.doesNotMatch(markup, /→|&rarr;/);
  assert.equal(JSON.stringify(value), before);
});

test("create transport sends mode without importing seed records or starting acquisition", async () => {
  const writes: { url: string; init: RequestInit }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (init?.method) writes.push({ url: String(url), init });
    return new Response(JSON.stringify(String(url).endsWith("/session") ? { token: "offline-token" } : workflow()), { headers: { "Content-Type": "application/json" } });
  }) as typeof fetch });
  const result = await gateway.create(input);
  assert.equal(writes.length, 1); assert.equal(writes[0].url, "/api/workflows");
  assert.deepEqual(JSON.parse(writes[0].init.body as string), input);
  assert.equal(result.seedReference?.status, "seed_only");
  assert.equal(result.completionScope?.full13Fresh, false);
  assert.equal(Object.hasOwn(JSON.parse(writes[0].init.body as string), "seedReference"), false);
  assert.equal(result.nodes.filter(node => node.status === "success").length, 0);
});
