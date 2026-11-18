import type { AppState } from "../types.ts";
import { assertWorkspaceSize, STORAGE_KEY } from "./simulator.ts";

export interface WorkflowStorageStatus {
  status: "saved" | "idle" | "error";
  savedAt: string | null;
  bytes: number;
  error: string | null;
}
let status: WorkflowStorageStatus = { status: "idle", savedAt: null, bytes: 0, error: null };
const listeners = new Set<() => void>();
export function getWorkflowStorageStatus() { return { ...status }; }
export function subscribeWorkflowStorage(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Commit only after a successful durable write; callers must not claim success on quota denial. */
export function persistWorkflowState(state: AppState): void {
  try {
    assertWorkspaceSize(state);
    if (!globalThis.localStorage) throw new Error("本机存储不可用");
    const json = JSON.stringify(state);
    globalThis.localStorage.setItem(STORAGE_KEY, json);
    status = { status: "saved", savedAt: new Date().toISOString(), bytes: new TextEncoder().encode(json).byteLength, error: null };
  } catch {
    const error = "工作流未能保存，本次操作未生效。请检查浏览器存储空间或权限后重试；现有记录保持不变。";
    status = { ...status, status: "error", error };
    listeners.forEach((listener) => listener());
    throw new Error(error);
  }
  listeners.forEach((listener) => listener());
}

export function readWorkflowStorageBytes(): number {
  try { return new TextEncoder().encode(globalThis.localStorage?.getItem(STORAGE_KEY) || "").byteLength; }
  catch { return 0; }
}
