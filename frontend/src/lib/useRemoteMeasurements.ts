/**
 * Loads recorded node results and parameter/readback snapshots for the
 * selected workflow.
 *
 * Enabled only in remote mode. The fetch is keyed on the same coarse
 * signature as the audit trail (node status/attempt, workflow status), not on
 * progress ticks, so a running calibration does not poll the results endpoint
 * every second.
 *
 * The two endpoints are fetched together but settle independently: a failure
 * to read parameter publication records must not blank out the measurement
 * charts that were already loaded.
 */
import { useEffect, useState } from "react";
import type { Workflow } from "../types.ts";
import {
  fetchWorkflowParameters,
  fetchWorkflowResults,
  getWorkflowResults,
  publishWorkflowParameters,
  publishWorkflowResults,
} from "./remoteMeasurements.ts";
import type { WorkflowMeasurementSnapshot } from "./remoteMeasurements.ts";

export interface RemoteMeasurementsState {
  snapshot: WorkflowMeasurementSnapshot | null;
  loading: boolean;
  error: string | null;
  /** The parameters endpoint failed; publication info may be stale or absent. */
  parametersError: string | null;
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "读取服务端测量结果失败，请稍后重试。";
}

function measurementSignature(workflow: Workflow | undefined): string {
  if (!workflow) return "";
  const nodes = workflow.nodes
    .map((node) => `${node.id}:${node.attempt}:${node.status}`)
    .join(",");
  return `${workflow.id}|${workflow.status}|${nodes}`;
}

export function useRemoteMeasurements(
  workflow: Workflow | undefined,
  enabled: boolean,
): RemoteMeasurementsState {
  const workflowId = workflow?.id ?? "";
  const signature = measurementSignature(workflow);
  const [snapshot, setSnapshot] = useState<WorkflowMeasurementSnapshot | null>(
    () => getWorkflowResults(workflowId) ?? null,
  );
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [parametersError, setParametersError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || !workflowId) {
      setLoading(false);
      setError(null);
      setParametersError(null);
      setSnapshot(getWorkflowResults(workflowId) ?? null);
      return;
    }
    let disposed = false;
    setLoading(true);
    Promise.allSettled([
      fetchWorkflowResults(workflowId),
      fetchWorkflowParameters(workflowId),
    ]).then(([results, parameters]) => {
      if (disposed) return;
      if (results.status === "fulfilled") {
        setSnapshot(publishWorkflowResults(workflowId, results.value));
        setError(null);
      } else {
        // Keep any previously loaded snapshot: a transient failure must not
        // make charts flip back to demo values.
        setSnapshot(getWorkflowResults(workflowId) ?? null);
        setError(message(results.reason));
      }
      if (parameters.status === "fulfilled") {
        publishWorkflowParameters(workflowId, parameters.value);
        setParametersError(null);
      } else {
        setParametersError(message(parameters.reason));
      }
      setLoading(false);
    });
    return () => {
      disposed = true;
    };
  }, [enabled, workflowId, signature]);

  return { snapshot, loading, error, parametersError };
}
