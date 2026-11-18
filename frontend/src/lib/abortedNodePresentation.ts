import type { ExperimentNode, Workflow } from "../types.ts";

const decisionStates = new Set(["deciding", "decision_queued", "waiting_model"]);
const activeStates = new Set(["acquiring", "verifying", "queued", "retry_queued", "running", "pausing_at_checkpoint", "stopping_at_checkpoint"]);

/** Display-only overlay for legacy aborted snapshots. Never modifies evidence. */
export function abortedNodePresentation(workflow: Pick<Workflow, "status">, node: ExperimentNode) {
  if (workflow.status !== "aborted" || node.status === "success" || ["verified", "historical"].includes(node.engineState ?? "")) return undefined;
  const unstarted = node.attempt === 0 && !node.result;
  const detail = decisionStates.has(node.engineState ?? "")
    ? "任务已终止；本轮决策结果不再执行。已采集数据与质量结论保留。"
    : unstarted ? "此节点未执行，任务已终止。"
      : activeStates.has(node.engineState ?? "") || node.status === "running"
        ? "任务已终止；以下保留最后采集记录，质量结论见测量结果。"
        : `任务已终止。${node.detail}`;
  return { status: "abandoned" as const, label: unstarted ? "未执行" : "已终止", engineState: "aborted", detail };
}
