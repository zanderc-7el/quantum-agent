import test from "node:test";
import assert from "node:assert/strict";
import {
  ApiError,
  REMOTE_SESSION_KEY,
  clearRemoteSession,
  loginWithBackend,
  readRemoteSession,
} from "../src/lib/apiClient.ts";
import type { RemoteSession } from "../src/lib/apiClient.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";
import { createWorkflow } from "../src/lib/simulator.ts";
import type { AppState, Workflow } from "../src/types.ts";

const SESSION: RemoteSession = {
  accessToken: "token-1",
  user: {
    userId: "user-leader",
    username: "leader",
    displayName: "团队负责人",
    roleId: "calibration_leader",
  },
  savedAt: 1,
};

const storageValues = new Map<string, string>();
Object.defineProperty(globalThis, "sessionStorage", {
  configurable: true,
  value: {
    getItem: (key: string) => storageValues.get(key) ?? null,
    setItem: (key: string, value: string) => {
      storageValues.set(key, value);
    },
    removeItem: (key: string) => {
      storageValues.delete(key);
    },
  },
});

function installSession(): void {
  storageValues.clear();
  storageValues.set(REMOTE_SESSION_KEY, JSON.stringify(SESSION));
}

function makeWorkflow(overrides: Partial<Workflow> = {}): Workflow {
  return {
    ...createWorkflow({
      name: "M7 单比特完整校准",
      machine: "M7",
      qubits: 1,
      target: "Q12",
      type: "adaptive",
      policy: "threshold",
      scenario: "normal",
      experiment: "single",
    }),
    ownerId: "user-leader",
    owner: "团队负责人",
    ...overrides,
  };
}

function attentionWorkflow(attempt = 1): Workflow {
  const workflow = makeWorkflow({ status: "attention" });
  workflow.nodes[0] = {
    ...workflow.nodes[0],
    status: "attention",
    progress: 50,
    attempt,
  };
  return workflow;
}

function ok(data: unknown): Response {
  return new Response(
    JSON.stringify({ success: true, data, error: null }),
    { status: 200, headers: { "content-type": "application/json" } },
  );
}

function fail(
  status: number,
  code: string,
  message: string,
  details: Record<string, unknown> = {},
): Response {
  return new Response(
    JSON.stringify({
      success: false,
      data: null,
      error: { code, message, details },
    }),
    { status, headers: { "content-type": "application/json" } },
  );
}

interface RecordedCall {
  url: string;
  init: RequestInit;
}

function installFetch(
  handler: (url: string, init: RequestInit) => Response | Promise<Response>,
): RecordedCall[] {
  const calls: RecordedCall[] = [];
  globalThis.fetch = (async (input: unknown, init: RequestInit = {}) => {
    const url = String(input);
    calls.push({ url, init });
    return handler(url, init);
  }) as typeof fetch;
  return calls;
}

function body(call: RecordedCall): Record<string, unknown> {
  return JSON.parse(String(call.init.body)) as Record<string, unknown>;
}

test("remote list localizes the caller's owner id and sends the bearer token", async () => {
  installSession();
  const workflow = makeWorkflow();
  const calls = installFetch(() =>
    ok({
      items: [{ workflow, revision: 3 }],
      total: 1,
      limit: 200,
      offset: 0,
    }),
  );
  const gateway = createRemoteGateway({ localUserId: "leader", session: SESSION });
  const [listed] = await gateway.list();
  assert.equal(gateway.mode, "remote");
  assert.equal(listed.ownerId, "leader");
  assert.equal(listed.owner, "团队负责人");
  assert.match(
    calls[0].url,
    /^http:\/\/127\.0\.0\.1:18789\/api\/v1\/calibration\/workflows\?limit=200$/,
  );
  assert.equal(
    (calls[0].init.headers as Record<string, string>).Authorization,
    "Bearer token-1",
  );
});

test("remote creation pins the selected blueprint version", async () => {
  installSession();
  const workflow = makeWorkflow();
  const calls = installFetch(() => ok({ workflow, revision: 1 }));
  const gateway = createRemoteGateway({ session: SESSION });
  await gateway.create({ name: "单比特校准", machine: "M7", qubits: 1, target: "Q12",
    type: "adaptive", policy: "threshold", scenario: "normal", experiment: "single" });
  const payload = body(calls[0]);
  assert.equal(payload.blueprintId, "single");
  assert.equal(payload.blueprintVersion, 1);
});

