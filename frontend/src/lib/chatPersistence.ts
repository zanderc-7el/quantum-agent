import type { LocalAttachment } from './attachments';

export const CHAT_DATABASE_NAME = 'qcal-local-conversations-v1';
export const MAX_CHAT_ATTACHMENT_BYTES = 100 * 1024 * 1024;
export type ChatReplyKind = 'result' | 'comparison' | 'explanation' | 'plan' | 'two-plan' | 'attachments' | 'unsupported';
export interface StoredChatMessage { id: number; role: 'user' | 'assistant'; text: string; kind?: ChatReplyKind; attachments?: LocalAttachment[]; }
export interface ChatSnapshot { workflowId: string; input: string; messages: StoredChatMessage[]; attachments: LocalAttachment[]; }
export function mergeHydratedSnapshot(saved: ChatSnapshot, current: ChatSnapshot, inputEdited: boolean, resetSinceLoad = false): ChatSnapshot {
  if (saved.workflowId !== current.workflowId || resetSinceLoad) return current;
  return { ...saved, input: inputEdited ? current.input : saved.input };
}
type AttachmentMetadata = Omit<LocalAttachment, 'file'>;
type RecordMessage = Omit<StoredChatMessage, 'attachments'> & { attachmentIds: string[] };
interface ConversationRecord { workflowId: string; input: string; messages: RecordMessage[]; attachmentIds: string[]; files: AttachmentMetadata[]; attachmentBytes: number; updatedAt: number; }
interface FileRecord { key: string; workflowId: string; attachmentId: string; blob: Blob; }
export interface ChatStorageStats { status: 'ready' | 'unavailable' | 'error'; recordCount: number; attachmentCount: number; attachmentBytes: number; maxAttachmentBytes: number; error?: string; }
export class ChatPersistenceError extends Error {
  readonly code: 'unavailable' | 'capacity' | 'stale' | 'storage' | 'invalid';
  constructor(message: string, code: ChatPersistenceError['code']) { super(message); this.name = 'ChatPersistenceError'; this.code = code; }
}
const listeners = new Set<() => void>();
let openPromise: Promise<IDBDatabase> | undefined;
let lastError: string | undefined;
let resetInFlight: Promise<void> | undefined;
let localEpoch = 0;
const stores = ['conversations', 'attachments', 'metadata'];
const emit = () => listeners.forEach((listener) => listener());
export function subscribeChatStorage(listener: () => void): () => void { listeners.add(listener); return () => listeners.delete(listener); }
export function chatStorageError(error: unknown): string {
  if (error instanceof ChatPersistenceError) return error.message;
  if (error instanceof DOMException && error.name === 'QuotaExceededError') return '浏览器存储空间不足。请释放空间或删除旧对话附件，再重试保存。';
  return '本浏览器数据存储失败。当前新内容仍在内存中，请保留页面并重试。';
}
function fail(error: unknown): never { if (!(error instanceof ChatPersistenceError && error.code === 'stale')) { lastError = chatStorageError(error); emit(); } throw error; }
function openDatabase(): Promise<IDBDatabase> {
  if (typeof indexedDB === 'undefined') return Promise.reject(new ChatPersistenceError('当前环境无法使用 IndexedDB，刷新后不能恢复新内容。请在允许本地存储的浏览器中打开。', 'unavailable'));
  if (!openPromise) {
    openPromise = new Promise<IDBDatabase>((resolve, reject) => {
      const request = indexedDB.open(CHAT_DATABASE_NAME, 1);
      let abandoned = false;
      request.onupgradeneeded = () => {
        const db = request.result;
        db.createObjectStore('conversations', { keyPath: 'workflowId' });
        db.createObjectStore('attachments', { keyPath: 'key' });
        db.createObjectStore('metadata');
      };
      request.onsuccess = () => { const db = request.result; if (abandoned) { db.close(); return; } db.onversionchange = () => { db.close(); openPromise = undefined; }; resolve(db); };
      request.onerror = () => reject(request.error || new ChatPersistenceError('无法打开浏览器本地数据库。', 'storage'));
      request.onblocked = () => { abandoned = true; reject(new ChatPersistenceError('本地数据库被其他页面占用，请关闭旧页面后重试。', 'storage')); };
    }).catch((error) => { openPromise = undefined; throw error; });
  }
  return openPromise;
}
function done(transaction: IDBTransaction): Promise<void> { return new Promise((resolve, reject) => { transaction.oncomplete = () => resolve(); transaction.onabort = () => reject(transaction.error || new ChatPersistenceError('本地存储事务已取消。', 'storage')); transaction.onerror = () => {}; }); }
function requestValue<T>(request: IDBRequest<T>): Promise<T> { return new Promise((resolve, reject) => { request.onsuccess = () => resolve(request.result); request.onerror = () => reject(request.error); }); }
const fileKey = (workflowId: string, id: string) => JSON.stringify([workflowId, id]);
export function assertChatAttachmentCapacity(records: { workflowId: string; attachmentBytes: number }[], next: { workflowId: string; attachmentBytes: number }): void {
  const bytes = records.filter((record) => record.workflowId !== next.workflowId).reduce((sum, record) => sum + record.attachmentBytes, next.attachmentBytes);
  if (bytes > MAX_CHAT_ATTACHMENT_BYTES) throw new ChatPersistenceError('本浏览器全部对话的附件已达到 100 MB 上限。请移除附件或清空旧对话后重试。', 'capacity');
}

