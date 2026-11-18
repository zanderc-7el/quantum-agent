import test from "node:test";
import assert from "node:assert/strict";
import type { AppState, Workflow } from "../src/types.ts";
import {
  applyAction,
  createInitialState,
  createWorkflow,
  isValidWorkflow,
  loadState,
  MAX_WORKFLOWS,
  MAX_WORKSPACE_BYTES,
  saveState,
  STORAGE_KEY,
  tickWorkflow,
} from "../src/lib/simulator.ts";
import { createMockGateway } from "../src/lib/gateway.ts";
import {
  exportWorkspace,
  mergeWorkspaceImport,
  previewWorkspaceImport,
  WorkspaceImportError,
} from "../src/lib/workspaceTransfer.ts";

function fresh(): Workflow {
  return createWorkflow({
    name: "导入验收",
    machine: "M7",
    experiment: "single",
    qubits: 1,
    target: "Q12",
    type: "adaptive",
    policy: "threshold",
    scenario: "normal",
  });
}
function stateOf(...workflows: Workflow[]): AppState {
  return { version: 1, workflows };
}
function clone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}
function withLargeHistory(workflow: Workflow, events: number): Workflow {
  return {
    ...workflow,
    events: Array.from({ length: events }, () => ({
      time: "2026-10-23T02:00:00.000Z",
      kind: "info" as const,
      message: "x".repeat(5000),
    })),
  };
}

function nearCapacity(workflow: Workflow, remaining = 20): AppState {
  const result = stateOf(withLargeHistory(workflow, 1000));
  const target = MAX_WORKSPACE_BYTES - remaining;
  const emptyEvent = {
    time: "2026-10-23T02:00:00.000Z",
    kind: "info" as const,
    message: "",
  };
  while (true) {
    const bytes = Buffer.byteLength(JSON.stringify(result));
    const available =
      target - bytes - Buffer.byteLength(JSON.stringify(emptyEvent)) - 1;
    if (available < 0) break;
    result.workflows[0].events.push({
      ...emptyEvent,
      message: "x".repeat(Math.min(5000, available)),
    });
    if (available <= 5000) break;
  }
  return result;
}

test("version-1 export is compatible, read-only and previews counts without executing tasks", () => {
  const original = createInitialState();
  const before = JSON.stringify(original);
  const json = exportWorkspace(original);
  const envelope = JSON.parse(json);
  assert.equal(envelope.version, 1);
  assert.equal(envelope.dataSource, "simulated");
  assert.ok(Number.isFinite(Date.parse(envelope.exportedAt)));
  assert.deepEqual(envelope.workflows, original.workflows);
  assert.equal(JSON.stringify(original), before);
  const preview = previewWorkspaceImport(json);
  assert.equal(preview.valid, true);
  assert.equal(preview.total, 8);
  assert.equal(preview.importable, 8);
  assert.equal(preview.runningToPause, 1);
  assert.equal(preview.single, 6);
  assert.equal(preview.two, 2);
  assert.equal(preview.statusCounts.attention, 1);
  assert.equal(previewWorkspaceImport(JSON.stringify(original)).valid, true);
  assert.equal(previewWorkspaceImport("\uFEFF" + json).valid, true);
});

test("merge preserves original tasks, assigns new IDs, pauses imported runs and is idempotent", () => {
  const current = stateOf(fresh());
  const running = tickWorkflow(applyAction(fresh(), "start"), 1.5);
  const queued = { ...fresh(), status: "queued" as const };
  const input = exportWorkspace(stateOf(running, queued));
  const originalJson = JSON.stringify(current);
  const merged = mergeWorkspaceImport(current, input);
  assert.equal(merged.result.imported, 2);
  assert.equal(merged.result.paused, 1);
  assert.equal(JSON.stringify(current), originalJson);
  assert.deepEqual(merged.state.workflows[2], current.workflows[0]);
  const imported = merged.state.workflows[0];
  assert.notEqual(imported.id, running.id);
  assert.equal(imported.importSourceId, running.id);
  assert.equal(imported.status, "paused");
  assert.equal(imported.elapsed, 1.5);
  assert.equal(tickWorkflow(imported, 99), imported);
  assert.equal(merged.state.workflows[1].status, "queued");
  assert.ok(merged.state.workflows.every(isValidWorkflow));
  const second = mergeWorkspaceImport(merged.state, input);
  assert.equal(second.state, merged.state);
  assert.equal(second.result.imported, 0);
  assert.equal(second.result.skipped, 2);
  const reexport = exportWorkspace(merged.state);
  const third = mergeWorkspaceImport(second.state, reexport);
  assert.equal(third.result.imported, 0);
  assert.equal(third.result.skipped, 3);
});