test("approve binds the attention node and attempt with an idempotency key", async () => {
  installSession();
  const initial = attentionWorkflow(1);
  const updated = { ...initial, status: "running" as const };
  const calls = installFetch((url, init) => {
    if (init.method === "GET" || !init.method)
      return ok({ workflow: initial, revision: 5 });
    return ok({ workflow: updated, revision: 6 });
  });
  const gateway = createRemoteGateway({ localUserId: "leader", session: SESSION });
  const result = await gateway.act(initial.id, "approve");
  assert.equal(result.status, "running");

  const post = calls.find((call) => call.init.method === "POST");
  assert.ok(post);
  const payload = body(post!);
  assert.equal(payload.action, "approve");
  assert.equal(payload.expectedRevision, 5);
  assert.equal(payload.nodeId, initial.nodes[0].id);
  assert.equal(payload.attempt, 1);
  assert.match(String(payload.requestId), /^web-/);
});

test("a stale revision is retried once with the same request id and the latest snapshot", async () => {
  installSession();
  const initial = attentionWorkflow(1);
  // The authoritative snapshot in a 409 refers to the same workflow.
  const latest = { ...attentionWorkflow(2), id: initial.id };
  let posts = 0;
  const calls = installFetch((url, init) => {
    if (init.method === "GET" || !init.method)
      return ok({ workflow: initial, revision: 5 });
    posts += 1;
    if (posts === 1)
      return fail(409, "CONFLICT", "任务已被其他操作更新。", {
        workflow: latest,
        actualRevision: 9,
      });
    return ok({ workflow: { ...latest, status: "running" }, revision: 10 });
  });
  const gateway = createRemoteGateway({ localUserId: "leader", session: SESSION });
  const result = await gateway.act(initial.id, "retry");
  assert.equal(result.status, "running");
  const sent = calls
    .filter((call) => call.init.method === "POST")
    .map((call) => body(call));
  assert.equal(sent.length, 2);
  assert.equal(sent[0].requestId, sent[1].requestId);
  assert.equal(sent[0].expectedRevision, 5);
  assert.equal(sent[1].expectedRevision, 9);
  assert.equal(sent[1].nodeId, latest.nodes[0].id);
  assert.equal(sent[1].attempt, 2);
});

test("policy changes carry the server revision", async () => {
  installSession();
  const initial = makeWorkflow();
  const calls = installFetch((_url, init) => {
    if (init.method === "PATCH")
      return ok({ workflow: { ...initial, policy: "autopilot" }, revision: 8 });
    return ok({ workflow: initial, revision: 7 });
  });
  const gateway = createRemoteGateway({ localUserId: "leader", session: SESSION });
  const updated = await gateway.policy(initial.id, "autopilot");
  assert.equal(updated.policy, "autopilot");
  const patch = calls.find((call) => call.init.method === "PATCH");
  assert.ok(patch);
  const payload = body(patch!);
  assert.equal(payload.policy, "autopilot");
  assert.equal(payload.expectedRevision, 7);
});

test("workspace import posts the raw envelope the file dialog produced", async () => {
  installSession();
  const raw = JSON.stringify({ version: 1, workflows: [] });
  const calls = installFetch(() =>
    ok({ total: 1, imported: 1, skipped: 0, paused: 0, importedIds: ["wf-import-1"] }),
  );
  const gateway = createRemoteGateway({ localUserId: "leader", session: SESSION });
  const result = await gateway.importWorkflows!(raw);
  assert.deepEqual(result.importedIds, ["wf-import-1"]);
  const post = calls.find((call) => call.init.method === "POST");
  assert.ok(post);
  assert.equal(post!.init.body, raw);
  assert.equal(
    (post!.init.headers as Record<string, string>)["Content-Type"],
    "application/json",
  );
});

