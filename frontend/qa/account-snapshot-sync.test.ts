import test from "node:test";
import assert from "node:assert/strict";
import { bindAccountSnapshotRefresh, createAccountSnapshotSync } from "../src/lib/accountSnapshotSync.ts";
import { createRemoteGateway } from "../src/lib/remoteGateway.ts";
import type { ConnectionState } from "../src/lib/remoteGateway.ts";
import type { MembershipStatus } from "../src/lib/account.ts";

const status = (model: string): MembershipStatus => ({
  provider: "ziyor", configured: true, authenticated: true,
  modelConnection: { provider: "ziyor", model, active: true, verified: true },
  capabilities: { sendCode: true, login: true, connect: true, test: true },
});
const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function clock() {
  let now = 0, sequence = 0;
  const timers = new Map<number, { at: number; callback: () => void }>();
  return {
    options: {
      now: () => now,
      schedule: (callback: () => void, delay: number) => {
        const id = ++sequence; timers.set(id, { at: now + delay, callback });
        return id as unknown as ReturnType<typeof setTimeout>;
      },
      cancel: (id: ReturnType<typeof setTimeout>) => { timers.delete(id as unknown as number); },
    },
    advance(ms: number) {
      now += ms;
      for (const [id, timer] of [...timers]) if (timer.at <= now) {
        timers.delete(id); timer.callback();
      }
    },
    pending: () => timers.size,
  };
}
function lifecycle(sync: ReturnType<typeof createAccountSnapshotSync>) {
  const windowTarget = new EventTarget(), documentTarget = new EventTarget();
  let visible = true;
  const connections = new Set<(state: ConnectionState) => void>();
  const seen: ConnectionState[] = [];
  const unbind = bindAccountSnapshotRefresh(sync, {
    windowTarget, documentTarget, visible: () => visible,
    subscribeConnection: listener => { connections.add(listener); listener("connecting"); return () => { connections.delete(listener); }; },
    onConnection: state => seen.push(state),
  });
  return {
    focus: () => windowTarget.dispatchEvent(new Event("focus")),
    visibility(next: boolean) { visible = next; documentTarget.dispatchEvent(new Event("visibilitychange")); },
    connection: (state: ConnectionState) => connections.forEach(listener => listener(state)),
    unbind, seen,
  };
}

test("the periodic account GET observes an external Qwen to DeepSeek switch without any mutation or inference", async () => {
  const time = clock(), shown: string[] = [], requests: { url: string; method: string }[] = [];
  let current = "zeelin-qwen3.8-max";
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    requests.push({ url: String(url), method: init?.method ?? "GET" });
    return new Response(JSON.stringify(status(current)), { status: 200 });
  }) as typeof fetch });
  const sync = createAccountSnapshotSync({ ...time.options, readStatus: () => gateway.accountStatus(), onStatus: value => shown.push(value.modelConnection.model!) });
  await sync.refresh(true);
  current = "DeepSeek-V4.1-Flash"; time.advance(15000); await sync.refresh();
  assert.deepEqual(shown, ["zeelin-qwen3.8-max", "DeepSeek-V4.1-Flash"]);
  assert.deepEqual(requests, Array(2).fill({ url: "/api/account/status", method: "GET" }));
  sync.dispose();
});

test("focus, visibility and SSE recovery coalesce, and repeated live snapshots do not poll", async () => {
  const time = clock(); let calls = 0;
  const sync = createAccountSnapshotSync({ ...time.options, readStatus: async () => { calls++; return status("new"); }, onStatus: () => {} });
  const events = lifecycle(sync);
  await sync.refresh(true);
  time.advance(100);
  events.focus(); events.visibility(true); events.connection("reconnecting"); events.connection("live");
  events.connection("live"); events.connection("live");
  assert.equal(calls, 1); assert.equal(time.pending(), 1);
  time.advance(900); await flush(); assert.equal(calls, 2);
  time.advance(2000); events.connection("live"); await flush(); assert.equal(calls, 2);
  events.connection("reconnecting"); events.connection("live"); await flush(); assert.equal(calls, 3);
  events.unbind(); sync.dispose();
});

