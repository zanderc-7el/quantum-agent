import type { AppState, Workflow, WorkflowStatus } from "../types.ts";
import {
  isValidWorkflow,
  MAX_WORKFLOWS,
  MAX_WORKSPACE_BYTES,
  MAX_WORKFLOW_EVENTS,
} from "./simulator.ts";

export interface WorkspaceImportPreview {
  valid: boolean;
  errors: string[];
  total: number;
  importable: number;
  skipped: number;
  runningToPause: number;
  single: number;
  two: number;
  statusCounts: Partial<Record<WorkflowStatus, number>>;
}

export interface WorkspaceImportResult {
  total: number;
  imported: number;
  skipped: number;
  paused: number;
  importedIds: string[];
}

export class WorkspaceImportError extends Error {
  readonly errors: string[];
  constructor(errors: string[]) {
    super(errors.join("；"));
    this.name = "WorkspaceImportError";
    this.errors = errors;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function byteLength(value: string): number {
  return new TextEncoder().encode(value).byteLength;
}
function sourceId(workflow: Workflow): string {
  return workflow.importSourceId ?? workflow.id;
}
function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function prepareImportedWorkflow(original: Workflow, id: string): Workflow {
  const workflow = copy(original);
  workflow.id = id;
  workflow.importSourceId = sourceId(original);
  const paused = workflow.status === "running";
  if (paused) workflow.status = "paused";
  workflow.events = [
    ...workflow.events,
    {
      time: new Date().toISOString(),
      kind: "info" as const,
      message: paused
        ? "从工作区文件导入，运行中的任务已转为暂停，等待你明确继续。"
        : "从工作区文件导入，保留原有模拟执行记录。",
    },
  ].slice(-MAX_WORKFLOW_EVENTS);
  return workflow;
}

function validateState(state: unknown): string[] {
  if (!isRecord(state)) return ["文件根节点必须是工作区JSON对象。"];
  const errors: string[] = [];
  const keys = new Set(["version", "workflows", "dataSource", "exportedAt"]);
  if (Object.keys(state).some((key) => !keys.has(key)))
    errors.push("工作区包含不支持的字段，请使用本应用导出的JSON。");
  if (state.version !== 1) errors.push("工作区版本不兼容，仅支持 version: 1。");
  if (state.dataSource !== undefined && state.dataSource !== "simulated")
    errors.push("仅支持标记为 simulated 的模拟工作区记录。");
  if (
    state.exportedAt !== undefined &&
    (typeof state.exportedAt !== "string" ||
      !Number.isFinite(Date.parse(state.exportedAt)))
  )
    errors.push("导出时间格式无效。");
  if (!Array.isArray(state.workflows))
    return [...errors, "workflows 必须是任务数组。"];
  if (state.workflows.length > MAX_WORKFLOWS)
    return [...errors, `文件最多包含 ${MAX_WORKFLOWS} 个任务。`];
  const ids = new Set<string>();
  state.workflows.forEach((workflow, index) => {
    if (!isValidWorkflow(workflow)) {
      errors.push(
        `第 ${index + 1} 条任务的字段或执行状态无效，请检查实验顺序、节点状态、进度、计时与目标比特。`,
      );
      return;
    }
    if (ids.has(workflow.id)) errors.push(`第 ${index + 1} 条任务的ID重复。`);
    ids.add(workflow.id);
  });
  return errors;
}

function parseWorkspace(json: string): {
  state?: AppState;
  errors: string[];
  total: number;
} {
  if (typeof json !== "string")
    return { errors: ["请读取JSON文本后再导入。"], total: 0 };
  if (byteLength(json) > MAX_WORKSPACE_BYTES)
    return { errors: ["JSON文件超过5MB限制。"], total: 0 };
  let parsed: unknown;
  try {
    parsed = JSON.parse(json.replace(/^\uFEFF/, ""));
  } catch {
    return { errors: ["JSON格式无效，文件未被导入。"], total: 0 };
  }
  const total =
    isRecord(parsed) && Array.isArray(parsed.workflows)
      ? parsed.workflows.length
      : 0;
  const errors = validateState(parsed);
  if (errors.length) return { errors, total };
  return {
    state: { version: 1, workflows: (parsed as AppState).workflows },
    errors,
    total,
  };
}

/** A compatible version-1 JSON envelope; no execution occurs during export. */
export function exportWorkspace(state: AppState): string {
  const errors = validateState(state);
  if (errors.length) throw new WorkspaceImportError(errors);
  const envelope = {
    version: 1,
    dataSource: "simulated",
    exportedAt: new Date().toISOString(),
    workflows: state.workflows,
  };
  const pretty = JSON.stringify(envelope, null, 2);
  let json =
    byteLength(pretty) <= MAX_WORKSPACE_BYTES
      ? pretty
      : JSON.stringify(envelope);
  // A valid near-capacity workspace must remain exportable for backup.
  if (byteLength(json) > MAX_WORKSPACE_BYTES)
    json = JSON.stringify({ version: 1, workflows: state.workflows });
  if (byteLength(json) > MAX_WORKSPACE_BYTES)
    throw new WorkspaceImportError([
      "工作区超过5MB限制，请先减少记录体积后导出。",
    ]);
  return json;
}

/** Read-only preview. Invalid imports return explicit errors and never load demo seeds. */
export function previewWorkspaceImport(
  json: string,
  existingWorkflows: Workflow[] = [],
): WorkspaceImportPreview {
  const parsed = parseWorkspace(json);
  const preview: WorkspaceImportPreview = {
    valid: false,
    errors: [...parsed.errors],
    total: parsed.total,
    importable: 0,
    skipped: 0,
    runningToPause: 0,
    single: 0,
    two: 0,
    statusCounts: {},
  };
  if (!parsed.state) return preview;
  const existingErrors = validateState({
    version: 1,
    workflows: existingWorkflows,
  });
  if (existingErrors.length) {
    preview.errors.push("当前工作区状态无效，不能安全合并导入。");
    return preview;
  }
  const known = new Set(
    existingWorkflows.flatMap((workflow) => [workflow.id, sourceId(workflow)]),
  );
  const projected: Workflow[] = [];
  for (const workflow of parsed.state.workflows) {
    preview.statusCounts[workflow.status] =
      (preview.statusCounts[workflow.status] ?? 0) + 1;
    preview[workflow.experiment] += 1;
    const id = sourceId(workflow);
    if (known.has(id) || known.has(workflow.id)) {
      preview.skipped += 1;
      continue;
    }
    known.add(id);
    known.add(workflow.id);
    preview.importable += 1;
    projected.push(
      prepareImportedWorkflow(
        workflow,
        "wf-import-00000000-0000-4000-8000-000000000000",
      ),
    );
    if (workflow.status === "running") preview.runningToPause += 1;
  }
  if (existingWorkflows.length + preview.importable > MAX_WORKFLOWS)
    preview.errors.push(
      `合并后将超过 ${MAX_WORKFLOWS} 个任务上限，请减少导入任务。`,
    );
  if (
    byteLength(
      JSON.stringify({
        version: 1,
        workflows: [...projected, ...existingWorkflows],
      }),
    ) > MAX_WORKSPACE_BYTES
  )
    preview.errors.push("合并后的工作区超过5MB限制，不能导入。");
  preview.valid = preview.errors.length === 0;
  return preview;
}

/** Atomic merge with new IDs, provenance-based deduplication, and paused imported runs. */
export function mergeWorkspaceImport(
  current: AppState,
  json: string,
): { state: AppState; result: WorkspaceImportResult } {
  const preview = previewWorkspaceImport(json, current.workflows);
  if (!preview.valid) throw new WorkspaceImportError(preview.errors);
  const parsed = parseWorkspace(json).state!;
  const known = new Set(
    current.workflows.flatMap((workflow) => [workflow.id, sourceId(workflow)]),
  );
  const usedIds = new Set(current.workflows.map((workflow) => workflow.id));
  const imported: Workflow[] = [];
  for (const original of parsed.workflows) {
    const origin = sourceId(original);
    if (known.has(origin) || known.has(original.id)) continue;
    known.add(origin);
    known.add(original.id);
    let newId: string;
    do {
      newId = `wf-import-${globalThis.crypto?.randomUUID?.() ?? `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`}`;
    } while (usedIds.has(newId));
    usedIds.add(newId);
    const workflow = prepareImportedWorkflow(original, newId);
    imported.push(workflow);
  }
  const state: AppState = {
    version: 1,
    workflows: [...imported, ...current.workflows],
  };
  if (byteLength(JSON.stringify(state)) > MAX_WORKSPACE_BYTES)
    throw new WorkspaceImportError([
      "合并后的工作区超过5MB限制，未导入任何任务。",
    ]);
  return {
    state: imported.length ? state : current,
    result: {
      total: preview.total,
      imported: imported.length,
      skipped: preview.skipped,
      paused: preview.runningToPause,
      importedIds: imported.map((workflow) => workflow.id),
    },
  };
}
