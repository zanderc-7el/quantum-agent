import test from "node:test";
import assert from "node:assert/strict";
import { authenticate, clearSession, DEMO_ACCOUNTS, getUserById, readSession, saveSession, SESSION_KEY, SESSION_TTL_MS } from "../src/lib/auth.ts";
import type { SessionStorage, User } from "../src/lib/auth.ts";
import { canApproveWorkflow, canChangePolicy, canCreateWorkflow, canExportWorkspace, canImportWorkspace, canManageTeam, canOperateWorkflow, canPerformAction, canResetWorkspace, canViewWorkflow } from "../src/lib/access.ts";
import type { AppState, CreateWorkflowInput, Workflow, WorkflowAction } from "../src/types.ts";
import { applyAction, createInitialState, createWorkflow, isValidWorkflow, loadState, STORAGE_KEY } from "../src/lib/simulator.ts";
import { createRoleGateway } from "../src/lib/roleGateway.ts";
import { createMockGateway } from "../src/lib/gateway.ts";
import type { MockWorkflowGateway, WorkflowGateway } from "../src/lib/gateway.ts";
import { getWorkflowStorageStatus, persistWorkflowState, readWorkflowStorageBytes, subscribeWorkflowStorage } from "../src/lib/workflowStorage.ts";

function memoryStorage(initial?: string): SessionStorage & { values: Map<string, string> } {
  const values = new Map<string, string>();
  if (initial !== undefined) values.set(SESSION_KEY, initial);
  return {
    values,
    getItem: (key) => values.get(key) ?? null,
    setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); },
  };
}
const leader = getUserById("leader")!;
const operator = getUserById("operator")!;
const now = 1_800_000_000_000;
const own = (): Workflow => ({ ...createInitialState().workflows[0], ownerId: "operator", status: "running" });

test("only the published exact demo credentials authenticate, without returning passwords", () => {
  for (const account of DEMO_ACCOUNTS) {
    const user = authenticate(` ${account.username} `, account.password);
    assert.ok(user);
    assert.deepEqual(Object.keys(user).sort(), ["displayName", "id", "role", "username"]);
    assert.equal(user.role, account.role);
    assert.equal(authenticate(account.username, account.password + " "), null);
    assert.equal(authenticate(account.username.toUpperCase(), account.password), null);
  }
  assert.equal(authenticate("leader", "Operator123!"), null);
  assert.equal(authenticate("operator", "Leader123!"), null);
  assert.equal(authenticate("", ""), null);
  assert.equal(authenticate("unknown", "Leader123!"), null);
});

test("account directory and returned users cannot mutate later logins", () => {
  assert.ok(Object.isFrozen(DEMO_ACCOUNTS));
  assert.ok(DEMO_ACCOUNTS.every(Object.isFrozen));
  const first = authenticate("operator", "Operator123!")!;
  first.role = "leader";
  first.displayName = "Forged administrator";
  assert.equal(authenticate("operator", "Operator123!")?.role, "operator");
  assert.equal(canManageTeam(first), false);
});

test("session serialization holds only account ID and login time", () => {
  const storage = memoryStorage();
  assert.equal(saveSession(operator, now, storage), true);
  assert.deepEqual(JSON.parse(storage.values.get(SESSION_KEY)!), { userId: "operator", loggedInAt: now });
  assert.deepEqual(readSession(now + 100, storage), operator);
  assert.equal(storage.values.size, 1);
  assert.equal(storage.values.get(SESSION_KEY)!.includes("password"), false);
  assert.equal(storage.values.get(SESSION_KEY)!.includes("role"), false);
});

test("eight-hour validity is strict and reading never slides the login time", () => {
  const storage = memoryStorage();
  assert.equal(saveSession(leader, now, storage), true);
  const original = storage.values.get(SESSION_KEY);
  assert.deepEqual(readSession(now + SESSION_TTL_MS - 1, storage), leader);
  assert.equal(storage.values.get(SESSION_KEY), original);
  assert.equal(readSession(now + SESSION_TTL_MS, storage), null);
  assert.equal(storage.values.has(SESSION_KEY), false);
  assert.equal(readSession(now + 1, storage), null);
});

