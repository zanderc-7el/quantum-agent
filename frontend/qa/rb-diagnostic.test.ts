import test from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { buildSync } from 'esbuild';
import { createElement } from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { ApiError, createRemoteGateway } from '../src/lib/remoteGateway.ts';
import { workflowControls } from '../src/lib/workflowControls.ts';
import { createRbDiagnosticFlow, rbDiagnosticEligible, rbDiagnosticReceiptMatches, rbDiagnosticRequest } from '../src/lib/rbDiagnostic.ts';
import type { RbDiagnosticInspection, RbDiagnosticMarker, RbDiagnosticRequest, Workflow } from '../src/types.ts';
import type { RbDiagnosticViewState } from '../src/lib/rbDiagnostic.ts';

const policy = 'rb-after-cpmg-diagnostic/1';
const sourceProofSha256 = 'a'.repeat(64);
const wf = (): Workflow => ({ id: 'fixture-wf', mode: 'real', coverage: 'working_point', adapterId: 'arclight-private-f8-q11-v2',
  revision: 7, status: 'attention', engineState: 'quality_attention', currentNodeId: 'cpmg', policy: 'autopilot', nodes: [
    { id: 'cpmg', resourceId: 'Q11', procedureId: 'sq_cpmg', latestAttemptId: 'cpmg-failed', attempt: 4, status: 'attention',
      engineState: 'quality_attention', result: { quality: { status: 'failed' } } },
    { id: 'rb', resourceId: 'Q11', procedureId: 'sq_rb', attempt: 0, engineState: 'stale', status: 'waiting', dependsOn: ['cpmg'] },
  ] } as unknown as Workflow);
const inspection = (): RbDiagnosticInspection => ({ schema: 'qcal-rb-diagnostic-inspection/1', workflowId: 'fixture-wf',
  revision: 7, policyVersion: policy, eligible: true, reasons: [], sourceNodeId: 'cpmg', sourceAttemptId: 'cpmg-failed',
  targetNodeId: 'rb', sourceProofSha256, physicalParameterVersions: {}, marker: null });
const marker = (state: RbDiagnosticMarker['state'] = 'queued'): RbDiagnosticMarker => ({ id: 'marker-1',
  commandOperationId: 'op-one', policyVersion: policy, state, sourceNodeId: 'cpmg', sourceAttemptId: 'cpmg-failed',
  targetNodeId: 'rb', attemptId: state === 'queued' ? null : 'rb-attempt', sourceProofSha256, physicalParameterVersions: {},
  requestedAt: '2026-11-19T00:00:00Z', publication: 'disabled_diagnostic_only' });
const receipt = (state: RbDiagnosticMarker['state'] = 'queued'): Workflow => ({ ...wf(), revision: 8,
  currentNodeId: 'rb', status: state === 'queued' ? 'queued' : state === 'running' ? 'running' : 'attention',
  engineState: state === 'queued' ? 'diagnostic_queued' : 'diagnostic_complete', rbDiagnostic: marker(state) });
const deferred = <T>() => { let resolve!: (v: T) => void, reject!: (e: unknown) => void;
  const promise = new Promise<T>((a, b) => { resolve = a; reject = b; }); return { promise, resolve, reject }; };

test('only exact current eligible inspection creates the seven-field operator command without physical values or target', () => {
  const workflow = wf(), report = inspection(), before = JSON.stringify([workflow, report]);
  assert.equal(rbDiagnosticEligible(workflow, report), true);
  assert.deepEqual(rbDiagnosticRequest(workflow, report, 'op-one'), { action: 'queue_rb_diagnostic', operationId: 'op-one',
    expectedRevision: 7, nodeId: 'cpmg', attemptId: 'cpmg-failed', policyVersion: policy, sourceProofSha256 });
  assert.equal(JSON.stringify([workflow, report]), before);
});

