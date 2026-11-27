import test from 'node:test';
import assert from 'node:assert/strict';
import { currentResultComparisonSource, attemptResultComparisonSource } from '../src/lib/resultComparisonSource.ts';
import type { Attempt, ExperimentNode, MeasurementResult, Workflow } from '../src/types.ts';

export function fixture(procedureId = 'sq_echo') {
  const result: MeasurementResult = { dataKind: 'real', sourceRunId: 'attempt-viewed/verify', acquiredAt: '2026-11-19T05:26:18+08:00', metrics: {}, quality: { status: 'failed', reasons: ['quality_attention'] }, artifacts: [{ id: 'registered-raw', kind: 'raw', name: 'iq.npz', url: '/api/artifacts/raw', sha256: 'a'.repeat(64) }], series: [{ name: 'IQ', fit: false, x: [0, 20e-6, 40e-6], y: [.2, .3, .1], xUnit: 's' }] };
  const node = { id: 'Q11:echo', resourceId: 'Q11', procedureId, result, latestAttemptId: 'attempt-newer', name: 'Echo', label: 'Echo', qubits: 'Q11', status: 'pending', engineState: 'idle', progress: 0, attempt: 3, detail: '', capability: 'available', dependsOn: [] } as ExperimentNode;
  const workflow = { id: 'wf-history', mode: 'real', revision: 10, policy: 'strict', status: 'paused', engineState: 'paused', nodes: [node], events: [], currentNodeId: node.id, name: 'History', target: 'Q11', type: 'adaptive' } as unknown as Workflow;
  const attempt = { id: 'attempt-viewed', nodeId: node.id, mode: 'real', procedureId, result, number: 2, state: 'quality_attention', startedAt: result.acquiredAt, operationId: 'attempt-viewed/measure', parameters: {}, parameterVersions: {} } as Attempt;
  return { workflow, node, attempt, result };
}

test('current result uses its displayed source, never the newer latest attempt', () => {
  const f = fixture(), before = JSON.stringify(f);
  assert.deepEqual(currentResultComparisonSource(f.workflow, f.node), { workflowId: f.workflow.id, nodeId: f.node.id, attemptId: 'attempt-viewed' });
  assert.equal(JSON.stringify(f), before);
  delete f.result.sourceRunId;
  assert.equal(currentResultComparisonSource(f.workflow, f.node), undefined);
});

test('only the five supported time-domain procedures expose original-point simulation', () => {
  for (const method of ['sq_rabi_time', 'sq_t1', 'sq_ramsey', 'sq_echo', 'sq_cpmg']) {
    const f = fixture(method); assert.ok(currentResultComparisonSource(f.workflow, f.node), method);
  }
  for (const method of ['sq_rabi_amplitude', 'sq_spectroscopy', 'sq_rabi_frequency', 'sq_drag_irb', 'ro_iq01', 'unknown']) {
    const f = fixture(method); assert.equal(currentResultComparisonSource(f.workflow, f.node), undefined, method);
  }
});

test('no entry for replay, incomplete evidence, pending verification or non-numeric/fit-only curves', () => {
  const changes: ((f: ReturnType<typeof fixture>) => void)[] = [
    f => { f.workflow.mode = 'replay'; }, f => { f.workflow.nodes = []; },
    f => { f.result.dataKind = 'historical'; }, f => { f.result.acquiredAt = ''; },
    f => { f.result.artifacts = []; }, f => { f.result.artifacts[0].sha256 = ''; },
    f => { f.result.series = []; }, f => { f.result.series![0].fit = true; },
    f => { f.result.series![0].y[0] = NaN; }, f => { f.result.series![0].x[1] = Infinity; },
    f => { f.result.series![0].y = [1]; }, f => { f.result.verificationPending = true; },
    f => { f.result.sourceRunId = 'different-format'; },
  ];
  for (const change of changes) { const f = fixture(); change(f); assert.equal(currentResultComparisonSource(f.workflow, f.node), undefined); }
});

test('historical entry keeps failed/stale evidence but rejects another node, method or result attempt', () => {
  const f = fixture(); f.result.stale = true;
  assert.deepEqual(attemptResultComparisonSource(f.workflow, f.node, f.attempt), { workflowId: f.workflow.id, nodeId: f.node.id, attemptId: f.attempt.id });
  const changes: ((a: Attempt) => void)[] = [a => { a.nodeId = 'Q18:echo'; }, a => { a.mode = 'replay'; }, a => { a.procedureId = 'sq_t1'; }, a => { a.id = 'attempt-other'; }];
  for (const change of changes) { const a = structuredClone(f.attempt); change(a); assert.equal(attemptResultComparisonSource(f.workflow, f.node, a), undefined); }
});

test('signal entry also supports power maps and keeps original attempt identity without a fitted 1D curve', () => {
  const f = fixture('ro_power_shift'); f.result.series = [];
  const expected = {workflowId:f.workflow.id,nodeId:f.node.id,attemptId:f.attempt.id,panel:'signals'};
  assert.equal(currentResultComparisonSource(f.workflow,f.node),undefined);
  assert.deepEqual(currentResultComparisonSource(f.workflow,f.node,'signals'),expected);
  assert.deepEqual(attemptResultComparisonSource(f.workflow,f.node,f.attempt,'signals'),expected);
  assert.equal(attemptResultComparisonSource(f.workflow,f.node,{...f.attempt,nodeId:'other'},'signals'),undefined);
});