test("corrupted, extended, unknown, future and nonfinite sessions are removed", () => {
  const bad = [
    "{", "null", "[]", "false", "x".repeat(1025),
    JSON.stringify({ userId: "unknown", loggedInAt: now }),
    JSON.stringify({ userId: "operator", loggedInAt: now, role: "leader" }),
    JSON.stringify({ userId: "leader", loggedInAt: now, password: "Leader123!" }),
    JSON.stringify({ userId: "leader", loggedInAt: now + 1 }),
    JSON.stringify({ userId: "leader", loggedInAt: -1 }),
    JSON.stringify({ userId: "leader", loggedInAt: now + 0.5 }),
    JSON.stringify({ userId: "leader", loggedInAt: String(now) }),
    '{"userId":"leader","loggedInAt":1e999}',
  ];
  for (const raw of bad) {
    const storage = memoryStorage(raw);
    assert.equal(readSession(now, storage), null, raw);
    assert.equal(storage.values.has(SESSION_KEY), false, raw);
  }
});

test("logout removes the session; only a successful new save may restore it", () => {
  const storage = memoryStorage();
  assert.equal(saveSession(leader, now, storage), true);
  assert.equal(clearSession(storage), true);
  assert.equal(storage.values.has(SESSION_KEY), false);
  assert.equal(readSession(now, storage), null);
  assert.equal(clearSession(storage), true);
  assert.equal(saveSession(operator, now + 1, storage), true);
  assert.deepEqual(readSession(now + 1, storage), operator);
});

test("denied storage never authenticates a stale session after logout", () => {
  const denied: SessionStorage = {
    getItem: () => JSON.stringify({ userId: "leader", loggedInAt: now }),
    setItem: () => { throw new Error("blocked"); },
    removeItem: () => { throw new Error("blocked"); },
  };
  assert.equal(clearSession(denied), false);
  assert.equal(readSession(now, denied), null);
  assert.equal(saveSession(operator, now, denied), false);
  assert.equal(readSession(now, denied), null);
  const unreadable: SessionStorage = { ...denied, getItem: () => { throw new Error("blocked"); } };
  assert.equal(readSession(now, unreadable), null);
});

test("forged users and invalid time cannot be persisted", () => {
  const storage = memoryStorage();
  for (const user of [{ ...operator, role: "leader" as const }, { ...leader, id: "unregistered" }, { ...leader, username: "operator" }])
    assert.equal(saveSession(user, now, storage), false);
  for (const invalid of [-1, NaN, Infinity, now + 0.5])
    assert.equal(saveSession(leader, invalid, storage), false);
  assert.equal(storage.values.size, 0);
});

const remoteOperator: User = {
  id: "user-directory-7",
  username: "alice",
  displayName: "Alice · 操作员",
  role: "operator",
  source: "remote",
};

test("remote identities round-trip through the session store", () => {
  const storage = memoryStorage();
  assert.equal(saveSession(remoteOperator, now, storage), true);
  assert.deepEqual(JSON.parse(storage.values.get(SESSION_KEY)!), {
    userId: "user-directory-7",
    loggedInAt: now,
    account: { username: "alice", displayName: "Alice · 操作员", role: "operator" },
  });
  assert.equal(storage.values.get(SESSION_KEY)!.includes("source"), false);
  assert.deepEqual(readSession(now + 1, storage), remoteOperator);
  assert.equal(readSession(now + SESSION_TTL_MS, storage), null);
});

test("a malformed stored remote account invalidates the session", () => {
  const bad = [
    { username: "", displayName: "Alice", role: "operator" },
    { username: "alice", displayName: "Alice", role: "guest" },
    { username: "alice", displayName: "Alice" },
    { username: "alice", displayName: "Alice", role: "operator", extra: 1 },
  ];
  for (const account of bad) {
    const storage = memoryStorage(
      JSON.stringify({ userId: "user-directory-7", loggedInAt: now, account }),
    );
    assert.equal(readSession(now, storage), null, JSON.stringify(account));
    assert.equal(storage.values.has(SESSION_KEY), false);
  }
});