test('missing, malformed, stale or unapproved inspection never enables execution', () => {
  for (const patch of [{ eligible: false }, { eligible: 'true' }, { schema: 'unknown' }, { policyVersion: 'new' },
    { workflowId: 'other' }, { revision: 6 }, { revision: NaN }, { revision: '7' }, { sourceNodeId: 'other' },
    { targetNodeId: 'cpmg' }, { sourceAttemptId: 'old' }, { sourceProofSha256: '' }, { sourceProofSha256: 'x'.repeat(64) }])
    assert.equal(rbDiagnosticEligible(wf(), { ...inspection(), ...patch } as RbDiagnosticInspection), false);
  for (const key of ['schema', 'workflowId', 'revision', 'policyVersion', 'eligible', 'sourceNodeId', 'sourceAttemptId', 'targetNodeId', 'sourceProofSha256']) {
    const report = inspection(); delete (report as unknown as Record<string, unknown>)[key];
    assert.equal(rbDiagnosticEligible(wf(), report), false, key);
  }
  assert.equal(rbDiagnosticEligible(wf(), undefined), false);
});

test('server eligibility is never reused across modes, adapters, active tasks or changed source nodes', () => {
  const changes: ((value: Workflow) => void)[] = [
    x => { x.mode = 'replay'; }, x => { x.coverage = 'full'; }, x => { x.adapterId = 'arclight-private-f8-q11-v1'; },
    x => { x.status = 'running'; }, x => { x.revision = 8; }, x => { x.nodes[0].resourceId = 'Q18'; },
    x => { x.nodes[0].result!.stale = true; }, x => { x.nodes[0].result!.quality.status = 'passed'; },
    x => { x.nodes[0].latestAttemptId = 'newer'; }, x => { x.nodes.push({ ...x.nodes[0] }); },
    x => { x.nodes[1].engineState = 'verified'; },
  ];
  for (const change of changes) { const workflow = wf(); change(workflow); assert.equal(rbDiagnosticEligible(workflow, inspection()), false); }
});

test('receipt requires command operation ID and all source bindings, not proof alone', () => {
  const request = rbDiagnosticRequest(wf(), inspection(), 'op-one')!;
  assert.equal(rbDiagnosticReceiptMatches(receipt(), request, 'rb'), true);
  for (const field of ['commandOperationId', 'policyVersion', 'sourceNodeId', 'sourceAttemptId', 'targetNodeId', 'sourceProofSha256', 'publication']) {
    const workflow = receipt(); (workflow.rbDiagnostic as unknown as Record<string, unknown>)[field] = 'other';
    assert.equal(rbDiagnosticReceiptMatches(workflow, request, 'rb'), false, field);
  }
});

function harness() {
  let workflow = wf(), checked = inspection(); const history: RbDiagnosticViewState[] = [], calls: unknown[] = [];
  const gateway = { get: async () => { calls.push('get'); return workflow; },
    rbDiagnosticInspection: async () => { calls.push('inspect'); return checked; },
    queueRbDiagnostic: async (_id: string, request: RbDiagnosticRequest) => { calls.push(request); return receipt(); } };
  const make = () => createRbDiagnosticFlow({ workflow: () => workflow, gateway, operationId: () => 'op-one', update: value => history.push(value) });
  return { gateway, calls, history, make, setWorkflow: (v: Workflow) => { workflow = v; }, setInspection: (v: RbDiagnosticInspection) => { checked = v; },
    latest: () => history.at(-1)! };
}

test('explicit check is read-only, then double clicks submit only once and stop on receipt', async () => {
  const h = harness(), flow = h.make(), pending = deferred<Workflow>();
  h.gateway.queueRbDiagnostic = async (_id, request) => { h.calls.push(request); return pending.promise; };
  assert.equal(h.calls.length, 0); await flow.check(); assert.deepEqual(h.calls, ['get', 'inspect']);
  const first = flow.queue(); await flow.queue(); flow.observe();
  assert.equal(h.calls.filter(v => typeof v === 'object').length, 1);
  pending.resolve(receipt()); await first; assert.equal(h.latest().awaitingReceipt, false);
  await flow.queue(); assert.equal(h.calls.filter(v => typeof v === 'object').length, 1);
});

