import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import type { ReadoutRevalidation, ReadoutWithdrawalRequest, ReadoutWithdrawal, Workflow } from '../src/types.ts';
import { readoutWithdrawalCandidates, readoutWithdrawalError } from '../src/lib/readoutWithdrawal.ts';
import { ApiError, createRemoteGateway } from '../src/lib/remoteGateway.ts';

// Offline UI and transport fixtures only. No model, real account or device calls.
const marker: ReadoutRevalidation = { id: 'candidate-test', state: 'active', resourceId: 'Q11', optimizerNodeId: 'optimizer', rabiNodeId: 'rabi', sourceAttemptId: 'optimizer-attempt', values: { readout_hz: 7000000000, readout_amp: .3 }, parameterVersions: { readout_hz: 8, readout_amp: 8 } };
const workflow = { id: 'offline-wf', mode: 'real', status: 'paused', revision: 17, nodes: [], readoutRevalidations: { Q11: marker } } as unknown as Workflow;
const request: ReadoutWithdrawalRequest = { operationId: 'withdrawal-op-test', expectedRevision: 17, revalidationId: marker.id, sourceAttemptId: marker.sourceAttemptId, reason: '候选条件下复测不稳定' };
const receipt: ReadoutWithdrawal = { ruleVersion: 'readout-withdrawal/1', source: 'operator_withdrawal', operationId: request.operationId, validationStatus: 'unverified', revalidationId: 'replacement-test', before: { values: marker.values, parameterVersions: marker.parameterVersions }, after: { values: { readout_hz: 7000100000, readout_amp: .18 }, parameterVersions: { readout_hz: 9, readout_amp: 9 } }, sourceEvidence: { optimizerAttemptId: marker.sourceAttemptId, baselineIqAttemptId: 'baseline-iq-attempt', operations: [] }, invalidatedNodes: ['rabi', 'iq'], reason: request.reason, createdAt: '2026-11-18T00:00:00Z' };

test('withdrawal candidates require stopped real workflow and active or fixed-failed original marker', () => {
  const before = JSON.stringify(workflow);
  for (const status of ['paused', 'attention'] as const) for (const state of ['active', 'fixed_failed']) {
    assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, status, readoutRevalidations: { Q11: { ...marker, state } } }).map(item => item.id), [marker.id]);
  }
  for (const status of ['draft', 'running', 'queued', 'completed', 'failed', 'aborted'] as const) assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, status }), []);
  for (const mode of ['replay', undefined] as const) assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, mode }), []);
  for (const state of ['completed', 'withdrawn', 'unknown', '']) assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, readoutRevalidations: { Q11: { ...marker, state } } }), []);
  assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, readoutRevalidations: { Q11: { ...marker, origin: 'operator_withdrawal' } } }), []);
  assert.equal(JSON.stringify(workflow), before);
});

test('missing or ambiguous marker identities never form a withdrawal proposal', () => {
  for (const patch of [{ id: '' }, { sourceAttemptId: '' }, { sourceAttemptId: ' ' }]) assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, readoutRevalidations: { Q11: { ...marker, ...patch } } }), []);
  assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, readoutRevalidations: undefined }), []);
  assert.deepEqual(readoutWithdrawalCandidates({ ...workflow, readoutRevalidations: { Q11: marker, Q18: { ...marker, resourceId: 'Q18' } } }), []);
});

test('form proposal freezes revision/source and requires a bounded reason', () => {
  assert.equal(readoutWithdrawalError(workflow, request), undefined);
  assert.match(readoutWithdrawalError({ ...workflow, revision: 18 }, request)!, /版本已更新/);
  assert.match(readoutWithdrawalError({ ...workflow, status: 'running' }, request)!, /状态已变化/);
  for (const patch of [{ sourceAttemptId: 'other' }, { revalidationId: 'other' }]) assert.match(readoutWithdrawalError(workflow, { ...request, ...patch })!, /记录或任务状态/);
  assert.match(readoutWithdrawalError(workflow, { ...request, reason: ' ' })!, /填写/);
  assert.match(readoutWithdrawalError(workflow, { ...request, reason: 'x'.repeat(1001) })!, /1000/);
});

const compiled = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/ReadoutWithdrawal.tsx', import.meta.url))], bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', loader: { '.css': 'empty' } }).outputFiles[0].text;
const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
new Function('require', 'module', 'exports', compiled)(createRequire(import.meta.url), module, module.exports);
const render = (component: Parameters<typeof createElement>[0], props: Record<string, unknown>) => renderToStaticMarkup(createElement(component, props));

test('actual entry renders only actionable candidates and preserves the stopped-state receipt', () => {
  const component = module.exports.default;
  assert.match(render(component, { workflow, gateway: {} }), /撤回读出候选并重新验证/);
  assert.equal(render(component, { workflow: { ...workflow, status: 'running' }, gateway: {} }), '');
  const saved = render(component, { workflow: { ...workflow, lastReadoutWithdrawal: receipt, readoutRevalidations: { Q11: { ...marker, origin: 'operator_withdrawal' } } }, gateway: {} });
  assert.match(saved, /最近一次读出候选撤回/); assert.doesNotMatch(saved, /<button/);
  assert.match(saved, /未重新验证（unverified）/); assert.doesNotMatch(saved, /恢复已通过|自动继续/);
});