test("the SSE snapshot drives subscribers and closing the last one closes the stream", () => {
  installSession();
  class FakeEventSource {
    readonly url: string;
    readonly listeners = new Map<string, EventListener[]>();
    closed = false;
    constructor(url: string) {
      this.url = url;
    }
    addEventListener(type: string, listener: EventListener): void {
      this.listeners.set(type, [...(this.listeners.get(type) ?? []), listener]);
    }
    close(): void {
      this.closed = true;
    }
    emit(type: string, data: unknown): void {
      const event = { data: JSON.stringify(data) } as MessageEvent;
      for (const listener of this.listeners.get(type) ?? [])
        listener(event as unknown as Event);
    }
  }
  let created: FakeEventSource | null = null;
  const gateway = createRemoteGateway({
    localUserId: "leader",
    session: SESSION,
    createEventSource: (url) => {
      created = new FakeEventSource(url);
      return created as unknown as EventSource;
    },
  });
  const states: AppState[] = [];
  const unsubscribe = gateway.subscribe((state) => states.push(state));
  assert.ok(created);
  assert.match(created!.url, /\/api\/v1\/calibration\/stream\?token=token-1$/);
  const base = attentionWorkflow(1);
  created!.emit("snapshot", {
    workflows: [{ workflow: base, revision: 4 }],
  });
  assert.equal(states.at(-1)?.workflows[0].ownerId, "leader");
  created!.emit("workflow.updated", {
    workflow: { ...base, name: "已更新" },
    revision: 5,
  });
  assert.equal(states.at(-1)?.workflows[0].name, "已更新");
  unsubscribe();
  assert.equal(created!.closed, true);
});

test("backend login trusts the server identity and maps its calibration roles", async () => {
  storageValues.clear();
  const calls = installFetch(() =>
    ok({
      access_token: "abc",
      token_type: "bearer",
      user: {
        user_id: "user-leader",
        display_name: "团队负责人",
        role_id: "calibration_leader",
      },
    }),
  );
  const leader = await loginWithBackend(" leader ", "Leader123!");
  // The identity comes from the backend, not from the demo directory id.
  assert.deepEqual(leader, {
    id: "user-leader",
    username: "leader",
    displayName: "团队负责人",
    role: "leader",
    source: "remote",
  });
  assert.deepEqual(JSON.parse(calls[0].init.body as string), {
    username: "leader",
    password: "Leader123!",
  });
  assert.equal(readRemoteSession()?.accessToken, "abc");
  assert.ok(storageValues.get(REMOTE_SESSION_KEY)?.includes("abc"));

  clearRemoteSession();
  installFetch(() =>
    ok({
      access_token: "def",
      token_type: "bearer",
      user: {
        user_id: "user-operator",
        display_name: "实验工作人员",
        role_id: "calibration_operator",
      },
    }),
  );
  const operator = await loginWithBackend("operator", "Operator123!");
  assert.equal(operator.id, "user-operator");
  assert.equal(operator.role, "operator");
  assert.equal(readRemoteSession()?.user.roleId, "calibration_operator");
});

test("backend login rejects roles this workspace cannot represent", async () => {
  storageValues.clear();
  installFetch(() =>
    ok({
      access_token: "abc",
      token_type: "bearer",
      user: {
        user_id: "user-pm",
        display_name: "项目经理",
        role_id: "project-manager",
      },
    }),
  );
  await assert.rejects(
    () => loginWithBackend("pm", "Password123!"),
    (error: unknown) =>
      error instanceof ApiError && error.code === "UNSUPPORTED_ROLE",
  );
  assert.equal(readRemoteSession(), null);
  assert.equal(storageValues.has(REMOTE_SESSION_KEY), false);
});

test("backend login requires a non-empty account name", async () => {
  storageValues.clear();
  const calls = installFetch(() => ok({}));
  await assert.rejects(
    () => loginWithBackend("   ", "Leader123!"),
    (error: unknown) =>
      error instanceof ApiError && error.code === "INVALID_ACCOUNT",
  );
  assert.equal(calls.length, 0);
});

test("a rejected login surfaces the backend message instead of an expiry hint", async () => {
  storageValues.clear();
  installFetch(() => fail(401, "UNAUTHENTICATED", "用户名或密码错误。"));
  await assert.rejects(
    () => loginWithBackend("leader", "wrong"),
    (error: unknown) =>
      error instanceof ApiError && error.message === "用户名或密码错误。",
  );
  assert.equal(readRemoteSession(), null);
});