test('unknown POST outcome never auto-resends, and mismatched old receipt does not unlock another command', async () => {
  const h = harness(), flow = h.make();
  h.gateway.queueRbDiagnostic = async (_id, request) => { h.calls.push(request); throw new TypeError('network'); };
  await flow.check(); await flow.queue(); assert.equal(h.latest().awaitingReceipt, true);
  const old = receipt(); old.rbDiagnostic!.commandOperationId = 'old-op'; h.setWorkflow(old);
  await flow.check(); flow.observe(); await flow.queue();
  assert.equal(h.latest().awaitingReceipt, true); assert.equal(h.calls.filter(v => typeof v === 'object').length, 1);
  h.setWorkflow(receipt()); flow.observe(); assert.equal(h.latest().awaitingReceipt, false);
  assert.match(h.latest().message, /本次 RB 诊断指令回执/);
});

test('paused/interrupted matching receipt is displayed but never starts a replacement capture', async () => {
  const h = harness(), flow = h.make();
  h.gateway.queueRbDiagnostic = async (_id, request) => { h.calls.push(request); throw new TypeError('lost'); };
  await flow.check(); await flow.queue(); h.setWorkflow({ ...receipt('interrupted'), status: 'paused' });
  flow.observe(); await flow.queue(); assert.equal(h.calls.filter(v => typeof v === 'object').length, 1);
  assert.equal(h.latest().awaitingReceipt, false);
});

test('definite 409 rejection requires a new explicit eligibility check, with no automatic retry', async () => {
  const h = harness(), flow = h.make();
  h.gateway.queueRbDiagnostic = async (_id, request) => { h.calls.push(request); throw new ApiError('stale', 409); };
  await flow.check(); await flow.queue(); await flow.queue();
  assert.equal(h.latest().inspection, undefined); assert.equal(h.latest().awaitingReceipt, false);
  assert.equal(h.calls.filter(v => typeof v === 'object').length, 1);
});

test('SSE confirmation arriving before a lost POST response remains confirmed', async () => {
  const h = harness(), flow = h.make(), pending = deferred<Workflow>();
  h.gateway.queueRbDiagnostic = async (_id, request) => { h.calls.push(request); return pending.promise; };
  await flow.check(); const queued = flow.queue(); h.setWorkflow(receipt()); flow.observe();
  pending.reject(new TypeError('lost')); await queued;
  assert.equal(h.latest().awaitingReceipt, false); assert.equal(h.latest().error, '');
});

test('disposed StrictMode session and changed workflow cannot receive late eligibility', async () => {
  const h = harness(), old = h.make(), pending = deferred<RbDiagnosticInspection>();
  h.gateway.rbDiagnosticInspection = () => pending.promise;
  const check = old.check(); old.dispose(); const count = h.history.length;
  pending.resolve(inspection()); await check; assert.equal(h.history.length, count);
  const next = h.make(); h.setWorkflow({ ...wf(), id: 'other' }); await next.check(); await next.queue();
  assert.equal(h.calls.filter(v => typeof v === 'object').length, 0);
});

test('ordinary actions cannot enter or restart diagnostic lane; explicit backtrack becomes available only after closure', () => {
  for (const state of ['queued', 'running', 'completed', 'failed', 'interrupted', 'invalidated'] as const) {
    const workflow = receipt(state); workflow.status = state === 'running' ? 'running' : 'attention';
    workflow.nodes[1].result = { dataKind: 'real', metrics: {}, artifacts: [], quality: { status: 'passed', reasons: [] } };
    const controls = workflowControls(workflow);
    for (const action of ['start', 'resume', 'retry', 'approve', 'decide', 'recheck'] as const) assert.equal(controls[action], false, state + action);
    assert.equal(controls.backtrack, !['queued', 'running'].includes(state));
  }
  const normal = wf(); assert.equal(workflowControls(normal).decide, true);
});

