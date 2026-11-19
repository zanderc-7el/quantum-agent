import test from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import { buildSync } from "esbuild";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { groupHardwareOwners, hardwareCheckExplanation, workflowHardwareSummary } from "../src/lib/hardwareStatusPresentation.ts";
import type { HardwareOwnerRecord } from "../src/lib/hardwareStatusPresentation.ts";
import type { Workflow } from "../src/types.ts";

// Structural fixture for the observed one socket + two shared locks. It does
// not read OS processes, device sockets, workflow databases or API services.
const owners: HardwareOwnerRecord[] = [
  { pid: 66432, localPort: 62238 },
  { pid: 66432, kind: "chassis_lock", path: "D:/fixture/arclight/device.lock", operationId: "attempt-fixture/measure" },
  { pid: 66432, kind: "chassis_lock", path: "D:/fixture/f8/device.lock", operationId: "attempt-fixture/measure" },
];
const hardware = { enabled: false, ready: true, ownership: { verified: true, owners, endpoint: "fixture-device:7110", reason: "只读取连接表，未连接仪器" } };
const workflow = (): Workflow => ({
  id: "fixture", name: "Q11", mode: "real", status: "running", hardwareConfirmed: true,
  currentNodeId: "ramsey:Q11", nodes: [{ id: "ramsey:Q11", name: "Ramsey", label: "Ramsey", status: "running", engineState: "acquiring",
    latestAttemptId: "attempt-fixture", attempt: 1, progress: 20, qubits: "Q11", detail: "",
    telemetry: { controllerAttemptId: "attempt-fixture", controllerOperationId: "attempt-fixture/measure", completedRows: 20, totalRows: 100, pid: 99999 } }],
} as Workflow);
const compiled = buildSync({ entryPoints: [fileURLToPath(new URL("../src/components/RemotePanels.tsx", import.meta.url))],
  bundle: true, write: false, platform: "node", format: "cjs", packages: "external", loader: { ".css": "empty" },
}).outputFiles[0].text;
const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
new Function("require", "module", "exports", compiled)(createRequire(import.meta.url), module, module.exports);
const render = (props: Record<string, unknown>) => renderToStaticMarkup(createElement(module.exports.HardwareStatus, props));

test("same PID is grouped once while its socket and both lock records are preserved unchanged", () => {
  const before = JSON.stringify(owners), grouped = groupHardwareOwners(owners);
  assert.equal(grouped.length, 1); assert.equal(grouped[0].pid, 66432);
  assert.equal(grouped[0].records.length, 3); assert.deepEqual(grouped[0].records, owners);
  assert.equal(JSON.stringify(owners), before);
  const separate = groupHardwareOwners([...owners, { pid: 12, localPort: 3000 }, {}, { kind: "chassis_lock" }, { pid: -1 }]);
  assert.equal(separate.length, 5, "unknown PIDs must not be merged as if they were one process");
});

test("actual card renders PID once with separate connection and two lock kinds, not a global shutdown", () => {
  const html = render({ hardware, workflow: workflow() });
  assert.equal((html.match(/PID 66432/g) ?? []).length, 1);
  assert.match(html, /网络连接（本地端口 62238）/);
  assert.match(html, /共享锁（arclight\/device.lock）/);
  assert.match(html, /共享锁（f8\/device.lock）/);
  assert.match(html, /进程与本任务的归属尚未核验/);
  assert.match(html, /检查过程不会新建仪器连接/);
  assert.match(html, /不代表测量工作进程未连接/);
  assert.match(html, /默认直接连接未启用/);
  assert.match(html, /任务仍可由控制器单独授权采集/);
  assert.doesNotMatch(html, /全局执行器当前保持关闭|只读取连接表，未连接仪器/);
});

test("task-scoped acquisition wording uses matching controller progress, never matching PID as ownership proof", () => {
  const current = workflow(), before = JSON.stringify(current);
  assert.match(workflowHardwareSummary(current)!, /当前快照显示正在采集：Ramsey，20\/100 点/);
  const html = render({ hardware, workflow: current });
  assert.match(html, /不构成连接或锁的进程归属核验/);
  assert.doesNotMatch(html, /已核验归属|本任务工作进程/);
  assert.equal(JSON.stringify(current), before, "rendering must not mutate a workflow snapshot");
  current.nodes[0].engineState = "verifying";
  current.nodes[0].telemetry!.controllerOperationId = "attempt-fixture/verify";
  assert.match(workflowHardwareSummary(current)!, /正在独立复测采集/);
});

test("historical, paused, terminal, stale and mismatched telemetry never imply current acquisition", () => {
  const cases: ((value: Workflow) => void)[] = [
    value => { value.mode = "replay"; },
    value => { value.hardwareConfirmed = false; },
    value => { value.status = "paused"; },
    value => { value.status = "aborted"; },
    value => { value.status = "completed"; },
    value => { value.status = "failed"; },
    value => { value.status = "attention"; },
    value => { value.nodes[0].engineState = "deciding"; },
    value => { value.nodes[0].telemetry!.controllerAttemptId = "old-attempt"; },
    value => { value.nodes[0].telemetry!.controllerOperationId = "attempt-fixture/verify"; },
    value => { value.nodes[0].telemetry!.completedRows = 100; },
    value => { value.nodes[0].telemetry!.completedRows = NaN; },
    value => { value.nodes[0].telemetry = undefined; },
  ];
  for (const change of cases) {
    const value = workflow(); change(value);
    assert.doesNotMatch(workflowHardwareSummary(value) ?? "", /正在.*采集/);
  }
  assert.match(workflowHardwareSummary({ ...workflow(), status: "paused" })!, /是否已释放，以连接及锁记录为准/);
});

test("global settings have no task claim, and unknown check failures and actions stay visible", () => {
  assert.equal(workflowHardwareSummary(undefined), undefined);
  assert.equal(hardwareCheckExplanation("Windows socket 状态核查失败"), "检查说明：Windows socket 状态核查失败");
  const html = render({ hardware: { ...hardware, ready: false, reasons: ["fixture preflight reason"] }, action: createElement("button", null, "确认交接并继续") });
  assert.match(html, /fixture preflight reason/); assert.match(html, /确认交接并继续/);
  assert.doesNotMatch(html, /本任务已确认交接/);
  const empty = render({ hardware: { enabled: false, ownership: { verified: true, owners: [] } } });
  assert.match(empty, /设备连接占用检查已通过/);
  assert.doesNotMatch(empty, /当前快照显示正在|进程与本任务的归属尚未核验/);
});
