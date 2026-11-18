export interface WorkflowTimingSource { status: string; firstAcquisitionAt?: string | null; finishedAt?: string | null; }
export interface WorkflowTiming { state: "not_started" | "awaiting_start" | "awaiting_finish" | "clock_skew" | "running" | "finished"; seconds?: number; ticking: boolean; }
const terminal = new Set(["completed", "failed", "aborted", "cancelled", "canceled"]);
const timestamp = (value: unknown) => typeof value === "string" && value ? Date.parse(value) : NaN;
export function workflowTiming(workflow: WorkflowTimingSource, nowMs: number): WorkflowTiming {
  if (workflow.firstAcquisitionAt === null) return { state: "not_started", ticking: false };
  const start = timestamp(workflow.firstAcquisitionAt);
  if (!Number.isFinite(start)) return { state: "awaiting_start", ticking: false };
  const finish = timestamp(workflow.finishedAt), stopped = terminal.has(workflow.status);
  if (stopped && !Number.isFinite(finish)) return { state: "awaiting_finish", ticking: false };
  const hasFinish = Number.isFinite(finish), end = hasFinish ? finish : nowMs;
  if (!Number.isFinite(end) || end < start) return { state: "clock_skew", ticking: !stopped && !hasFinish };
  return { state: hasFinish ? "finished" : "running", seconds: Math.floor((end - start) / 1000), ticking: !hasFinish && !stopped };
}
export function formatWallClock(seconds: number) {
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60), remainder = Math.floor(seconds % 60);
  return hours ? `${hours} 小时 ${String(minutes).padStart(2, "0")} 分 ${String(remainder).padStart(2, "0")} 秒` : minutes ? `${minutes} 分 ${String(remainder).padStart(2, "0")} 秒` : `${remainder} 秒`;
}