test("remote identities are validated by shape, and only remote shape grants access", () => {
  assert.equal(canCreateWorkflow(remoteOperator), true);
  assert.equal(canManageTeam(remoteOperator), false);
  const remoteLeader: User = { ...remoteOperator, role: "leader" };
  assert.equal(canManageTeam(remoteLeader), true);
  // A user cannot be turned into a directory member by relabelling the source.
  assert.equal(canManageTeam({ ...remoteLeader, source: "demo" }), false);
  // The remote shim must be structurally complete.
  assert.equal(canCreateWorkflow({ ...remoteLeader, displayName: 7 } as unknown as User), false);
  const storage = memoryStorage();
  assert.equal(saveSession({ ...remoteLeader, username: "" }, now, storage), false);
});

test("both accounts may create and read/export team data; guests may not", () => {
  for (const user of [leader, operator]) {
    assert.equal(canCreateWorkflow(user), true);
    assert.equal(canViewWorkflow(user, own()), true);
    assert.equal(canExportWorkspace(user), true);
  }
  for (const user of [null, undefined, { ...operator, role: "leader" as const }]) {
    assert.equal(canCreateWorkflow(user), false);
    assert.equal(canViewWorkflow(user, own()), false);
    assert.equal(canExportWorkspace(user), false);
    assert.equal(canOperateWorkflow(user, own()), false);
  }
});

test("operators act only on stable ownerId, never display names or legacy owner strings", () => {
  assert.equal(canOperateWorkflow(operator, own()), true);
  assert.equal(canOperateWorkflow(operator, { ...own(), ownerId: "leader" }), false);
  assert.equal(canOperateWorkflow(operator, { ...own(), ownerId: undefined, owner: operator.displayName }), false);
  assert.equal(canOperateWorkflow(operator, { ...own(), ownerId: undefined, owner: "operator" }), false);
  assert.equal(canOperateWorkflow(operator, null), false);
  assert.equal(canOperateWorkflow(leader, { ...own(), ownerId: undefined }), true);
  assert.equal(canOperateWorkflow(leader, { ...own(), ownerId: "other-team-member" }), true);
});

test("team management, approval, policy, import and reset remain leader-only", () => {
  for (const permission of [canManageTeam, canApproveWorkflow, canChangePolicy, canImportWorkspace, canResetWorkspace]) {
    assert.equal(permission(leader), true);
    assert.equal(permission(operator), false);
    assert.equal(permission(null), false);
  }
});

test("approval-gate rejection is leader-only; an operator may retry their failed work", () => {
  const attention = { ...own(), status: "attention" as const };
  assert.equal(canPerformAction(operator, attention, "approve"), false);
  assert.equal(canPerformAction(operator, attention, "retry"), false);
  assert.equal(canPerformAction(leader, attention, "approve"), true);
  assert.equal(canPerformAction(leader, attention, "retry"), true);
  assert.equal(canPerformAction(operator, { ...own(), status: "failed" }, "retry"), true);
  assert.equal(canPerformAction(operator, { ...own(), ownerId: "leader", status: "failed" }, "retry"), false);
});

test("all task actions deny foreign owners and unknown actions fail closed", () => {
  const actions: WorkflowAction[] = ["start", "pause", "resume", "approve", "retry", "abort"];
  for (const action of actions) {
    assert.equal(canPerformAction(operator, { ...own(), ownerId: "leader" }, action), false);
    assert.equal(canPerformAction(null, own(), action), false);
    assert.equal(canPerformAction(leader, null, action), false);
  }
  for (const action of ["start", "pause", "resume", "abort"] as const)
    assert.equal(canPerformAction(operator, own(), action), true);
  assert.equal(canPerformAction(leader, own(), "delete" as WorkflowAction), false);
  assert.equal(canPerformAction({ ...leader, id: "operator" } as User, own(), "start"), false);
});

const input: CreateWorkflowInput = {
  experiment: "single", name: "权限验证任务", machine: "M7", target: "Q12",
  qubits: 1, type: "adaptive", policy: "autopilot", scenario: "normal",
};
const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));
async function withLocalStorage(storage: SessionStorage, run: () => Promise<void> | void) {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, value: storage });
  try { await run(); }
  finally {
    if (descriptor) Object.defineProperty(globalThis, "localStorage", descriptor);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
}