test("hidden-page visibility and focus do not refresh, becoming visible refreshes once", async () => {
  let calls = 0;
  const sync = createAccountSnapshotSync({ readStatus: async () => { calls++; return status("current"); }, onStatus: () => {} });
  const events = lifecycle(sync);
  events.visibility(false); events.focus(); await flush(); assert.equal(calls, 0);
  events.visibility(true); events.focus(); await flush(); assert.equal(calls, 1);
  events.unbind(); sync.dispose();
});

test("a slow pre-connect GET cannot overwrite the callback or clear a newer in-flight GET", async () => {
  const time = clock(), old = deferred<MembershipStatus>(), fresh = deferred<MembershipStatus>(), shown: string[] = [];
  let calls = 0;
  const sync = createAccountSnapshotSync({ ...time.options, readStatus: () => ++calls === 1 ? old.promise : fresh.promise, onStatus: value => shown.push(value.modelConnection.model!) });
  const first = sync.refresh(true); await flush(); assert.equal(calls, 1);
  sync.accept(status("DeepSeek-after-connect"));
  const second = sync.refresh(true); await flush(); assert.equal(calls, 2);
  old.resolve(status("Qwen-before-connect")); await first;
  assert.equal(sync.refresh(true), second); assert.equal(calls, 2);
  assert.deepEqual(shown, ["DeepSeek-after-connect"]);
  fresh.resolve(status("DeepSeek-current")); await second;
  assert.deepEqual(shown, ["DeepSeek-after-connect", "DeepSeek-current"]);
  sync.dispose();
});

test("an old rejected GET cannot mark the post-connect account unavailable", async () => {
  const old = deferred<MembershipStatus>(); let unavailable = 0;
  const shown: string[] = [];
  const sync = createAccountSnapshotSync({ readStatus: () => old.promise, onStatus: value => shown.push(value.modelConnection.model!), onUnavailable: () => unavailable++ });
  const request = sync.refresh(true); await flush(); sync.accept(status("new"));
  old.reject(new Error("old transport failure")); await request;
  assert.equal(unavailable, 0); assert.deepEqual(shown, ["new"]); sync.dispose();
});

test("manual refresh bypasses throttle and consumes a queued refresh without an extra GET", async () => {
  const time = clock(); let calls = 0;
  const sync = createAccountSnapshotSync({ ...time.options, readStatus: async () => { calls++; return status("current"); }, onStatus: () => {} });
  await sync.refresh(); time.advance(100); await sync.refresh(); assert.equal(time.pending(), 1);
  await sync.refresh(true); assert.equal(calls, 2); assert.equal(time.pending(), 0);
  time.advance(1000); await flush(); assert.equal(calls, 2); sync.dispose();
});

test("current GET errors are reported, and a later successful GET restores the current snapshot", async () => {
  let calls = 0, unavailable = false;
  const shown: string[] = [];
  const sync = createAccountSnapshotSync({ readStatus: async () => {
    if (++calls === 1) throw new Error("offline"); return status("restored");
  }, onUnavailable: () => { unavailable = true; }, onStatus: value => { unavailable = false; shown.push(value.modelConnection.model!); } });
  await sync.refresh(true); assert.equal(unavailable, true);
  await sync.refresh(true); assert.equal(unavailable, false); assert.deepEqual(shown, ["restored"]); sync.dispose();
});

test("unmount removes listeners and queued refreshes, and ignores pending results across StrictMode remount", async () => {
  const time = clock(), old = deferred<MembershipStatus>(); let oldCalls = 0;
  const shown: string[] = [];
  const first = createAccountSnapshotSync({ ...time.options, readStatus: () => { oldCalls++; return old.promise; }, onStatus: value => shown.push(value.modelConnection.model!) });
  const events = lifecycle(first), pending = first.refresh(true); await flush();
  events.unbind(); first.dispose();
  events.focus(); events.visibility(true); events.connection("live");
  const second = createAccountSnapshotSync({ ...time.options, readStatus: async () => status("remounted"), onStatus: value => shown.push(value.modelConnection.model!) });
  await second.refresh(true); old.resolve(status("unmounted-old")); await pending;
  assert.equal(oldCalls, 1); assert.deepEqual(shown, ["remounted"]);
  await second.refresh(); assert.equal(time.pending(), 1); second.dispose();
  assert.equal(time.pending(), 0); time.advance(2000); await flush();
  assert.deepEqual(shown, ["remounted"]);
});
