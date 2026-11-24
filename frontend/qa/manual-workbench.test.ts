import test from 'node:test';
import assert from 'node:assert/strict';
import type { Workflow } from '../src/types.ts';
import { manualCreationMatches, manualWorkbenchAction } from '../src/lib/manualWorkbench.ts';

const fixture = (): Workflow => ({ id: 'manual', name: '人工实验', machine: '八号机', qubits: 1, target: 'Q11', experiment: 'single', type: 'adaptive', policy: 'strict', scenario: 'normal', mode: 'real', status: 'paused', engineState: 'paused', progress: 0, owner: '操作员', createdAt: '2026-11-21', elapsed: 0, iterations: 0, approved: false, events: [], currentNodeId: 'first', nodes: [
  { id: 'first', name: 'S21', label: '找腔', status: 'pending', engineState: 'pending', progress: 0, attempt: 0, qubits: 'Q11', detail: '', dependsOn: [], capability: 'available' },
  { id: 'next', name: 'Rabi', label: '拉比', status: 'pending', engineState: 'pending', progress: 0, attempt: 0, qubits: 'Q11', detail: '', dependsOn: ['first'], capability: 'available' },
] });

test('a historical waiting-hardware node cannot authorize an already completed handover', () => {
  const workflow=fixture(); workflow.engineState='manual_ready'; workflow.hardwareConfirmed=true;
  workflow.nodes[0].engineState='waiting_hardware'; workflow.nodes[0].status='attention';
  assert.equal(manualWorkbenchAction(workflow,'first','confirm_hardware'),undefined);
  workflow.engineState='waiting_hardware'; workflow.status='attention';
  assert.deepEqual(manualWorkbenchAction(workflow,'first','confirm_hardware'),{action:'confirm_hardware'});
});

test('creation receipt reconciliation only opens new matching manual experiments and leaves duplicates ambiguous', () => {
  const attempted = { name: '人工实验', resourceId: 'Q11', knownIds: ['previous'] };
  const matched = { ...fixture(), id: 'created', coverage: 'full' as const, resourceIds: ['Q11'] };
  const records = [matched, { ...matched, id: 'previous' }, { ...matched, id: 'auto', policy: 'autopilot' as const }, { ...matched, id: 'other', resourceIds: ['Q12'], target: 'Q12' }, { ...matched, id: 'name', name: '另一个' }];
  assert.deepEqual(manualCreationMatches(records, attempted).map(item => item.id), ['created']);
  assert.equal(manualCreationMatches([...records, { ...matched, id: 'duplicate' }], attempted).length, 2);
  assert.deepEqual(manualCreationMatches([], attempted), []);
});

test('manual submit targets the selected eligible node and cannot bypass its dependencies', () => {
  const workflow = fixture();
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'run_step'), { action: 'run_step', nodeId: 'first' });
  assert.equal(manualWorkbenchAction(workflow, 'next', 'run_step'), undefined);
  assert.equal(manualWorkbenchAction(workflow, 'foreign', 'run_step'), undefined);
  workflow.currentNodeId = 'next';
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'run_step'), { action: 'run_step', nodeId: 'first' });
});

test('dirty parameters block submit and acceptance without preventing workflow scoped stop', () => {
  const workflow = fixture();
  assert.equal(manualWorkbenchAction(workflow, 'first', 'run_step', true), undefined);
  assert.deepEqual(manualWorkbenchAction(workflow, 'next', 'abort', true), { action: 'abort' });
  workflow.status = 'running'; workflow.engineState = 'acquiring';
  assert.deepEqual(manualWorkbenchAction(workflow, 'next', 'pause', true), { action: 'pause' });
  assert.equal(manualWorkbenchAction(workflow, 'first', 'run_step'), undefined);
});

