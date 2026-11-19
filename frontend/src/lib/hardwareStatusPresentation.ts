import type { Workflow } from "../types.ts";

export interface HardwareOwnerRecord {
  pid?: number;
  localPort?: number;
  kind?: string;
  path?: string;
  operationId?: string;
  state?: string;
}

/** Group separate socket/lock records without asserting task ownership. */
export function groupHardwareOwners(records: HardwareOwnerRecord[]) {
  const groups = new Map<string, { key: string; pid?: number; records: HardwareOwnerRecord[] }>();
  records.forEach((record, index) => {
    const pid = Number.isSafeInteger(record.pid) && record.pid! > 0 ? record.pid : undefined;
    // Missing PIDs do not establish a common owner.
    const key = pid === undefined ? `unknown-${index}` : `pid-${pid}`;
    const group = groups.get(key) ?? { key, pid, records: [] };
    group.records.push(record); groups.set(key, group);
  });
  return [...groups.values()];
}

export function hardwareOwnerRecordLabel(record: HardwareOwnerRecord, index: number): string {
  if (record.kind === "chassis_lock") {
    const path = typeof record.path === "string" ? record.path.split(/[\\/]/).filter(Boolean).slice(-2).join("/") : "路径未提供";
    return `共享锁（${path}）`;
  }
  if (Number.isSafeInteger(record.localPort) && record.localPort! > 0 && record.localPort! <= 65535) {
    return `网络连接（本地端口 ${record.localPort}）`;
  }
  return `占用记录 ${index + 1}（来源未标注）`;
}

export function hardwareCheckExplanation(reason?: string): string | undefined {
  if (reason === "只读取连接表，未连接仪器") {
    return "检查方式：读取本机连接表及共享锁；检查过程不会新建仪器连接，不代表测量工作进程未连接。";
  }
  return reason ? `检查说明：${reason}` : undefined;
}

/** Workflow progress is separate evidence from the OS socket/lock inventory. */
export function workflowHardwareSummary(workflow?: Workflow): string | undefined {
  if (!workflow || workflow.mode !== "real") return undefined;
  if (workflow.hardwareConfirmed !== true) return "本任务尚无已确认的硬件交接记录。";
  if (workflow.status === "paused") return "本任务已确认交接并处于暂停状态；设备是否已释放，以连接及锁记录为准。";
  if (["aborted", "completed", "failed"].includes(workflow.status)) return "本任务已结束；交接确认属于历史记录，不能据此判断设备当前归属。";
  const node = workflow.nodes.find(item => item.id === workflow.currentNodeId);
  const telemetry = node?.telemetry;
  const attempt = node?.latestAttemptId;
  const phase = node?.engineState === "verifying" ? "verify" : node?.engineState === "acquiring" ? "measure" : undefined;
  const completed = telemetry?.completedRows, total = telemetry?.totalRows;
  const matching = attempt && telemetry?.controllerAttemptId === attempt &&
    telemetry.controllerOperationId === `${attempt}/${phase}`;
  if (workflow.status === "running" && node?.status === "running" && phase && matching &&
      typeof completed === "number" && Number.isSafeInteger(completed) && completed > 0 &&
      typeof total === "number" && Number.isSafeInteger(total) && total > completed) {
    return `本任务已确认交接；当前快照显示正在${phase === "verify" ? "独立复测采集" : "采集"}：${node.label}，${completed}/${total} 点。此进度不构成连接或锁的进程归属核验。`;
  }
  return "本任务已确认硬件交接；当前采集进度与设备记录归属分别核对。";
}
