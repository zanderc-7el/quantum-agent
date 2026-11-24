export type StopAction = 'pause' | 'abort';
export interface StopRequest { workflowId: string; action: StopAction; sequence: number }

/** A later abort owns its pending state even if an older pause finishes last. */
export function createWorkflowStopGate() {
  let sequence = 0;
  const pending = new Map<string, StopRequest>();
  return {
    current: (workflowId: string) => pending.get(workflowId),
    begin(workflowId: string, action: StopAction) {
      const previous = pending.get(workflowId);
      if (previous && (previous.action === 'abort' || action === 'pause')) return undefined;
      const request = {workflowId, action, sequence: ++sequence};
      pending.set(workflowId, request);
      return request;
    },
    owns(request: StopRequest) { return pending.get(request.workflowId) === request; },
    finish(request: StopRequest) {
      if (pending.get(request.workflowId) !== request) return false;
      pending.delete(request.workflowId);
      return true;
    },
  };
}
