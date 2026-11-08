/**
 * `WorkflowGateway` implementation backed by the QuantaMind calibration API.
 *
 * The server is authoritative: every command carries `expectedRevision` and
 * `requestId`, and updates arrive over SSE. The adapter keeps a small cache so
 * the UI can render between events and so approve/retry can bind to the exact
 * node and attempt awaiting a decision.
 */
import type {
  AppState,
  CreateWorkflowInput,
  Policy,
  Workflow,
  WorkflowAction,
} from "../types.ts";
import type { WorkspaceImportResult } from "./workspaceTransfer.ts";
import type { WorkflowGateway } from "./gateway.ts";
import {
  ApiError,
  apiRequest,
  apiBaseUrl,
  readRemoteSession,
} from "./apiClient.ts";
import type { RemoteSession } from "./apiClient.ts";

const BASE = "/api/v1/calibration";
const PAGE_SIZE = 200;

interface WorkflowEnvelope {
  workflow: Workflow;
  revision: number;
}

export interface RemoteGatewayOptions {
  /** Local demo identity used by the UI's permission checks. */
  localUserId?: string;
  session?: RemoteSession | null;
  /** SSE factory injection keeps the adapter testable without a browser. */
  createEventSource?: (url: string) => EventSource;
}

function copy<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

function newRequestId(): string {
  const cryptoObject = globalThis.crypto as Crypto | undefined;
  if (typeof cryptoObject?.randomUUID === "function")
    return `web-${cryptoObject.randomUUID()}`;
  return `web-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

export function createRemoteGateway(
  options: RemoteGatewayOptions = {},
): WorkflowGateway {
  const session = options.session ?? readRemoteSession();
  if (!session)
    throw new Error("远程模式需要先登录后端账号，请重新登录。");
  const selfId = session.user.userId;
  const localUserId = options.localUserId;
  const workflows = new Map<string, Workflow>();
  const revisions = new Map<string, number>();
  const listeners = new Set<(state: AppState) => void>();
  let source: EventSource | null = null;

  /** Server ownerIds are stable identities; the UI compares against demo IDs. */
  const localize = (workflow: Workflow): Workflow =>
    localUserId && workflow.ownerId === selfId
      ? { ...workflow, ownerId: localUserId }
      : workflow;

  const upsert = (item: WorkflowEnvelope): Workflow => {
    const workflow = localize(item.workflow);
    workflows.set(workflow.id, workflow);
    revisions.set(workflow.id, item.revision);
    return workflow;
  };

  const snapshot = (): AppState => ({
    version: 1,
    workflows: [...workflows.values()].map(copy),
  });

  const emit = (): void => {
    if (listeners.size === 0) return;
    const state = snapshot();
    for (const listener of listeners) listener(state);
  };

  const cached = async (id: string): Promise<Workflow> => {
    const existing = workflows.get(id);
    return existing ?? (await fetchOne(id));
  };

  const fetchOne = async (id: string): Promise<Workflow> => {
    const data = await apiRequest<WorkflowEnvelope>(
      `${BASE}/workflows/${encodeURIComponent(id)}`,
    );
    const workflow = upsert(data);
    emit();
    return workflow;
  };

  /** The node awaiting a decision: approval needs the attention node, retry accepts either. */
  const activeTarget = (workflow: Workflow): Workflow["nodes"][number] | undefined =>
    workflow.nodes.find((node) => node.status === "attention") ??
    workflow.nodes.find((node) => node.status === "running");

  /**
   * Send a mutating command and recover once from a stale revision.
   *
   * A 409 carries the authoritative snapshot, so the caller's command is
   * rebuilt against it and retried with the same idempotency key. The server
   * has not applied the first attempt, which makes the retry safe.
   */
  const command = async (
    id: string,
    path: string,
    method: "POST" | "PATCH",
    build: (
      workflow: Workflow,
      revision: number,
      requestId: string,
    ) => Record<string, unknown>,
  ): Promise<Workflow> => {
    let workflow = await cached(id);
    const requestId = newRequestId();
    for (let attempt = 0; ; attempt += 1) {
      const revision = revisions.get(id) ?? 1;
      try {
        const data = await apiRequest<WorkflowEnvelope>(path, {
          method,
          body: build(workflow, revision, requestId),
        });
        const updated = upsert(data);
        emit();
        return copy(updated);
      } catch (error) {
        if (!(error instanceof ApiError) || error.status !== 409 || attempt > 0)
          throw error;
        const latest = error.details.workflow as Workflow | undefined;
        const actual = error.details.actualRevision;
        if (!latest || typeof actual !== "number") throw error;
        upsert({ workflow: latest, revision: actual });
        workflow = workflows.get(id)!;
        emit();
      }
    }
  };

  const streamUrl = (): string =>
    `${apiBaseUrl()}${BASE}/stream?token=${encodeURIComponent(session.accessToken)}`;

  const ensureStream = (): void => {
    if (source || listeners.size === 0) return;
    const factory =
      options.createEventSource ??
      ((url: string) => new EventSource(url));
    let created: EventSource | null = null;
    try {
      created = factory(streamUrl());
    } catch {
      created = null;
    }
    if (!created) return;
    source = created;
    const stream = created;
    const onFrame = (type: string) => (event: Event) => {
      const message = event as MessageEvent<string>;
      let payload: { workflows?: WorkflowEnvelope[] } & Partial<WorkflowEnvelope>;
      try {
        payload = JSON.parse(message.data);
      } catch {
        return; // A malformed frame is repaired by the next list/snapshot.
      }
      if (type === "snapshot") {
        workflows.clear();
        revisions.clear();
        for (const item of payload.workflows ?? []) upsert(item);
      } else if (type === "workflow.deleted") {
        if (!payload.workflow) return;
        workflows.delete(payload.workflow.id);
        revisions.delete(payload.workflow.id);
      } else if (payload.workflow && typeof payload.revision === "number") {
        upsert(payload as WorkflowEnvelope);
      } else return;
      emit();
    };
    stream.addEventListener("snapshot", onFrame("snapshot"));
    stream.addEventListener("workflow.updated", onFrame("workflow.updated"));
    stream.addEventListener("workflow.deleted", onFrame("workflow.deleted"));
    // EventSource reconnects on its own and replays Last-Event-ID; a failed
    // connection needs no manual retry here.
  };

  const closeStream = (): void => {
    if (!source) return;
    source.close();
    source = null;
  };

  return {
    mode: "remote",
    async list() {
      const data = await apiRequest<{ items: WorkflowEnvelope[] }>(
        `${BASE}/workflows?limit=${PAGE_SIZE}`,
      );
      workflows.clear();
      revisions.clear();
      for (const item of data.items) upsert(item);
      emit();
      return [...workflows.values()].map(copy);
    },
    async get(id) {
      return copy(await fetchOne(id));
    },
    async create(input: CreateWorkflowInput) {
      const blueprintId = input.experiment ?? (input.qubits === 2 ? "two" : "single");
      const data = await apiRequest<WorkflowEnvelope>(`${BASE}/workflows`, {
        method: "POST",
        body: { ...input, blueprintId, blueprintVersion: 1 },
      });
      const workflow = upsert(data);
      emit();
      return copy(workflow);
    },
    act(id: string, action: WorkflowAction) {
      return command(id, `${BASE}/workflows/${encodeURIComponent(id)}/actions`, "POST", (workflow, revision, requestId) => {
        const body: Record<string, unknown> = {
          action,
          expectedRevision: revision,
          requestId,
        };
        if (action === "approve" || action === "retry") {
          const target = activeTarget(workflow);
          if (target) {
            body.nodeId = target.id;
            body.attempt = target.attempt;
          }
        }
        return body;
      });
    },
    policy(id: string, policy: Policy) {
      return command(id, `${BASE}/workflows/${encodeURIComponent(id)}/policy`, "PATCH", (_workflow, revision, requestId) => ({
        policy,
        expectedRevision: revision,
        requestId,
      }));
    },
    async importWorkflows(json: string) {
      return apiRequest<WorkspaceImportResult>(`${BASE}/workspace/import`, {
        method: "POST",
        body: json,
      });
    },
    subscribe(listener) {
      listeners.add(listener);
      ensureStream();
      return () => {
        listeners.delete(listener);
        if (listeners.size === 0) closeStream();
      };
    },
  };
}
