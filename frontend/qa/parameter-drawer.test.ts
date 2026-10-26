import test from 'node:test';
import assert from 'node:assert/strict';
import { applyAction, createWorkflow, tickWorkflow } from '../src/lib/simulator.ts';
import { selectDrawerNode } from '../src/lib/parameterDrawerState.ts';

function fresh() {
  return createWorkflow({ name: '参数侧栏验收', machine: 'M7', experiment: 'single', qubits: 1, target: 'Q12', type: 'adaptive', policy: 'threshold', scenario: 'normal' });
}

test('floating parameters follow the running node as execution advances', () => {
  const draft = fresh();
  assert.equal(selectDrawerNode(draft)?.id, 'ReadoutFreqDip');
  const running = tickWorkflow(applyAction(draft, 'start'), 5);
  assert.equal(selectDrawerNode(running)?.id, 'F01Spectroscopy');
  assert.equal(selectDrawerNode(running)?.progress, 25);
});

test('pinned parameters keep their node but resolve values from the new snapshot', () => {
  const initial = tickWorkflow(applyAction(fresh(), 'start'), 1);
  const id = initial.nodes[0].id;
  const later = tickWorkflow(initial, 4);
  assert.equal(selectDrawerNode(later, id)?.id, id);
  assert.equal(selectDrawerNode(later, id)?.progress, 100);
  assert.equal(selectDrawerNode(later, id)?.status, 'success');
  assert.equal(selectDrawerNode(initial, id)?.progress, 25);
  assert.equal(selectDrawerNode(later, 'removed-node')?.id, 'F01Spectroscopy');
});

test('abort halfway follows the last executed node instead of unexecuted abandoned nodes', () => {
  const halfway = tickWorkflow(applyAction(fresh(), 'start'), 5);
  const aborted = applyAction(halfway, 'abort');
  assert.equal(aborted.status, 'aborted');
  assert.equal(selectDrawerNode(aborted)?.id, 'F01Spectroscopy');
  assert.equal(selectDrawerNode(aborted)?.progress, 25);
  assert.equal(selectDrawerNode(aborted)?.attempt, 1);
  assert.equal(selectDrawerNode(aborted)?.status, 'abandoned');
  assert.notEqual(selectDrawerNode(aborted)?.id, aborted.nodes.at(-1)?.id);
});

test('terminal tasks with no execution keep the first node and an empty task stays empty', () => {
  const draft = fresh();
  const unstarted = { ...draft, status: 'aborted' as const, nodes: draft.nodes.map(node => ({ ...node, status: 'abandoned' as const })) };
  assert.equal(selectDrawerNode(unstarted)?.id, 'ReadoutFreqDip');
  assert.equal(selectDrawerNode({ ...unstarted, nodes: [] }), undefined);
});

test('completion keeps the last executed node and attention takes priority', () => {
  const complete = tickWorkflow(applyAction(fresh(), 'start'), 1000);
  assert.equal(complete.status, 'completed');
  assert.equal(selectDrawerNode(complete)?.id, complete.nodes.at(-1)?.id);
  assert.equal(selectDrawerNode(complete)?.progress, 100);
  const exception = tickWorkflow(applyAction({ ...fresh(), scenario: 'anomaly' }, 'start'), 1000);
  assert.equal(selectDrawerNode(exception)?.status, 'attention');
  assert.equal(selectDrawerNode(exception)?.id, 'ReadoutParaSweepByIQProb');
});