test('confirmation uses fresh passed result only and binds the exact selected node', () => {
  const workflow = fixture(); workflow.status = 'attention'; workflow.engineState = 'awaiting_acceptance';
  const node = workflow.nodes[0]; node.engineState = 'awaiting_acceptance'; node.attempt = 1;
  node.result = { dataKind: 'real', metrics: {}, artifacts: [], quality: { status: 'passed', reasons: [] } };
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'approve'), { action: 'approve', nodeId: 'first' });
  assert.equal(manualWorkbenchAction(workflow, 'next', 'approve'), undefined);
  assert.equal(manualWorkbenchAction(workflow, 'first', 'approve', true), undefined);
  for (const flag of ['partial', 'stale', 'verificationPending'] as const) {
    node.result[flag] = true;
    assert.equal(manualWorkbenchAction(workflow, 'first', 'approve'), undefined);
    delete node.result[flag];
  }
  node.result.quality.status = 'failed';
  assert.equal(manualWorkbenchAction(workflow, 'first', 'approve'), undefined);
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'backtrack', false, 'first'), { action: 'backtrack', nodeId: 'first' });
});

test('backtrack only accepts the current node adjustment or actual current ancestors', () => {
  const workflow = fixture(); workflow.currentNodeId = 'next';
  workflow.nodes.push({ ...workflow.nodes[0], id: 'sibling', dependsOn: ['first'] });
  assert.deepEqual(manualWorkbenchAction(workflow, 'sibling', 'backtrack', false, 'first'), { action: 'backtrack', nodeId: 'first' });
  assert.equal(manualWorkbenchAction(workflow, 'sibling', 'backtrack', false, 'sibling'), undefined);
  assert.equal(manualWorkbenchAction(workflow, 'next', 'backtrack', false, 'foreign'), undefined);
});

test('a just accepted current experiment can be explicitly reset without launching another measurement', () => {
  const workflow = fixture(); workflow.engineState = 'manual_ready';
  Object.assign(workflow.nodes[0], { status: 'success', engineState: 'verified', attempt: 1, result: { dataKind: 'real', metrics: {}, artifacts: [], quality: { status: 'passed', reasons: [] } } });
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'backtrack', false, 'first'), { action: 'backtrack', nodeId: 'first' });
  assert.equal(manualWorkbenchAction(workflow, 'first', 'run_step'), undefined);
  assert.equal(manualWorkbenchAction(workflow, 'next', 'backtrack', false, 'next'), undefined);
  assert.equal(manualWorkbenchAction(workflow, 'first', 'backtrack', true, 'first'), undefined);
  workflow.nodes[1].engineState = 'needs_reconciliation';
  assert.equal(manualWorkbenchAction(workflow, 'first', 'backtrack', false, 'first'), undefined);
  workflow.nodes[1].engineState = 'pending';
  workflow.status = 'completed';
  assert.equal(manualWorkbenchAction(workflow, 'first', 'backtrack', false, 'first'), undefined);
});

test('manual workspace cannot issue actions on normal workflows or start an automatic chain', () => {
  const workflow = fixture();
  for (const action of ['start', 'resume', 'retry', 'decide'] as const) assert.equal(manualWorkbenchAction(workflow, 'first', action), undefined);
  workflow.policy = 'autopilot';
  for (const action of ['run_step', 'approve', 'backtrack', 'abort'] as const) assert.equal(manualWorkbenchAction(workflow, 'first', action), undefined);
  workflow.policy = 'strict'; workflow.mode = 'replay';
  assert.equal(manualWorkbenchAction(workflow, 'first', 'run_step'), undefined);
});

test('reconciliation and wiring use their existing workflow gates and terminal tasks cannot stop again', () => {
  const workflow = fixture(); workflow.status = 'attention'; workflow.engineState = 'needs_reconciliation';
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'reconcile'), { action: 'reconcile' });
  assert.equal(manualWorkbenchAction(workflow, 'first', 'run_step'), undefined);
  workflow.engineState = 'waiting_hardware';
  assert.deepEqual(manualWorkbenchAction(workflow, 'first', 'confirm_hardware'), { action: 'confirm_hardware' });
  assert.equal(manualWorkbenchAction(workflow, 'first', 'reconcile'), undefined);
  workflow.status = 'completed'; workflow.engineState = 'completed';
  assert.equal(manualWorkbenchAction(workflow, 'first', 'abort'), undefined);
});
