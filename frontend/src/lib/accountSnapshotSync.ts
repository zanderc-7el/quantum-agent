import type { MembershipStatus } from "./account.ts";
import type { ConnectionState } from "./remoteGateway.ts";

type Timer = ReturnType<typeof setTimeout>;
export interface AccountSnapshotSync {
  refresh(force?: boolean): Promise<void>;
  accept(status: MembershipStatus): void;
  connection(state: ConnectionState): void;
  dispose(): void;
}

/** Read-only account snapshots. A local account operation invalidates older GETs. */
export function createAccountSnapshotSync(options: {
  readStatus: () => Promise<MembershipStatus>;
  onStatus: (status: MembershipStatus) => void;
  onUnavailable?: () => void;
  now?: () => number;
  schedule?: (callback: () => void, delay: number) => Timer;
  cancel?: (timer: Timer) => void;
}): AccountSnapshotSync {
  const now = options.now ?? Date.now;
  const schedule = options.schedule ?? setTimeout;
  const cancel = options.cancel ?? clearTimeout;
  let disposed = false;
  let generation = 0;
  let lastStarted = -Infinity;
  let flight: Promise<void> | undefined;
  let scheduled: Timer | undefined;
  let connection: ConnectionState = "connecting";
  const clearScheduled = () => {
    if (scheduled !== undefined) cancel(scheduled);
    scheduled = undefined;
  };
  const refresh = (force = false): Promise<void> => {
    if (disposed) return Promise.resolve();
    if (flight) return flight;
    // Focus, visibility and SSE recovery can occur together. Coalesce them,
    // but retain one trailing refresh instead of dropping a recent recovery.
    const delay = force ? 0 : 1000 - (now() - lastStarted);
    if (delay > 0) {
      if (scheduled === undefined) scheduled = schedule(() => {
        scheduled = undefined;
        void refresh(true);
      }, delay);
      return Promise.resolve();
    }
    clearScheduled();
    lastStarted = now();
    const requested = ++generation;
    const task = Promise.resolve().then(() => {
      if (!disposed && requested === generation) return options.readStatus();
    }).then(status => {
      if (!disposed && requested === generation && status) options.onStatus(status);
    }).catch(() => {
      if (!disposed && requested === generation) options.onUnavailable?.();
    }).finally(() => {
      if (flight === task) flight = undefined;
    });
    flight = task;
    return task;
  };
  return {
    refresh,
    accept(status) {
      if (disposed) return;
      generation++;
      // A slow pre-operation GET may still resolve, but cannot overwrite this
      // snapshot or hold up later GETs. Its finally cannot clear a newer flight.
      flight = undefined;
      clearScheduled();
      lastStarted = now();
      options.onStatus(status);
    },
    connection(next) {
      const recovered = next === "live" && connection !== "live";
      connection = next;
      if (recovered) void refresh();
    },
    dispose() {
      disposed = true;
      generation++;
      clearScheduled();
      flight = undefined;
    },
  };
}

/** Only refreshes GET snapshots; never invokes account connect or inference. */
export function bindAccountSnapshotRefresh(sync: AccountSnapshotSync, options: {
  windowTarget: Pick<EventTarget, "addEventListener" | "removeEventListener">;
  documentTarget: Pick<EventTarget, "addEventListener" | "removeEventListener">;
  visible: () => boolean;
  subscribeConnection: (listener: (state: ConnectionState) => void) => () => void;
  onConnection: (state: ConnectionState) => void;
}): () => void {
  const refreshVisible = () => { if (options.visible()) void sync.refresh(); };
  options.windowTarget.addEventListener("focus", refreshVisible);
  options.documentTarget.addEventListener("visibilitychange", refreshVisible);
  const unsubscribe = options.subscribeConnection(state => {
    options.onConnection(state);
    sync.connection(state);
  });
  return () => {
    options.windowTarget.removeEventListener("focus", refreshVisible);
    options.documentTarget.removeEventListener("visibilitychange", refreshVisible);
    unsubscribe();
  };
}
