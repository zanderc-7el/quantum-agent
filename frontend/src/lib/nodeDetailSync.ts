import type { ExperimentNode, Workflow } from '../types.ts';

type Timer = ReturnType<typeof setTimeout>;
export interface NodeDetailSync {
  observe(revision: number | undefined, boundary: string): void;
  dispose(): void;
}

/** A new operation can keep the same attempt and acquiring engine state.
 * Point counters/progress deliberately do not participate in this boundary.
 */
export function nodeDetailBoundary(workflow: Workflow, node: ExperimentNode): string {
  const telemetry = node.telemetry;
  const operation = telemetry?.controllerOperationId ?? telemetry?.operationId;
  return JSON.stringify([node.attempt, node.latestAttemptId, node.engineState, node.status,
    workflow.status, workflow.engineState, operation, telemetry?.phase]);
}

/** One selected object per instance. GET-only, single flight, bounded SSE refresh.
 * Existing detail belongs to the caller and is never cleared by a refresh/error.
 * A phase/attempt boundary supersedes an older response, then gets the next GET
 * immediately after any in-flight GET settles. Ordinary updates coalesce at 2s.
 */
export function createNodeDetailSync<T>(options: {
  read: () => Promise<T>;
  onValue: (value: T) => void;
  onError: (message: string) => void;
  now?: () => number;
  schedule?: (callback: () => void, delay: number) => Timer;
  cancel?: (timer: Timer) => void;
}): NodeDetailSync {
  const now = options.now ?? Date.now, schedule = options.schedule ?? setTimeout, cancel = options.cancel ?? clearTimeout;
  let disposed = false, seen = false, dirty = false, urgent = false;
  let revision: number | undefined, boundary = '', epoch = 0, lastStarted = -Infinity;
  let timer: Timer | undefined, flight: Promise<void> | undefined;
  const clearTimer = () => { if (timer !== undefined) cancel(timer); timer = undefined; };
  const pump = () => {
    if (disposed || !dirty || flight) return;
    const delay = urgent ? 0 : Math.max(0, 2000 - (now() - lastStarted));
    if (delay > 0) {
      if (timer === undefined) timer = schedule(() => { timer = undefined; pump(); }, delay);
      return;
    }
    clearTimer(); dirty = false; urgent = false; lastStarted = now();
    const requestedEpoch = epoch;
    const task = Promise.resolve().then(() => { if (!disposed) return options.read(); }).then(value => {
      if (!disposed && requestedEpoch === epoch && value !== undefined) options.onValue(value);
    }).catch(cause => {
      if (!disposed && requestedEpoch === epoch) options.onError(cause instanceof Error ? cause.message : String(cause));
    }).finally(() => {
      if (flight === task) flight = undefined;
      pump();
    });
    flight = task;
  };
  return {
    observe(nextRevision, nextBoundary) {
      if (disposed || (seen && nextRevision === revision && nextBoundary === boundary)) return;
      if (!seen || nextBoundary !== boundary) { epoch++; urgent = true; }
      seen = true; revision = nextRevision; boundary = nextBoundary; dirty = true;
      pump();
    },
    dispose() { disposed = true; epoch++; dirty = false; clearTimer(); },
  };
}