/** Only explicit conversation fields are serialized; credentials/settings are never read or saved. */
export function prepareChatRecord(snapshot: ChatSnapshot): { record: ConversationRecord; files: FileRecord[] } {
  if (!snapshot.workflowId || snapshot.input.length > 4000) throw new ChatPersistenceError('对话标识或草稿长度无效，未保存。', 'invalid');
  const unique = new Map<string, LocalAttachment>();
  const collect = (attachment: LocalAttachment) => {
    if (!(attachment.file instanceof Blob) || attachment.file.size !== attachment.size || !attachment.id) throw new ChatPersistenceError('附件原文件缺失或大小不一致，未保存。', 'invalid');
    const old = unique.get(attachment.id);
    if (old && (old.name !== attachment.name || old.size !== attachment.size)) throw new ChatPersistenceError('附件标识冲突，未保存。', 'invalid');
    unique.set(attachment.id, attachment);
  };
  snapshot.attachments.forEach(collect);
  snapshot.messages.forEach((message) => message.attachments?.forEach(collect));
  const files = [...unique.values()];
  const attachmentBytes = files.reduce((total, attachment) => total + attachment.file.size, 0);
  if (attachmentBytes > MAX_CHAT_ATTACHMENT_BYTES) throw new ChatPersistenceError('本浏览器的对话附件上限为 100 MB。请移除待发送附件或清空旧对话后重试。', 'capacity');
  const record: ConversationRecord = {
    workflowId: snapshot.workflowId, input: snapshot.input,
    messages: snapshot.messages.map((message) => ({ id: message.id, role: message.role, text: message.text, ...(message.kind ? { kind: message.kind } : {}), attachmentIds: (message.attachments || []).map((attachment) => attachment.id) })),
    attachmentIds: snapshot.attachments.map((attachment) => attachment.id),
    files: files.map((attachment) => ({ id: attachment.id, name: attachment.name, size: attachment.size, lastModified: attachment.lastModified, extension: attachment.extension, preview: attachment.preview, previewTruncated: attachment.previewTruncated })), attachmentBytes, updatedAt: Date.now(),
  };
  return { record, files: files.map((attachment) => ({ key: fileKey(snapshot.workflowId, attachment.id), workflowId: snapshot.workflowId, attachmentId: attachment.id, blob: attachment.file })) };
}

export function restoreChatRecord(record: ConversationRecord, blobs: Map<string, Blob>): ChatSnapshot {
  const attachments = new Map(record.files.map((metadata) => {
    const blob = blobs.get(metadata.id);
    if (!(blob instanceof Blob) || blob.size !== metadata.size) throw new ChatPersistenceError(`附件“${metadata.name}”的本地原文件缺失，请重试读取或重新添加。`, 'invalid');
    const file = new File([blob], metadata.name, { type: blob.type, lastModified: metadata.lastModified });
    return [metadata.id, { ...metadata, file }] as const;
  }));
  const resolve = (id: string) => { const attachment = attachments.get(id); if (!attachment) throw new ChatPersistenceError('附件关联记录损坏，未覆盖当前对话。', 'invalid'); return attachment; };
  return { workflowId: record.workflowId, input: record.input, attachments: record.attachmentIds.map(resolve), messages: record.messages.map(({ attachmentIds, ...message }) => ({ ...message, attachments: attachmentIds.map(resolve) })) };
}

export async function loadChatSnapshot(workflowId: string): Promise<{ snapshot: ChatSnapshot | null; epoch: number }> {
  try {
    if (resetInFlight) await resetInFlight;
    const token = localEpoch;
    const db = await openDatabase();
    const transaction = db.transaction(stores, 'readonly');
    const completion = done(transaction);
    void completion.catch(() => {});
    // Queue all initial reads before yielding so the transaction remains active.
    const recordPromise = requestValue(transaction.objectStore('conversations').get(workflowId)) as Promise<ConversationRecord | undefined>;
    const epochPromise = requestValue(transaction.objectStore('metadata').get('epoch')) as Promise<number | undefined>;
    const result = await new Promise<ChatSnapshot | null>((resolve, reject) => {
      recordPromise.then((record) => {
        try {
        if (!record) { resolve(null); return; }
        if (!Array.isArray(record.files) || !Array.isArray(record.messages) || !Array.isArray(record.attachmentIds) || record.workflowId !== workflowId || typeof record.input !== 'string') throw new ChatPersistenceError('本地对话记录格式损坏，未覆盖当前内容。可重试或清空浏览器对话数据。', 'invalid');
        const reads = record.files.map((attachment) => requestValue(transaction.objectStore('attachments').get(fileKey(workflowId, attachment.id))) as Promise<FileRecord | undefined>);
        Promise.all(reads).then((values) => { try { resolve(restoreChatRecord(record, new Map(values.filter((value): value is FileRecord => !!value).map((value) => [value.attachmentId, value.blob])))); } catch (error) { reject(error); } }, reject);
        } catch (error) { reject(error); }
      }, reject);
    });
    const epoch = (await epochPromise) || 0;
    await completion;
    if (token !== localEpoch) throw new ChatPersistenceError('数据已重置，请重新读取当前对话。', 'stale');
    lastError = undefined;
    return { snapshot: result, epoch };
  } catch (error) { return fail(error); }
}

