import type { ChatReply } from "./remoteGateway.ts";

export interface ChatScope { projectId: string; workflowId: string; }
export interface RemoteChatMessage { id: string; role: "user" | "assistant" | "notice"; text: string; status?: string; model?: string; requestId?: string; createdAt: string; }
export interface ChatPending { id: string; state: "running" | "unresolved"; createdAt: string; }
export interface RemoteConversation {
  version: 1; projectId: string; workflowId: string; sessionId?: string;
  generation?: string;
  messages: RemoteChatMessage[]; pending?: ChatPending; omittedMessages: number; storageError?: string;
}
interface StorageLike { getItem(key: string): string | null; setItem(key: string, value: string): void; }
const PREFIX = "qcal.remote-chat.v1:";
export const MAX_REMOTE_MESSAGES = 200;
export const MAX_REMOTE_CACHE_BYTES = 1024 * 1024;
const MAX_TEXT = 40000;
const scopeKey = (scope: ChatScope) => PREFIX + JSON.stringify([scope.projectId, scope.workflowId]);
const nonempty = (value: unknown, limit = 4096): value is string => typeof value === "string" && value.length > 0 && value.length <= limit;
const validSession = (value: unknown): value is string => typeof value === "string" && /^[a-zA-Z0-9_-]{1,120}$/.test(value);
const unresolvedText = "先前请求结果待核查；可能仍在服务端运行。没有自动重新发送。";