test('actual form explains revalidation and displays async errors without parameter controls', () => {
  const props = { marker, reason: '', busy: false, locked: false, error: '任务版本冲突，请重新打开', onReason() {}, onCancel() {}, onSubmit() {} };
  const html = render(module.exports.ReadoutWithdrawalForm, props);
  assert.match(html, /role="dialog"/); assert.match(html, /服务端将恢复同一任务原 IQ 验收记录/);
  assert.match(html, /新参数版本/); assert.match(html, /所有原始数据保留/); assert.match(html, /保存后任务保持暂停/);
  assert.match(html, /optimizer-attempt/); assert.match(html, /role="alert"/); assert.match(html, /版本冲突/);
  assert.match(html, /type="submit" disabled=""/); assert.doesNotMatch(html, /type="number"/);
  const locked = render(module.exports.ReadoutWithdrawalForm, { ...props, reason: request.reason, locked: true });
  assert.match(locked, /aria-label="撤回原因"[^>]*disabled=""/); assert.match(locked, /重试同一请求/);
});

test('actual receipt shows values, versions and both attempt sources without asserting scientific acceptance', () => {
  const before = JSON.stringify(receipt);
  const html = render(module.exports.ReadoutWithdrawalReceipt, { receipt });
  for (const text of ['7000000000', '7000100000', '0.3', '0.18', 'v8', 'v9', 'optimizer-attempt', 'baseline-iq-attempt', '未重新验证']) assert.ok(html.includes(text), text);
  assert.match(html, /待复测节点：2/); assert.doesNotMatch(html, /恢复已通过|复测已通过|校准完成/);
  assert.equal(JSON.stringify(receipt), before);
});

test('gateway sends only five identity/reason fields, accepts paused result, and never resumes', async () => {
  const writes: { url: string; init: RequestInit }[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (init?.method) writes.push({ url: String(url), init });
    return new Response(JSON.stringify(String(url).endsWith('/session') ? { token: 'offline-token' } : { ...workflow, revision: 18, lastReadoutWithdrawal: receipt }), { headers: { 'Content-Type': 'application/json' } });
  }) as typeof fetch });
  const result = await gateway.readoutWithdrawal('wf/a', { ...request, values: { readout_amp: 999 } } as ReadoutWithdrawalRequest);
  assert.equal(result.status, 'paused'); assert.equal(result.lastReadoutWithdrawal?.validationStatus, 'unverified');
  assert.equal(writes.length, 1); assert.equal(writes[0].url, '/api/workflows/wf%2Fa/readout-withdrawal'); assert.equal(writes[0].init.method, 'POST');
  assert.deepEqual(JSON.parse(writes[0].init.body as string), request);
  assert.equal((writes[0].init.headers as Record<string, string>)['X-Qcal-Token'], 'offline-token');
});

test('authentication retry and explicit network retry retain the same idempotent request', async () => {
  let sessions = 0; const payloads: string[] = []; const tokens: string[] = [];
  const gateway = createRemoteGateway({ fetch: (async (url, init) => {
    if (String(url).endsWith('/session')) return new Response(JSON.stringify({ token: `offline-${++sessions}` }));
    payloads.push(init!.body as string); tokens.push((init!.headers as Record<string, string>)['X-Qcal-Token']);
    if (payloads.length === 1) return new Response(JSON.stringify({ error: { message: 'Expired', code: 'UNAUTHORIZED' } }), { status: 401 });
    if (payloads.length === 2) throw new Error('offline network response lost');
    return new Response(JSON.stringify({ ...workflow, revision: 18, lastReadoutWithdrawal: receipt }));
  }) as typeof fetch });
  await assert.rejects(gateway.readoutWithdrawal(workflow.id, request), /response lost/);
  await gateway.readoutWithdrawal(workflow.id, request);
  assert.equal(sessions, 2); assert.equal(payloads.length, 3); assert.equal(new Set(payloads).size, 1);
  assert.deepEqual(tokens, ['offline-1', 'offline-2', 'offline-2']);
});

test('conflict errors remain explicit and are not retried as a new operation', async () => {
  let writes = 0;
  const gateway = createRemoteGateway({ fetch: (async (url) => {
    if (String(url).endsWith('/session')) return new Response(JSON.stringify({ token: 'offline-token' }));
    writes++; return new Response(JSON.stringify({ error: { message: '任务版本已变化', code: 'REVISION_CONFLICT' } }), { status: 409 });
  }) as typeof fetch });
  await assert.rejects(gateway.readoutWithdrawal(workflow.id, request), (error: unknown) => error instanceof ApiError && error.status === 409 && error.code === 'REVISION_CONFLICT');
  assert.equal(writes, 1);
});