test("role gateway persists operator identity atomically and overrides autopilot on create", async () => {
  let stored: AppState = { version: 1, workflows: [] };
  const gateway = createRoleGateway(operator, undefined, {
    load: () => stored,
    save: (value) => { stored = clone(value); },
  }, () => true);
  let event: AppState | null = null;
  const off = gateway.subscribe((value) => { event = value; });
  const result = await gateway.create(input);
  assert.equal(result.policy, "threshold");
  assert.equal(result.ownerId, "operator");
  assert.equal(result.owner, operator.displayName);
  assert.equal(stored.workflows[0].ownerId, "operator");
  assert.equal((event as AppState | null)?.workflows[0].ownerId, "operator");
  assert.equal(isValidWorkflow(result), true);
  const reopened = createRoleGateway(operator, undefined, { load: () => stored, save: () => {} }, () => true);
  assert.equal((await reopened.get(result.id)).ownerId, "operator");
  assert.equal((await reopened.act(result.id, "start")).status, "running");
  off();
});

test("operator gateway rejects legacy/foreign mutation and all approval/management endpoints", async () => {
  const initial = createInitialState();
  const mine = { ...initial.workflows.find((workflow) => workflow.status === "attention")!, ownerId: operator.id };
  assert.ok(mine.id);
  initial.workflows.push({ ...mine, id: "owned-review-gate" });
  let writes = 0;
  const gateway = createRoleGateway(operator, undefined, { load: () => initial, save: () => { writes += 1; } }, () => true);
  for (const action of ["start", "pause", "resume", "approve", "retry", "abort"] as const)
    await assert.rejects(gateway.act(initial.workflows[0].id, action), /权限/);
  await assert.rejects(gateway.act("owned-review-gate", "approve"), /权限/);
  await assert.rejects(gateway.act("owned-review-gate", "retry"), /权限/);
  await assert.rejects(gateway.policy("owned-review-gate", "autopilot"), /权限/);
  await assert.rejects(gateway.importWorkflows!(JSON.stringify(initial)), /权限/);
  assert.throws(() => (gateway as MockWorkflowGateway).resetDemo(), /权限/);
  assert.equal(writes, 0);
});

test("leader gateway retains requested policy and may manage ownerless legacy tasks", async () => {
  const initial = createInitialState();
  const gateway = createRoleGateway(leader, undefined, { load: () => initial, save: () => {} }, () => true);
  const result = await gateway.create({ ...input, policy: "strict" });
  assert.equal(result.policy, "strict");
  assert.equal(result.ownerId, leader.id);
  const draft = initial.workflows.find((workflow) => workflow.status === "draft")!;
  assert.equal((await gateway.act(draft.id, "start")).status, "running");
  assert.equal((await gateway.policy(draft.id, "autopilot")).policy, "autopilot");
});

test("ownerId survives durable reload, while legacy records stay ownerless", async () => {
  const storage = memoryStorage();
  await withLocalStorage(storage, () => {
    const original = createInitialState();
    const mine = { ...createWorkflow(input), ownerId: operator.id, owner: operator.displayName };
    const state = { ...original, workflows: [mine, ...original.workflows] };
    persistWorkflowState(state);
    const loaded = loadState();
    assert.equal(loaded.workflows[0].ownerId, operator.id);
    assert.equal(loaded.workflows[1].ownerId, undefined);
    assert.deepEqual(loaded, state);
    assert.equal(getWorkflowStorageStatus().status, "saved");
    assert.equal(readWorkflowStorageBytes(), new TextEncoder().encode(JSON.stringify(state)).byteLength);
    for (const ownerId of ["", "operator user", 12, null])
      assert.equal(isValidWorkflow({ ...mine, ownerId }), false);
  });
});

