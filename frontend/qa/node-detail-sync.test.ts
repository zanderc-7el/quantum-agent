import test from 'node:test';
import assert from 'node:assert/strict';
import { createNodeDetailSync, nodeDetailBoundary } from '../src/lib/nodeDetailSync.ts';
import type { ExperimentNode, Workflow } from '../src/types.ts';

const flush = async () => { for (let i = 0; i < 12; i++) await Promise.resolve(); };
function deferred<T>() {
  let resolve!: (value: T) => void, reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function clock() {
  let now = 0, sequence = 0;
  const timers = new Map<number, { at: number; callback: () => void }>();
  return { options: { now: () => now,
    schedule: (callback: () => void, delay: number) => { const id = ++sequence; timers.set(id, { at: now + delay, callback }); return id as unknown as ReturnType<typeof setTimeout>; },
    cancel: (timer: ReturnType<typeof setTimeout>) => { timers.delete(timer as unknown as number); } },
    advance(ms: number) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.callback(); } },
    pending: () => timers.size };
}

test('continuous 0.5-second SSE revisions retain existing history and coalesce GETs at two seconds', async () => {
  const time = clock(); let calls = 0, shown: number | undefined;
  const values: number[] = [];
  const sync = createNodeDetailSync({ ...time.options, read: async () => ++calls,
    onValue: value => { shown = value; values.push(value); }, onError: () => assert.fail('unexpected error') });
  sync.observe(1, 'attempt-1/acquiring'); await flush();
  for (let revision = 2; revision <= 21; revision++) {
    time.advance(500); sync.observe(revision, 'attempt-1/acquiring'); await flush();
    assert.notEqual(shown, undefined, 'same-node refresh must retain its previous detail');
  }
  assert.equal(calls, 6); assert.deepEqual(values, [1, 2, 3, 4, 5, 6]);
  sync.dispose(); assert.equal(time.pending(), 0);
});

test('phase/attempt change bypasses ordinary throttle; pending old-phase response never replaces new-boundary history', async () => {
  const time = clock(), old = deferred<string>(); let calls = 0;
  const shown: string[] = [];
  const sync = createNodeDetailSync({ ...time.options,
    read: () => ++calls === 1 ? old.promise : Promise.resolve('verification-history'),
    onValue: value => shown.push(value), onError: () => assert.fail('unexpected error') });
  sync.observe(1, 'attempt-1/acquiring'); await flush();
  time.advance(50); sync.observe(2, 'attempt-1/verifying'); sync.observe(3, 'attempt-1/verifying');
  assert.equal(calls, 1, 'single flight until current GET settles');
  old.resolve('obsolete-selection-history'); await flush();
  assert.equal(calls, 2); assert.deepEqual(shown, ['verification-history']);
  time.advance(50); sync.observe(4, 'attempt-2/acquiring'); await flush();
  assert.equal(calls, 3, 'new attempt does not wait two seconds');
  sync.dispose();
});

test('real Echo measure-to-verify operation boundary rejects late selection detail with identical attempt and acquiring states', async () => {
  const time = clock(), selection = deferred<string>(); let calls = 0;
  const shown: string[] = [];
  const workflow = { status: 'running', engineState: 'acquiring' } as Workflow;
  const node = { id: 'Q11:s07_t2_echo', attempt: 2, latestAttemptId: 'attempt-echo',
    engineState: 'acquiring', status: 'running', telemetry: { controllerOperationId: 'attempt-echo/measure',
      operationId: 'attempt-echo/measure', completedRows: 82, totalRows: 82 } } as ExperimentNode;
  const sync = createNodeDetailSync({ ...time.options,
    read: () => ++calls === 1 ? selection.promise : Promise.resolve('fresh-verification-history'),
    onValue: value => shown.push(value), onError: () => assert.fail('unexpected error') });
  sync.observe(100, nodeDetailBoundary(workflow, node)); await flush();
  time.advance(50);
  node.telemetry = { controllerOperationId: 'attempt-echo/verify', operationId: 'attempt-echo/verify', completedRows: 1, totalRows: 82 };
  sync.observe(101, nodeDetailBoundary(workflow, node));
  selection.resolve('late-selection-history'); await flush();
  assert.equal(calls, 2, 'verification GET follows immediately without ordinary two-second delay');
  assert.deepEqual(shown, ['fresh-verification-history']);
  sync.dispose();
});

