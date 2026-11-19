import test from "node:test";
import assert from "node:assert/strict";
import { parseExportSession, reportDownloadUrl } from "../src/lib/reportExportSession.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";
import type { ReportExportJob } from "../src/lib/remoteGateway.ts";
import * as exportSessions from "../src/lib/reportExportSession.ts";
const request = { operationId: "op-1", expectedRevision: 9, format: "pptx", appendixCount: 16 } as const;
test("export recovery preserves frozen request, never mixes workflows, and confines download URLs", () => {
  const raw = JSON.stringify({ workflowId: "wf-1", request });
  assert.deepEqual(parseExportSession(raw, "wf-1")?.request, request);
  assert.equal(parseExportSession(raw, "wf-2"), undefined);
  assert.equal(parseExportSession('{broken', "wf-1"), undefined);
  const job: ReportExportJob = { id: "report-1", workflowId: "wf-1", revision: 9, status: "completed", createdAt: "now", downloadUrl: "https://untrusted.example/" };
  assert.equal(reportDownloadUrl(job), "/api/report-exports/report-1/file");
  assert.equal(reportDownloadUrl({ ...job, status: "processing" }), undefined);
  assert.equal(parseExportSession(JSON.stringify({ workflowId: "wf-1", request, job: { ...job, workflowId: "wf-2" } }), "wf-1"), undefined);
});
test("chat sends selected node ID only, and PPT token retry preserves identical operation", async () => {
  const writes: { url: string; body: Record<string,unknown> }[] = []; let exports = 0;
  const respond = (value: unknown, status = 200) => new Response(JSON.stringify(value), {status});
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (String(url).endsWith("/session")) return respond({ token: "local-token" });
    if (init?.method === "POST") writes.push({ url: String(url), body: JSON.parse(String(init.body)) });
    if (String(url).endsWith("report-exports") && exports++ === 0) return respond({error:{message:"expired"}},401);
    return respond({id:"export-1",workflowId:"wf-1",status:"queued"});
  }) as typeof fetch });
  await gateway.chat("当前测量怎么样", "wf-1", "session-1", "node-1");
  await gateway.createReportExport("wf-1", request);
  assert.deepEqual(writes[0].body, {message:"当前测量怎么样",workflowId:"wf-1",sessionId:"session-1",nodeId:"node-1"});
  assert.deepEqual(writes[1].body, request); assert.deepEqual(writes[2].body, request);
});

test("server report recovery updates old downloads without losing an unresolved or running request", () => {
  assert.equal(typeof exportSessions.recoverExportSession, "function");
  const old = { id: "report-old", workflowId: "wf-1", revision: 9, status: "completed", createdAt: "2026-11-19T00:00:00Z", operationId: "op-1", appendixCount: 16 } as const;
  const newer = { ...old, id: "report-new", operationId: "op-2", createdAt: "2026-11-19T00:05:00Z" };
  const current = { workflowId: "wf-1", request, job: old };
  const restored = exportSessions.recoverExportSession(current, [newer, old], "wf-1");
  assert.equal(restored?.job?.id, "report-new");
  assert.equal(restored?.request.operationId, "op-2");
  assert.equal(exportSessions.recoverExportSession(undefined, [newer], "wf-1")?.job?.id, "report-new");
  assert.equal(exportSessions.recoverExportSession(current, [{ ...newer, workflowId: "wf-2" }], "wf-1"), current);
  const unresolved = { workflowId: "wf-1", request };
  assert.equal(exportSessions.recoverExportSession(unresolved, [newer], "wf-1"), unresolved);
  const running = { ...current, job: { ...old, status: "processing" as const } };
  assert.equal(exportSessions.recoverExportSession(running, [newer], "wf-1"), running);
  assert.equal(exportSessions.recoverExportSession(running, [old], "wf-1")?.job?.status, "completed");
});

test("recovering report history only reads the selected workflow", async () => {
  const calls: { url: string; method: string }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    calls.push({ url: String(url), method: init?.method ?? "GET" });
    return new Response("[]", { status: 200 });
  }) as typeof fetch });
  assert.equal(typeof gateway.reportExports, "function");
  assert.deepEqual(await gateway.reportExports("wf-1"), []);
  assert.deepEqual(calls, [{ url: "/api/workflows/wf-1/report-exports", method: "GET" }]);
});