export async function saveChatSnapshot(snapshot: ChatSnapshot, expectedEpoch: number): Promise<void> {
  const token = localEpoch;
  try {
    if (resetInFlight) await resetInFlight;
    const prepared = prepareChatRecord(snapshot);
    const db = await openDatabase();
    if (token !== localEpoch) throw new ChatPersistenceError('数据已重置，已阻止旧对话回写。', 'stale');
    const transaction = db.transaction(stores, 'readwrite');
    let reason: unknown;
    const completion = done(transaction);
    void completion.catch(() => {});
    const conversations = transaction.objectStore('conversations');
    const allRequest = conversations.getAll();
    const epochRequest = transaction.objectStore('metadata').get('epoch');
    let records: ConversationRecord[] | undefined;
    let epoch: number | undefined;
    const commit = () => {
      if (!records || epoch === undefined) return;
      if (epoch !== expectedEpoch || token !== localEpoch) { reason = new ChatPersistenceError('本地数据已在另一操作中重置，已阻止旧内容回写；请重新读取。', 'stale'); transaction.abort(); return; }
      try { assertChatAttachmentCapacity(records, prepared.record); } catch (error) { reason = error; transaction.abort(); return; }
      const old = records.find((record) => record.workflowId === snapshot.workflowId);
      const attachmentStore = transaction.objectStore('attachments');
      const nextIds = new Set(prepared.record.files.map((file) => file.id));
      old?.files.filter((file) => !nextIds.has(file.id)).forEach((file) => attachmentStore.delete(fileKey(snapshot.workflowId, file.id)));
      prepared.files.forEach((file) => attachmentStore.put(file));
      conversations.put(prepared.record);
    };
    allRequest.onsuccess = () => { records = allRequest.result; commit(); };
    epochRequest.onsuccess = () => { epoch = epochRequest.result || 0; commit(); };
    try { await completion; } catch (error) { throw reason || error; }
    lastError = undefined; emit();
  } catch (error) { return fail(error); }
}

/** Synchronously invalidates in-flight reads/writes, then atomically clears records and blobs. */
export function clearChatStorage(): Promise<void> {
  localEpoch += 1;
  const previous = resetInFlight;
  const operation = (async () => {
    try {
      if (previous) await previous.catch(() => {});
      const db = await openDatabase();
      const transaction = db.transaction(stores, 'readwrite');
      const completion = done(transaction);
    void completion.catch(() => {});
      const metadata = transaction.objectStore('metadata');
      const epochRequest = metadata.get('epoch');
      epochRequest.onsuccess = () => { metadata.put((epochRequest.result || 0) + 1, 'epoch'); transaction.objectStore('conversations').clear(); transaction.objectStore('attachments').clear(); };
      await completion; lastError = undefined; emit();
    } catch (error) { fail(error); }
  })();
  resetInFlight = operation;
  void operation.finally(() => { if (resetInFlight === operation) resetInFlight = undefined; }).catch(() => {});
  return operation;
}

export async function readChatStorageStats(): Promise<ChatStorageStats> {
  const empty = { recordCount: 0, attachmentCount: 0, attachmentBytes: 0, maxAttachmentBytes: MAX_CHAT_ATTACHMENT_BYTES };
  try {
    if (resetInFlight) await resetInFlight;
    const db = await openDatabase();
    const transaction = db.transaction('conversations', 'readonly');
    const completion = done(transaction);
    void completion.catch(() => {});
    const records = await requestValue(transaction.objectStore('conversations').getAll()) as ConversationRecord[];
    await completion;
    return { status: lastError ? 'error' : 'ready', recordCount: records.length, attachmentCount: records.reduce((sum, record) => sum + record.files.length, 0), attachmentBytes: records.reduce((sum, record) => sum + record.attachmentBytes, 0), maxAttachmentBytes: MAX_CHAT_ATTACHMENT_BYTES, ...(lastError ? { error: lastError } : {}) };
  } catch (error) { return { ...empty, status: typeof indexedDB === 'undefined' ? 'unavailable' : 'error', error: chatStorageError(error) }; }
}