test("durable storage rejection preserves disk, snapshot and subscribers for every mutation", async () => {
  const draft = createWorkflow(input);
  const running = applyAction({ ...createWorkflow(input), id: "run-for-atomicity" }, "start");
  const state: AppState = { version: 1, workflows: [draft, running] };
  const persisted = JSON.stringify(state);
  const storage = memoryStorage();
  storage.values.set(STORAGE_KEY, persisted);
  storage.setItem = () => { throw new Error("QuotaExceededError"); };
  await withLocalStorage(storage, async () => {
    const gateway = createMockGateway({ load: () => state });
    const before = gateway.snapshot();
    let snapshots = 0;
    const off = gateway.subscribe(() => { snapshots += 1; });
    const unchanged = () => {
      assert.deepEqual(gateway.snapshot(), before);
      assert.equal(storage.values.get(STORAGE_KEY), persisted);
      assert.equal(snapshots, 0);
      assert.equal(getWorkflowStorageStatus().status, "error");
      assert.match(getWorkflowStorageStatus().error!, /未能保存/);
    };
    await assert.rejects(gateway.create(input), /未能保存/); unchanged();
    await assert.rejects(gateway.act(draft.id, "start"), /未能保存/); unchanged();
    await assert.rejects(gateway.policy(draft.id, "strict"), /未能保存/); unchanged();
    const external = { ...createWorkflow(input), id: "external-import" };
    await assert.rejects(gateway.importWorkflows(JSON.stringify({ version: 1, workflows: [external] })), /未能保存/); unchanged();
    assert.throws(() => gateway.advance(1), /未能保存/); unchanged();
    assert.throws(() => gateway.resetDemo(), /未能保存/); unchanged();
    off();
  });
});

test("durable status becomes saved after recovery and unsubscribe detaches listeners", async () => {
  const storage = memoryStorage();
  await withLocalStorage(storage, () => {
    const observed: string[] = [];
    const off = subscribeWorkflowStorage(() => observed.push(getWorkflowStorageStatus().status));
    const state: AppState = { version: 1, workflows: [createWorkflow(input)] };
    persistWorkflowState(state);
    assert.deepEqual(observed, ["saved"]);
    assert.ok(getWorkflowStorageStatus().savedAt);
    off();
    persistWorkflowState(state);
    assert.deepEqual(observed, ["saved"]);
  });
});

test("expired sessions reject every role gateway mutation before calling the adapter", async () => {
  let writes = 0;
  const gateway = createRoleGateway(leader, undefined, { load: createInitialState, save: () => { writes += 1; } }, () => false);
  const id = (await gateway.list())[0].id;
  await assert.rejects(gateway.create(input), /登录已过期/);
  await assert.rejects(gateway.act(id, "pause"), /登录已过期/);
  await assert.rejects(gateway.policy(id, "strict"), /登录已过期/);
  await assert.rejects(gateway.importWorkflows!("{}"), /登录已过期/);
  assert.throws(() => (gateway as MockWorkflowGateway).advance(1), /登录已过期/);
  assert.throws(() => (gateway as MockWorkflowGateway).resetDemo(), /登录已过期/);
  assert.equal(writes, 0);
});

test("logout or expiry during a slow ownership lookup prevents the subsequent mutation", async () => {
  for (const mutation of ["act", "policy"] as const) {
    let active = true;
    let completeLookup: ((value: Workflow) => void) | undefined;
    let writes = 0;
    const adapter: WorkflowGateway = {
      mode: "remote",
      list: async () => [own()],
      get: async () => new Promise<Workflow>((resolve) => { completeLookup = resolve; }),
      create: async () => { writes += 1; return own(); },
      act: async () => { writes += 1; return own(); },
      policy: async () => { writes += 1; return own(); },
      subscribe: () => () => {},
    };
    const gateway = createRoleGateway(leader, adapter, undefined, () => active);
    const pending = mutation === "act" ? gateway.act(own().id, "pause") : gateway.policy(own().id, "strict");
    assert.ok(completeLookup);
    active = false;
    completeLookup(own());
    await assert.rejects(pending, /登录已过期/);
    assert.equal(writes, 0);
  }
});

test("unregistered identities cannot create through the role gateway", async () => {
  let writes = 0;
  for (const fake of [{ ...leader, id: "unregistered" }, { ...operator, role: "leader" as const }]) {
    const gateway = createRoleGateway(fake, undefined, { load: createInitialState, save: () => { writes += 1; } }, () => true);
    await assert.rejects(gateway.create(input), /权限/);
  }
  assert.equal(writes, 0);
});
