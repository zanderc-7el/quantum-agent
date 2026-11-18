import type {
  AppState,
  CreateWorkflowInput,
  Policy,
  Workflow,
  WorkflowAction,
} from "../types.ts";
import {
  applyAction,
  assertWorkspaceSize,
  createInitialState,
  createWorkflow,
  loadState,
  MAX_WORKFLOWS,
  STORAGE_KEY,
  setPolicy,
  tickWorkflow,
} from "./simulator.ts";
import { mergeWorkspaceImport } from "./workspaceTransfer.ts";
import type { WorkspaceImportResult } from "./workspaceTransfer.ts";
import { persistWorkflowState } from "./workflowStorage.ts";

/** Proposed frontend adapter contract; these methods are not a claim that server endpoints exist. */
export interface WorkflowGateway {
  readonly mode: "mock" | "remote";
  list(): Promise<Workflow[]>;
  get(id: string): Promise<Workflow>;
  create(input: CreateWorkflowInput): Promise<Workflow>;
  act(id: string, action: WorkflowAction): Promise<Workflow>;
  policy(id: string, policy: Policy): Promise<Workflow>;
  subscribe(listener: (state: AppState) => void): () => void;
  importWorkflows?(json: string): Promise<WorkspaceImportResult>;
}

export interface MockGatewayDependencies {
  load?: () => AppState;
  save?: (state: AppState) => void;
  actor?: { id: string; displayName: string };
}

export interface MockWorkflowGateway extends WorkflowGateway {
  readonly mode: "mock";
  /** Explicit clock injection keeps simulation deterministic and avoids hidden background timers. */
  advance(seconds?: number): void;
  snapshot(): AppState;
  resetDemo(): void;
  importWorkflows(json: string): Promise<WorkspaceImportResult>;
}

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function createMockGateway(
  dependencies: MockGatewayDependencies = {},
): MockWorkflowGateway {
  let state = copy((dependencies.load ?? loadState)());
  const persist = dependencies.save ?? persistWorkflowState;
  const listeners = new Set<(state: AppState) => void>();
  const snapshot = () => copy(state);
  const synchronize = () => {
    if (dependencies.load) return;
    try {
      if (!globalThis.localStorage?.getItem(STORAGE_KEY)) return;
      const saved = loadState();
      if (JSON.stringify(saved) !== JSON.stringify(state)) {
        state = saved;
        for (const listener of listeners) listener(snapshot());
      }
    } catch { /* Preserve the active snapshot when storage access is denied. */ }
  };
  const commit = (next: AppState) => {
    assertWorkspaceSize(next);
    persist(copy(next));
    state = next;
    for (const listener of listeners) listener(snapshot());
  };
  const find = (id: string) => {
    synchronize();
    const workflow = state.workflows.find((item) => item.id === id);
    if (!workflow) throw new Error(`未找到工作流 ${id}，请刷新列表后重试。`);
    return workflow;
  };
  const update = (id: string, mutate: (workflow: Workflow) => Workflow) => {
    const previous = find(id);
    const workflow = mutate(previous);
    if (workflow !== previous) {
      commit({
        ...state,
        workflows: state.workflows.map((item) =>
          item.id === id ? workflow : item,
        ),
      });
    }
    return copy(workflow);
  };
  return {
    mode: "mock",
    snapshot,
    resetDemo() {
      commit(createInitialState());
    },
    async list() {
      synchronize();
      return copy(state.workflows);
    },
    async get(id) {
      return copy(find(id));
    },
    async create(input) {
      synchronize();
      if (state.workflows.length >= MAX_WORKFLOWS)
        throw new Error(
          `演示工作区最多保存 ${MAX_WORKFLOWS} 个任务，已达到上限。请先导出需要保留的记录，再使用重置演示数据功能。`,
        );
      const workflow = createWorkflow(input);
      if (dependencies.actor) {
        workflow.ownerId = dependencies.actor.id;
        workflow.owner = dependencies.actor.displayName;
      }
      commit({ ...state, workflows: [workflow, ...state.workflows] });
      return copy(workflow);
    },
    async act(id, action) {
      return update(id, (workflow) => applyAction(workflow, action));
    },
    async policy(id, policy) {
      return update(id, (workflow) => setPolicy(workflow, policy));
    },
    async importWorkflows(json) {
      synchronize();
      const merged = mergeWorkspaceImport(state, json);
      if (merged.state !== state) {
        commit(merged.state);
      }
      return copy(merged.result);
    },
    subscribe(listener) {
      listeners.add(listener);
      const onStorage = (event: StorageEvent) => { if (event.key === STORAGE_KEY) synchronize(); };
      if (!dependencies.load && typeof window !== "undefined") window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(listener);
        if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
      };
    },
    advance(seconds = 1) {
      synchronize();
      const workflows = state.workflows.map((workflow) =>
        tickWorkflow(workflow, seconds),
      );
      if (
        workflows.some((workflow, index) => workflow !== state.workflows[index])
      ) {
        commit({ ...state, workflows });
      }
    },
  };
}

/** Supply a remote implementation after the backend contract is agreed. Default always stays mock. */
export function createGateway(adapter?: WorkflowGateway): WorkflowGateway {
  return adapter ?? createMockGateway();
}
