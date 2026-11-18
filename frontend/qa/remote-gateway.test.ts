import test from "node:test";
import assert from "node:assert/strict";
import { artifactUrl, createRemoteGateway, reportUrl } from "../src/lib/remoteGateway.ts";
import type { AppState, Workflow } from "../src/types.ts";

const fixture = (revision = 1): Workflow => ({ id: "run-1", name: "实际任务", mode: "replay", revision, engineState: "attention", experiment: "single", machine: "八号机", qubits: 1, target: "Q01", resourceIds: ["chip-8:Q01"], type: "adaptive", status: "attention", progress: 10, owner: "操作员", createdAt: "2026-11-17T00:00:00Z", policy: "threshold", scenario: "normal", elapsed: 12, iterations: 0, nodes: [{ id: "n1", name: "S21", label: "找腔", status: "attention", progress: 100, attempt: 1, qubits: "Q01", detail: "", latestAttemptId: "attempt-1" }], currentNodeId: "n1", events: [], approved: false });
class FakeStream {
  onopen?: () => void; onerror?: () => void;
  handlers = new Map<string, (event: unknown) => void>(); closed = false;
  addEventListener(type: string, handler: (event: unknown) => void) { this.handlers.set(type, handler); }
  emit(seq: number, workflow: Workflow) { this.handlers.get("snapshot")?.({ data: JSON.stringify({ version: 1, seq, workflows: [workflow] }), lastEventId: String(seq) }); }
  close() { this.closed = true; }
}
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });

test("remote writes bind backend token, revision, current attempt and unique operation; resource IDs stay exact", async () => {
  const calls: { url: string; init?: RequestInit }[] = [];
  const fetcher = async (url: string | URL | Request, init?: RequestInit) => {
    calls.push({ url: String(url), init });
    if (String(url).endsWith("/session")) return response({ token: "backend-session-token", actor: { id: "local", displayName: "操作员", role: "leader" } });
    if (String(url).endsWith("/actions")) return response(fixture(4));
    if (String(url).endsWith("/policy")) return response(fixture(5));
    if (String(url).endsWith("/workflows") && init?.method === "POST") return response(fixture(6));
    return response(fixture(3));
  };
  const gateway = createRemoteGateway({ fetch: fetcher as typeof fetch });
  await gateway.command("run-1", "backtrack", "preceding-step");
  await gateway.policy("run-1", "strict");
  await gateway.create({ name: "Q01", target: "Q01", resourceIds: ["chip-8:Q01"], mode: "real", machine: "八号机", qubits: 1, type: "adaptive", policy: "threshold", scenario: "normal" });
  const writes = calls.filter(call => call.init?.method);
  assert.equal(calls.filter(call => call.url.endsWith("/session")).length, 1);
  assert.equal((writes[0].init?.headers as Record<string, string>)["X-Qcal-Token"], "backend-session-token");
  const command = JSON.parse(writes[0].init?.body as string);
  assert.deepEqual({ ...command, operationId: undefined }, { action: "backtrack", expectedRevision: 3, nodeId: "n1", attemptId: "attempt-1", targetNodeId: "preceding-step", operationId: undefined });
  assert.ok(command.operationId.length > 20);
  assert.notEqual(command.operationId, JSON.parse(writes[1].init?.body as string).operationId);
  assert.equal(JSON.parse(writes[1].init?.body as string).expectedRevision, 4);
  assert.deepEqual(JSON.parse(writes[2].init?.body as string).resourceIds, ["chip-8:Q01"]);
  assert.equal(JSON.parse(writes[2].init?.body as string).target, "Q01");
});

test("SSE wins against late list results, rejects older sequence and resumes cursor after resubscribe", async () => {
  let resolveList!: (value: Response) => void;
  const streams: FakeStream[] = [], urls: string[] = [];
  const gateway = createRemoteGateway({ fetch: (() => new Promise<Response>(resolve => { resolveList = resolve; })) as typeof fetch, eventSource: url => { urls.push(url); const stream = new FakeStream(); streams.push(stream); return stream as unknown as EventSource; } });
  const seen: AppState[] = [];
  const unsubscribe = gateway.subscribe(value => seen.push(value));
  const listing = gateway.list();
  streams[0].emit(8, fixture(5));
  streams[0].emit(7, fixture(2));
  resolveList(response([fixture(1)]));
  assert.equal((await listing)[0].revision, 5);
  assert.equal(seen.length, 1);
  unsubscribe(); assert.equal(streams[0].closed, true);
  gateway.subscribe(value => seen.push(value));
  assert.equal(urls[1], "/api/events?after=8");
});

test("backend command errors stay errors and never fabricate a successful workflow", async () => {
  const gateway = createRemoteGateway({ fetch: (async (url: string | URL | Request) => String(url).endsWith("/session") ? response({ token: "t" }) : String(url).endsWith("/actions") ? response({ error: { code: "hardware_wait", message: "执行器未就绪" } }, 409) : response(fixture())) as typeof fetch });
  await assert.rejects(gateway.command("run-1", "start"), /执行器未就绪/);
});

test("chat returns explicit unavailable model state, artifact routes encode IDs", async () => {
  const gateway = createRemoteGateway({ fetch: (async (url: string | URL | Request) => response(String(url).endsWith("/session") ? { token: "t" } : { sessionId: "s", message: "等待模型连接", status: "waiting_model" })) as typeof fetch });
  const result = await gateway.chat("下一步", "run-1");
  assert.equal(result.status, "waiting_model");
  assert.equal(result.message, "等待模型连接");
  assert.equal(artifactUrl("a/b?x=1"), "/api/artifacts/a%2Fb%3Fx%3D1");
  assert.equal(reportUrl("run 1"), "/api/workflows/run%201/report?format=html");
});

test("401 refresh retries exactly once with the original operation ID and payload", async () => {
  let sessions = 0;
  const writes: { token: string; body: string }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url: string | URL | Request, init?: RequestInit) => {
    if (String(url).endsWith("/session")) return response({ token: `token-${++sessions}` });
    if (String(url).endsWith("/actions")) {
      writes.push({ token: (init?.headers as Record<string, string>)["X-Qcal-Token"], body: init?.body as string });
      return writes.length === 1 ? response({ error: { code: "session_expired", message: "会话过期" } }, 401) : response(fixture(2));
    }
    return response(fixture());
  }) as typeof fetch });
  await gateway.command("run-1", "retry");
  assert.equal(sessions, 2);
  assert.deepEqual(writes.map(write => write.token), ["token-1", "token-2"]);
  assert.equal(writes[0].body, writes[1].body);
  assert.equal(JSON.parse(writes[0].body).operationId, JSON.parse(writes[1].body).operationId);
});

test("a second 401 is surfaced instead of looping or executing a new operation", async () => {
  let sessions = 0, attempts = 0;
  const gateway = createRemoteGateway({ fetch: (async (url: string | URL | Request) => {
    if (String(url).endsWith("/session")) return response({ token: `token-${++sessions}` });
    if (String(url).endsWith("/actions")) { attempts++; return response({ error: { message: "会话无效" } }, 401); }
    return response(fixture());
  }) as typeof fetch });
  await assert.rejects(gateway.command("run-1", "retry"), /会话无效/);
  assert.equal(sessions, 2); assert.equal(attempts, 2);
});