test("existing original IDs are skipped and malformed import never falls back to seed data", () => {
  const current = createInitialState();
  const same = mergeWorkspaceImport(current, exportWorkspace(current));
  assert.equal(same.state, current);
  assert.equal(same.result.skipped, 8);
  for (const text of [
    "{",
    "[]",
    JSON.stringify({ version: 2, workflows: [] }),
    JSON.stringify({ version: 1 }),
    JSON.stringify({ version: 1, workflows: [], extra: true }),
    '{"version":1,"workflows":[],"__proto__":{"polluted":true}}',
  ]) {
    const preview = previewWorkspaceImport(text, current.workflows);
    assert.equal(preview.valid, false);
    assert.ok(preview.errors.length > 0);
    assert.throws(
      () => mergeWorkspaceImport(current, text),
      WorkspaceImportError,
    );
  }
  assert.equal(({} as Record<string, unknown>).polluted, undefined);
});

test("invalid fields, node order, target, time, progress and lifecycle semantics reject the entire import", () => {
  const mutations: ((workflow: Workflow) => void)[] = [
    (workflow) => {
      (workflow as unknown as Record<string, unknown>).extra = "unexpected";
    },
    (workflow) => {
      (workflow.nodes[0] as unknown as Record<string, unknown>).extra =
        "unexpected";
    },
    (workflow) => {
      workflow.qubits = 3;
    },
    (workflow) => {
      workflow.nodes[0].qubits = "Q13";
    },
    (workflow) => {
      workflow.target = "Q12-Q13";
    },
    (workflow) => {
      workflow.createdAt = "not a date";
    },
    (workflow) => {
      workflow.elapsed = 20;
    },
    (workflow) => {
      workflow.iterations = 1;
    },
    (workflow) => {
      workflow.nodes[0].progress = 20;
      workflow.progress = 2;
    },
    (workflow) => {
      workflow.nodes[0].attempt = 1;
    },
    (workflow) => {
      workflow.nodes.reverse();
    },
    (workflow) => {
      workflow.status = "running";
      workflow.nodes[1].status = "running";
      workflow.nodes[1].attempt = 1;
    },
    (workflow) => {
      workflow.status = "completed";
    },
    (workflow) => {
      workflow.events[0].time = "invalid";
    },
  ];
  for (const mutate of mutations) {
    const invalid = fresh();
    mutate(invalid);
    assert.equal(isValidWorkflow(invalid), false);
    const json = JSON.stringify(stateOf(fresh(), invalid));
    assert.equal(previewWorkspaceImport(json).valid, false);
    assert.throws(
      () => mergeWorkspaceImport(stateOf(), json),
      WorkspaceImportError,
    );
  }
  const duplicate = fresh();
  assert.equal(
    previewWorkspaceImport(JSON.stringify(stateOf(duplicate, duplicate))).valid,
    false,
  );
});

test("UTF-8 file size is checked at exactly 5MB and rejects one extra byte", () => {
  const base = JSON.stringify(stateOf());
  const exact =
    " ".repeat(MAX_WORKSPACE_BYTES - Buffer.byteLength(base)) + base;
  assert.equal(previewWorkspaceImport(exact).valid, true);
  assert.equal(previewWorkspaceImport(exact + " ").valid, false);
  const unicode = "中".repeat(Math.floor(MAX_WORKSPACE_BYTES / 3) + 1);
  assert.ok(
    previewWorkspaceImport(unicode).errors.some((error) =>
      error.includes("5MB"),
    ),
  );
});

test("file and merged task counts enforce 200 while duplicate imports remain allowed at capacity", () => {
  const full = stateOf(...Array.from({ length: MAX_WORKFLOWS }, fresh));
  assert.equal(previewWorkspaceImport(exportWorkspace(full)).valid, true);
  assert.equal(
    previewWorkspaceImport(JSON.stringify(stateOf(...full.workflows, fresh())))
      .valid,
    false,
  );
  assert.equal(
    previewWorkspaceImport(exportWorkspace(stateOf(fresh())), full.workflows)
      .valid,
    false,
  );
  assert.equal(
    mergeWorkspaceImport(full, exportWorkspace(full)).result.skipped,
    MAX_WORKFLOWS,
  );
});