test('boundary includes explicit phase and legacy operationId but ignores point-level telemetry changes', () => {
  const workflow = { status: 'running', engineState: 'acquiring' } as Workflow;
  const node = { attempt: 1, latestAttemptId: 'attempt-one', status: 'running', engineState: 'acquiring',
    telemetry: { operationId: 'attempt-one/measure', phase: 'selection', completedRows: 1, progress: 1 } } as ExperimentNode;
  const original = nodeDetailBoundary(workflow, node);
  node.telemetry = { ...node.telemetry, completedRows: 50, progress: 60, rowNumber: 50 };
  assert.equal(nodeDetailBoundary(workflow, node), original);
  node.telemetry = { ...node.telemetry, controllerOperationId: 'attempt-one/measure' };
  assert.equal(nodeDetailBoundary(workflow, node), original, 'controller identity overrides an equivalent legacy identity');
  node.telemetry = { ...node.telemetry, operationId: 'adapter-internal-job' };
  assert.equal(nodeDetailBoundary(workflow, node), original, 'internal job ID must not override controller operation');
  node.telemetry = { ...node.telemetry, phase: 'verification' };
  assert.notEqual(nodeDetailBoundary(workflow, node), original);
  node.telemetry = { operationId: 'attempt-one/verify', phase: 'selection' };
  assert.notEqual(nodeDetailBoundary(workflow, node), original, 'legacy operation fallback is still a boundary');
});

test('switching workflow/node disposes old response and timer, including late rejection', async () => {
  for (const reject of [false, true]) {
    const time = clock(), old = deferred<string>(), shown: string[] = [], errors: string[] = [];
    const first = createNodeDetailSync({ ...time.options, read: () => old.promise, onValue: v => shown.push(v), onError: e => errors.push(e) });
    first.observe(1, 'old-object'); await flush(); first.observe(2, 'old-object'); first.dispose();
    const second = createNodeDetailSync({ ...time.options, read: async () => 'new-object', onValue: v => shown.push(v), onError: e => errors.push(e) });
    second.observe(1, 'new-object'); await flush();
    if (reject) old.reject(new Error('obsolete error')); else old.resolve('obsolete detail');
    await flush(); time.advance(5000); await flush();
    assert.deepEqual(shown, ['new-object']); assert.deepEqual(errors, []); assert.equal(time.pending(), 0);
    second.dispose();
  }
});

test('refresh failure keeps previous detail and later revision recovers without a blank state', async () => {
  const time = clock(); let calls = 0, shown: string | undefined;
  const errors: string[] = [];
  const sync = createNodeDetailSync({ ...time.options, read: async () => { if (++calls === 2) throw new Error('offline temporary error'); return 'history-' + calls; },
    onValue: value => { shown = value; }, onError: error => errors.push(error) });
  sync.observe(1, 'same'); await flush(); time.advance(2000); sync.observe(2, 'same'); await flush();
  assert.equal(shown, 'history-1'); assert.deepEqual(errors, ['offline temporary error']);
  time.advance(2000); sync.observe(3, 'same'); await flush(); assert.equal(shown, 'history-3'); sync.dispose();
});

test('unchanged snapshots do not refetch and unmount cancels trailing GET', async () => {
  const time = clock(); let calls = 0;
  const sync = createNodeDetailSync({ ...time.options, read: async () => ++calls, onValue: () => {}, onError: () => {} });
  sync.observe(1, 'same'); await flush();
  for (let i = 0; i < 50; i++) sync.observe(1, 'same');
  time.advance(100); sync.observe(2, 'same'); assert.equal(time.pending(), 1);
  sync.dispose(); time.advance(5000); await flush(); assert.equal(calls, 1); assert.equal(time.pending(), 0);
});
