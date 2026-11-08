import test from 'node:test';
import assert from 'node:assert/strict';
import { makeDemoWorkflows } from '../src/demo-workflows.mjs';
const input = { name: 'Server demo', machine: 'Simulated QPU', qubits: 1, target: 'Q12', type: 'adaptive', policy: 'autopilot', scenario: 'normal' };

test('server demo creates, starts, pauses, resumes and completes using original simulator', () => {
  const service = makeDemoWorkflows();
  const draft = service.create(input);
  assert.equal(draft.status, 'draft');
  assert.equal(service.act(draft.id, 'start').status, 'running');
  service.advance(2);
  const paused = service.act(draft.id, 'pause');
  service.advance(100);
  assert.deepEqual(service.get(draft.id), paused);
  assert.equal(service.act(draft.id, 'resume').status, 'running');
  service.advance(100);
  assert.equal(service.get(draft.id).status, 'completed');
  assert.equal(service.get(draft.id).progress, 100);
  assert.deepEqual(service.act(draft.id, 'start'), service.get(draft.id));
});

test('strict approvals and two-qubit workflows retain simulator semantics', () => {
  const service = makeDemoWorkflows();
  const draft = service.create({ ...input, experiment: 'two', qubits: 2, target: 'Q12,Q13', policy: 'strict' });
  service.act(draft.id, 'start');
  service.advance(100);
  assert.equal(service.get(draft.id).status, 'attention');
  service.policy(draft.id, 'autopilot');
  assert.equal(service.get(draft.id).status, 'attention');
  service.act(draft.id, 'approve');
  service.advance(300);
  assert.equal(service.get(draft.id).status, 'completed');
});

test('bad inputs fail explicitly, not found is 404 and snapshots are isolated', () => {
  const service = makeDemoWorkflows();
  for (const value of [null, [], {}, { ...input, name: 42 }, { ...input, status: 'completed' }, { ...input, ownerId: '../admin' }]) assert.throws(() => service.create(value), error => error.code === 'INVALID_INPUT');
  assert.throws(() => service.get('missing'), error => error.status === 404);
  const workflow = service.create(input);
  assert.throws(() => service.act(workflow.id, 'launch-real-device'), error => error.code === 'INVALID_INPUT');
  assert.throws(() => service.policy(workflow.id, 'invalid'), error => error.code === 'INVALID_INPUT');
  const list = service.list(); list[0].name = 'changed';
  assert.equal(service.get(workflow.id).name, input.name);
});

test('persistent workflows survive restart and running workflows pause', () => {
  let persisted;
  const service = makeDemoWorkflows({ save: state => { persisted = state; } });
  const workflow = service.create(input);
  service.act(workflow.id, 'start'); service.advance(1);
  const restarted = makeDemoWorkflows({ load: () => persisted, save: state => { persisted = state; } });
  assert.equal(restarted.get(workflow.id).status, 'paused');
  assert.equal(restarted.get(workflow.id).elapsed, 1);
  assert.equal(persisted.workflows[0].status, 'paused');
  assert.throws(() => makeDemoWorkflows({ load: () => ({ version: 1, workflows: [{}] }) }), /Invalid persisted/);
});

test('persistence failure does not acknowledge or mutate state', () => {
  const service = makeDemoWorkflows({ load: () => ({ version: 1, workflows: [] }), save: () => { throw new Error('disk unavailable'); } });
  assert.throws(() => service.create(input), /disk unavailable/);
  assert.deepEqual(service.list(), []);
});


test('only fresh storage receives two inactive demo seeds', () => {
  const fresh = makeDemoWorkflows();
  assert.equal(fresh.list().length, 2);
  assert.deepEqual(fresh.list().map(workflow => workflow.experiment), ['single', 'two']);
  assert.ok(fresh.list().every(workflow => workflow.status === 'draft'));
  assert.deepEqual(makeDemoWorkflows({ load: () => ({ version: 1, workflows: [] }) }).list(), []);
});