test("preview and merge reject a combined payload above the same storage limit", () => {
  const current = stateOf(
    withLargeHistory(fresh(), 400),
    withLargeHistory(fresh(), 400),
  );
  const incoming = exportWorkspace(stateOf(withLargeHistory(fresh(), 400)));
  assert.equal(previewWorkspaceImport(incoming).valid, true);
  const preview = previewWorkspaceImport(incoming, current.workflows);
  assert.equal(preview.valid, false);
  assert.ok(preview.errors.some((error) => error.includes("5MB")));
  assert.throws(() => mergeWorkspaceImport(current, incoming), /5MB/);
});

test("gateway import is atomic, publishes once, saves provenance and skips repeat imports without writes", async () => {
  let saves = 0;
  let notifications = 0;
  const original = stateOf(fresh());
  const gateway = createMockGateway({
    load: () => original,
    save: () => {
      saves += 1;
    },
  });
  gateway.subscribe(() => {
    notifications += 1;
  });
  await assert.rejects(gateway.importWorkflows("{"), WorkspaceImportError);
  assert.deepEqual(gateway.snapshot(), original);
  assert.equal(saves, 0);
  const json = exportWorkspace(stateOf(applyAction(fresh(), "start")));
  const result = await gateway.importWorkflows(json);
  assert.equal(result.imported, 1);
  assert.equal((await gateway.get(result.importedIds[0])).status, "paused");
  assert.equal(saves, 1);
  assert.equal(notifications, 1);
  await gateway.importWorkflows(json);
  assert.equal(saves, 1);
  assert.equal(notifications, 1);
});

test("save size errors are explicit and gateway refuses mutation atomically when persistence fails", async () => {
  const oversized = stateOf(withLargeHistory(fresh(), 1100));
  assert.throws(() => saveState(oversized), /5MB/);
  const initial = stateOf(fresh());
  const gateway = createMockGateway({
    load: () => initial,
    save: () => {
      throw new Error("存储失败");
    },
  });
  await assert.rejects(
    gateway.act(initial.workflows[0].id, "start"),
    /存储失败/,
  );
  assert.deepEqual(gateway.snapshot(), initial);
});

test("create and automatic advance cannot commit state beyond the persisted 5MB limit", async () => {
  const initial = nearCapacity(
    tickWorkflow(applyAction(fresh(), "start"), 3.96),
  );
  assert.ok(isValidWorkflow(initial.workflows[0]));
  assert.ok(Buffer.byteLength(JSON.stringify(initial)) <= MAX_WORKSPACE_BYTES);
  const backup = exportWorkspace(initial);
  assert.ok(Buffer.byteLength(backup) <= MAX_WORKSPACE_BYTES);
  assert.deepEqual(JSON.parse(backup).workflows, initial.workflows);
  let writes = 0;
  const gateway = createMockGateway({
    load: () => initial,
    save: () => {
      writes += 1;
    },
  });
  const input = {
    name: "容量边界",
    machine: "M7",
    experiment: "single" as const,
    qubits: 1,
    target: "Q12",
    type: "adaptive" as const,
    policy: "threshold" as const,
    scenario: "normal" as const,
  };
  await assert.rejects(gateway.create(input), /5MB/);
  assert.throws(() => gateway.advance(1), /5MB/);
  assert.deepEqual(gateway.snapshot(), initial);
  assert.equal(writes, 0);
});

test("imported provenance and an explicitly empty workspace survive local storage roundtrips", () => {
  const descriptor = Object.getOwnPropertyDescriptor(
    globalThis,
    "localStorage",
  );
  const values = new Map<string, string>();
  try {
    Object.defineProperty(globalThis, "localStorage", {
      configurable: true,
      value: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value),
      },
    });
    const merged = mergeWorkspaceImport(
      stateOf(),
      exportWorkspace(stateOf(fresh())),
    ).state;
    saveState(merged);
    assert.deepEqual(loadState(), merged);
    saveState(stateOf());
    assert.deepEqual(loadState(), stateOf());
    assert.ok(values.has(STORAGE_KEY));
  } finally {
    if (descriptor)
      Object.defineProperty(globalThis, "localStorage", descriptor);
    else Reflect.deleteProperty(globalThis, "localStorage");
  }
});