export function createRemoteChatStore(options: { storage?: () => StorageLike | undefined; id?: () => string; now?: () => string } = {}) {
  const memory = new Map<string, RemoteConversation>();
  const listeners = new Map<string, Set<() => void>>();
  const inFlight = new Map<string, Promise<RemoteConversation>>();
  const corrupt = new Set<string>();
  const history = new Map<string, RemoteConversation>();
  const id = options.id ?? (() => crypto.randomUUID());
  const now = options.now ?? (() => new Date().toISOString());
  const storage = options.storage ?? (() => globalThis.localStorage);
  function persist(key: string, value: RemoteConversation): RemoteConversation {
    if (corrupt.has(key)) return { ...value, storageError: "本地对话缓存损坏或版本不兼容；旧缓存未覆盖，本次内容暂存内存。" };
    try {
      const target = storage();
      if (!target) throw new Error("storage unavailable");
      const { storageError: ignored, ...record } = value; void ignored;
      target.setItem(key, JSON.stringify(record));
      return record;
    } catch { return { ...value, storageError: "本地对话保存失败；当前内容仍在内存中，刷新后可能无法恢复。" }; }
  }
  function bounded(value: RemoteConversation) {
    let next = { ...value, messages: value.messages.map(message => ({ ...message, text: message.text.length > MAX_TEXT ? message.text.slice(0, MAX_TEXT) + "\n[展示缓存已按长度截断]" : message.text })) };
    while (next.messages.length > MAX_REMOTE_MESSAGES || (next.messages.length > 2 && new TextEncoder().encode(JSON.stringify(next)).length > MAX_REMOTE_CACHE_BYTES)) {
      next = { ...next, messages: next.messages.slice(1), omittedMessages: next.omittedMessages + 1 };
    }
    return next;
  }
  function publish(scope: ChatScope, value: RemoteConversation) {
    const key = scopeKey(scope);
    const saved = persist(key, bounded(value)); memory.set(key, saved);
    listeners.get(key)?.forEach(listener => listener()); return saved;
  }
  function get(scope: ChatScope): RemoteConversation {
    const key = scopeKey(scope), existing = memory.get(key); if (existing) return existing;
    let value: RemoteConversation = { version: 1, ...scope, messages: [], omittedMessages: 0 };
    try {
      const raw = storage()?.getItem(key);
      if (raw) {
        if (new TextEncoder().encode(raw).length > MAX_REMOTE_CACHE_BYTES + 8192) throw new Error("cache size");
        const data = JSON.parse(raw) as RemoteConversation;
        if (data.version !== 1 || data.projectId !== scope.projectId || data.workflowId !== scope.workflowId || !Array.isArray(data.messages) || data.messages.length > MAX_REMOTE_MESSAGES || !Number.isSafeInteger(data.omittedMessages) || data.omittedMessages < 0) throw new Error("cache shape");
        if (data.sessionId !== undefined && !validSession(data.sessionId)) throw new Error("session shape");
        if (data.pending && (!nonempty(data.pending.id, 128) || !["running", "unresolved"].includes(data.pending.state) || !nonempty(data.pending.createdAt, 64))) throw new Error("pending shape");
        if (!data.messages.every(message => nonempty(message.id, 128) && ["user", "assistant", "notice"].includes(message.role) && typeof message.text === "string" && message.text.length <= MAX_TEXT + 100 && nonempty(message.createdAt, 64) && (message.status === undefined || nonempty(message.status, 120)) && (message.model === undefined || nonempty(message.model, 256)) && (message.requestId === undefined || nonempty(message.requestId, 128)))) throw new Error("message shape");
        if (data.generation !== undefined && !validSession(data.generation)) throw new Error("generation shape");
        value = { version: 1, ...scope, generation: data.generation, sessionId: data.sessionId, messages: data.messages, pending: data.pending, omittedMessages: data.omittedMessages };
        if (value.pending?.state === "running") {
          value = { ...value, pending: { ...value.pending, state: "unresolved" }, messages: [...value.messages, { id: id(), role: "notice", text: unresolvedText, status: "unresolved", requestId: value.pending.id, createdAt: now() }] };
          return publish(scope, value);
        }
      }
    } catch {
      corrupt.add(key); value = { ...value, storageError: "本地对话缓存无法读取；旧缓存未覆盖，本次内容暂存内存。" };
    }
    memory.set(key, value); return value;
  }
  function send(scope: ChatScope, text: string, request: (text: string, sessionId?: string) => Promise<ChatReply>): Promise<RemoteConversation> {
    const before = get(scope);
    const key = `${scopeKey(scope)}:${before.generation ?? "legacy"}`, active = inFlight.get(key); if (active) return active;
    if (before.pending) return Promise.reject(new Error("先前请求结果待核查；请先确认是否允许发送新消息。"));
    if (!text.trim() || text.length > 20000) return Promise.reject(new Error("消息不能为空且不能超过 20000 字符。"));
    const requestId = id(), startedAt = now();
    const pending = publish(scope, { ...before, messages: [...before.messages, { id: id(), role: "user", text: text.trim(), requestId, createdAt: startedAt }], pending: { id: requestId, state: "running", createdAt: startedAt } });
    const operation = Promise.resolve().then(() => request(text.trim(), before.sessionId)).then(reply => {
      const current = conversationForReply(scope, before);
      return publishReply(scope, before, { ...current, sessionId: validSession(reply.sessionId) ? reply.sessionId : current.sessionId, pending: undefined,
        messages: [...current.messages, { id: id(), role: "assistant", text: typeof reply.message === "string" ? reply.message : "服务端未返回可展示的文本。", status: reply.status, model: reply.model, requestId, createdAt: now() }] });
    }, cause => {
      const current = conversationForReply(scope, before), detail = cause instanceof Error ? cause.message : String(cause);
      return publishReply(scope, before, { ...current, pending: { ...pending.pending!, state: "unresolved" }, messages: [...current.messages, { id: id(), role: "notice", text: `请求未取得回执：${detail.slice(0, 2000)}\n${unresolvedText}`, status: "unresolved", requestId, createdAt: now() }] });
    }).finally(() => { inFlight.delete(key); });
    inFlight.set(key, operation); return operation;
  }
  const historyKey = (scope: ChatScope, value: RemoteConversation) => `${scopeKey(scope)}:history:${value.generation ?? "legacy"}`;
  function conversationForReply(scope: ChatScope, before: RemoteConversation) {
    const current = get(scope);
    return current.generation === before.generation ? current : history.get(historyKey(scope, before)) ?? before;
  }
  function publishReply(scope: ChatScope, before: RemoteConversation, value: RemoteConversation) {
    if (get(scope).generation === before.generation) return publish(scope, value);
    const key = historyKey(scope, before), saved = persist(key, bounded(value));
    history.set(key, saved); return saved;
  }
  return {
    get, send,
    newConversation(scope: ChatScope) {
      const before = get(scope), key = historyKey(scope, before);
      const archived = persist(key, before);
      history.set(key, archived);
      // Keep the active conversation if archiving fails: never destroy the only durable copy.
      if (archived.storageError) throw new Error("旧对话保存失败，暂未创建新对话。请检查本地存储空间。");
      return publish(scope, { version: 1, ...scope, generation: id(), messages: [], omittedMessages: 0 });
    },
    subscribe(scope: ChatScope, listener: () => void) {
      const key = scopeKey(scope); let collection = listeners.get(key); if (!collection) { collection = new Set(); listeners.set(key, collection); }
      collection.add(listener); return () => { collection?.delete(listener); };
    },
    allowNewMessage(scope: ChatScope) {
      const current = get(scope);
      if (current.pending?.state === "unresolved" && !inFlight.has(`${scopeKey(scope)}:${current.generation ?? "legacy"}`)) publish(scope, { ...current, pending: undefined });
    },
  };
}
export const remoteChatStore = createRemoteChatStore();