test('inspection GET is explicit and seven-field POST reuses token-refresh operation without uploading arbitrary fields', async () => {
  const calls: { url: string; init?: RequestInit }[] = []; let sessions = 0, writes = 0;
  const gateway = createRemoteGateway({ fetch: async (input, init) => {
    const url = String(input); calls.push({ url, init });
    if (url.endsWith('/session')) return Response.json({ token: ++sessions === 1 ? 'first' : 'second', actor: {} });
    if (url.endsWith('/rb-diagnostic')) return Response.json(inspection());
    if (url.endsWith('/actions')) return ++writes === 1 ? Response.json({ error: { message: 'expired' } }, { status: 401 }) : Response.json(receipt());
    throw new Error(url);
  } });
  await gateway.rbDiagnosticInspection('fixture-wf'); assert.equal(writes, 0);
  const request = { ...rbDiagnosticRequest(wf(), inspection(), 'op-one')!, targetNodeId: 'other', parameters: { drive_amp: 999 } };
  await gateway.queueRbDiagnostic('fixture-wf', request);
  const posts = calls.filter(c => c.url.endsWith('/actions'));
  assert.equal(posts.length, 2); assert.equal(posts[0].init!.body, posts[1].init!.body);
  assert.deepEqual(JSON.parse(String(posts[0].init!.body)), rbDiagnosticRequest(wf(), inspection(), 'op-one'));
  assert.equal((posts[1].init!.headers as Record<string, string>)['X-Qcal-Token'], 'second');
});

const compiled = buildSync({ entryPoints: [fileURLToPath(new URL('../src/components/RbDiagnostic.tsx', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs', packages: 'external', loader: { '.css': 'empty' },
}).outputFiles[0].text;
const module = { exports: {} as Record<string, Parameters<typeof createElement>[0]> };
new Function('require', 'module', 'exports', compiled)(createRequire(import.meta.url), module, module.exports);
const render = (name: string, workflow: Workflow) => renderToStaticMarkup(createElement(module.exports[name], { workflow, gateway: {} }));

test('initial component requires server inspection and does not make an executable queue available', () => {
  const html = render('default', wf());
  assert.match(html, /检查 RB 诊断条件/);
  assert.match(html, /disabled=""[^>]*>启动一次 RB 诊断/);
  assert.match(html, /CPMG 未通过记录保留/);
  assert.match(html, /不表示完整流程通过/);
});

test('manual workflows cannot queue the diagnostic bypass even with a previously eligible inspection', () => {
  const workflow = wf(); workflow.policy = 'strict';
  assert.equal(rbDiagnosticEligible(workflow, inspection()), false);
  assert.equal(rbDiagnosticRequest(workflow, inspection(), 'op-manual'), undefined);
  assert.doesNotMatch(render('default', workflow), /启动一次 RB 诊断|检查 RB 诊断条件/);
});

test('completed diagnostic receipt never labels the workflow completed or CPMG passed', () => {
  const workflow = receipt('completed'); workflow.rbDiagnostic!.resultStatus = 'passed';
  workflow.nodes[1].latestAttemptId='rb-attempt';workflow.nodes[1].engineState='verified';
  workflow.nodes[1].result={dataKind:'real',metrics:{},quality:{status:'passed',reasons:[]},artifacts:[],provenance:{freshAcquisition:true}};
  const html = render('RbDiagnosticStatus', workflow);
  assert.match(html, /RB 诊断独立验收通过/); assert.match(html, /不表示 10 项全流程通过/);
  assert.match(html, /不发布校准参数/); assert.doesNotMatch(html, /CPMG 已通过|全部测项通过/);
  workflow.rbDiagnostic!.resultStatus = 'failed';
  assert.doesNotMatch(render('RbDiagnosticStatus', workflow), /RB 诊断独立验收通过/);
  workflow.rbDiagnostic!.resultStatus = 'passed';workflow.nodes[1].result.stale=true;
  assert.doesNotMatch(render('RbDiagnosticStatus', workflow), /RB 诊断独立验收通过/);
  workflow.nodes[1].result.stale=false;workflow.nodes[1].latestAttemptId='another-attempt';
  assert.doesNotMatch(render('RbDiagnosticStatus', workflow), /RB 诊断独立验收通过/);
});
