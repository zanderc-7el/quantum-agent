import type { AdapterIdentity, AppState, Catalog, CreateWorkflowInput, NodeDetail, Policy, ReadoutWithdrawalRequest, RbDiagnosticInspection, RbDiagnosticRequest, RemoteAction, Workflow } from "../types.ts";
import type { WorkflowGateway } from "./gateway.ts";
import type { User } from "./auth.ts";
import type { MembershipModelCatalog, MembershipStatus, SendCodeResult } from "./account.ts";
import type { LiveSeriesPage } from "./liveSeries.ts";
import type { BudgetValues } from "./budgetEdit.ts";

export interface LocalSession { token: string; actor: Omit<User, "username"> & { username?: string }; }
export type ConnectionState = "connecting" | "live" | "reconnecting" | "offline";
export interface ChatReply { sessionId: string; message: string; status: string; model?: string; decisions?: unknown[]; }
export interface ReportExportRequest { operationId: string; expectedRevision: number; format: "pptx"; appendixCount: 16 | 64; }
export interface ReportExportJob { id: string; workflowId: string; revision: number; status: "queued" | "processing" | "completed" | "failed" | "interrupted"; createdAt: string; operationId?: string; appendixCount?: 16 | 64; downloadUrl?: string; error?: { code: string; message: string }; }
export interface ArchiveStatus { schemaVersion: number; storage: string; location: string; local: { recordCount: number; acquisitionCount: number; attemptCount: number; reportCount: number; latestArchivedAt?: string }; cloud: { configured: boolean; state: string; pendingCount: number; syncedCount: number; message: string }; }
export interface ArchiveItem { id: string; kind: "acquisition" | "attempt" | "report"; workflowId: string; workflowName?: string; nodeLabel?: string; resourceId?: string; mode?: "real" | "replay"; qualityStatus?: string; role?: string; createdAt: string; acquiredAt?: string; artifactCount: number; revision?: number; }
export interface Health { status: string; agent: unknown; hardware: unknown; version: string; adapter?: AdapterIdentity; capabilities?: unknown; projectId?: string; projectRoot?: string; }
export class ApiError extends Error {
  status: number;
  code?: string;
  constructor(message: string, status: number, code?: string) {
    super(message); this.name = "ApiError"; this.status = status; this.code = code;
  }
}
export interface RemoteGateway extends WorkflowGateway {
  session(): Promise<LocalSession>;
  catalog(): Promise<Catalog>;
  health(): Promise<Health>;
  node(id: string, nodeId: string): Promise<NodeDetail>;
  liveSeries(id: string, nodeId: string, after: number, operationId: string | null, signal?: AbortSignal): Promise<LiveSeriesPage>;
  report(id: string): Promise<unknown>;
  createReportExport(id: string, request: ReportExportRequest): Promise<ReportExportJob>;
  reportExport(id: string): Promise<ReportExportJob>;
  reportExports(workflowId: string): Promise<ReportExportJob[]>;
  archiveStatus(): Promise<ArchiveStatus>;
  archiveList(workflowId?: string): Promise<{ items: ArchiveItem[]; nextCursor?: string }>;
  chat(message: string, workflowId?: string, sessionId?: string, nodeId?: string): Promise<ChatReply>;
  command(id: string, action: RemoteAction, targetNodeId?: string): Promise<Workflow>;
  parameters(id: string, nodeId: string, expectedRevision: number, parameters: Record<string, number>): Promise<Workflow>;
  budget(id: string, expectedRevision: number, budget: BudgetValues, reason: string): Promise<Workflow>;
  supplementalAcceptance(id: string, expectedRevision: number, reason: string): Promise<Workflow>;
  readoutWithdrawal(id: string, request: ReadoutWithdrawalRequest): Promise<Workflow>;
  rbDiagnosticInspection(id: string): Promise<RbDiagnosticInspection>;
  queueRbDiagnostic(id: string, request: RbDiagnosticRequest): Promise<Workflow>;
  connection(listener: (state: ConnectionState) => void): () => void;
  accountStatus(): Promise<MembershipStatus>;
  accountModels(): Promise<MembershipModelCatalog>;
  accountSendCode(phone: string): Promise<SendCodeResult>;
  accountLogin(phone: string, code: string, useForAgent: boolean): Promise<unknown>;
  accountConnect(model: string): Promise<unknown>;
  accountLogout(): Promise<unknown>;
  accountTest(): Promise<unknown>;
}

