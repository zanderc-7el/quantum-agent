import test from "node:test";
import assert from "node:assert/strict";
import { createRemoteChatStore, MAX_REMOTE_CACHE_BYTES, MAX_REMOTE_MESSAGES } from "../src/lib/remoteChatStore.ts";
import type { ChatScope } from "../src/lib/remoteChatStore.ts";
import type { ChatReply } from "../src/lib/remoteGateway.ts";
import { staleNodeLabel } from "../src/lib/workflowResources.ts";
import type { ExperimentNode } from "../src/types.ts";
const scope: ChatScope = { projectId: "project-a:D:/copy", workflowId: "wf-q11" };
function fixture() {
  const records = new Map<string, string>(); let sequence = 0;
  const storage = { getItem: (key: string) => records.get(key) ?? null, setItem: (key: string, value: string) => { records.set(key, value); } };
  const options = { storage: () => storage, id: () => `id-${++sequence}`, now: () => "2026-11-18T06:00:00Z" };
  return { records, options, store: createRemoteChatStore(options) };
}
const reply = (sessionId = "session-a", message = "实际状态说明"): ChatReply => ({ sessionId, message, status: "ok", model: "model-a" });
test("completed task conversation and session survive renderer reload without workflow state", async () => {
  const { store, records, options } = fixture();
  await store.send(scope, "目前测到哪一步", async () => reply());
  const reopened = createRemoteChatStore(options), loaded = reopened.get(scope);
  assert.deepEqual(loaded.messages.map(item => item.role), ["user", "assistant"]);
  assert.equal(loaded.sessionId, "session-a"); assert.equal(loaded.pending, undefined);
  let resumedSession: string | undefined;
  await reopened.send(scope, "下一步是什么", async (_text, session) => { resumedSession = session; return reply(); });
  assert.equal(resumedSession, "session-a"); assert.equal(reopened.get(scope).messages.length, 4);
  assert.equal(records.size, 1); assert.equal(JSON.stringify([...records.values()]).includes('"nodes"'), false);
});
test("cache isolates project roots, workflows and general chat", async () => {
  const { store } = fixture(); await store.send(scope, "Q11", async () => reply());
  for (const other of [{ ...scope, workflowId: "wf-q18" }, { ...scope, projectId: "project-b:D:/elsewhere" }, { ...scope, workflowId: "general" }]) {
    assert.equal(store.get(other).messages.length, 0); assert.equal(store.get(other).sessionId, undefined);
  }
});
test("singleflight survives component unsubscribe and notifies a returning subscriber", async () => {
  const { store } = fixture(); let calls = 0, resolve!: (value: ChatReply) => void, notified = 0;
  const response = new Promise<ChatReply>(done => { resolve = done; });
  const first = store.send(scope, "同一问题", async () => { calls++; return response; });
  const unsubscribe = store.subscribe(scope, () => {}); unsubscribe();
  const cleanup = store.subscribe(scope, () => { notified++; });
  const second = store.send(scope, "重复点击", async () => { calls++; return reply(); });
  assert.equal(first, second); assert.equal(store.get(scope).pending?.state, "running");
  await Promise.resolve(); assert.equal(calls, 1); resolve(reply()); await first;
  assert.equal(notified, 1); assert.equal(store.get(scope).messages.length, 2); cleanup();
});
test("reloading in-flight request never resends or claims completion", async () => {
  const { store, options } = fixture(); let calls = 0, finish!: (value: ChatReply) => void;
  const pending = store.send(scope, "收费请求", async () => { calls++; return new Promise<ChatReply>(resolve => { finish = resolve; }); });
  await Promise.resolve();
  const reloaded = createRemoteChatStore(options);
  assert.equal(reloaded.get(scope).pending?.state, "unresolved");
  assert.equal(reloaded.get(scope).messages.at(-1)?.role, "notice");
  assert.match(reloaded.get(scope).messages.at(-1)!.text, /没有自动重新发送/);
  await assert.rejects(reloaded.send(scope, "未核查时再次提交", async () => { calls++; return reply(); }), /待核查/);
  assert.equal(calls, 1);
  reloaded.allowNewMessage(scope);
  assert.equal(reloaded.get(scope).pending, undefined);
  assert.equal(reloaded.get(scope).messages.at(-1)?.status, "unresolved");
  finish(reply()); await pending;
});
test("transport errors and model failure replies remain in persistent history", async () => {
  const { store, options } = fixture();
  await store.send(scope, "第一次", async () => { throw new Error("连接中断"); });
  const reopened = createRemoteChatStore(options);
  assert.equal(reopened.get(scope).pending?.state, "unresolved"); assert.match(reopened.get(scope).messages.at(-1)!.text, /连接中断/);
  reopened.allowNewMessage(scope);
  await reopened.send(scope, "新问题", async () => ({ ...reply(), status: "waiting_model", message: "模型返回空正文" }));
  const saved = createRemoteChatStore(options).get(scope);
  assert.equal(saved.messages.at(-1)?.status, "waiting_model"); assert.equal(saved.sessionId, "session-a"); assert.equal(saved.pending, undefined);
});
test("damaged cache stays intact and quota failures remain visible", async () => {
  const { store, records, options } = fixture(); await store.send(scope, "建立记录", async () => reply());
  const [key] = records.keys(); records.set(key, "{broken");
  const reopened = createRemoteChatStore(options);
  assert.equal(reopened.get(scope).messages.length, 0); assert.ok(reopened.get(scope).storageError);
  await reopened.send(scope, "新的消息", async () => reply()); assert.equal(records.get(key), "{broken");
  const failed = createRemoteChatStore({ storage: () => ({ getItem: () => null, setItem: () => { throw new Error("QuotaExceededError"); } }) });
  await failed.send(scope, "仍保留内存", async () => reply()); assert.equal(failed.get(scope).messages.length, 2); assert.match(failed.get(scope).storageError!, /保存失败/);
});
test("cache has explicit message and byte limits and reports omitted history", async () => {
  const { store, records, options } = fixture();
  for (let index = 0; index < 110; index++) await store.send(scope, "问题".repeat(5000), async () => reply("session-a", "回答".repeat(25000)));
  assert.ok(store.get(scope).messages.length <= MAX_REMOTE_MESSAGES); assert.ok(store.get(scope).omittedMessages > 0);
  for (const raw of records.values()) assert.ok(new TextEncoder().encode(raw).length <= MAX_REMOTE_CACHE_BYTES);
  assert.ok(createRemoteChatStore(options).get(scope).messages.length > 0);
});
test("never-measured stale node is waiting for dependencies without changing engine state", () => {
  const node = { engineState: "stale", attempt: 0 } as ExperimentNode;
  assert.equal(staleNodeLabel(node), "等待前置测量"); assert.equal(node.engineState, "stale");
  assert.equal(staleNodeLabel({ ...node, attempt: 1 }), "依赖变化 · 待复测");
  assert.equal(staleNodeLabel({ ...node, result: { dataKind: "real" } as ExperimentNode["result"] }), "依赖变化 · 待复测");
});
