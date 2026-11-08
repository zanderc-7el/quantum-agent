/**
 * Loads the design targets and acceptance floors bound to the selected
 * workflow's Skill.
 *
 * Enabled only in remote mode, and refetched on the same coarse signature as
 * the recorded results (workflow status plus node status/attempt), so a
 * running calibration does not poll the Skill endpoint every second. A failed
 * fetch keeps the previous binding: a transient error must not silently
 * downgrade a device's design target to the front-end demo constant.
 *
 * `refresh()` pulls the binding again on demand, so a Skill or device profile
 * edited while the workflow sits idle is visible without waiting for the next
 * status change.
 */
import { useCallback, useEffect, useState } from "react";
import type { Workflow } from "../types.ts";
import { fetchWorkflowSkill } from "./remoteInsights.ts";
import {
  getWorkflowTargets,
  publishWorkflowBinding,
} from "./remoteTargets.ts";
import type { WorkflowTargetSnapshot } from "./remoteTargets.ts";

export interface RemoteTargetsState {
  snapshot: WorkflowTargetSnapshot | null;
  loading: boolean;
  /**
   * A manual refresh is in flight. Separated from `loading` so an automatic
   * refetch on a node transition does not flash a "refreshing" state.
   */
  refreshing: boolean;
  error: string | null;
  /** Retry immediately instead of waiting for the next status change. */
  refresh: () => void;
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "读取 Skill 设计目标失败，请稍后重试。";
}

function targetSignature(workflow: Workflow | undefined): string {
  if (!workflow) return "";
  const nodes = workflow.nodes
    .map((node) => `${node.id}:${node.attempt}:${node.status}`)
    .join(",");
  return `${workflow.id}|${workflow.status}|${nodes}`;
}

export function useRemoteTargets(
  workflow: Workflow | undefined,
  enabled: boolean,
): RemoteTargetsState {
  const workflowId = workflow?.id ?? "";
  const signature = targetSignature(workflow);
  const [snapshot, setSnapshot] = useState<WorkflowTargetSnapshot | null>(
    () => getWorkflowTargets(workflowId) ?? null,
  );
  const [loading, setLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [reload, setReload] = useState(0);
  const refresh = useCallback(() => {
    setRefreshing(true);
    setReload((value) => value + 1);
  }, []);

  useEffect(() => {
    if (!enabled || !workflowId) {
      setLoading(false);
      setRefreshing(false);
      setError(null);
      setSnapshot(getWorkflowTargets(workflowId) ?? null);
      return;
    }
    let disposed = false;
    setLoading(true);
    fetchWorkflowSkill(workflowId)
      .then((binding) => {
        if (disposed) return;
        setSnapshot(publishWorkflowBinding(workflowId, binding));
        setError(null);
      })
      .catch((failure: unknown) => {
        if (disposed) return;
        setSnapshot(getWorkflowTargets(workflowId) ?? null);
        setError(message(failure));
      })
      .finally(() => {
        if (!disposed) {
          setLoading(false);
          setRefreshing(false);
        }
      });
    return () => {
      disposed = true;
    };
  }, [enabled, workflowId, signature, reload]);

  return { snapshot, loading, refreshing, error, refresh };
}
