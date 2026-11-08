/**
 * Loads the audit trail for the selected workflow and runs an on-demand replay.
 *
 * Only enabled in remote mode while the blueprint is visible; mock workflows
 * have no server audit and must not show an empty "server" panel.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import type { Workflow } from "../types.ts";
import {
  fetchWorkflowAudit,
  fetchWorkflowReplay,
} from "./remoteInsights.ts";
import type { WorkflowAudit, WorkflowReplay } from "./remoteInsights.ts";

export interface DecisionTrailState {
  audit: WorkflowAudit | null;
  loading: boolean;
  error: string | null;
  replay: WorkflowReplay | null;
  replayBusy: boolean;
  replayError: string | null;
  runReplay: () => void;
}

function message(error: unknown): string {
  return error instanceof Error
    ? error.message
    : "读取服务端审计失败，请稍后重试。";
}

/**
 * Refetch when a node completes, is retried, or the workflow status changes.
 * Progress ticks are deliberately excluded so a running calibration does not
 * hammer the audit endpoint.
 */
function auditSignature(workflow: Workflow | undefined): string {
  if (!workflow) return "";
  const nodes = workflow.nodes
    .map((node) => `${node.id}:${node.attempt}:${node.status}`)
    .join(",");
  // Event lists can be capped server-side, so the last timestamp is part of the
  // key as well; otherwise a rotation of old events would look unchanged.
  const lastEvent = workflow.events[workflow.events.length - 1];
  const events = `${workflow.events.length}:${lastEvent?.time ?? ""}`;
  return `${workflow.id}|${workflow.status}|${events}|${nodes}`;
}

export function useDecisionTrail(
  workflow: Workflow | undefined,
  enabled: boolean,
): DecisionTrailState {
  const [audit, setAudit] = useState<WorkflowAudit | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replay, setReplay] = useState<WorkflowReplay | null>(null);
  const [replayBusy, setReplayBusy] = useState(false);
  const [replayError, setReplayError] = useState<string | null>(null);

  const workflowId = workflow?.id ?? "";
  const signature = auditSignature(workflow);
  const workflowIdRef = useRef(workflowId);
  const replayRequestRef = useRef(0);
  workflowIdRef.current = workflowId;

  useEffect(() => {
    if (!enabled || !workflowId) {
      setAudit(null);
      setError(null);
      setLoading(false);
      return;
    }
    let disposed = false;
    setLoading(true);
    fetchWorkflowAudit(workflowId, 500)
      .then((next) => {
        if (disposed) return;
        setAudit(next);
        setError(null);
      })
      .catch((failure: unknown) => {
        if (disposed) return;
        setAudit(null);
        setError(message(failure));
      })
      .finally(() => {
        if (!disposed) setLoading(false);
      });
    return () => {
      disposed = true;
    };
  }, [enabled, workflowId, signature]);

  // A replay is a snapshot of one revision; any new measurement invalidates it.
  // Switching workflows also abandons an in-flight replay so the new workflow's
  // button is not stuck in the busy state.
  useEffect(() => {
    replayRequestRef.current += 1;
    setReplay(null);
    setReplayError(null);
    setReplayBusy(false);
  }, [workflowId, signature]);

  const runReplay = useCallback(() => {
    if (!enabled || !workflowId) return;
    const requestedId = workflowId;
    const request = replayRequestRef.current + 1;
    replayRequestRef.current = request;
    setReplayBusy(true);
    setReplayError(null);
    fetchWorkflowReplay(requestedId)
      .then((next) => {
        if (workflowIdRef.current === requestedId && replayRequestRef.current === request) {
          setReplay(next);
        }
      })
      .catch((failure: unknown) => {
        if (
          workflowIdRef.current !== requestedId ||
          replayRequestRef.current !== request
        ) {
          return;
        }
        setReplay(null);
        setReplayError(message(failure));
      })
      .finally(() => {
        if (
          workflowIdRef.current === requestedId &&
          replayRequestRef.current === request
        ) {
          setReplayBusy(false);
        }
      });
  }, [enabled, workflowId]);

  return {
    audit,
    loading,
    error,
    replay,
    replayBusy,
    replayError,
    runReplay,
  };
}
