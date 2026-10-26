import test from 'node:test';
import assert from 'node:assert/strict';
import type { ChatSnapshot } from '../src/lib/chatPersistence.ts';
import { assertChatAttachmentCapacity, MAX_CHAT_ATTACHMENT_BYTES, mergeHydratedSnapshot, prepareChatRecord, restoreChatRecord, readChatStorageStats, saveChatSnapshot } from '../src/lib/chatPersistence.ts';
import { prepareLocalAttachments } from '../src/lib/attachments.ts';

async function snapshot(): Promise<ChatSnapshot> {
  const { accepted } = await prepareLocalAttachments([new File(['实验记录\nT1 = 50.84'], 'experiment.txt', { type: 'text/plain', lastModified: 1234 }), new File(['%PDF-1.7\nfixture'], 'paper.pdf', { type: 'application/pdf', lastModified: 5678 })]);
  return { workflowId: 'wf-a', input: '尚未发送的草稿', messages: [{ id: 1, role: 'user', text: '附件对应此消息', attachments: [accepted[0]] }, { id: 2, role: 'assistant', text: '尚未接入模型', kind: 'attachments' }], attachments: [accepted[1]] };
}

test('persistent record restores draft, sent attachment association, pending PDF, and exact original file bytes', async () => {
  const original = await snapshot();
  const prepared = prepareChatRecord(original);
  const clonedRecord = structuredClone(prepared.record);
  const blobs = new Map(prepared.files.map((file) => [file.attachmentId, structuredClone(file.blob)]));
  const restored = restoreChatRecord(clonedRecord, blobs);
  assert.equal(restored.workflowId, original.workflowId);
  assert.equal(restored.input, original.input);
  assert.equal(restored.messages[0].attachments?.[0].name, 'experiment.txt');
  assert.equal(restored.attachments[0].name, 'paper.pdf');
  assert.equal(restored.attachments[0].file.lastModified, 5678);
  assert.equal(restored.messages[0].attachments?.[0].file.lastModified, 1234);
  assert.equal(await restored.attachments[0].file.text(), await original.attachments[0].file.text());
  assert.equal(await restored.messages[0].attachments![0].file.text(), await original.messages[0].attachments![0].file.text());
  assert.equal(restored.attachments[0].preview, null);
});

test('serialization uses explicit conversation fields and deduplicates attachment IDs across draft and message', async () => {
  const original = await snapshot();
  original.attachments.push(original.messages[0].attachments![0]);
  const contaminated = Object.assign(original, { apiKey: 'must-not-save', token: 'not-a-conversation-field' });
  Object.assign(contaminated.attachments[0], { apiKey: 'also-not-saved' });
  const prepared = prepareChatRecord(contaminated);
  assert.equal(prepared.files.length, 2);
  assert.equal(prepared.record.files.length, 2);
  assert.equal(prepared.record.attachmentBytes, prepared.files.reduce((sum, file) => sum + file.blob.size, 0));
  assert.ok(!JSON.stringify(prepared.record).includes('apiKey'));
  assert.ok(!JSON.stringify(prepared.record).includes('must-not-save'));
  assert.ok(!JSON.stringify(prepared.record).includes('token'));
});

test('missing attachment bytes fail explicitly instead of silently dropping message files', async () => {
  const prepared = prepareChatRecord(await snapshot());
  assert.throws(() => restoreChatRecord(prepared.record, new Map()), /原文件缺失/);
  assert.throws(() => restoreChatRecord(prepared.record, new Map(prepared.files.map((file) => [file.attachmentId, new Blob(['wrong'])]))), /原文件缺失/);
});

test('100 MB shared capacity counts other workflows but permits replacing and clearing existing records', () => {
  const limit = MAX_CHAT_ATTACHMENT_BYTES;
  const existing = [{ workflowId: 'wf-a', attachmentBytes: limit * .8 }, { workflowId: 'wf-b', attachmentBytes: limit * .2 }];
  assert.doesNotThrow(() => assertChatAttachmentCapacity(existing, { workflowId: 'wf-a', attachmentBytes: limit * .8 }));
  assert.throws(() => assertChatAttachmentCapacity(existing, { workflowId: 'wf-new', attachmentBytes: 1 }), /100 MB/);
  assert.doesNotThrow(() => assertChatAttachmentCapacity(existing, { workflowId: 'wf-a', attachmentBytes: 0 }));
  assert.doesNotThrow(() => assertChatAttachmentCapacity([], { workflowId: 'wf-a', attachmentBytes: limit }));
  assert.throws(() => assertChatAttachmentCapacity([], { workflowId: 'wf-a', attachmentBytes: limit + 1 }), /100 MB/);
});

test('hydration cannot overwrite newer input, reset contents or a different workflow', async () => {
  const saved = await snapshot();
  const current: ChatSnapshot = { workflowId: 'wf-a', input: '输入过程中产生的新草稿', messages: [], attachments: [] };
  const merged = mergeHydratedSnapshot(saved, current, true);
  assert.equal(merged.input, current.input);
  assert.equal(merged.messages[0].text, saved.messages[0].text);
  assert.equal(merged.attachments[0].file, saved.attachments[0].file);
  assert.equal(mergeHydratedSnapshot(saved, current, false).input, saved.input);
  assert.equal(mergeHydratedSnapshot(saved, current, true, true), current);
  const other = { ...current, workflowId: 'wf-b' };
  assert.equal(mergeHydratedSnapshot(saved, other, false), other);
});

test('unavailable IndexedDB is an explicit failure and stats never claim a durable save', async () => {
  const stats = await readChatStorageStats();
  assert.equal(stats.status, 'unavailable');
  assert.equal(stats.maxAttachmentBytes, 100 * 1024 * 1024);
  assert.match(stats.error!, /IndexedDB/);
  await assert.rejects(saveChatSnapshot(await snapshot(), 0), /IndexedDB/);
});