/** SSE is the authoritative state stream. No simulator or measurement selector is used here. */
export function createRemoteGateway(options: { fetch?: typeof fetch; eventSource?: (url: string) => EventSource; base?: string; commandTimeoutMs?: number; readTimeoutMs?: number } = {}): RemoteGateway {
  const request = options.fetch ?? globalThis.fetch.bind(globalThis);
  const base = options.base ?? "";
  let sessionPromise: Promise<LocalSession> | undefined;
  let snapshot: AppState = { version: 1, workflows: [] };
  let source: EventSource | undefined;
  let lastSeq = -1;
  let connectionState: ConnectionState = "connecting";
  const listeners = new Set<(snapshot: AppState) => void>();
  const connections = new Set<(state: ConnectionState) => void>();
  const setConnection = (state: ConnectionState) => { connectionState = state; connections.forEach(listener => listener(state)); };
  const read = async <T>(path: string, init?: RequestInit, timeoutMs?: number): Promise<T> => {
    const controller = timeoutMs === undefined ? undefined : new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const perform = async () => {
      const response = await request(`${base}/api${path}`, { credentials: "same-origin", ...init, ...(controller ? {signal: controller.signal} : {}) });
      const value = await response.json().catch(() => ({}));
      if (!response.ok) throw new ApiError(value?.error?.message || `请求失败（${response.status}）`, response.status, value?.error?.code);
      return value as T;
    };
    try {
      if (timeoutMs === undefined) return await perform();
      return await Promise.race([perform(), new Promise<never>((_, reject) => {
        timer = setTimeout(() => {
          const mutation = !!init?.method && init.method !== "GET";
          reject(new ApiError(mutation ? "回执待确认，请刷新任务" : "任务状态读取超时，请刷新任务", 408, mutation ? "RECEIPT_UNCONFIRMED" : "REQUEST_TIMEOUT"));
          controller?.abort();
        }, timeoutMs);
      })]);
    } finally { if (timer !== undefined) clearTimeout(timer); }
  };
  const session = () => sessionPromise ??= read<LocalSession>("/session", undefined, options.readTimeoutMs ?? 10000).catch(error => { sessionPromise = undefined; throw error; });
  const write = async <T>(path: string, method: string, body: unknown, timeoutMs?: number) => {
    const authPromise = session();
    const auth = await authPromise;
    // Serialize once: a refreshed token retries the same operation, never a new command.
    const payload = JSON.stringify(body);
    const send = (token: string) => read<T>(path, { method, headers: { "Content-Type": "application/json", "X-Qcal-Token": token }, body: payload }, timeoutMs);
    try { return await send(auth.token); }
    catch (error) {
      if (!(error instanceof ApiError) || error.status !== 401) throw error;
      if (sessionPromise === authPromise) sessionPromise = undefined;
      const refreshed = await session();
      return send(refreshed.token);
    }
  };
  const acceptWorkflow = (workflow: Workflow) => {
    const old = snapshot.workflows.find(item => item.id === workflow.id);
    if (old && (old.revision ?? 0) > (workflow.revision ?? 0)) return;
    snapshot = { ...snapshot, workflows: old ? snapshot.workflows.map(item => item.id === workflow.id ? workflow : item) : [workflow, ...snapshot.workflows] };
    listeners.forEach(listener => listener(snapshot));
  };
  const get = async (id: string) => {
    const value = await read<Workflow>(`/workflows/${encodeURIComponent(id)}`, undefined, options.readTimeoutMs ?? 10000);
    acceptWorkflow(value);
    return snapshot.workflows.find(item => item.id === id) ?? value;
  };
  const command = async (id: string, action: RemoteAction, targetNodeId?: string) => {
    const workflow = snapshot.workflows.find(item => item.id === id) ?? await get(id);
    const explicitNode = action === "run_step" || (action === "approve" && !!targetNodeId);
    const node = explicitNode ? workflow.nodes.find(item => item.id === targetNodeId) : workflow.nodes.find(item => item.id === workflow.currentNodeId) ?? workflow.nodes.find(item => ["running", "attention"].includes(item.status));
    if (explicitNode && !node) throw new Error("请选择本任务中的步骤");
    const stop = action === "pause" || action === "abort";
    const result = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/actions`, "POST", {
      action, operationId: crypto.randomUUID(), expectedRevision: workflow.revision ?? 0,
      ...(!stop ? {nodeId: node?.id, attemptId: node?.latestAttemptId, ...(action === "backtrack" && targetNodeId ? { targetNodeId } : {})} : {}),
    }, options.commandTimeoutMs ?? 30000);
    acceptWorkflow(result);
    return result;
  };
  const openStream = () => {
    if (source) return;
    setConnection("connecting");
    try {
      source = (options.eventSource ?? (url => new EventSource(url)))(`${base}/api/events?after=${Math.max(0, lastSeq)}`);
      source.onopen = () => setConnection("live");
      source.onerror = () => setConnection("reconnecting");
      source.addEventListener("snapshot", event => {
        try {
          const incoming = JSON.parse((event as MessageEvent).data) as AppState;
          const seq = Number((event as MessageEvent).lastEventId || incoming.seq || 0);
          if (!Array.isArray(incoming.workflows) || !Number.isFinite(seq) || seq < lastSeq) return;
          lastSeq = seq;
          snapshot = incoming;
          setConnection("live");
          listeners.forEach(listener => listener(snapshot));
        } catch { setConnection("reconnecting"); }
      });
    } catch { setConnection("offline"); }
  };
  return {
    mode: "remote", session, get, command,
    async list() {
      const seq = lastSeq;
      const workflows = await read<Workflow[]>("/workflows", undefined, options.readTimeoutMs ?? 10000);
      if (seq === lastSeq) snapshot = { ...snapshot, workflows: workflows.map(item => {
        const existing = snapshot.workflows.find(old => old.id === item.id);
        return existing && (existing.revision ?? 0) > (item.revision ?? 0) ? existing : item;
      }) };
      return snapshot.workflows;
    },
    async create(input: CreateWorkflowInput) { const value = await write<Workflow>("/workflows", "POST", input); acceptWorkflow(value); return value; },
    act: command,
    async parameters(id, nodeId, expectedRevision, parameters) {
      const value = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/nodes/${encodeURIComponent(nodeId)}/parameters`, "PATCH", { operationId: crypto.randomUUID(), expectedRevision, parameters });
      acceptWorkflow(value); return value;
    },
    async budget(id, expectedRevision, budget, reason) {
      const value = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/budget`, "PATCH", { operationId: crypto.randomUUID(), expectedRevision, budget, reason });
      acceptWorkflow(value); return value;
    },
    async supplementalAcceptance(id, expectedRevision, reason) {
      const value = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/supplemental-acceptance`, "POST", { operationId: crypto.randomUUID(), expectedRevision, policyVersion: "capture-consistency/1", reason });
      acceptWorkflow(value); return value;
    },
    rbDiagnosticInspection: id => read<RbDiagnosticInspection>(`/workflows/${encodeURIComponent(id)}/rb-diagnostic`),
    async queueRbDiagnostic(id, request) {
      const { operationId, expectedRevision, nodeId, attemptId, policyVersion, sourceProofSha256 } = request;
      const value = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/actions`, "POST", {
        action: "queue_rb_diagnostic", operationId, expectedRevision, nodeId, attemptId, policyVersion, sourceProofSha256,
      });
      if (value.id !== id) throw new ApiError("工作流回执不匹配，请刷新状态核对。", 502);
      acceptWorkflow(value); return value;
    },
    async readoutWithdrawal(id, request) {
      const { operationId, expectedRevision, revalidationId, sourceAttemptId, reason } = request;
      const value = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/readout-withdrawal`, "POST", { operationId, expectedRevision, revalidationId, sourceAttemptId, reason });
      acceptWorkflow(value); return value;
    },
    async policy(id: string, policy: Policy) {
      const workflow = snapshot.workflows.find(item => item.id === id) ?? await get(id);
      const value = await write<Workflow>(`/workflows/${encodeURIComponent(id)}/policy`, "PATCH", { policy, operationId: crypto.randomUUID(), expectedRevision: workflow.revision ?? 0 });
      acceptWorkflow(value); return value;
    },
    subscribe(listener) {
      listeners.add(listener); openStream();
      return () => { listeners.delete(listener); if (!listeners.size) { source?.close(); source = undefined; } };
    },
    connection(listener) { connections.add(listener); listener(connectionState); return () => { connections.delete(listener); }; },
    catalog: () => read<Catalog>("/catalog"),
    health: () => read<Health>("/health"),
    accountStatus: () => read<MembershipStatus>("/account/status"),
    accountModels: () => read<MembershipModelCatalog>("/account/models"),
    accountSendCode: phone => write<SendCodeResult>("/account/send-code", "POST", { phone, operationId: crypto.randomUUID() }),
    accountLogin: (phone, code, useForAgent) => write<unknown>("/account/login", "POST", { phone, code, useForAgent, operationId: crypto.randomUUID() }),
    accountConnect: model => write<unknown>("/account/connect", "POST", { model, operationId: crypto.randomUUID() }),
    accountLogout: () => write<unknown>("/account/logout", "POST", { operationId: crypto.randomUUID() }),
    accountTest: () => write<unknown>("/account/test", "POST", { operationId: crypto.randomUUID() }),
    node: (id, nodeId) => read<NodeDetail>(`/workflows/${encodeURIComponent(id)}/nodes/${encodeURIComponent(nodeId)}`),
    liveSeries: (id, nodeId, after, operationId, signal) => {
      const query = new URLSearchParams({ after: String(after) });
      if (operationId) query.set("operationId", operationId);
      return read<LiveSeriesPage>(`/workflows/${encodeURIComponent(id)}/nodes/${encodeURIComponent(nodeId)}/live-series?${query}`, { signal });
    },
    report: id => read<unknown>(`/workflows/${encodeURIComponent(id)}/report`),
    createReportExport: (id, request) => write<ReportExportJob>(`/workflows/${encodeURIComponent(id)}/report-exports`, "POST", request),
    reportExport: id => read<ReportExportJob>(`/report-exports/${encodeURIComponent(id)}`),
    reportExports: id => read<ReportExportJob[]>(`/workflows/${encodeURIComponent(id)}/report-exports`),
    archiveStatus: () => read<ArchiveStatus>("/archive/status"),
    archiveList: workflowId => read<{ items: ArchiveItem[]; nextCursor?: string }>(`/archive?limit=30${workflowId ? `&workflowId=${encodeURIComponent(workflowId)}` : ""}`),
    chat: (message, workflowId, sessionId, nodeId) => write<ChatReply>("/chat", "POST", { message, workflowId, sessionId, ...(nodeId ? { nodeId } : {}) }),
  };
}

export function artifactUrl(id: string) { return `/api/artifacts/${encodeURIComponent(id)}`; }
export function reportUrl(id: string) { return `/api/workflows/${encodeURIComponent(id)}/report?format=html`; }
